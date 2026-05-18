import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.ai_log import AILog
from app.models.conversation import Conversation
from app.models.knowledge_article import KnowledgeArticle, KnowledgeArticleFeedback
from app.models.message import Message
from app.services.ai_fallback import (
    FALLBACK_REASON_PAYLOAD_KEY,
    record_ai_fallback,
)
from app.services.ai_service_client import ai_service_headers
from app.services.conversation_intent import (
    ConversationAction,
    detect_conversation_policy,
)
from app.services.knowledge_base import (
    LATENCY_PAYLOAD_KEY,
    KnowledgeSearchFilters,
    find_knowledge_answer,
)
from app.services.service_catalog import CatalogItem, detect_catalog_item, get_catalog_item

logger = logging.getLogger(__name__)

# Зеркало SECURITY_TRIGGER_TERMS из ai/ai-service/answerer.py.
# Backend проверяет это ДО обращения к LLM-кэшу и AI-сервису,
# чтобы кэшированный некорректный ответ никогда не вернулся пользователю.
_SECURITY_TERMS = (
    "фишинг",
    "подозрительное письмо",
    "подозрительная ссылка",
    "странная ссылка",
    "вредоносная ссылка",
    "мошенническое письмо",
    "просят пароль",
    "требуют пароль",
    "ввести пароль",
    "ввести пароль по ссылке",
    "ввел пароль",
    "ввёл пароль",
    "вводить пароль",
    "перешел по ссылке",
    "перешёл по ссылке",
    "перейти по ссылке",
    "пройти по ссылке",
    "открыл ссылку",
    "ссылку открыл",
    "открыл вложение",
    "компрометация",
    "скомпрометирован",
    "письмо со ссылкой",
    "письмо с ссылкой",
    "письмо с просьбой",
    "письмо с требованием",
    "прислали ссылку",
    "пришла ссылка",
    "просят перейти",
    "нажмите на ссылку",
    "перейдите по ссылке",
    "подтвердите данные",
    "подтвердите пароль",
    "верифицируйте",
    "проверьте аккаунт",
    "угон аккаунта",
    "взломали почту",
    "взломали аккаунт",
    "не вводите пароль",
    "якобы от it",
    "якобы от ит",
)

_SECURITY_SAFE_ANSWER = (
    "Похоже на фишинг или подозрительное письмо. Не вводите пароль по ссылке "
    "и не открывайте вложения. Если уже перешли по вредоносной ссылке или ввели "
    "пароль, это может быть компрометация учётной записи. "
    "Сохраните письмо и передайте его специалисту для проверки."
)


def _is_security_message(history: list[dict[str, str]]) -> bool:
    text = (
        " ".join(m.get("content", "") for m in history if m.get("role") == "user")
        .casefold()
        .replace("ё", "е")
    )
    return any(term.replace("ё", "е") in text for term in _SECURITY_TERMS)


# ── ПСЕВДО-СТРИМИНГ ────────────────────────────────────────────────────────────
# Вместо реального SSE/WebSocket-стриминга токенов мы имитируем прогресс
# через последовательные обновления поля Conversation.ai_stage. Каждый
# «этап» сохраняется отдельным коротким commit'ом в своей сессии (не в
# транзакции воркера) — это единственный способ сделать изменение видимым
# для клиентов-поллеров до завершения основной джобы.
#
# Каждый этап соответствует реальному шагу в пайплайне:
#   thinking  → разбираем вопрос (до поиска по KB)
#   searching → выполняем гибридный FTS+semantic поиск по KB
#   found_kb  → KB-статья найдена, формируем ответ из неё
#   generating→ LLM формирует ответ (самый долгий шаг)
#   None      → обработка завершена / ошибка; stage сброшен
#
# Клиент читает ai_stage из GET /conversations/ (поллинг conversations
# уже есть при ai_processing=true, интервал 2 сек). Пользователь видит
# "Ищу в базе знаний..." без намёка на "псевдо".


async def _set_ai_stage(conversation_id: int, stage: str | None) -> None:
    """ПСЕВДО-СТРИМИНГ: обновляет стадию обработки в отдельной сессии.

    Открывает новый AsyncSession и сразу делает commit, чтобы поллящие
    клиенты видели стадию ещё во время работы основной транзакции воркера.
    Ошибки намеренно проглатываются — они не должны прерывать основной
    пайплайн генерации ответа.
    """
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as stage_db:
            conv = await stage_db.get(Conversation, conversation_id)
            if conv is not None:
                conv.ai_stage = stage
                await stage_db.commit()
    except Exception:  # noqa: BLE001
        # Не прерываем основной поток: стадия — UX-украшение, не бизнес-логика.
        logger.debug(
            "Не удалось обновить ai_stage для диалога %d",
            conversation_id,
            exc_info=True,
        )


# Лимиты на историю, передаваемую в LLM:
#  - MESSAGES — потолок по штукам (защита от диалогов на 200 сообщений).
#  - TOKENS   — потолок по бюджету токенов (защита от длинных простыней).
#
# AI-сервис принимает list[ChatMessage] с каждым content до 10000 символов,
# но контекстное окно Mistral-7B ~8k токенов; если сложить 20 сообщений
# по 2000 символов, мы переполним окно и модель отрежет начало (или упадёт).
# Соответственно: берём ПОСЛЕДНИЕ 20 сообщений, но если их суммарный
# объём в токенах превышает MAX_HISTORY_TOKENS — выкидываем самые старые,
# пока не уложимся. Самый свежий user-message сохраняем всегда — без него
# у модели нет точки отсчёта.
MAX_HISTORY_MESSAGES = 20
# 1 русский токен ≈ 2-3 символа, английский ≈ 4. Используем оценку 1 токен ≈ 3 символа
# (см. estimate_token_count в knowledge_embeddings — там по словам, что грубее).
# Раньше было 4096 — это давало до ~12k символов в промпте, и на CPU
# каждый токен в prefill'е добавляет 10–20 мс. 2048 токенов (~6k симв)
# покрывает реальный диалог поддержки (5–10 сообщений по 50–500 симв).
# Если нужен больший контекст для конкретного клиента — поднимать через
# отдельную настройку, но дефолт оптимизируем под скорость.
MAX_HISTORY_TOKENS = 2048
_CHARS_PER_TOKEN = 3

# Поля, которые мы соглашаемся хранить в Message.sources. Всё, что приходит
# от AI-сервиса/KB вне этого whitelist'а — отбрасываем: фронтовая схема
# SourceRead типизирована, и неизвестные поля только добавят шум в JSON.
_SOURCE_FIELDS = {
    "title",
    "url",
    "article_id",
    "chunk_id",
    "snippet",
    "retrieval",
    "score",
    "decision",
}
# Sources на одно AI-сообщение лимитируются: 5 ссылок — потолок UX'а.
# Всё, что больше, перегружает чат и обычно не релевантно.
_MAX_SOURCES = 5


def _normalize_sources(raw: object) -> list[dict] | None:
    """Приводит ai_payload['sources'] к консистентному формату для БД.

    Зачем нормализуем:
      - LLM возвращает {title, url}; KB-build возвращает 8 полей; intake/fallback
        возвращают [] (или вообще не возвращают). Сохраняя как есть, JSON-колонка
        обрастает полиморфизмом, и фронт ломается на неожиданном формате.
      - Без `title` source бесполезен (UI рендерит «Источник: <title>»),
        такие записи режем.
      - Дубликаты по `article_id` — частые при гибридном поиске (FTS+semantic
        нашли одну и ту же статью в разных чанках); merge оставляет одну.

    Возвращаем None, если после нормализации список пуст — это
    отличает «AI не присылал источников вообще» от «список приехал, но
    после фильтра остался пустым» (оба → None в БД, но при отладке хорошо
    видеть, что в логах source_input был непустым).
    """
    if not isinstance(raw, list):
        return None

    seen_article_ids: set[int] = set()
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        # Дедуплицируем по article_id (если есть — KB-источники).
        article_id = item.get("article_id")
        if isinstance(article_id, int):
            if article_id in seen_article_ids:
                continue
            seen_article_ids.add(article_id)
        # Оставляем только whitelisted-поля. Если score пришёл строкой
        # ("0.85") — приведение в float делать не будем, фронт сам справится
        # с union[float | str | None]. Главное — не пропускать мусор.
        normalized = {k: v for k, v in item.items() if k in _SOURCE_FIELDS}
        normalized["title"] = title.strip()
        cleaned.append(normalized)
        if len(cleaned) >= _MAX_SOURCES:
            break
    return cleaned or None


def _estimate_tokens(text: str) -> int:
    """Грубая оценка токенов для русско-английских текстов.

    Точный токенайзер (tiktoken/sentencepiece) тянуть в backend не хочется —
    это +30 МБ в Docker-образ ради метрики «сколько примерно». Оценка
    `len(text) / 3` стабильно даёт верхнюю границу для русского текста
    и нижнюю для английского — нам важно не переоценить и обрезать
    лишнее, поэтому занижаем символы на токен (округление вверх).
    """
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


async def load_history_for_ai(
    db: AsyncSession,
    conversation_id: int,
) -> list[dict[str, str]]:
    """История диалога для LLM с учётом токенного бюджета.

    Алгоритм:
      1) Берём последние MAX_HISTORY_MESSAGES сообщений из БД (DESC).
      2) Идём от свежих к старым, копим бюджет MAX_HISTORY_TOKENS.
      3) Как только следующее сообщение не влезает — отбрасываем его и всё,
         что старше (середину диалога не вырезаем — это ломает связность).
      4) Разворачиваем в хронологический порядок для модели.

    Самый свежий user-message — всегда в выдаче, даже если он один превышает
    бюджет (модель сама обрежет, но мы не хотим тихо удалять последний вопрос
    пользователя — он точно нужен).
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
    )
    rows = list(result.scalars().all())  # DESC: [last, ..., first]

    # Копим с самого свежего, отбрасываем старые при переполнении бюджета.
    budget = MAX_HISTORY_TOKENS
    kept_desc: list[Message] = []
    for index, message in enumerate(rows):
        if message.role not in {"user", "ai"}:
            continue
        cost = _estimate_tokens(message.content)
        if cost <= budget or index == 0:
            # Первое (самое свежее) сообщение — всегда оставляем, даже если
            # оно одно перебирает бюджет: без него у LLM нет вопроса.
            kept_desc.append(message)
            budget -= cost
        else:
            break

    kept = list(reversed(kept_desc))
    history: list[dict[str, str]] = []
    for message in kept:
        role = "user" if message.role == "user" else "assistant"
        history.append({"role": role, "content": message.content})
    return history


async def recent_kb_article_ids_for_conversation(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 3,
) -> set[int]:
    result = await db.execute(
        select(KnowledgeArticleFeedback.article_id)
        .where(KnowledgeArticleFeedback.conversation_id == conversation_id)
        .order_by(KnowledgeArticleFeedback.id.desc())
        .limit(limit)
    )
    return {int(article_id) for article_id in result.scalars().all()}


async def negative_kb_article_ids_for_conversation(
    db: AsyncSession,
    conversation_id: int,
) -> set[int]:
    """Статьи, которые пользователь явно отметил как not_helped или not_relevant
    в текущем диалоге. Эти статьи НИКОГДА не должны быть предложены повторно
    в том же диалоге — раздражающий шаблон «AI 3 раза предложил то же самое».

    В отличие от recent_kb_article_ids (которая исключает последние 3 без
    учёта оценки) — здесь мы исключаем по явному negative-сигналу за всю
    историю диалога.
    """
    result = await db.execute(
        select(KnowledgeArticleFeedback.article_id)
        .where(
            KnowledgeArticleFeedback.conversation_id == conversation_id,
            KnowledgeArticleFeedback.feedback.in_(("not_helped", "not_relevant")),
        )
        .distinct()
    )
    return {int(article_id) for article_id in result.scalars().all()}


async def get_ai_answer(
    conversation_id: int,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Спрашивает AI-сервис и возвращает payload с замеренной латенси.

    Латенси (полное время попытки, включая retry на second URL и timeout)
    кладётся в payload[LATENCY_PAYLOAD_KEY] в миллисекундах. Это поле потом
    уходит в AILog.ai_response_time_ms — питч-дек обещает «1,01 сек среднее»,
    и без честного замера эту цифру нечем подтвердить.

    LLM-кэш (см. services/llm_cache.py): на повторные одинаковые
    истории отдаём готовый ответ instant'ом. Не кэшируем неуверенные
    ответы и fallback'и (там стратегия — повторить попытку).
    """
    import httpx

    from app.services.llm_cache import get_llm_cache, is_cacheable

    settings = get_settings()
    started = time.perf_counter()

    def _with_latency(payload: dict[str, Any]) -> dict[str, Any]:
        payload[LATENCY_PAYLOAD_KEY] = int((time.perf_counter() - started) * 1000)
        return payload

    # ── Cache lookup ─────────────────────────────────────────────────────
    # Делаем ДО сетевого вызова. На hit возвращаем за <1мс.
    cache = get_llm_cache()
    cached = cache.get(messages)
    if cached is not None:
        logger.info(
            "LLM cache hit",
            extra={
                "conversation_id": conversation_id,
                "model_version": cached.get("model_version"),
            },
        )
        # _with_latency обновит latency_ms (получится несколько мс — честно).
        return _with_latency(cached)

    fallback = {
        "answer": (
            "Не удалось подготовить надёжный автоматический ответ. "
            "Создам черновик запроса, чтобы специалист получил описание проблемы "
            "и продолжил разбор."
        ),
        "confidence": 0.0,
        "escalate": True,
        "sources": [],
        "model_version": settings.AI_MODEL_VERSION_FALLBACK,
    }

    service_urls = [settings.AI_SERVICE_URL.rstrip("/")]
    if service_urls[0] == "http://ai-service:8001":
        service_urls.append("http://localhost:8001")

    data: Any = None
    last_reason: str | None = None
    try:
        for service_url in service_urls:
            try:
                async with httpx.AsyncClient(timeout=settings.AI_SERVICE_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        f"{service_url}/ai/answer",
                        headers=ai_service_headers(),
                        json={
                            "conversation_id": conversation_id,
                            "messages": messages,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    break
            except httpx.TimeoutException as exc:
                last_reason = "timeout"
                logger.warning(
                    "AI Service timeout: %s",
                    exc,
                    extra={"conversation_id": conversation_id, "ai_service_url": service_url},
                )
            except (httpx.ConnectError, httpx.UnsupportedProtocol) as exc:
                last_reason = "connect"
                logger.warning(
                    "AI Service connect error: %s",
                    exc,
                    extra={"conversation_id": conversation_id, "ai_service_url": service_url},
                )
            except httpx.HTTPStatusError as exc:
                last_reason = "http_5xx"
                logger.warning(
                    "AI Service HTTP error: %s",
                    exc,
                    extra={"conversation_id": conversation_id, "ai_service_url": service_url},
                )
        if data is None:
            fallback[FALLBACK_REASON_PAYLOAD_KEY] = last_reason or "connect"
            return _with_latency(fallback)
    except ValueError as exc:
        logger.warning(
            "AI Service returned invalid JSON: %s",
            exc,
            extra={"conversation_id": conversation_id},
            exc_info=True,
        )
        fallback[FALLBACK_REASON_PAYLOAD_KEY] = "broken_json"
        return _with_latency(fallback)

    if not isinstance(data, dict):
        fallback[FALLBACK_REASON_PAYLOAD_KEY] = "empty_response"
        return _with_latency(fallback)

    data.setdefault("answer", "")
    data.setdefault("confidence", 0.5)
    data.setdefault("escalate", False)
    data.setdefault("sources", [])
    data.setdefault("model_version", settings.AI_MODEL_VERSION_FALLBACK)
    payload = _with_latency(data)

    # Кэшируем ТОЛЬКО уверенные не-fallback ответы. См. is_cacheable —
    # там whitelist условий. Это критично, потому что плохой кэш
    # хуже отсутствия кэша: пользователь получит залипший вранливый
    # ответ снова и снова.
    if is_cacheable(payload, settings.RAG_CONFIDENCE_RED_ZONE):
        cache.put(messages, payload)

    logger.info(
        "AI Service responded",
        extra={
            "conversation_id": conversation_id,
            "ai_latency_ms": payload[LATENCY_PAYLOAD_KEY],
            "model_version": payload.get("model_version"),
            "ai_source": "llm",
        },
    )
    return payload


async def generate_ai_message(db: AsyncSession, conversation_id: int) -> Message:
    # ПСЕВДО-СТРИМИНГ: первый этап — анализируем вопрос.
    await _set_ai_stage(conversation_id, "thinking")

    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        await _set_ai_stage(conversation_id, None)
        raise ValueError(f"Conversation {conversation_id} not found")

    history = await load_history_for_ai(db, conversation_id)

    # ── Security check — bypass KB, LLM cache, and AI service entirely ────────
    # Проверяем ДО кэша: если старый ответ закэширован с неправильными словами
    # (например при регрессии модели), он никогда не вернётся пользователю.
    if _is_security_message(history):
        ai_payload = {
            "answer": _SECURITY_SAFE_ANSWER,
            "confidence": 0.95,
            "escalate": True,
            "sources": [],
        }
        await _set_ai_stage(conversation_id, None)
        # Пропускаем всю дальнейшую логику — сразу к записи сообщения.
        # goto-эмуляция через вложенный else ниже невозможна, поэтому
        # дублируем только минимально необходимую часть финализации.
        requires_escalation = True
        ai_message = Message(
            conversation_id=conversation_id,
            role="ai",
            content=ai_payload["answer"],
            ai_confidence=ai_payload["confidence"],
            ai_escalate=True,
            requires_escalation=True,
        )
        db.add(ai_message)
        if conversation.status in {"ai_processing", "escalated"}:
            conversation.status = "active"
        from app.services.intake_requirements import build_intake_state

        conversation.intake_state = build_intake_state(conversation.intake_state, history)
        await db.flush()
        await db.refresh(ai_message)
        return ai_message

    # ── Policy check FIRST — escalation rules take priority over catalog ──────
    # Catalog detection scans all user messages in history, so a keyword from
    # an earlier message (e.g. "монитор") can trigger a catalog intake flow
    # even when the user has just rejected the KB answer and expects escalation.
    # By checking detect_conversation_policy first we ensure that
    # should_escalate_failed_kb_followup (and other escalation rules) fire
    # correctly before catalog detection gets a chance to intercept.
    policy = detect_conversation_policy(history)
    if policy.action == ConversationAction.ESCALATE:
        ai_payload = policy.to_ai_payload()
    else:
        # ── Catalog-driven intake flow ────────────────────────────────────────
        catalog_item = _resolve_catalog_item(conversation, history)
        if catalog_item is not None:
            ai_payload = await _run_intake_step(db, conversation, catalog_item, history)
        else:
            # ПСЕВДО-СТРИМИНГ: ищем ответ в базе знаний.
            await _set_ai_stage(conversation_id, "searching")
            # Сначала собираем явный negative-список — статьи, которые
            # пользователь в этом же диалоге отметил «не помогло». Это
            # безусловное исключение, не зависит от policy.avoid_repeating_kb:
            # если человек сказал «не помогло» — не пихаем то же снова.
            exclude_article_ids: set[int] = await negative_kb_article_ids_for_conversation(
                db, conversation_id
            )
            if policy.avoid_repeating_kb:
                # Дополнительно — исключаем недавно показанные (без явного
                # negative-сигнала), чтобы не зацикливаться на одной статье.
                exclude_article_ids |= await recent_kb_article_ids_for_conversation(
                    db, conversation_id
                )
            ai_payload = await find_knowledge_answer(
                db,
                history,
                exclude_article_ids=exclude_article_ids if exclude_article_ids else None,
            )
            if ai_payload is None:
                # ПСЕВДО-СТРИМИНГ: KB не нашла подходящий ответ — идём в LLM.
                await _set_ai_stage(conversation_id, "generating")
                ai_payload = await get_ai_answer(conversation_id, history)
            else:
                # ПСЕВДО-СТРИМИНГ: KB вернула ответ, быстро его формируем.
                await _set_ai_stage(conversation_id, "found_kb")

    # ПСЕВДО-СТРИМИНГ: обработка завершена, сбрасываем стадию.
    # Делаем до основного flush'а — клиент увидит сброс сразу после
    # появления AI-сообщения, не раньше.
    await _set_ai_stage(conversation_id, None)

    # Если AI ушёл в fallback — фиксируем причину для дашборда «Сбои AI».
    # KB-ответ и intake-rules сюда не попадают (там reason не выставляется).
    fallback_reason = ai_payload.get(FALLBACK_REASON_PAYLOAD_KEY)
    if fallback_reason:
        await record_ai_fallback(
            db,
            service="answer",
            reason=fallback_reason,
            conversation_id=conversation_id,
        )

    confidence = ai_payload.get("confidence")
    escalate = bool(ai_payload.get("escalate"))

    red_zone_threshold = get_settings().RAG_CONFIDENCE_RED_ZONE
    requires_escalation = escalate or (confidence is not None and confidence < red_zone_threshold)

    ai_message = Message(
        conversation_id=conversation_id,
        role="ai",
        content=ai_payload.get("answer", ""),
        ai_confidence=confidence,
        ai_escalate=escalate,
        sources=_normalize_sources(ai_payload.get("sources")),
        requires_escalation=requires_escalation,
    )
    db.add(ai_message)
    if conversation.status in {"ai_processing", "escalated"}:
        conversation.status = "active"

    # Обновляем intake_state по итогам текущего обмена: извлекаем офис,
    # тип затронутого объекта и другие поля из всех сообщений пользователя.
    # Это позволяет фронту отображать прогресс заполнения формы запроса
    # без отдельного API-вызова.
    from app.services.intake_requirements import build_intake_state

    conversation.intake_state = build_intake_state(
        conversation.intake_state,
        history,
    )

    await db.flush()

    if ai_payload.get("knowledge_article_id") is not None:
        article = await db.get(KnowledgeArticle, int(ai_payload["knowledge_article_id"]))
        if article is not None:
            article.view_count += 1
            db.add(
                KnowledgeArticleFeedback(
                    article_id=article.id,
                    conversation_id=conversation_id,
                    message_id=ai_message.id,
                    user_id=conversation.user_id,
                    query=ai_payload.get("knowledge_query") or "",
                    score=float(ai_payload.get("knowledge_score") or 0.0),
                    decision=ai_payload.get("knowledge_decision") or "answer",
                )
            )
        # Латенси из payload (find_knowledge_answer / get_ai_answer уже её
        # измерили). Если по какой-то причине поля нет — 0 как honest «не знаем»
        # вместо None, чтобы дашборд не ломался на NULL в AVG.
        latency_ms = int(ai_payload.get(LATENCY_PAYLOAD_KEY) or 0)
        db.add(
            AILog(
                ticket_id=None,
                conversation_id=conversation_id,
                model_version=ai_payload.get("model_version") or "knowledge-base-v1",
                predicted_category="knowledge_base",
                predicted_priority="низкий",
                confidence_score=float(confidence or 0.0),
                routed_to_agent_id=None,
                ai_response_draft=ai_payload.get("answer"),
                ai_response_time_ms=latency_ms,
                outcome="resolved_by_ai",
            )
        )

    await db.flush()

    # article = KnowledgeArticle(
    # department="IT",
    # title="Автоматический ответ", # Явно задаём обязательное поле
    # body="Результат обработки...",
    # is_active=True
    # )
    # db.add(article)
    # await db.flush()  # → Данные валидны, ошибка не возникнет

    await db.refresh(ai_message)
    return ai_message


def _resolve_catalog_item(
    conversation: Conversation,
    history: list[dict[str, str]],
) -> CatalogItem | None:
    """Возвращает активный CatalogItem для диалога.

    Если catalog_code уже привязан к разговору — берём его напрямую.
    Иначе пробуем определить по истории сообщений.
    """
    if conversation.catalog_code:
        return get_catalog_item(conversation.catalog_code)
    return detect_catalog_item(history)


async def _run_intake_step(
    db: AsyncSession,
    conversation: Conversation,
    item: CatalogItem,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    """Один шаг сбора данных по catalog item.

    Логика:
      1. Если catalog_code ещё не записан — это первое обнаружение.
         Сначала пробуем KB с фильтром по отделу: вдруг есть готовый ответ.
         Если KB не отвечает или отправляет на эскалацию — начинаем опрос.
      2. Если catalog_code уже есть — продолжаем опрос.
         Берём последний user-ответ как значение последнего запрошенного поля.
      3. Когда все поля собраны — возвращаем payload с готовым резюме для черновика.
    """
    collected: dict[str, str] = dict(conversation.intake_fields or {})
    first_detection = conversation.catalog_code is None

    if first_detection:
        # Попробовать KB с фильтром по отделу каталога
        kb_filters = (
            KnowledgeSearchFilters(department=item.kb_department) if item.kb_department else None
        )
        kb_payload = await find_knowledge_answer(db, history, filters=kb_filters)
        if kb_payload and kb_payload.get("knowledge_decision") != "escalate":
            # KB нашёл хороший ответ — возвращаем его, intake не нужен
            return kb_payload

        # KB не помог — стартуем опрос
        conversation.catalog_code = item.code
        conversation.intake_fields = {}
        collected = {}
    else:
        # Записываем ответ пользователя на последний вопрос
        last_asked = collected.pop("_last_asked", None)
        if last_asked:
            last_user_msg = _last_user_message(history)
            if last_user_msg:
                collected[last_asked] = last_user_msg

    next_field = item.next_missing(collected)

    if next_field is None:
        # Все поля собраны — строим резюме для черновика
        conversation.intake_fields = collected
        return _build_draft_payload(item, collected)

    # Задаём следующий вопрос
    question = item.question_for(next_field)
    if first_detection and not collected:
        answer = f"Оформлю запрос «{item.title}». {question}"
    else:
        answer = question

    collected["_last_asked"] = next_field
    conversation.intake_fields = collected

    return {
        "answer": answer,
        "confidence": 1.0,
        "escalate": False,
        "sources": [],
        "model_version": "intake-rules-v1",
    }


def _last_user_message(history: list[dict[str, str]]) -> str | None:
    for msg in reversed(history):
        if msg.get("role") == "user":
            return msg.get("content", "").strip() or None
    return None


def _build_draft_payload(item: CatalogItem, collected: dict[str, str]) -> dict[str, Any]:
    lines = [f"**Черновик обращения: {item.title}**", ""]
    field_labels = {
        "username": "Заявитель",
        "office": "Офис / кабинет",
        "error_code": "Код ошибки",
        "affected_system": "Система",
        "operation": "Операция",
        "device_description": "Устройство",
        "software_name": "Программа",
        "justification": "Обоснование",
        "sender_email": "Адрес отправителя",
        "already_clicked": "Перешли по ссылке",
        "description": "Описание",
        "device_type": "Тип устройства",
        "serial_number": "Серийный номер",
        "circumstances": "Обстоятельства",
        "document_type": "Тип документа",
        "delivery_date": "Срок готовности",
        "purpose": "Назначение",
        "vacation_start": "Начало отпуска",
        "vacation_end": "Конец отпуска",
        "vacation_type": "Тип отпуска",
        "item_description": "Что закупить",
        "budget": "Бюджет",
    }
    for field_name in item.required_fields:
        label = field_labels.get(field_name, field_name)
        value = collected.get(field_name, "—")
        lines.append(f"- **{label}:** {value}")

    lines += [
        "",
        "Данные собраны. Подтвердите отправку или скорректируйте любое поле.",
    ]
    if item.is_emergency:
        lines.insert(0, "⚠️ Срочный запрос — будет обработан приоритетно.")
        lines.insert(1, "")

    return {
        "answer": "\n".join(lines),
        "confidence": 1.0,
        "escalate": True,
        "sources": [],
        "model_version": "intake-rules-v1",
        "catalog_code": item.code,
    }

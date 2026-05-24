"""
Роутер диалогов (conversations).

Эндпоинты:
  POST /api/v1/conversations/
      — создать новый диалог. Привязывается к текущему пользователю из JWT.

  GET  /api/v1/conversations/
      — список диалогов текущего пользователя.

  POST /api/v1/conversations/{id}/messages
      — добавить сообщение в диалог. Принимает текст, возвращает
        сообщение пользователя + ответ AI с метаданными
        (sources, confidence, escalate, requires_escalation).

  GET  /api/v1/conversations/{id}/messages
      — получить всю историю сообщений диалога.

  POST /api/v1/conversations/{id}/escalate
      — 1-click autofill: AI собирает из истории диалога title/body/
        category/priority/steps_tried, создаёт черновик тикета (status=
        "pending_user", confirmed_by_user=False) и переводит диалог
        в status="escalated". Пользователю остаётся один клик "Отправить".
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constants.departments import DEPARTMENTS_SET
from app.database import get_db
from app.dependencies import get_current_user
from app.models.ai_log import AILog
from app.models.conversation import Conversation
from app.models.knowledge_article import KnowledgeArticleFeedback
from app.models.message import Message
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.ticket import TicketRead
from app.services.ai_classifier import classify_ticket, classify_ticket_heuristic
from app.services.ai_extract import extract_steps_tried_heuristic
from app.services.ai_jobs import enqueue_ai_response_job, notify_ai_jobs_channel
from app.services.audit import log_event
from app.services.conversation_draft import refresh_pending_ticket_from_conversation
from app.services.request_context import build_request_context
from app.services.routing import assign_agent
from app.services.ticket_body import (
    build_context_block,
    clean_optional_text,
    clean_text_with_fallback,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])
DRAFT_AI_TIMEOUT_SECONDS = 2.0

# ── Схемы запросов/ответов (определены здесь чтобы не плодить файлы) ──────────


class ConversationRead(BaseModel):
    """Данные диалога в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: str
    # ПСЕВДО-СТРИМИНГ: текущая стадия обработки. null — нет активной генерации.
    # Значения: thinking / searching / found_kb / generating
    ai_stage: str | None = None
    intake_state: dict | None = None
    created_at: datetime
    updated_at: datetime | None = None


class MessageCreate(BaseModel):
    """Тело запроса при отправке сообщения."""

    content: str


class SourceRead(BaseModel):
    """Источник из RAG, на который опирался AI при ответе."""

    title: str
    url: str | None = None
    article_id: int | None = None
    chunk_id: int | None = None
    snippet: str | None = None
    retrieval: str | None = None
    score: float | None = None
    decision: str | None = None


class MessageRead(BaseModel):
    """Данные одного сообщения в ответе.

    Для AI-сообщений дополнительно отдаём:
      - sources              — что AI цитировал;
      - ai_confidence        — насколько модель уверена;
      - ai_escalate          — модель сама попросила эскалацию;
      - requires_escalation  — итоговый флаг "красной зоны": True, если
                               фоновая обработка решила, что нужна эскалация.
                               Клиент использует этот
                               флаг, чтобы НЕ показывать ответ как
                               окончательный, а предложить 1-click
                               эскалацию через POST /escalate.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str  # "user" или "ai"
    content: str
    sources: list[SourceRead] | None = None
    ai_confidence: float | None = None
    ai_escalate: bool | None = None
    requires_escalation: bool | None = None
    user_feedback: str | None = None
    created_at: datetime | None = None


class MessageFeedbackPayload(BaseModel):
    """Оценка пользователем AI-ответа: помог / не помог."""

    feedback: Literal["helped", "not_helped"]


class AddMessageResponse(BaseModel):
    """Ответ на отправку сообщения в диалог.

    AI-ответ генерируется асинхронно через job-очередь. HTTP-запрос возвращает
    управление сразу после сохранения user_message — без ожидания LLM. Клиент
    получает:

      user_message       — только что сохранённое сообщение пользователя.
      conversation_status — "ai_processing" (модель ещё работает) или "active"
                           (если по какой-то причине AI-job не создалась —
                           деградация, но не блокировка).
      ai_job_id          — id задачи в очереди ai_jobs. Опционально использовать
                           для GET /jobs/{id}, чтобы наблюдать прогресс. Когда
                           job в статусе "done"/"failed" — AI-ответ уже в
                           GET /messages.
      poll_hint          — путь, по которому клиент должен поллить, чтобы
                           забрать AI-ответ. Указан явно, чтобы фронт не
                           догадывался об URL'е.

    Рекомендованный паттерн на клиенте:
      1. POST /messages → получить ai_job_id, conversation_status="ai_processing".
      2. Поллить GET /messages раз в ~1 сек, пока conversation.status не станет
         "active" (т.е. появится AI-сообщение). Таймаут на клиенте — разумный
         (60 сек), после чего показать «AI не успел, попробуйте ещё раз».
    """

    user_message: "MessageRead"
    conversation_status: str
    ai_job_id: int | None = None
    poll_hint: str


class EscalationContext(BaseModel):
    requester_name: str | None = Field(default=None, max_length=100)
    requester_email: EmailStr | None = None
    office: str | None = Field(default=None, max_length=100)
    affected_item: str | None = Field(default=None, max_length=150)
    asset_id: int | None = Field(default=None, gt=0)
    request_type: str | None = Field(default=None, max_length=60)
    request_details: str | None = Field(default=None, max_length=2000)

    @field_validator("requester_name", "office", "affected_item")
    @classmethod
    def strip_nullable_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("requester_email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("request_type", "request_details")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EscalatePayload(BaseModel):
    context: EscalationContext = Field(default_factory=EscalationContext)


# ── POST /conversations/ — создать диалог ─────────────────────────────────────


@router.post(
    "/",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Начать новый диалог",
    description="Создаёт новый диалог для авторизованного пользователя. "
    "user_id берётся из JWT токена автоматически.",
)
async def create_conversation(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = Conversation(
        user_id=current_user.id,
        status="active",
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return conversation


# ── GET /conversations/ — список диалогов текущего пользователя ───────────────


@router.get(
    "/",
    response_model=list[ConversationRead],
    summary="Список диалогов пользователя",
    description="Возвращает все диалоги авторизованного пользователя.",
)
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.created_at.desc())
    )
    return result.scalars().all()


# ── Хелпер: загрузка диалога с проверкой доступа ──────────────────────────────


async def _get_conversation_for_user(
    conversation_id: int,
    db: AsyncSession,
    current_user: User,
) -> Conversation:
    """Загрузить диалог и убедиться, что текущий пользователь — его владелец.

    404 (а не 403) при отсутствии доступа: не палим существование ID
    перебором — та же логика, что в get_ticket_for_user в tickets.py.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conversation = result.scalar_one_or_none()

    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Диалог не найден",
        )
    return conversation


# ── POST /conversations/{id}/messages — добавить сообщение ────────────────────


@router.post(
    "/{conversation_id}/messages",
    response_model=AddMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Отправить сообщение в диалог",
    description=(
        "Сохраняет сообщение пользователя и ставит задачу на генерацию "
        "AI-ответа в очередь (ai_jobs). HTTP-запрос возвращается сразу — "
        "AI-ответ обрабатывается фоновым воркером.\n\n"
        "Клиент получает `ai_job_id`, `conversation_status` и `poll_hint`. "
        "Чтобы получить AI-ответ, клиент должен поллить GET /messages пока "
        "не появится сообщение с role=ai (или conversation.status снова "
        "станет 'active')."
    ),
)
async def add_message(
    conversation_id: int,
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AddMessageResponse:
    conversation = await _get_conversation_for_user(conversation_id, db, current_user)

    if conversation.status == "ai_processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Дождитесь ответа перед отправкой следующего сообщения.",
        )

    # PII-маскировка контента до сохранения: содержимое сообщений живёт долго
    # и попадает в RAG / эскалацию / outbound-логи. Здесь же — единственное
    # место, где user-input приходит в чат, поэтому маскировка тут.
    from app.services.pii import mask_pii  # ленивый импорт — pii нужен только здесь

    # Сохраняем сообщение пользователя (с маскировкой)
    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=mask_pii(payload.content),
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)
    await refresh_pending_ticket_from_conversation(db, conversation_id, creator=current_user)

    conversation.status = "ai_processing"
    job = await enqueue_ai_response_job(db, conversation_id)
    await db.flush()

    # pg_notify: будим ai_worker после того как транзакция закоммитится.
    # BackgroundTask гарантированно запускается после завершения yield-зависимостей
    # (get_db коммитит сессию), поэтому джоба уже в БД к моменту NOTIFY.
    from app.config import get_settings

    background_tasks.add_task(notify_ai_jobs_channel, get_settings().DATABASE_URL)

    return AddMessageResponse(
        user_message=MessageRead.model_validate(user_message),
        conversation_status=conversation.status,
        ai_job_id=job.id,
        poll_hint=f"/api/v1/conversations/{conversation_id}/messages",
    )


# ── GET /conversations/{id}/messages — история сообщений ──────────────────────


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageRead],
    summary="История сообщений диалога",
    description="Возвращает все сообщения диалога в хронологическом порядке.",
)
async def get_messages(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_conversation_for_user(conversation_id, db, current_user)

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return result.scalars().all()


# ── POST /conversations/{id}/messages/{message_id}/feedback ───────────────────


@router.post(
    "/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageRead,
    summary="Оценить AI-ответ (помог / не помог)",
    description=(
        "Сохраняет оценку пользователя по конкретному AI-сообщению. "
        "Работает для любых ответов AI, в том числе без статьи KB — это "
        "общая петля качества. Повторный вызов перезаписывает оценку."
    ),
)
async def submit_message_feedback(
    conversation_id: int,
    message_id: int,
    payload: MessageFeedbackPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Message:
    await _get_conversation_for_user(conversation_id, db, current_user)

    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    message = result.scalar_one_or_none()
    if message is None or message.role != "ai":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сообщение не найдено",
        )

    message.user_feedback = payload.feedback
    await db.flush()
    await db.refresh(message)
    return message


# ── POST /conversations/{id}/escalate — 1-click autofill ─────────────────────


class EscalateResponse(BaseModel):
    """Ответ при эскалации диалога в тикет.

    ticket — созданный pre-filled тикет (status=pending_user,
             confirmed_by_user=False). Пользователь видит черновик и
             одним кликом подтверждает отправку.
    """

    ticket: TicketRead
    conversation_id: int


def _intake_fields(conversation: Conversation) -> dict[str, object]:
    if not isinstance(conversation.intake_state, dict):
        return {}
    fields = conversation.intake_state.get("fields")
    return fields if isinstance(fields, dict) else {}


def _context_value(*values: object) -> str | None:
    for value in values:
        cleaned = clean_optional_text(value)
        if cleaned:
            return cleaned
    return None


def _asset_display_name(asset: object) -> str:
    name = clean_optional_text(getattr(asset, "name", None))
    serial_number = clean_optional_text(getattr(asset, "serial_number", None))
    if name and serial_number:
        return f"{name} ({serial_number})"
    return name or ""


def _context_assets(context_defaults: dict[str, object]) -> list[object]:
    assets = context_defaults.get("assets")
    return assets if isinstance(assets, list) else []


def _find_context_asset(
    context: EscalationContext,
    context_defaults: dict[str, object],
    intake_fields: dict[str, object],
) -> object | None:
    assets = _context_assets(context_defaults)
    if not assets:
        return None

    if context.asset_id is not None:
        for asset in assets:
            if getattr(asset, "id", None) == context.asset_id:
                return asset

    explicit_affected_item = _context_value(context.affected_item)
    affected_item = _context_value(explicit_affected_item, intake_fields.get("affected_item"))
    if affected_item:
        normalized = affected_item.casefold()
        for asset in assets:
            if normalized in {
                _asset_display_name(asset).casefold(),
                str(getattr(asset, "name", "")).casefold(),
                str(getattr(asset, "serial_number", "")).casefold(),
            }:
                return asset
        if explicit_affected_item:
            return None

    primary_asset = context_defaults.get("primary_asset")
    return primary_asset if primary_asset in assets else None


def _intake_request_details(intake_fields: dict[str, object]) -> str | None:
    details = [
        _context_value(intake_fields.get("problem")),
        _context_value(intake_fields.get("symptoms")),
        _context_value(intake_fields.get("what_tried")),
        _context_value(intake_fields.get("business_impact")),
    ]
    return "\n".join(detail for detail in details if detail) or None


@router.post(
    "/{conversation_id}/escalate",
    response_model=EscalateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="1-click эскалация диалога в тикет",
    description=(
        "AI читает историю диалога, классифицирует проблему "
        "(category/priority), извлекает что пользователь уже пробовал "
        "(steps_tried) и создаёт черновик тикета с conversation_id. "
        "Тикет создаётся со status=pending_user и confirmed_by_user=False — "
        "пользователь видит pre-filled форму и одним кликом подтверждает. "
        "Диалог переходит в status=escalated."
    ),
)
async def escalate_conversation(
    conversation_id: int,
    request: Request,
    payload: EscalatePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = await _get_conversation_for_user(conversation_id, db, current_user)
    if conversation.status == "escalated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Диалог уже эскалирован в тикет. Подтвердите черновик или начните новый диалог."
            ),
        )

    # Подтягиваем все сообщения диалога — без лимита: для классификации
    # нам нужен максимум контекста (диалог короткий, обычно 5-15 сообщений).
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = list(msg_result.scalars().all())

    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя эскалировать пустой диалог",
        )

    # Собираем title и body для классификатора:
    #   title — первое сообщение пользователя (обычно это и есть суть);
    #   body  — вся история одной строкой "роль: текст".
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="В диалоге нет сообщений пользователя — нечего эскалировать",
        )

    title = user_msgs[0].content[:255]  # Ticket.title VARCHAR(255)
    classify_body = "\n\n".join(m.content for m in user_msgs)
    body_parts = []
    for m in messages:
        prefix = "Пользователь" if m.role == "user" else "AI"
        body_parts.append(f"{prefix}: {m.content}")

    # Черновик должен появляться быстро. Если внешний сервис отвечает долго,
    # используем локальную эвристику и не заставляем пользователя ждать модель.
    try:
        ai_result = await asyncio.wait_for(
            classify_ticket(
                ticket_id=None,
                title=title,
                body=classify_body,
            ),
            timeout=DRAFT_AI_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Draft classification timed out, using local fallback",
            extra={"conversation_id": conversation_id},
        )
        ai_result = classify_ticket_heuristic(title, classify_body)

    department = ai_result.get("department") or "IT"
    # AI-классификатор обучен на 7-отдельной таксономии (см.
    # app/constants/departments.py), но иногда возвращает "other" или новый,
    # не предусмотренный класс — приземляем в "IT" как безопасный default
    # (а не теряем тикет в 422).
    if department not in DEPARTMENTS_SET:
        department = "IT"

    context_defaults = await build_request_context(db, current_user)
    intake_fields = _intake_fields(conversation)
    steps_tried = _context_value(
        intake_fields.get("what_tried"),
        extract_steps_tried_heuristic(messages),
    )
    selected_asset = _find_context_asset(payload.context, context_defaults, intake_fields)
    selected_asset_name = _asset_display_name(selected_asset) if selected_asset else None

    requester_name = clean_text_with_fallback(
        _context_value(
            payload.context.requester_name,
            intake_fields.get("requester_name"),
            context_defaults.get("requester_name"),
        ),
        current_user.username,
    )
    requester_email = clean_text_with_fallback(
        _context_value(
            payload.context.requester_email,
            intake_fields.get("requester_email"),
            context_defaults.get("requester_email"),
        ),
        current_user.email,
    )
    office = _context_value(
        payload.context.office,
        intake_fields.get("office"),
        getattr(selected_asset, "office", None) if selected_asset else None,
        context_defaults.get("office"),
    )
    affected_item = _context_value(
        payload.context.affected_item,
        intake_fields.get("affected_item"),
        selected_asset_name,
    )
    request_type = _context_value(
        payload.context.request_type,
        conversation.intake_state.get("request_type")
        if isinstance(conversation.intake_state, dict)
        else None,
    )
    request_details = _context_value(
        payload.context.request_details,
        _intake_request_details(intake_fields),
    )

    form_lines: list[str] = []
    if request_type:
        form_lines.append(f"Тип запроса: {request_type}")
    if request_details:
        form_lines.append(f"Уточнение формы: {request_details}")

    body = build_context_block(
        requester_name=requester_name,
        requester_email=requester_email,
        office=office,
        affected_item=affected_item,
        creator_name=current_user.username,
        creator_email=current_user.email,
    )
    if form_lines:
        body += "\n\nФорма запроса:\n" + "\n".join(form_lines)
    body += "\n\n" + "\n\n".join(body_parts)

    settings = get_settings()
    ticket = Ticket(
        user_id=current_user.id,
        conversation_id=conversation_id,
        title=title,
        body=body,
        requester_name=requester_name,
        requester_email=requester_email,
        office=office,
        affected_item=affected_item,
        asset_id=getattr(selected_asset, "id", None) if selected_asset else None,
        request_type=request_type,
        request_details=request_details,
        steps_tried=steps_tried,
        # Пользователь не выставлял приоритет вручную — берём середину.
        # ai_priority используется в роутинге, user_priority остаётся 3.
        user_priority=3,
        department=department,
        status="pending_user",  # ждёт подтверждения "одним кликом"
        ticket_source="ai_generated",
        confirmed_by_user=False,
        ai_category=ai_result.get("category"),
        ai_priority=ai_result.get("priority"),
        ai_confidence=ai_result.get("confidence"),
        ai_processed_at=datetime.now(UTC),
    )
    db.add(ticket)
    await db.flush()

    feedback_result = await db.execute(
        select(KnowledgeArticleFeedback)
        .where(
            KnowledgeArticleFeedback.conversation_id == conversation_id,
            KnowledgeArticleFeedback.escalated_ticket_id.is_(None),
        )
        .order_by(KnowledgeArticleFeedback.created_at.desc(), KnowledgeArticleFeedback.id.desc())
    )
    for feedback in feedback_result.scalars().all():
        feedback.escalated_ticket_id = ticket.id

    # Назначаем агента сразу — даже на pending_user тикет, чтобы старший
    # уже мог посмотреть на черновик и при подтверждении взять в работу.
    await assign_agent(db, ticket)
    await db.flush()

    # Логируем решение AI — outcome="escalated_ai_ticket": AI сам предложил
    # тикет, пользователь ещё не подтвердил, но факт эскалации зафиксирован.
    db.add(
        AILog(
            ticket_id=ticket.id,
            conversation_id=conversation_id,
            model_version=(ai_result.get("model_version") or settings.AI_MODEL_VERSION_FALLBACK),
            predicted_category=ai_result.get("category") or "неизвестно",
            predicted_priority=ai_result.get("priority") or "средний",
            confidence_score=float(ai_result.get("confidence") or 0.0),
            routed_to_agent_id=ticket.agent_id,
            ai_response_draft=ai_result.get("draft_response"),
            ai_response_time_ms=ai_result.get("response_time_ms"),
            outcome="escalated_ai_ticket",
        )
    )

    # Переводим диалог в "escalated" — UI скрывает поле ввода и
    # показывает ссылку на созданный тикет.
    conversation.status = "escalated"

    await db.refresh(ticket)

    await log_event(
        db,
        action="conversation.escalate",
        user_id=current_user.id,
        target_type="conversation",
        target_id=conversation_id,
        request=request,
        details={
            "ticket_id": ticket.id,
            "department": ticket.department,
            "ai_confidence": ticket.ai_confidence,
            "office": ticket.office,
            "affected_item": ticket.affected_item,
            "request_type": ticket.request_type,
        },
    )

    return EscalateResponse(
        ticket=TicketRead.model_validate(ticket),
        conversation_id=conversation_id,
    )


# ── Внутренние функции ────────────────────────────────────────────────────────
#
# _extract_steps_tried переехал в app/services/ai_extract.py (LLM + heuristic
# fallback). Здесь больше нет приватных хелперов — всё живёт в сервисах.

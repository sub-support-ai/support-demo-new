import json
import logging
import time

import httpx

from app.config import get_settings
from app.services.ai_fallback import FALLBACK_REASON_PAYLOAD_KEY
from app.services.ai_service_client import ai_service_headers

settings = get_settings()
logger = logging.getLogger(__name__)

AI_SERVICE_URL = settings.AI_SERVICE_URL


def _candidate_ai_service_urls() -> list[str]:
    urls = [AI_SERVICE_URL.rstrip("/")]
    if urls[0] == "http://ai-service:8001":
        urls.append("http://localhost:8001")
    return urls


_CLASSIFICATION_FALLBACK = {
    "category": "other",
    "department": "IT",
    "priority": "средний",
    "confidence": 0.0,
    "draft_response": "Требуется ручная проверка специалистом поддержки.",
    "model_version": settings.AI_MODEL_VERSION_FALLBACK,
}

from app.constants.departments import DEPARTMENTS_SET as _VALID_DEPARTMENTS  # noqa: E402

_PRIORITY_RANK = {
    "низкий": 0,
    "средний": 1,
    "высокий": 2,
    "критический": 3,
}
_CRITICAL_TERMS = (
    "авар",
    "критич",
    "простой",
    "у всех",
    "весь отдел",
    "вся команда",
    "массов",
    "сервер не работает",
    "база недоступ",
    "1с не работает",
    "касса не работает",
)
_HIGH_TERMS = (
    "сроч",
    "не работает",
    "не могу работать",
    "слом",
    "порван",
    "перегор",
    "недоступ",
    "заблок",
    "не включается",
    "не запускается",
    "надо заменить",
    "нужно заменить",
)
_LOW_TERMS = (
    "как сделать",
    "как обновить",
    "хочу обновить",
    "обновить программу",
    "обновить приложение",
    "как установить",
    "как настроить",
    "инструкция",
    "подскажите",
    "где скачать",
)


def _infer_priority_from_text(title: str, body: str) -> str | None:
    text = f"{title}\n{body}".lower()
    if any(term in text for term in _CRITICAL_TERMS):
        return "критический"
    if any(term in text for term in _HIGH_TERMS):
        return "высокий"
    if any(term in text for term in _LOW_TERMS):
        return "низкий"
    return None


def _choose_priority(current: object, inferred: str | None) -> str:
    current_priority = current if isinstance(current, str) else None
    if current_priority not in _PRIORITY_RANK:
        current_priority = _CLASSIFICATION_FALLBACK["priority"]
    if inferred is None:
        return current_priority
    if inferred == "низкий":
        return inferred
    return (
        inferred
        if _PRIORITY_RANK[inferred] > _PRIORITY_RANK[current_priority]
        else current_priority
    )


def classify_ticket_heuristic(title: str, body: str) -> dict:
    data = dict(_CLASSIFICATION_FALLBACK)
    data["priority"] = _choose_priority(
        data.get("priority"),
        _infer_priority_from_text(title, body),
    )
    data["response_time_ms"] = 0
    data[FALLBACK_REASON_PAYLOAD_KEY] = "fast_local_fallback"
    return data


async def classify_ticket(ticket_id: int | None, title: str, body: str) -> dict:
    """
    Отправляет тикет в AI Service, получает классификацию от Mistral.

    В ответ кладём `response_time_ms` — длительность вызова в миллисекундах.
    Используется в AILog.ai_response_time_ms для метрик (питч-дек обещает
    среднее время 1,01 сек — честно считаем по этому полю).
    """
    started = time.perf_counter()
    data: dict | object | None = None
    last_reason: str | None = None
    for service_url in _candidate_ai_service_urls():
        if not service_url.startswith(("http://", "https://")):
            last_reason = "connect"
            logger.warning(
                "AI Service classify URL has unsupported protocol",
                extra={"ticket_id": ticket_id, "ai_service_url": service_url},
            )
            continue
        try:
            async with httpx.AsyncClient(timeout=settings.AI_SERVICE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{service_url}/ai/classify",
                    headers=ai_service_headers(),
                    json={
                        "ticket_id": ticket_id,
                        "title": title,
                        "body": body,
                    },
                )
                response.raise_for_status()
                try:
                    data = response.json()
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "AI Service вернул невалидный JSON для classify",
                        extra={"ticket_id": ticket_id, "ai_service_url": service_url},
                        exc_info=True,
                    )
                    last_reason = "broken_json"
                    data = dict(_CLASSIFICATION_FALLBACK)
                break
        except httpx.TimeoutException as e:
            last_reason = "timeout"
            logger.warning(
                "AI Service classify timeout: %s",
                e,
                extra={"ticket_id": ticket_id, "ai_service_url": service_url},
            )
        except (httpx.ConnectError, httpx.UnsupportedProtocol) as e:
            last_reason = "connect"
            logger.warning(
                "AI Service classify connect error: %s",
                e,
                extra={"ticket_id": ticket_id, "ai_service_url": service_url},
            )
        except httpx.HTTPStatusError as e:
            last_reason = "http_5xx"
            logger.warning(
                "AI Service classify HTTP error: %s",
                e,
                extra={"ticket_id": ticket_id, "ai_service_url": service_url},
            )
    if data is None:
        if last_reason is not None:
            logger.warning(
                "Все адреса AI Service недоступны (reason=%s)",
                last_reason,
                extra={"ticket_id": ticket_id},
            )
        data = dict(_CLASSIFICATION_FALLBACK)

    if not isinstance(data, dict):
        last_reason = last_reason or "empty_response"
        data = dict(_CLASSIFICATION_FALLBACK)

    data.setdefault("category", _CLASSIFICATION_FALLBACK["category"])
    data.setdefault("priority", _CLASSIFICATION_FALLBACK["priority"])
    data.setdefault("confidence", _CLASSIFICATION_FALLBACK["confidence"])
    data.setdefault("draft_response", _CLASSIFICATION_FALLBACK["draft_response"])
    data.setdefault("model_version", settings.AI_MODEL_VERSION_FALLBACK)

    department = data.get("department") or _CLASSIFICATION_FALLBACK["department"]
    if department not in _VALID_DEPARTMENTS:
        department = _CLASSIFICATION_FALLBACK["department"]
    data["department"] = department
    data["priority"] = _choose_priority(
        data.get("priority"),
        _infer_priority_from_text(title, body),
    )

    data["response_time_ms"] = int((time.perf_counter() - started) * 1000)
    if last_reason is not None:
        # Подхватывается в routers/tickets.create_ticket → record_ai_fallback.
        data[FALLBACK_REASON_PAYLOAD_KEY] = last_reason
    return data

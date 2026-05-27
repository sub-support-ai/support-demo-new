from __future__ import annotations

import re
from typing import Any

from app.services.security_terms import INTAKE_SECURITY_TERMS as _SECURITY_TERMS

COMMON_REQUIRED_FIELDS = [
    "requester_name",
    "requester_email",
    "office",
    "affected_item",
    "problem",
    "symptoms",
    "business_impact",
    "what_tried",
]

SECURITY_REQUIRED_FIELDS = [
    "requester_name",
    "requester_email",
    "office",
    "incident_type",
    "system_or_account",
    "what_happened",
    "what_user_did",
    "time_detected",
]

CRITICAL_REQUIRED_FIELDS = [
    "urgency_reason",
    "affected_users_count",
    "is_business_stopped",
    "is_security_or_safety_risk",
]

FIELD_LABELS = {
    "requester_name": "имя заявителя",
    "requester_email": "рабочий email",
    "office": "офис, город или кабинет",
    "affected_item": "что именно затронуто",
    "problem": "краткое описание проблемы",
    "symptoms": "симптомы или текст ошибки",
    "business_impact": "как это мешает работе",
    "what_tried": "что уже пробовали",
    "urgency_reason": "почему это срочно",
    "affected_users_count": "сколько пользователей затронуто",
    "is_business_stopped": "остановлена ли работа",
    "is_security_or_safety_risk": "есть ли риск безопасности",
    "incident_type": "тип инцидента",
    "system_or_account": "система или учётная запись",
    "what_happened": "что произошло",
    "what_user_did": "что пользователь уже сделал",
    "time_detected": "когда обнаружили проблему",
}


def _field_label(field: str) -> str:
    """Человекочитаемая подпись поля.

    Для известных полей берём из FIELD_LABELS. Незнакомые поля никогда не
    показываем сырым ключом (`requester_name`) — это утечка технической
    схемы наружу; вместо этого превращаем snake_case в обычный текст.
    """
    label = FIELD_LABELS.get(field)
    if label:
        return label
    return field.replace("_", " ").strip()


QUESTION_PRIORITY = [
    "office",
    "affected_item",
    "requester_name",
    "requester_email",
    "what_tried",
    "business_impact",
    "symptoms",
    "urgency_reason",
    "affected_users_count",
    "is_business_stopped",
    "is_security_or_safety_risk",
    "incident_type",
    "system_or_account",
    "what_happened",
    "what_user_did",
    "time_detected",
]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Zа-яА-Я]{2,}")
_OFFICE_RE = re.compile(
    r"(?:офис|город|кабинет|кб\.?|локация|объект)\s*[:\-]?\s*([^,;\n.]+)",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(r"(\d+)\s*(?:человек|пользовател|сотрудник|коллег)", re.IGNORECASE)

_AFFECTED_ITEMS = (
    ("VPN", ("vpn", "впн")),
    ("1С", ("1с", "1c")),
    ("монитор", ("монитор", "экран")),
    ("мышь", ("мыш",)),
    ("клавиатура", ("клавиатур",)),
    ("ноутбук", ("ноутбук", "ноут")),
    ("принтер/МФУ", ("принтер", "мфу", "сканер")),
    ("почта", ("почт", "outlook")),
    ("учётная запись", ("парол", "логин", "учетн", "учётн", "аккаунт")),
    ("пропуск", ("пропуск", "турникет")),
)

_TRIED_TERMS = (
    "пробовал",
    "пробовала",
    "пытался",
    "перезапуск",
    "перезагруз",
    "проверил",
    "проверила",
)
_IMPACT_TERMS = (
    "не могу работать",
    "мешает работе",
    "работа стоит",
    "останов",
    "не могу подключиться",
    "не могу зайти",
    "срывает",
)
_URGENT_TERMS = ("срочно", "критично", "авар", "горит", "опасн", "работа стоит")
_ERROR_TERMS = ("ошибка", "error", "не подключ", "не откры", "не работает", "не запуска", "заблок")


def get_required_fields(
    department: str | None,
    request_type: str | None,
    priority: str | None,
) -> list[str]:
    department_normalized = (department or "").casefold()
    priority_normalized = (priority or "").casefold()
    fields = (
        SECURITY_REQUIRED_FIELDS.copy()
        if department_normalized == "security"
        else COMMON_REQUIRED_FIELDS.copy()
    )
    if priority_normalized == "критический":
        fields.extend(CRITICAL_REQUIRED_FIELDS)
    return list(dict.fromkeys(fields))


def build_intake_state(
    existing_state: dict[str, Any] | None,
    messages: list[dict[str, str]],
    *,
    department: str | None = None,
    request_type: str | None = None,
    priority: str | None = None,
    requester_name: str | None = None,
    requester_email: str | None = None,
) -> dict[str, Any]:
    user_messages = [
        message.get("content", "").strip()
        for message in messages
        if message.get("role") == "user" and message.get("content", "").strip()
    ]
    combined_user_text = "\n".join(user_messages)

    state = existing_state.copy() if isinstance(existing_state, dict) else {}
    state["mode"] = "collecting_context"
    state["department"] = (
        department or state.get("department") or infer_department(combined_user_text)
    )
    state["request_type"] = (
        request_type or state.get("request_type") or infer_request_type(combined_user_text)
    )
    state["priority"] = priority or state.get("priority") or infer_priority(combined_user_text)

    fields = dict(state.get("fields") or {})
    if requester_name and not fields.get("requester_name"):
        fields["requester_name"] = requester_name
    if requester_email and not fields.get("requester_email"):
        fields["requester_email"] = requester_email

    if user_messages and not fields.get("problem"):
        fields["problem"] = _clip(user_messages[0], 240)

    for text in user_messages:
        for field, value in extract_fields_from_message(text).items():
            if value and not fields.get(field):
                fields[field] = value

    required_fields = get_required_fields(
        state.get("department"),
        state.get("request_type"),
        state.get("priority"),
    )
    missing_fields = [field for field in required_fields if not _has_value(fields.get(field))]
    asked_fields = [
        field
        for field in state.get("asked_fields", [])
        if field in required_fields and field in missing_fields
    ]

    question_fields = choose_next_question_fields(missing_fields, asked_fields)
    asked_fields = list(dict.fromkeys([*asked_fields, *question_fields]))
    last_question = build_next_question(question_fields)

    state["fields"] = fields
    state["required_fields"] = required_fields
    state["missing_fields"] = missing_fields
    state["asked_fields"] = asked_fields
    state["last_question_fields"] = question_fields
    state["last_question"] = last_question
    if not missing_fields:
        state["mode"] = "draft_ready"
    return state


def build_intake_answer(state: dict[str, Any], *, reason: str | None = None) -> str:
    fields = state.get("fields") or {}
    missing_fields = state.get("missing_fields") or []

    if not missing_fields:
        return (
            "Черновик заявки почти готов. Проверьте карточку создания запроса: там уже собраны "
            "основные данные из диалога. Если всё верно, создайте запрос; если нет — поправьте поля."
        )

    collected = _format_collected_fields(fields)
    question = state.get("last_question") or build_next_question(missing_fields[:4])
    intro = _intro_for_reason(reason)
    parts = [intro]
    if collected:
        parts.append("Уже понял:\n" + collected)
    parts.append(
        "Чтобы не растягивать диалог, напишите одним сообщением: "
        + question.removeprefix("Уточните: ").rstrip(".")
        + "."
    )
    parts.append("Если удобнее, эти же данные можно заполнить в карточке создания запроса.")
    return "\n\n".join(parts)


def extract_fields_from_message(text: str) -> dict[str, str]:
    normalized = _normalize(text)
    fields: dict[str, str] = {}

    email = _EMAIL_RE.search(text)
    if email:
        fields["requester_email"] = email.group(0)
        name = _extract_name_near_email(text, email.group(0))
        if name:
            fields["requester_name"] = name

    office = _extract_office(text)
    if office:
        fields["office"] = office

    affected_item = infer_affected_item(normalized)
    if affected_item:
        fields["affected_item"] = affected_item

    if _contains_any(normalized, _ERROR_TERMS):
        fields["symptoms"] = _clip(text, 240)

    if _contains_any(normalized, _TRIED_TERMS):
        fields["what_tried"] = _clip(text, 240)

    if _contains_any(normalized, _IMPACT_TERMS):
        fields["business_impact"] = _clip(text, 240)

    if _contains_any(normalized, _URGENT_TERMS):
        fields["urgency_reason"] = _clip(text, 240)

    affected_count = _COUNT_RE.search(text)
    if affected_count:
        fields["affected_users_count"] = affected_count.group(0)

    if "работа стоит" in normalized or "не можем работать" in normalized:
        fields["is_business_stopped"] = "да"

    if "опасн" in normalized or "искр" in normalized or "дым" in normalized:
        fields["is_security_or_safety_risk"] = "да"

    if _contains_any(normalized, _SECURITY_TERMS):
        fields["incident_type"] = infer_request_type(text) or "Инцидент ИБ"
        fields["what_happened"] = _clip(text, 240)
        if "парол" in normalized or "аккаунт" in normalized or "учетн" in normalized:
            fields["system_or_account"] = "учётная запись"

    return fields


def choose_next_question_fields(missing_fields: list[str], asked_fields: list[str]) -> list[str]:
    if not missing_fields:
        return []
    fresh_fields = [
        field
        for field in QUESTION_PRIORITY
        if field in missing_fields and field not in asked_fields
    ]
    if not fresh_fields:
        fresh_fields = [field for field in QUESTION_PRIORITY if field in missing_fields]
    if not fresh_fields:
        fresh_fields = missing_fields
    return fresh_fields[:4]


def build_next_question(fields: list[str]) -> str:
    if not fields:
        return ""
    labels = [_field_label(field) for field in fields]
    return "Уточните: " + ", ".join(labels) + "."


def infer_department(text: str) -> str:
    normalized = _normalize(text)
    if _contains_any(normalized, _SECURITY_TERMS):
        return "security"
    return "IT"


def infer_request_type(text: str) -> str | None:
    normalized = _normalize(text)
    if "vpn" in normalized or "впн" in normalized:
        return "VPN"
    if "1с" in normalized or "1c" in normalized:
        return "1С"
    if "парол" in normalized:
        return "Сброс пароля"
    if "монитор" in normalized or "экран" in normalized:
        return "Оборудование"
    if "мыш" in normalized or "клавиатур" in normalized:
        return "Оборудование"
    if _contains_any(normalized, _SECURITY_TERMS):
        return "Инцидент ИБ"
    return None


def infer_priority(text: str) -> str:
    normalized = _normalize(text)
    if "критич" in normalized or "авар" in normalized or "опасн" in normalized:
        return "критический"
    if "срочно" in normalized or "работа стоит" in normalized:
        return "высокий"
    return "средний"


def infer_affected_item(normalized_text: str) -> str | None:
    for value, terms in _AFFECTED_ITEMS:
        if _contains_any(normalized_text, terms):
            return value
    return None


def _intro_for_reason(reason: str | None) -> str:
    if reason == "kb_solution_rejected":
        return "Понял, инструкция не решила проблему. Дальше соберу данные для заявки специалисту."
    if reason == "direct_support_handoff":
        return "Похоже, здесь нужна проверка специалистом. Соберу данные для заявки."
    return "Соберу данные для заявки специалисту."


def _format_collected_fields(fields: dict[str, Any]) -> str:
    visible_order = [
        "problem",
        "symptoms",
        "affected_item",
        "office",
        "what_tried",
        "business_impact",
        "requester_name",
        "requester_email",
    ]
    lines = []
    for field in visible_order:
        value = fields.get(field)
        if _has_value(value):
            lines.append(f"- {_field_label(field)}: {value}")
    return "\n".join(lines[:6])


def _extract_office(text: str) -> str | None:
    match = _OFFICE_RE.search(text)
    if match:
        return _clip(match.group(1).strip(), 100)

    chunks = [chunk.strip() for chunk in re.split(r"[,;\n]", text) if chunk.strip()]
    for chunk in chunks:
        normalized = _normalize(chunk)
        if normalized in {"москва", "казань", "франкфурт", "спб", "санкт-петербург"}:
            return _clip(chunk, 100)
    return None


def _extract_name_near_email(text: str, email: str) -> str | None:
    before_email = text.split(email, 1)[0]
    chunks = [chunk.strip(" ,.;:-") for chunk in re.split(r"[,;\n]", before_email) if chunk.strip()]
    if not chunks:
        return None
    candidate = chunks[-1]
    candidate = re.sub(r"^(я|меня зовут|заявитель)\s+", "", candidate, flags=re.IGNORECASE).strip()
    if 2 <= len(candidate) <= 100 and not any(char.isdigit() for char in candidate):
        return candidate
    return None


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_normalize(term) in text for term in terms)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _clip(text: str, max_length: int) -> str:
    value = " ".join(text.strip().split())
    return value[:max_length].rstrip()

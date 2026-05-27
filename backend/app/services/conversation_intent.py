from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.security_terms import SECURITY_INCIDENT_TERMS


class ConversationIntent(StrEnum):
    ANSWER = "answer"
    CREATE_DRAFT = "create_draft"
    EMERGENCY = "emergency"
    DIRECT_HANDOFF = "direct_handoff"
    FAILED_KB_HANDOFF = "failed_kb_handoff"
    COLLECT_CONTEXT = "collect_context"


class ConversationAction(StrEnum):
    SEARCH_KB = "search_kb"
    ESCALATE = "escalate"


class ConversationTriageClass(StrEnum):
    SELF_SERVICE = "self_service"
    SPECIALIST_REQUIRED = "specialist_required"
    CRITICAL_SECURITY = "critical_security"


SUPPORT_DRAFT_INTENT_TERMS = (
    "тикет",
    "заявк",
    "черновик",
    "обращен",
    "запрос",
    "техподдерж",
    "тех поддерж",
    "специалист",
    "саппорт",
    "support",
)
SUPPORT_DRAFT_ACTION_TERMS = (
    "созда",
    "сформир",
    "оформ",
    "заведи",
    "завести",
    "отправ",
    "эскал",
)
URGENT_TERMS = (
    "срочно",
    "авар",
    "критич",
    "опасн",
    "горит",
    "дым",
    "искр",
)
SAFETY_RISK_TERMS = (
    "горит",
    "дым",
    "искр",
    "запах гари",
    "удар током",
    "оголенный",
    "оголённый",
    "коротит",
    "плавится",
)
SPECIALIST_REQUIRED_INCIDENT_TERMS = (
    "порвался",
    "порвал",
    "оторвал",
    "оторвался",
    "сломался",
    "сломалась",
    "сломано",
    "сгорел",
    "сгорела",
    "разбился",
    "треснул",
    "поврежден",
    "повреждён",
    "поврежд",
    "не включается",
    "залил",
    "залили",
    "залита",
)
PHYSICAL_INCIDENT_TERMS = (
    "провод",
    "кабел",
    "розетк",
    "удлинител",
    "электр",
    "питани",
    "сломал",
    "сломался",
    "порвал",
    "порвался",
    "оторвал",
    "поврежд",
)
KB_REPEAT_REQUEST_TERMS = (
    "повтори",
    "повторите",
    "еще раз",
    "ещё раз",
    "покажи инструкцию",
    "покажите инструкцию",
    "напомни",
    "напомните",
)
KB_ANSWER_MARKERS = (
    "нашёл решение в базе знаний",
    "нашел решение в базе знаний",
    "похоже на статью базы знаний",
)
INTAKE_ANSWER_MARKERS = (
    "соберу данные для черновика обращения",
    "соберу данные для заявки",
    "данные для заявки специалисту",
    "подготовлю черновик запроса",
    "черновик заявки",
    "карточке создания запроса",
    "передам его в нужный отдел",
)
KB_FAILED_FOLLOWUP_TERMS = (
    "не помог",
    "не поможет",
    "не сработ",
    "не получилось",
    "не подходит",
    "это не то",
    "не то",
    "бесполезно",
    "всё еще",
    "все еще",
    "по-прежнему",
    "ничего не изменилось",
    "та же ошибка",
    "осталось",
)
SUPPORT_HANDOFF_TERMS = (
    "надо менять",
    "нужно менять",
    "нужно заменить",
    "надо заменить",
    "заменить",
    "замена",
    "сгорел",
    "сгорела",
    "сломался",
    "сломалась",
    "сломано",
    "физически",
    "мастер",
    "специалист",
    "пусть придут",
    "передай",
    "оформи",
    "создай запрос",
    "заявку",
)
SUPPORT_OBJECT_TERMS = (
    "монитор",
    "экран",
    "ноутбук",
    "компьютер",
    "системный блок",
    "мыш",
    "клавиатур",
    "принтер",
    "мфу",
    "сканер",
    "док-станц",
    "гарнитур",
    "камера",
    "кабель",
    "провод",
    "зарядк",
    "блок питания",
    "оборудован",
)


INTAKE_MODEL_VERSION = "intake-rules-v1"
INTAKE_CONFIDENCE = 0.5


@dataclass(frozen=True)
class ConversationPolicy:
    intent: ConversationIntent
    action: ConversationAction
    requires_draft: bool
    triage_class: ConversationTriageClass
    avoid_repeating_kb: bool = False
    answer_override: str | None = None
    confidence: float | None = None
    model_version: str | None = None
    reason: str | None = None

    def to_ai_payload(self) -> dict[str, Any]:
        if self.action != ConversationAction.ESCALATE or not self.answer_override:
            raise ValueError("Only escalation policy can be converted to AI payload")
        return {
            "answer": self.answer_override,
            "confidence": self.confidence,
            "escalate": True,
            "sources": [],
            "model_version": self.model_version or INTAKE_MODEL_VERSION,
        }


def detect_conversation_policy(messages: list[dict[str, str]]) -> ConversationPolicy:
    if not _user_messages(messages):
        return _answer_policy()

    if is_explicit_draft_request(messages):
        return _draft_policy(
            ConversationIntent.CREATE_DRAFT,
            build_intake_answer(),
            triage_class=ConversationTriageClass.SPECIALIST_REQUIRED,
        )

    if has_critical_or_security_incident(messages):
        return _draft_policy(
            ConversationIntent.EMERGENCY,
            build_critical_security_answer(messages),
            triage_class=ConversationTriageClass.CRITICAL_SECURITY,
            reason="critical_or_security_incident",
        )

    if should_handoff_without_kb(messages) or should_specialist_required_without_kb(messages):
        return _draft_policy(
            ConversationIntent.DIRECT_HANDOFF,
            build_direct_handoff_answer(),
            triage_class=ConversationTriageClass.SPECIALIST_REQUIRED,
            reason="direct_support_handoff",
        )

    if should_escalate_failed_kb_followup(messages):
        return _draft_policy(
            ConversationIntent.FAILED_KB_HANDOFF,
            build_failed_kb_followup_answer(),
            triage_class=ConversationTriageClass.SPECIALIST_REQUIRED,
            reason="kb_solution_rejected",
        )

    if should_continue_context_collection(messages):
        return _draft_policy(
            ConversationIntent.COLLECT_CONTEXT,
            build_continue_context_collection_answer(),
            triage_class=ConversationTriageClass.SPECIALIST_REQUIRED,
            reason="collecting_ticket_context",
        )

    return _answer_policy(avoid_repeating_kb=should_avoid_repeating_kb_answer(messages))


def should_offer_support_draft(messages: list[dict[str, str]]) -> bool:
    return (
        is_explicit_draft_request(messages)
        or has_critical_or_security_incident(messages)
        or should_specialist_required_without_kb(messages)
    )


def is_explicit_draft_request(messages: list[dict[str, str]]) -> bool:
    user_messages = _normalised_user_messages(messages)
    if not user_messages:
        return False

    latest = user_messages[-1]
    has_draft_action = _contains_any(latest, SUPPORT_DRAFT_ACTION_TERMS)
    has_draft_object = _contains_any(latest, SUPPORT_DRAFT_INTENT_TERMS)
    return has_draft_action and has_draft_object


def has_urgent_physical_incident(messages: list[dict[str, str]]) -> bool:
    user_messages = _normalised_user_messages(messages)
    if not user_messages:
        return False
    combined = "\n".join(user_messages)
    has_urgent_context = _contains_any(combined, URGENT_TERMS)
    has_physical_incident = _contains_any(combined, PHYSICAL_INCIDENT_TERMS)
    return has_urgent_context and has_physical_incident


def has_critical_or_security_incident(messages: list[dict[str, str]]) -> bool:
    user_messages = _normalised_user_messages(messages)
    if not user_messages:
        return False
    latest_user = user_messages[-1]
    has_security_incident = _contains_any(latest_user, SECURITY_INCIDENT_TERMS)
    has_safety_risk = _contains_any(latest_user, SAFETY_RISK_TERMS)
    return has_security_incident or has_safety_risk or has_urgent_physical_incident(messages)


def should_specialist_required_without_kb(messages: list[dict[str, str]]) -> bool:
    latest_user = _latest_user_message(messages)
    if not latest_user or _has_prior_kb_answer(messages):
        return False
    has_physical_object = _contains_any(latest_user, SUPPORT_OBJECT_TERMS) or _contains_any(
        latest_user,
        PHYSICAL_INCIDENT_TERMS,
    )
    has_damage = _contains_any(latest_user, SPECIALIST_REQUIRED_INCIDENT_TERMS)
    return has_physical_object and has_damage


def should_handoff_without_kb(messages: list[dict[str, str]]) -> bool:
    latest_user = _latest_user_message(messages)
    if not latest_user or _has_prior_kb_answer(messages):
        return False
    has_handoff = _contains_any(latest_user, SUPPORT_HANDOFF_TERMS)
    has_support_object = _contains_any(latest_user, SUPPORT_OBJECT_TERMS)
    has_physical_context = _contains_any(latest_user, PHYSICAL_INCIDENT_TERMS)
    return has_handoff and (has_support_object or has_physical_context)


def should_avoid_repeating_kb_answer(messages: list[dict[str, str]]) -> bool:
    latest_user = _latest_user_message(messages)
    has_prior_assistant = any(
        _normalise(message.get("content", ""))
        for message in messages
        if message.get("role") == "assistant"
    )
    if not latest_user or not has_prior_assistant:
        return False
    return not is_explicit_repeat_request(latest_user)


def should_escalate_failed_kb_followup(messages: list[dict[str, str]]) -> bool:
    latest_user = _latest_user_message(messages)
    if not latest_user or not _has_prior_kb_answer(messages):
        return False
    if is_explicit_repeat_request(latest_user):
        return False
    return _contains_any(latest_user, KB_FAILED_FOLLOWUP_TERMS) or _contains_any(
        latest_user,
        SUPPORT_HANDOFF_TERMS,
    )


def should_continue_context_collection(messages: list[dict[str, str]]) -> bool:
    latest_user = _latest_user_message(messages)
    if not latest_user or is_explicit_repeat_request(latest_user):
        return False
    return _has_prior_intake_prompt(messages)


def is_explicit_repeat_request(text: str) -> bool:
    return _contains_any(_normalise(text), KB_REPEAT_REQUEST_TERMS)


def build_intake_answer() -> str:
    return (
        "Соберу данные для черновика обращения. Из истории возьму описание проблемы "
        "и уже упомянутые действия. Уточните тип запроса, заявителя, офис, затронутый объект "
        "и конкретные детали в карточке под этим сообщением; "
        "после этого сформирую черновик для специалиста."
    )


def build_critical_security_answer(messages: list[dict[str, str]]) -> str:
    latest_user = _latest_user_message(messages)
    if _contains_any(latest_user, SECURITY_INCIDENT_TERMS):
        return (
            "Похоже на инцидент безопасности. Не переходите по ссылкам, не открывайте вложения "
            "и не вводите пароли. Если уже открывали файл или вводили данные, не выключайте устройство "
            "и не удаляйте письмо — это поможет проверке.\n\n"
            "Сейчас оформим срочный запрос специалисту. Заполните карточку под этим сообщением: "
            "заявитель, офис или рабочее место, какое письмо/система затронуты, что уже сделали "
            "и когда это произошло."
        )
    return (
        "Похоже на срочный инцидент с риском для оборудования или безопасности. Если есть дым, искры, "
        "запах гари или оголённый провод, не трогайте устройство и отключите питание только если это "
        "можно сделать безопасно. Ограничьте доступ к месту проблемы.\n\n"
        "Сейчас оформим срочный запрос специалисту. Заполните карточку под этим сообщением: "
        "офис, кабинет или рабочее место, что именно повреждено и есть ли инвентарный номер."
    )


def build_failed_kb_followup_answer() -> str:
    return (
        "Понял, инструкция из базы знаний не решает ситуацию. Дальше оформим "
        "запрос специалисту и передадим уже описанные детали.\n\n"
        "Заполните карточку под этим сообщением: офис, рабочее место или кабинет, затронутое оборудование/систему "
        "и важные детали: что именно нужно заменить или проверить, есть ли инвентарный "
        "номер, насколько это мешает работе. После этого подготовлю черновик запроса "
        "и передам его в нужный отдел."
    )


def build_direct_handoff_answer() -> str:
    return (
        "Похоже, здесь нужна проверка или замена оборудования специалистом.\n\n"
        "Заполните карточку под этим сообщением: офис, рабочее место или кабинет, что именно не работает, есть ли "
        "инвентарный номер и насколько проблема мешает работе. После этого подготовлю "
        "черновик запроса и передам его в нужный отдел."
    )


def build_continue_context_collection_answer() -> str:
    return (
        "Принял дополнительный контекст. Сейчас важно оформить понятный запрос "
        "специалисту.\n\n"
        "Эти уточнения сохранятся в истории диалога и попадут в описание для агента. "
        "Заполните карточку создания запроса: тип запроса, заявитель, офис, что "
        "затронуто и детали. После этого нажмите «Создать запрос»."
    )


def _answer_policy(avoid_repeating_kb: bool = False) -> ConversationPolicy:
    return ConversationPolicy(
        intent=ConversationIntent.ANSWER,
        action=ConversationAction.SEARCH_KB,
        requires_draft=False,
        triage_class=ConversationTriageClass.SELF_SERVICE,
        avoid_repeating_kb=avoid_repeating_kb,
    )


def _draft_policy(
    intent: ConversationIntent,
    answer: str,
    triage_class: ConversationTriageClass,
    reason: str | None = None,
) -> ConversationPolicy:
    return ConversationPolicy(
        intent=intent,
        action=ConversationAction.ESCALATE,
        requires_draft=True,
        triage_class=triage_class,
        answer_override=answer,
        confidence=INTAKE_CONFIDENCE,
        model_version=INTAKE_MODEL_VERSION,
        reason=reason,
    )


def _has_prior_kb_answer(messages: list[dict[str, str]]) -> bool:
    return any(
        _contains_any(_normalise(message.get("content", "")), KB_ANSWER_MARKERS)
        for message in messages
        if message.get("role") == "assistant"
    )


def _has_prior_intake_prompt(messages: list[dict[str, str]]) -> bool:
    return any(
        _contains_any(_normalise(message.get("content", "")), INTAKE_ANSWER_MARKERS)
        for message in messages
        if message.get("role") == "assistant"
    )


def _latest_user_message(messages: list[dict[str, str]]) -> str:
    latest = ""
    for message in messages:
        if message.get("role") == "user":
            latest = _normalise(message.get("content", ""))
    return latest


def _user_messages(messages: list[dict[str, str]]) -> list[str]:
    return [
        message.get("content", "").strip()
        for message in messages
        if message.get("role") == "user" and message.get("content", "").strip()
    ]


def _normalised_user_messages(messages: list[dict[str, str]]) -> list[str]:
    return [_normalise(message) for message in _user_messages(messages)]


def _normalise(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_normalise(term) in text for term in terms)

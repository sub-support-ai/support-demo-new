from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.models.ticket import Ticket
from app.models.user import User
from app.services.ai_extract import extract_steps_tried_heuristic
from app.services.ticket_body import build_context_block, clean_text_with_fallback

MAX_DRAFT_MESSAGES = 40


def _message_line(message: Message) -> str:
    prefix = "Пользователь" if message.role == "user" else "AI"
    return f"{prefix}: {message.content}"


def _build_draft_body(ticket: Ticket, messages: list[Message], creator: User | None) -> str:
    requester_name = clean_text_with_fallback(
        ticket.requester_name,
        creator.username if creator else "Пользователь",
    )
    requester_email = clean_text_with_fallback(
        ticket.requester_email,
        creator.email if creator else "unknown@example.com",
    )

    body = build_context_block(
        requester_name=requester_name,
        requester_email=requester_email,
        office=ticket.office,
        affected_item=ticket.affected_item,
        creator_name=creator.username if creator else None,
        creator_email=creator.email if creator else None,
    )

    form_lines: list[str] = []
    if ticket.request_type:
        form_lines.append(f"Тип запроса: {ticket.request_type}")
    if ticket.request_details:
        form_lines.append(f"Уточнение формы: {ticket.request_details}")
    if ticket.steps_tried:
        form_lines.append(f"Что уже пробовали: {ticket.steps_tried}")
    if form_lines:
        body += "\n\nФорма запроса:\n" + "\n".join(form_lines)

    visible_messages = messages[-MAX_DRAFT_MESSAGES:]
    transcript = "\n\n".join(_message_line(message) for message in visible_messages)
    if transcript:
        if len(visible_messages) < len(messages):
            transcript = "Предыдущие сообщения скрыты, ниже последние уточнения.\n\n" + transcript
        body += "\n\nИстория диалога:\n" + transcript

    return body


async def refresh_pending_ticket_from_conversation(
    db: AsyncSession,
    conversation_id: int,
    *,
    creator: User | None = None,
) -> Ticket | None:
    ticket_result = await db.execute(
        select(Ticket)
        .where(
            Ticket.conversation_id == conversation_id,
            Ticket.status == "pending_user",
            Ticket.confirmed_by_user.is_(False),
        )
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .limit(1)
    )
    ticket = ticket_result.scalar_one_or_none()
    if ticket is None:
        return None

    message_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = list(message_result.scalars().all())
    if not messages:
        return ticket

    steps_tried = extract_steps_tried_heuristic(messages)
    if steps_tried:
        ticket.steps_tried = steps_tried

    creator = creator or await db.get(User, ticket.user_id)
    ticket.body = _build_draft_body(ticket, messages, creator)
    return ticket

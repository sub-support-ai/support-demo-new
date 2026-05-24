from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Message(Base):
    """
    Одно сообщение в диалоге между пользователем и AI.

    role = "user" — сообщение от пользователя
    role = "ai"   — ответ от AI-ассистента

    Все сообщения одного диалога связаны через conversation_id.
    Когда нужно создать тикет — AI берёт все сообщения диалога
    и формирует из них описание проблемы автоматически.

    Для AI-сообщений дополнительно сохраняем метаданные ответа модели:
      ai_confidence  — уверенность модели в ответе (0.0–1.0).
      ai_escalate    — модель сама попросила эскалацию (например, ответ
                       требует вмешательства человека).
      sources        — список источников, на которые опирался AI при ответе
                       (RAG): [{"title": "...", "url": "..."}]. Нужен для
                       цитирования в UI и для офлайн-аудита решений.
      requires_escalation — флаг "красной зоны": confidence < 0.6 ИЛИ
                       AI сам выставил escalate=True. Если True — клиент
                       НЕ должен показывать этот ответ как окончательный,
                       а предложить пользователю эскалацию на агента.

    Эти поля nullable, потому что:
      - на user-сообщениях их нет в принципе;
      - на старых AI-сообщениях (до миграции) их тоже нет.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # "user" или "ai"
    role: Mapped[str] = mapped_column(String(10), nullable=False)

    # Текст сообщения
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # ── AI-метаданные (только для role="ai") ──────────────────────────────────
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_escalate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Список источников из RAG: [{"title": "...", "url": "..."|None}, ...]
    # JSON, а не отдельная таблица — sources неотделимы от сообщения, всегда
    # читаются вместе с ним, никогда не запрашиваются изолированно. Отдельная
    # таблица добавила бы JOIN на каждое чтение чата ради нулевой выгоды.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    requires_escalation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Оценка пользователя по AI-ответу: "helped" / "not_helped" / None.
    # Петля качества для НЕ-KB ответов: у них нет статьи KB, чтобы крутить
    # её счётчики, поэтому последний вердикт пользователя храним прямо на
    # сообщении. Одно значение (последняя оценка) — для метрик доли полезных
    # ответов; история не нужна.
    user_feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ──────────────────────────────────────────────────────────────────────────

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Связь с диалогом
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

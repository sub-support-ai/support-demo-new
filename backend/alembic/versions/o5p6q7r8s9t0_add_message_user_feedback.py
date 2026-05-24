"""add user_feedback column to messages

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-23 00:00:00.000000

Stores the latest user feedback for AI messages without a KB article.
Nullable by design: old messages and user messages have no feedback.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o5p6q7r8s9t0"
down_revision: str | None = "n4o5p6q7r8s9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("user_feedback", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "user_feedback")

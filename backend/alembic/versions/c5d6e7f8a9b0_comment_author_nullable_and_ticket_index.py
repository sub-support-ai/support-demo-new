"""ticket_comments.author_id SET NULL + composite index (agent_id, status)

Revision ID: c5d6e7f8a9b0
Revises: b2c3d4e5f6a7
Create Date: 2026-05-10 00:00:00.000000

Changes:
  1. ticket_comments.author_id — nullable=True, ondelete SET NULL.
     Позволяет удалять пользователей, не теряя историю комментариев
     (author_username / author_role сохраняются как «снимок» на момент
     создания).

  2. Индекс (agent_id, status) на tickets — ускоряет запросы агента к
     «своим» открытым тикетам, а также SLA-воркер (WHERE agent_id=X AND
     status IN (...)). Без индекса каждый тик воркера делает seq-scan.

Примечание по идемпотентности:
  В зависимости от истории применения миграций FK на author_id может
  называться по-разному (имя генерирует Postgres или Alembic автоматически)
  или вообще отсутствовать. Используем динамический SQL для поиска и
  удаления любого существующего FK по колонке — не по имени.
  Индекс создаётся через CREATE INDEX IF NOT EXISTS.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

# PL/pgSQL-блок: найти любой FK на ticket_comments.author_id и удалить его.
_DROP_AUTHOR_FK = """
DO $$
DECLARE _fk text;
BEGIN
    SELECT tc.constraint_name INTO _fk
    FROM information_schema.table_constraints  tc
    JOIN information_schema.key_column_usage   kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
    WHERE tc.table_name      = 'ticket_comments'
      AND tc.constraint_type = 'FOREIGN KEY'
      AND kcu.column_name    = 'author_id'
    LIMIT 1;
    IF _fk IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ticket_comments DROP CONSTRAINT %I', _fk);
    END IF;
END;
$$;
"""


def upgrade() -> None:
    # ── 1. author_id: nullable + SET NULL ─────────────────────────────────────

    # Убираем любой существующий FK на author_id (имя может быть любым).
    op.execute(_DROP_AUTHOR_FK)

    # nullable=True — no-op если колонка уже nullable.
    op.alter_column(
        "ticket_comments",
        "author_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # Создаём FK с ondelete="SET NULL" под каноническим именем.
    op.create_foreign_key(
        "ticket_comments_author_id_fkey",
        "ticket_comments",
        "users",
        ["author_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 2. Составной индекс (agent_id, status) ────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tickets_agent_id_status ON tickets (agent_id, status);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tickets_agent_id_status;")

    # Убираем FK добавленный в upgrade.
    op.execute(_DROP_AUTHOR_FK)

    op.alter_column(
        "ticket_comments",
        "author_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.create_foreign_key(
        "ticket_comments_author_id_fkey",
        "ticket_comments",
        "users",
        ["author_id"],
        ["id"],
    )

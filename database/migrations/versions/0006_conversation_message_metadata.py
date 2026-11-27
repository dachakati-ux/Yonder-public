"""Add optional JSON metadata on conversation_messages for Chat generation."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_conversation_message_metadata"
down_revision: str | None = "0005_academy_durable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Keep the published revision identifier: PostgreSQL enforces Alembic's
    # default VARCHAR(32), unlike SQLite. Widen before Alembic records this
    # revision, including upgrades from existing 0005 installations.
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "alembic_version", "version_num",
            existing_type=sa.String(length=32), type_=sa.String(length=128),
        )
    inspector = sa.inspect(bind)
    if "conversation_messages" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("conversation_messages")}
    if "metadata" in columns:
        return
    op.add_column(
        "conversation_messages",
        sa.Column("metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversation_messages" not in set(inspector.get_table_names()):
        return
    columns = {col["name"] for col in inspector.get_columns("conversation_messages")}
    if "metadata" not in columns:
        return
    op.drop_column("conversation_messages", "metadata")

"""Add optional JSON metadata on conversation_messages for Chat generation."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_conversation_message_metadata"
down_revision: Union[str, None] = "0005_academy_durable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
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

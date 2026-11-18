"""Capacity usage events table for EnsureReady telemetry."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_capacity_usage_events"
down_revision: Union[str, None] = "0006_conversation_message_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "capacity_usage_events" in set(inspector.get_table_names()):
        return
    op.create_table(
        "capacity_usage_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("model_definition_id", sa.String(length=128), nullable=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("gpu_indexes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("principal_subject", sa.String(length=256), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_capacity_usage_events_ts", "capacity_usage_events", ["ts"])
    op.create_index(
        "ix_capacity_usage_events_event_type",
        "capacity_usage_events",
        ["event_type"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "capacity_usage_events" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_capacity_usage_events_event_type", table_name="capacity_usage_events")
    op.drop_index("ix_capacity_usage_events_ts", table_name="capacity_usage_events")
    op.drop_table("capacity_usage_events")

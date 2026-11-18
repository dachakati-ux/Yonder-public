"""Add admin_events and request_ledger tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_ops_ledger"
down_revision: str | None = "0002_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("principal_subject", sa.String(length=256), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_admin_events_created_at", "admin_events", ["created_at"])
    op.create_index("ix_admin_events_event_type", "admin_events", ["event_type"])
    op.create_index("ix_admin_events_request_id", "admin_events", ["request_id"])

    op.create_table(
        "request_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("model_alias", sa.String(length=128), nullable=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_request_ledger_request_id", "request_ledger", ["request_id"])
    op.create_index("ix_request_ledger_status", "request_ledger", ["status"])
    op.create_index("ix_request_ledger_created_at", "request_ledger", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_request_ledger_created_at", table_name="request_ledger")
    op.drop_index("ix_request_ledger_status", table_name="request_ledger")
    op.drop_index("ix_request_ledger_request_id", table_name="request_ledger")
    op.drop_table("request_ledger")
    op.drop_index("ix_admin_events_request_id", table_name="admin_events")
    op.drop_index("ix_admin_events_event_type", table_name="admin_events")
    op.drop_index("ix_admin_events_created_at", table_name="admin_events")
    op.drop_table("admin_events")

"""Initial control-plane tables."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_definitions",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(length=64), nullable=False),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("model_definition_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("gpu_assignments", sa.JSON(), nullable=False),
        sa.Column("runtime_configuration", sa.JSON(), nullable=False),
        sa.Column("load_progress_percent", sa.Float(), nullable=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_load", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("maintenance", sa.Boolean(), nullable=False),
        sa.Column("logs", sa.JSON(), nullable=False),
    )
    op.create_index("ix_deployments_model_definition_id", "deployments", ["model_definition_id"])
    op.create_table(
        "model_aliases",
        sa.Column("alias", sa.String(length=128), primary_key=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("experimental", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_model_aliases_deployment_id", "model_aliases", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_model_aliases_deployment_id", table_name="model_aliases")
    op.drop_table("model_aliases")
    op.drop_index("ix_deployments_model_definition_id", table_name="deployments")
    op.drop_table("deployments")
    op.drop_table("model_definitions")

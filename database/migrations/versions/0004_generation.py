"""Add generation_jobs and generation_artifacts tables."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_generation"
down_revision: Union[str, None] = "0003_ops_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("principal_subject", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("model_alias", sa.String(length=128), nullable=False),
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generation_jobs_principal", "generation_jobs", ["principal_subject"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index("ix_generation_jobs_created_at", "generation_jobs", ["created_at"])

    op.create_table(
        "generation_artifacts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("principal_subject", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_generation_artifacts_principal", "generation_artifacts", ["principal_subject"]
    )
    op.create_index("ix_generation_artifacts_job_id", "generation_artifacts", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_artifacts_job_id", table_name="generation_artifacts")
    op.drop_index("ix_generation_artifacts_principal", table_name="generation_artifacts")
    op.drop_table("generation_artifacts")
    op.drop_index("ix_generation_jobs_created_at", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_principal", table_name="generation_jobs")
    op.drop_table("generation_jobs")

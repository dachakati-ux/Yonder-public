"""Academy durable state: lesson_progress backfill + mission/challenge/workbench/quarantine."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_academy_durable"
down_revision: str | None = "0004_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # lesson_progress previously only appeared via create_all — ship for migrate-only labs.
    if "lesson_progress" not in existing:
        op.create_table(
            "lesson_progress",
            sa.Column("principal_subject", sa.String(length=256), primary_key=True),
            sa.Column("lesson_id", sa.String(length=128), primary_key=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("check_attempts", sa.Integer(), nullable=False),
            sa.Column("check_passed", sa.Boolean(), nullable=False),
            sa.Column("last_check_correct", sa.Boolean(), nullable=True),
            sa.Column("last_try_it_request_id", sa.String(length=128), nullable=True),
            sa.Column("try_it_recorded", sa.Boolean(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_lesson_progress_updated_at", "lesson_progress", ["updated_at"])

    if "mission_progress" not in existing:
        op.create_table(
            "mission_progress",
            sa.Column("principal_subject", sa.String(length=256), primary_key=True),
            sa.Column("mission_id", sa.String(length=128), primary_key=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("completed_steps", sa.JSON(), nullable=False),
            sa.Column("check_passed", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.String(length=64), nullable=False),
        )

    if "challenge_submission" not in existing:
        op.create_table(
            "challenge_submission",
            sa.Column("principal_subject", sa.String(length=256), primary_key=True),
            sa.Column("challenge_id", sa.String(length=128), primary_key=True),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("criteria_met", sa.JSON(), nullable=False),
            sa.Column("criteria_missed", sa.JSON(), nullable=False),
            sa.Column("passed", sa.Boolean(), nullable=False),
            sa.Column("submitted_at", sa.String(length=64), nullable=False),
        )
        op.create_index(
            "ix_challenge_submission_submitted_at",
            "challenge_submission",
            ["submitted_at"],
        )

    if "workbench_note" not in existing:
        op.create_table(
            "workbench_note",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("principal_subject", sa.String(length=256), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("tags", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.String(length=64), nullable=False),
            sa.Column("updated_at", sa.String(length=64), nullable=False),
        )
        op.create_index(
            "ix_workbench_note_principal_subject",
            "workbench_note",
            ["principal_subject"],
        )
        op.create_index("ix_workbench_note_updated_at", "workbench_note", ["updated_at"])

    if "corpus_quarantine" not in existing:
        op.create_table(
            "corpus_quarantine",
            sa.Column("corpus_id", sa.String(length=128), primary_key=True),
            sa.Column("quarantined_at", sa.String(length=64), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "corpus_quarantine" in existing:
        op.drop_table("corpus_quarantine")
    if "workbench_note" in existing:
        op.drop_index("ix_workbench_note_updated_at", table_name="workbench_note")
        op.drop_index(
            "ix_workbench_note_principal_subject", table_name="workbench_note"
        )
        op.drop_table("workbench_note")
    if "challenge_submission" in existing:
        op.drop_index(
            "ix_challenge_submission_submitted_at", table_name="challenge_submission"
        )
        op.drop_table("challenge_submission")
    if "mission_progress" in existing:
        op.drop_table("mission_progress")
    if "lesson_progress" in existing:
        op.drop_index("ix_lesson_progress_updated_at", table_name="lesson_progress")
        op.drop_table("lesson_progress")

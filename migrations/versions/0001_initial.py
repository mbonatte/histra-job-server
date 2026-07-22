"""Initial HiStrA job server schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_parallel_jobs", sa.Integer(), nullable=False),
        sa.Column("worker_version", sa.String(length=50), nullable=True),
        sa.Column("solver_version", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("scenario_id", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("current_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("job_definition", sa.JSON(), nullable=False),
        sa.Column("model_filename", sa.String(length=255), nullable=False),
        sa.Column("model_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_size_bytes", sa.Integer(), nullable=False),
        sa.Column("package_relative_path", sa.String(length=500), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_created_at"), "jobs", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_jobs_current_attempt_id"),
        "jobs",
        ["current_attempt_id"],
        unique=False,
    )
    op.create_index(op.f("ix_jobs_priority"), "jobs", ["priority"], unique=False)
    op.create_index(op.f("ix_jobs_scenario_id"), "jobs", ["scenario_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("worker_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("results_json", sa.JSON(), nullable=True),
        sa.Column("run_json", sa.JSON(), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_attempts_job_id"), "job_attempts", ["job_id"], unique=False)
    op.create_index(
        op.f("ix_job_attempts_lease_expires_at"),
        "job_attempts",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(op.f("ix_job_attempts_status"), "job_attempts", ["status"], unique=False)
    op.create_index(op.f("ix_job_attempts_worker_id"), "job_attempts", ["worker_id"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index(op.f("ix_artifacts_attempt_id"), "artifacts", ["attempt_id"], unique=False)
    op.create_index(op.f("ix_artifacts_job_id"), "artifacts", ["job_id"], unique=False)
    op.create_index(op.f("ix_artifacts_kind"), "artifacts", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_artifacts_kind"), table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_job_id"), table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_attempt_id"), table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_job_attempts_worker_id"), table_name="job_attempts")
    op.drop_index(op.f("ix_job_attempts_status"), table_name="job_attempts")
    op.drop_index(op.f("ix_job_attempts_lease_expires_at"), table_name="job_attempts")
    op.drop_index(op.f("ix_job_attempts_job_id"), table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_scenario_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_priority"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_current_attempt_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_created_at"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("workers")

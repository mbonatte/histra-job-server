from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus:
    QUEUED = "queued"
    LEASED = "leased"
    DOWNLOADING = "downloading"
    RUNNING = "running"
    EXTRACTING = "extracting"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ACTIVE = {LEASED, DOWNLOADING, RUNNING, EXTRACTING, UPLOADING, VALIDATING}
    TERMINAL = {COMPLETED, FAILED, CANCELLED}


class AttemptStatus:
    LEASED = "leased"
    DOWNLOADING = "downloading"
    RUNNING = "running"
    EXTRACTING = "extracting"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    ACTIVE = {LEASED, DOWNLOADING, RUNNING, EXTRACTING, UPLOADING, VALIDATING}
    TERMINAL = {COMPLETED, FAILED, EXPIRED, CANCELLED}


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_parallel_jobs: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    worker_version: Mapped[str | None] = mapped_column(String(50))
    solver_version: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    attempts: Mapped[list[JobAttempt]] = relationship(back_populates="worker")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scenario_id: Mapped[str | None] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.QUEUED, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), index=True)
    job_definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    package_relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list[JobAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobAttempt.created_at"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobAttempt(Base):
    __tablename__ = "job_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=AttemptStatus.LEASED, index=True)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    results_json: Mapped[dict | None] = mapped_column(JSON)
    run_json: Mapped[dict | None] = mapped_column(JSON)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="attempts")
    worker: Mapped[Worker] = relationship(back_populates="attempts")
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="artifacts")
    attempt: Mapped[JobAttempt | None] = relationship(back_populates="artifacts")

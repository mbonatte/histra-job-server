from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AttemptStatus, Job, JobAttempt, JobStatus, Worker


def now_utc() -> datetime:
    return datetime.now(UTC)


def requeue_expired_attempts(session: Session, now: datetime | None = None) -> int:
    now = now or now_utc()
    attempts = session.scalars(
        select(JobAttempt)
        .where(
            JobAttempt.status.in_(AttemptStatus.ACTIVE),
            JobAttempt.lease_expires_at < now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    changed = 0
    for attempt in attempts:
        attempt.status = AttemptStatus.EXPIRED
        attempt.finished_at = now
        attempt.failure_reason = "Worker lease expired"
        job = session.get(Job, attempt.job_id)
        if job is None or job.current_attempt_id != attempt.id or job.status in JobStatus.TERMINAL:
            continue
        job.current_attempt_id = None
        if job.attempt_count < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.error_message = "Previous worker lease expired; job requeued"
        else:
            job.status = JobStatus.FAILED
            job.error_message = "Worker lease expired and maximum attempts were reached"
        changed += 1
    if attempts:
        session.commit()
    return changed


def claim_next_job(session: Session, worker: Worker, lease_seconds: int) -> JobAttempt | None:
    requeue_expired_attempts(session)
    session.refresh(worker, with_for_update=True)
    active_count = session.scalar(
        select(func.count(JobAttempt.id)).where(
            JobAttempt.worker_id == worker.id,
            JobAttempt.status.in_(AttemptStatus.ACTIVE),
        )
    )
    if int(active_count or 0) >= worker.max_parallel_jobs:
        return None

    job = session.scalar(
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.attempt_count < Job.max_attempts)
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        session.rollback()
        return None

    now = now_utc()
    attempt = JobAttempt(
        job_id=job.id,
        worker_id=worker.id,
        status=AttemptStatus.LEASED,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        last_heartbeat_at=now,
    )
    session.add(attempt)
    session.flush()
    job.status = JobStatus.LEASED
    job.attempt_count += 1
    job.current_attempt_id = attempt.id
    job.error_message = None
    worker.last_seen_at = now
    session.commit()
    session.refresh(attempt)
    return attempt


def get_current_attempt(session: Session, job_id: str, attempt_id: str) -> tuple[Job, JobAttempt]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    attempt = session.get(JobAttempt, attempt_id)
    if attempt is None or attempt.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    return job, attempt


def require_active_attempt(job: Job, attempt: JobAttempt) -> None:
    if job.current_attempt_id != attempt.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This attempt is no longer the current attempt for the job",
        )
    if attempt.status not in AttemptStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Attempt is not active; current status is {attempt.status}",
        )
    current = now_utc()
    expires = attempt.lease_expires_at
    if expires.tzinfo is None:
        current = current.replace(tzinfo=None)
    if expires < current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attempt lease has expired",
        )

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any
from histra_builder import JobSpec, TemplateRegistry, job_sha256
from sqlalchemy import select
from sqlalchemy.orm import Session
from .cache import PackageCache
from .models import Attempt, Job, Runner, utcnow
from .package import build_attempt_package

class ConflictError(Exception): pass
class NotFoundError(Exception): pass
class InvalidStateError(Exception): pass
class IdentityError(Exception): pass


def create_job(session: Session, document: dict[str, Any], *, priority: int, max_attempts: int, commit: bool = True):
    spec = JobSpec.model_validate(document)
    canonical = spec.model_dump(mode="json")
    digest = job_sha256(canonical)
    existing = session.get(Job, spec.job_id)
    if existing:
        if existing.job_sha256 != digest:
            raise ConflictError("job_id already exists with different canonical content")
        return existing, False
    job = Job(id=spec.job_id, document=canonical, job_sha256=digest, status="queued", priority=priority, max_attempts=max_attempts)
    session.add(job)
    if commit: session.commit()
    else: session.flush()
    return job, True


def create_jobs_batch(session: Session, documents: list[dict[str, Any]], *, priority: int, max_attempts: int):
    results=[]
    try:
        for document in documents:
            job, created = create_job(session, document, priority=priority, max_attempts=max_attempts, commit=False)
            results.append((job, created))
        session.commit()
    except Exception:
        session.rollback()
        raise
    return results


def register_runner(session: Session, payload: Any) -> Runner:
    runner_id = payload.runner_id or secrets.token_urlsafe(18)
    runner = session.get(Runner, runner_id)
    if runner is None:
        runner = Runner(id=runner_id, name=payload.name, version=payload.version, capabilities=payload.capabilities)
        session.add(runner)
    else:
        runner.name, runner.version, runner.capabilities = payload.name, payload.version, payload.capabilities
        runner.last_seen_at = utcnow()
    session.commit()
    return runner


def expire_leases(session: Session, cache: PackageCache, now: datetime | None = None) -> int:
    now = now or utcnow()
    expired = session.scalars(select(Attempt).where(Attempt.status == "leased", Attempt.lease_expires_at.is_not(None), Attempt.lease_expires_at < now)).all()
    for attempt in expired:
        attempt.status, attempt.updated_at = "expired", now
        attempt.failure = {"error_type": "lease_expired", "message": "runner lease expired"}
        job = session.get(Job, attempt.job_id)
        if job is not None and job.status == "leased":
            job.status = "queued" if job.attempts_count < job.max_attempts else "failed"
            job.updated_at = now
        cache.delete(attempt.id)
    if expired: session.commit()
    return len(expired)


def claim_job(session: Session, *, runner_id: str, registry: TemplateRegistry, cache: PackageCache, lease_seconds: int):
    runner = session.get(Runner, runner_id)
    if runner is None: raise NotFoundError("runner is not registered")
    expire_leases(session, cache)
    statement = select(Job).where(Job.status == "queued", Job.attempts_count < Job.max_attempts).order_by(Job.priority.desc(), Job.created_at.asc()).limit(1).with_for_update(skip_locked=True)
    job = session.scalar(statement)
    if job is None: return None
    attempt = Attempt(id=str(uuid.uuid4()), job_id=job.id, runner_id=runner_id, sequence=job.attempts_count + 1, status="building", job_sha256=job.job_sha256)
    job.attempts_count += 1; job.status = "building"; job.updated_at = utcnow()
    session.add(attempt); session.flush()
    try:
        package_bytes, manifest = build_attempt_package(job.document, attempt_id=attempt.id, created_at=attempt.created_at, registry=registry)
        cache.put(attempt.id, package_bytes)
    except Exception as exc:
        now=utcnow(); attempt.status="failed"; attempt.failure={"error_type":type(exc).__name__,"message":str(exc)}; attempt.updated_at=now; attempt.completed_at=now; job.status="failed"; job.updated_at=now; session.commit()
        raise InvalidStateError(f"JOB compilation failed: {exc}") from exc
    now=utcnow(); attempt.status="leased"; attempt.hrx_sha256=manifest["hrx"]["sha256"]; attempt.package_manifest=manifest; attempt.lease_expires_at=now+timedelta(seconds=lease_seconds); attempt.updated_at=now; job.status="leased"; job.updated_at=now; runner.last_seen_at=now; session.commit()
    return job, attempt


def get_package(session: Session, *, job_id: str, attempt_id: str, runner_id: str, registry: TemplateRegistry, cache: PackageCache) -> bytes:
    attempt=session.get(Attempt, attempt_id)
    if attempt is None or attempt.job_id != job_id: raise NotFoundError("attempt does not exist")
    if attempt.runner_id != runner_id: raise IdentityError("attempt belongs to another runner")
    if attempt.status != "leased": raise InvalidStateError(f"package is unavailable while attempt is {attempt.status}")
    package=cache.get(attempt.id)
    if package is not None: return package
    job=session.get(Job,job_id); assert job is not None
    regenerated,manifest=build_attempt_package(job.document,attempt_id=attempt.id,created_at=attempt.created_at,registry=registry)
    if attempt.package_manifest != manifest or attempt.hrx_sha256 != manifest["hrx"]["sha256"]: raise ConflictError("regenerated package is not identical to the leased package")
    cache.put(attempt.id,regenerated); return regenerated


def heartbeat(session: Session, *, job_id: str, attempt_id: str, runner_id: str, lease_seconds: int) -> Attempt:
    attempt=session.get(Attempt,attempt_id)
    if attempt is None or attempt.job_id != job_id: raise NotFoundError("attempt does not exist")
    if attempt.runner_id != runner_id: raise IdentityError("attempt belongs to another runner")
    if attempt.status != "leased": raise InvalidStateError(f"cannot heartbeat an attempt in state {attempt.status}")
    now=utcnow(); attempt.lease_expires_at=now+timedelta(seconds=lease_seconds); attempt.updated_at=now
    runner=session.get(Runner,runner_id)
    if runner is not None: runner.last_seen_at=now
    session.commit(); return attempt


def complete_attempt(session: Session, *, job_id: str, attempt_id: str, payload: Any, cache: PackageCache) -> Attempt:
    attempt=session.get(Attempt,attempt_id)
    if attempt is None or attempt.job_id != job_id: raise NotFoundError("attempt does not exist")
    if attempt.runner_id != payload.runner_id: raise IdentityError("attempt belongs to another runner")
    if payload.job_id != job_id or payload.attempt_id != attempt_id: raise IdentityError("result identity does not match URL")
    if payload.job_sha256 != attempt.job_sha256 or payload.hrx_sha256 != attempt.hrx_sha256: raise IdentityError("result provenance does not match the leased package")
    envelope=payload.model_dump(mode="json")
    if attempt.status == "completed":
        if attempt.result != envelope: raise ConflictError("attempt is already completed with different results")
        return attempt
    if attempt.status != "leased": raise InvalidStateError(f"cannot complete an attempt in state {attempt.status}")
    now=utcnow(); attempt.status="completed"; attempt.result=envelope; attempt.logs=payload.logs; attempt.updated_at=now; attempt.completed_at=now
    job=session.get(Job,job_id); assert job is not None; job.status="completed"; job.result=envelope; job.updated_at=now; job.completed_at=now
    cache.delete(attempt.id); session.commit(); return attempt


def fail_attempt(session: Session, *, job_id: str, attempt_id: str, payload: Any, cache: PackageCache) -> Attempt:
    attempt=session.get(Attempt,attempt_id)
    if attempt is None or attempt.job_id != job_id: raise NotFoundError("attempt does not exist")
    if attempt.runner_id != payload.runner_id: raise IdentityError("attempt belongs to another runner")
    if attempt.status != "leased": raise InvalidStateError(f"cannot fail an attempt in state {attempt.status}")
    now=utcnow(); attempt.status="failed"; attempt.failure={"error_type":payload.error_type,"message":payload.message,"details":payload.details}; attempt.logs=payload.logs; attempt.updated_at=now; attempt.completed_at=now
    job=session.get(Job,job_id); assert job is not None; job.status="queued" if job.attempts_count < job.max_attempts else "failed"; job.updated_at=now
    cache.delete(attempt.id); session.commit(); return attempt


def cancel_job(session: Session, job_id: str, cache: PackageCache) -> Job:
    job=session.get(Job,job_id)
    if job is None: raise NotFoundError("job does not exist")
    if job.status in {"completed","cancelled"}: return job
    now=utcnow(); job.status="cancelled"; job.updated_at=now
    for attempt in job.attempts:
        if attempt.status in {"building","leased"}: attempt.status="cancelled"; attempt.updated_at=now; cache.delete(attempt.id)
    session.commit(); return job

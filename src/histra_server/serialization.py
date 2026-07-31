from __future__ import annotations

from datetime import timezone
from typing import Any
from .models import Attempt, Job, Runner


def iso(value):
    if value is None: return None
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")


def job_json(job: Job, include_document: bool=False, include_attempts: bool=False) -> dict[str,Any]:
    value={"job_id":job.id,"job_sha256":job.job_sha256,"status":job.status,"priority":job.priority,"max_attempts":job.max_attempts,"attempts_count":job.attempts_count,"created_at":iso(job.created_at),"updated_at":iso(job.updated_at),"completed_at":iso(job.completed_at),"result":job.result}
    if include_document: value["job"]=job.document
    if include_attempts: value["attempts"]=[attempt_json(item,include_payload=True) for item in job.attempts]
    return value


def attempt_json(attempt: Attempt, include_payload: bool=False) -> dict[str,Any]:
    value={"attempt_id":attempt.id,"job_id":attempt.job_id,"runner_id":attempt.runner_id,"sequence":attempt.sequence,"status":attempt.status,"job_sha256":attempt.job_sha256,"hrx_sha256":attempt.hrx_sha256,"lease_expires_at":iso(attempt.lease_expires_at),"created_at":iso(attempt.created_at),"updated_at":iso(attempt.updated_at),"completed_at":iso(attempt.completed_at),"failure":attempt.failure}
    if include_payload: value.update({"manifest":attempt.package_manifest,"result":attempt.result,"logs":attempt.logs})
    return value


def runner_json(runner: Runner, *, online: bool | None=None) -> dict[str,Any]:
    value={"runner_id":runner.id,"name":runner.name,"version":runner.version,"capabilities":runner.capabilities,"registered_at":iso(runner.registered_at),"last_seen_at":iso(runner.last_seen_at)}
    if online is not None: value["online"]=online
    return value

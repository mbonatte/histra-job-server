"""JSON-only immutable JOB submission API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..contracts import assert_supported_job, campaign_fields, job_metadata, job_sha256
from ..db import get_session
from ..models import Job, JobStatus
from ..schemas import JobDefinition, JobResponse

router = APIRouter(prefix="/api/v2", tags=["jobs-v2"])


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job_v2(
    definition: JobDefinition,
    priority: Annotated[int, Query(ge=-1_000_000, le=1_000_000)] = 0,
    max_attempts: Annotated[int, Query(ge=1, le=100)] = 3,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    session: Session = Depends(get_session),
):
    raw = definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        assert_supported_job(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    digest = job_sha256(raw)
    metadata = job_metadata(raw)
    supplied_digest = metadata.get("job_sha256")
    if supplied_digest and str(supplied_digest).lower() != digest:
        raise HTTPException(status_code=422, detail="metadata.job_sha256 is incorrect")
    metadata["job_sha256"] = digest
    metadata["submission_protocol"] = "api-v2-json"
    if idempotency_key:
        metadata["idempotency_key"] = idempotency_key[:200]
    raw["metadata"] = metadata

    existing = session.get(Job, definition.job_id)
    if existing is not None:
        if job_sha256(existing.job_definition) == digest:
            return existing
        raise HTTPException(
            status_code=409,
            detail="Job ID already exists with different content",
        )

    campaign = campaign_fields(raw)
    scenario = metadata.get("scenario_id") or campaign.get("campaign_id")
    job = Job(
        id=definition.job_id,
        scenario_id=str(scenario) if scenario is not None else None,
        status=JobStatus.QUEUED,
        priority=priority,
        max_attempts=max_attempts,
        job_definition=raw,
        model_filename=definition.model.path,
        model_sha256="",
        model_size_bytes=0,
        package_relative_path="",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.get("/jobs/{job_id}/definition")
def get_job_definition_v2(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job": job.job_definition,
        "job_sha256": job_sha256(job.job_definition),
        "generated_hrx_sha256": job.model_sha256 or None,
    }

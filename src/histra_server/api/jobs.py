from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..db import get_session
from ..models import Artifact, AttemptStatus, Job, JobAttempt, JobStatus, Worker, utcnow
from ..schemas import (
    ArtifactResponse,
    AttemptFailureRequest,
    AttemptHeartbeatRequest,
    AttemptResponse,
    ClaimRequest,
    ClaimResponse,
    JobDefinition,
    JobDetailResponse,
    JobPageResponse,
    JobResponse,
    RetryJobRequest,
)
from ..services.jobs import (
    claim_next_job,
    get_current_attempt,
    now_utc,
    require_active_attempt,
)
from ..storage import SavedFile, Storage

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _storage(request: Request) -> Storage:
    return request.app.state.storage


async def _read_json_upload(upload: UploadFile, max_bytes: int, label: str) -> dict:
    payload = await upload.read(max_bytes + 1)
    await upload.close()
    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{label} exceeds the {max_bytes}-byte limit",
        )
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} is not valid UTF-8 JSON: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} must contain a JSON object",
        )
    return value


def _artifact(job_id: str, attempt_id: str | None, kind: str, saved: SavedFile) -> Artifact:
    return Artifact(
        job_id=job_id,
        attempt_id=attempt_id,
        kind=kind,
        filename=saved.filename,
        relative_path=saved.relative_path,
        content_type=saved.content_type,
        size_bytes=saved.size_bytes,
        sha256=saved.sha256,
    )


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    job_file: Annotated[UploadFile, File(description="Runner-compatible job.json")],
    model_file: Annotated[UploadFile, File(description="HiStrA HRX model")],
    priority: Annotated[int, Form()] = 0,
    max_attempts: Annotated[int, Form(ge=1, le=100)] = 3,
    session: Session = Depends(get_session),
):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Persistent HRX uploads were removed; submit JOB JSON to POST /api/v2/jobs",
    )

    settings = _settings(request)
    storage = _storage(request)
    raw_definition = await _read_json_upload(job_file, settings.max_job_json_bytes, "job.json")
    try:
        definition = JobDefinition.model_validate(raw_definition)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    if session.get(Job, definition.job_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job already exists")

    expected_model_filename = definition.model.path
    if (
        model_file.filename
        and Path(model_file.filename).name.lower() != expected_model_filename.lower()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Uploaded model filename {model_file.filename!r} does not match "
                f"job model.path {expected_model_filename!r}"
            ),
        )

    job_id = definition.job_id
    try:
        job_saved = storage.write_json(
            f"jobs/{job_id}/input/job.json",
            raw_definition,
            filename="job.json",
        )
        model_saved = await storage.save_upload(
            model_file,
            f"jobs/{job_id}/input/{expected_model_filename}",
            max_bytes=settings.max_model_bytes,
            filename=expected_model_filename,
        )
        package_saved = storage.build_package(job_id, expected_model_filename)

        scenario_id = raw_definition.get("metadata", {}).get("scenario_id")
        job = Job(
            id=job_id,
            scenario_id=str(scenario_id) if scenario_id is not None else None,
            status=JobStatus.QUEUED,
            priority=priority,
            max_attempts=max_attempts,
            job_definition=raw_definition,
            model_filename=expected_model_filename,
            model_sha256=model_saved.sha256,
            model_size_bytes=model_saved.size_bytes,
            package_relative_path=package_saved.relative_path,
        )
        session.add(job)
        session.add_all(
            [
                _artifact(job_id, None, "job_json", job_saved),
                _artifact(job_id, None, "model_hrx", model_saved),
                _artifact(job_id, None, "job_package", package_saved),
            ]
        )
        session.commit()
        session.refresh(job)
        return job
    except Exception:
        session.rollback()
        storage.delete_job(job_id)
        raise


@router.get("/jobs", response_model=JobPageResponse)
def list_jobs(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
):
    query = select(Job)
    count_query = select(func.count(Job.id))
    if status_filter:
        query = query.where(Job.status == status_filter)
        count_query = count_query.where(Job.status == status_filter)
    jobs = session.scalars(query.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    total = int(session.scalar(count_query) or 0)
    return JobPageResponse(items=jobs, total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.scalar(select(Job).where(Job.id == job_id).options(selectinload(Job.attempts)))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/attempts", response_model=list[AttemptResponse])
def list_attempts(job_id: str, session: Session = Depends(get_session)):
    if session.get(Job, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return session.scalars(
        select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.created_at)
    ).all()


@router.post(
    "/jobs/claim",
    response_model=ClaimResponse,
    responses={204: {"description": "No job"}},
)
def claim_job(payload: ClaimRequest, request: Request, session: Session = Depends(get_session)):
    worker = session.get(Worker, payload.worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    if not worker.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker is disabled")
    attempt = claim_next_job(session, worker, _settings(request).lease_seconds)
    if attempt is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    job = session.get(Job, attempt.job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="Claimed job disappeared")
    storage = _storage(request)
    attempt_definition = json.loads(json.dumps(job.job_definition))
    attempt_definition["attempt_id"] = attempt.id
    attempt_definition.setdefault("model", {})["sha256"] = job.model_sha256
    try:
        attempt_job_saved = storage.write_json(
            f"jobs/{job.id}/attempts/{attempt.id}/input/job.json",
            attempt_definition,
            filename="job.json",
        )
        attempt_package_saved = storage.build_attempt_package(
            job.id, attempt.id, job.model_filename
        )
        session.add_all(
            [
                _artifact(job.id, attempt.id, "attempt_job_json", attempt_job_saved),
                _artifact(job.id, attempt.id, "attempt_package", attempt_package_saved),
            ]
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        job, attempt = get_current_attempt(session, job.id, attempt.id)
        attempt.status = AttemptStatus.FAILED
        attempt.finished_at = utcnow()
        attempt.failure_reason = f"Could not build worker package: {exc}"
        job.current_attempt_id = None
        job.error_message = attempt.failure_reason
        job.status = JobStatus.QUEUED if job.attempt_count < job.max_attempts else JobStatus.FAILED
        session.commit()
        raise HTTPException(status_code=500, detail="Could not build worker package") from exc

    base = f"/api/v1/jobs/{attempt.job_id}/attempts/{attempt.id}"
    return ClaimResponse(
        job_id=attempt.job_id,
        attempt_id=attempt.id,
        lease_expires_at=attempt.lease_expires_at,
        package_url=f"{base}/package",
        heartbeat_url=f"{base}/heartbeat",
        results_url=f"{base}/results",
        failure_url=f"{base}/failed",
    )


@router.get("/jobs/{job_id}/attempts/{attempt_id}/package")
def download_package(
    job_id: str,
    attempt_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    job, attempt = get_current_attempt(session, job_id, attempt_id)
    require_active_attempt(job, attempt)
    if job.package_relative_path:
        package_path = _storage(request).resolve(
            f"jobs/{job_id}/attempts/{attempt_id}/input/package.zip"
        )
        if not package_path.is_file():
            raise HTTPException(status_code=500, detail="Attempt package is missing from storage")
    else:
        try:
            built = request.app.state.package_builder.build(
                job.job_definition,
                job_id=job_id,
                attempt_id=attempt_id,
            )
        except Exception as exc:
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = utcnow()
            attempt.failure_reason = f"JOB-to-HRX build failed: {exc}"
            job.current_attempt_id = None
            job.error_message = attempt.failure_reason
            job.status = (
                JobStatus.QUEUED if job.attempt_count < job.max_attempts else JobStatus.FAILED
            )
            session.commit()
            raise HTTPException(
                status_code=502,
                detail=f"JOB-to-HRX build failed: {exc}",
            ) from exc
        job.model_sha256 = built.hrx_sha256
        job.model_size_bytes = built.hrx_size_bytes
        attempt.progress_json = {
            **(attempt.progress_json or {}),
            "job_sha256": built.job_sha256,
            "hrx_sha256": built.hrx_sha256,
            "builder_version": built.builder_version,
        }
        session.commit()
        package_path = built.path
    return FileResponse(
        package_path,
        media_type="application/zip",
        filename=f"{job_id}.zip",
    )


@router.post("/jobs/{job_id}/attempts/{attempt_id}/heartbeat", response_model=AttemptResponse)
def heartbeat_attempt(
    job_id: str,
    attempt_id: str,
    payload: AttemptHeartbeatRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    job, attempt = get_current_attempt(session, job_id, attempt_id)
    require_active_attempt(job, attempt)
    now = now_utc()
    attempt.status = payload.status
    attempt.progress_json = payload.progress
    attempt.last_heartbeat_at = now
    attempt.lease_expires_at = now + timedelta(seconds=_settings(request).lease_seconds)
    if payload.status == AttemptStatus.RUNNING and attempt.started_at is None:
        attempt.started_at = now
    job.status = payload.status
    worker = session.get(Worker, attempt.worker_id)
    if worker is not None:
        worker.last_seen_at = now
    session.commit()
    session.refresh(attempt)
    return attempt


@router.post("/jobs/{job_id}/attempts/{attempt_id}/results", response_model=JobResponse)
async def upload_results(
    job_id: str,
    attempt_id: str,
    request: Request,
    results_file: Annotated[UploadFile, File(description="Runner output/results.json")],
    run_file: Annotated[UploadFile, File(description="Runner output/run.json")],
    validation_file: Annotated[UploadFile | None, File()] = None,
    solver_log: Annotated[UploadFile | None, File()] = None,
    extractor_log: Annotated[UploadFile | None, File()] = None,
    session: Session = Depends(get_session),
):
    job, attempt = get_current_attempt(session, job_id, attempt_id)
    if attempt.status == AttemptStatus.COMPLETED and job.status == JobStatus.COMPLETED:
        return job
    require_active_attempt(job, attempt)
    settings = _settings(request)
    storage = _storage(request)

    results_data = await _read_json_upload(
        results_file, settings.max_result_file_bytes, "results.json"
    )
    run_data = await _read_json_upload(run_file, settings.max_result_file_bytes, "run.json")
    validation_data = (
        await _read_json_upload(validation_file, settings.max_result_file_bytes, "validation.json")
        if validation_file is not None
        else None
    )

    for label, payload in (("results.json", results_data), ("run.json", run_data)):
        if payload.get("job_id") != job_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{label} job_id does not match the claimed job",
            )
        if payload.get("attempt_id") != attempt_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{label} attempt_id does not match the server attempt",
            )
    if results_data.get("schema_version") != "1.0" or run_data.get("schema_version") != "1.0":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="results.json and run.json must use schema_version '1.0'",
        )
    if run_data.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="run.json status must be 'completed'; use the failure endpoint otherwise",
        )
    input_sha256 = run_data.get("model", {}).get("input_sha256")
    if (
        job.model_sha256
        and input_sha256 is not None
        and str(input_sha256).lower() != job.model_sha256.lower()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="run.json model.input_sha256 does not match the server HRX",
        )
    analysis_results = results_data.get("analyses")
    if not isinstance(analysis_results, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="results.json analyses must be an object keyed by analysis name",
        )
    requested_names = {item["name"] for item in job.job_definition["analyses"]}
    missing_names = sorted(requested_names - set(analysis_results))
    if missing_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"results.json is missing requested analyses: {missing_names}",
        )

    attempt.status = AttemptStatus.VALIDATING
    job.status = JobStatus.VALIDATING
    session.flush()

    saved_files: list[tuple[str, SavedFile]] = []
    saved_files.append(
        (
            "results_json",
            storage.write_json(
                f"jobs/{job_id}/attempts/{attempt_id}/results/results.json",
                results_data,
                filename="results.json",
            ),
        )
    )
    saved_files.append(
        (
            "run_json",
            storage.write_json(
                f"jobs/{job_id}/attempts/{attempt_id}/results/run.json",
                run_data,
                filename="run.json",
            ),
        )
    )
    if validation_data is not None:
        saved_files.append(
            (
                "validation_json",
                storage.write_json(
                    f"jobs/{job_id}/attempts/{attempt_id}/results/validation.json",
                    validation_data,
                    filename="validation.json",
                ),
            )
        )
    if solver_log is not None:
        saved_files.append(
            (
                "solver_log",
                await storage.save_upload(
                    solver_log,
                    f"jobs/{job_id}/attempts/{attempt_id}/logs/solver.log",
                    max_bytes=settings.max_log_file_bytes,
                    filename="solver.log",
                ),
            )
        )
    if extractor_log is not None:
        saved_files.append(
            (
                "extractor_log",
                await storage.save_upload(
                    extractor_log,
                    f"jobs/{job_id}/attempts/{attempt_id}/logs/extractor.log",
                    max_bytes=settings.max_log_file_bytes,
                    filename="extractor.log",
                ),
            )
        )

    for kind, saved in saved_files:
        session.add(_artifact(job_id, attempt_id, kind, saved))

    now = utcnow()
    attempt.results_json = results_data
    attempt.run_json = run_data
    attempt.validation_json = validation_data
    attempt.exit_code = run_data.get("process_exit_code", run_data.get("exit_code"))
    attempt.status = AttemptStatus.COMPLETED
    attempt.finished_at = now
    if attempt.started_at is None:
        attempt.started_at = attempt.created_at
    job.status = JobStatus.COMPLETED
    job.completed_at = now
    job.current_attempt_id = attempt.id
    job.error_message = None
    session.commit()
    session.refresh(job)
    return job


@router.post("/jobs/{job_id}/attempts/{attempt_id}/failed", response_model=JobResponse)
def fail_attempt(
    job_id: str,
    attempt_id: str,
    payload: AttemptFailureRequest,
    session: Session = Depends(get_session),
):
    job, attempt = get_current_attempt(session, job_id, attempt_id)
    if attempt.status in AttemptStatus.TERMINAL:
        return job
    require_active_attempt(job, attempt)
    now = utcnow()
    attempt.status = AttemptStatus.FAILED
    attempt.finished_at = now
    attempt.exit_code = payload.exit_code
    attempt.failure_reason = payload.reason
    attempt.run_json = payload.run
    attempt.validation_json = payload.validation
    job.current_attempt_id = None
    job.error_message = payload.reason
    if payload.retryable and job.attempt_count < job.max_attempts:
        job.status = JobStatus.QUEUED
    else:
        job.status = JobStatus.FAILED
    session.commit()
    session.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: str,
    payload: RetryJobRequest,
    session: Session = Depends(get_session),
):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status in JobStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is currently active")
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Completed jobs are immutable"
        )
    job.max_attempts = max(job.max_attempts, job.attempt_count + payload.additional_attempts)
    job.status = JobStatus.QUEUED
    job.current_attempt_id = None
    job.error_message = None
    session.commit()
    session.refresh(job)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed jobs cannot be cancelled",
        )
    if job.current_attempt_id:
        attempt = session.get(JobAttempt, job.current_attempt_id)
        if attempt and attempt.status in AttemptStatus.ACTIVE:
            attempt.status = AttemptStatus.CANCELLED
            attempt.finished_at = utcnow()
            attempt.failure_reason = "Job cancelled on the server"
    job.status = JobStatus.CANCELLED
    job.current_attempt_id = None
    session.commit()
    session.refresh(job)
    return job


@router.get("/jobs/{job_id}/results")
def get_results(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    attempt = session.scalar(
        select(JobAttempt)
        .where(JobAttempt.job_id == job_id, JobAttempt.status == AttemptStatus.COMPLETED)
        .order_by(JobAttempt.finished_at.desc())
        .limit(1)
    )
    if attempt is None or attempt.results_json is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results available")
    return JSONResponse(attempt.results_json)


@router.get("/jobs/{job_id}/artifacts", response_model=list[ArtifactResponse])
def list_job_artifacts(job_id: str, session: Session = Depends(get_session)):
    if session.get(Job, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return session.scalars(
        select(Artifact).where(Artifact.job_id == job_id).order_by(Artifact.created_at)
    ).all()


@router.get("/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    path = _storage(request).resolve(artifact.relative_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file is missing"
        )
    return FileResponse(path, media_type=artifact.content_type, filename=artifact.filename)

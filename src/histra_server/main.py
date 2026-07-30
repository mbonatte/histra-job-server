from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from histra_builder import TemplateRegistry

from .cache import PackageCache
from .config import Settings
from .db import Base, create_session_factory
from .models import Attempt, Job, Runner
from .schemas import ClaimRequest, FailureUpload, HeartbeatRequest, ResultsUpload, RunnerRegistration
from .service import (
    ConflictError,
    IdentityError,
    InvalidStateError,
    NotFoundError,
    cancel_job,
    claim_job,
    complete_attempt,
    create_job,
    fail_attempt,
    get_package,
    heartbeat,
    register_runner,
)


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_json(job: Job, include_document: bool = False) -> dict[str, Any]:
    result = {
        "job_id": job.id,
        "job_sha256": job.job_sha256,
        "status": job.status,
        "priority": job.priority,
        "max_attempts": job.max_attempts,
        "attempts_count": job.attempts_count,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "completed_at": _iso(job.completed_at),
        "result": job.result,
    }
    if include_document:
        result["job"] = job.document
    return result


def _attempt_json(attempt: Attempt) -> dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "job_id": attempt.job_id,
        "runner_id": attempt.runner_id,
        "sequence": attempt.sequence,
        "status": attempt.status,
        "job_sha256": attempt.job_sha256,
        "hrx_sha256": attempt.hrx_sha256,
        "lease_expires_at": _iso(attempt.lease_expires_at),
        "created_at": _iso(attempt.created_at),
        "completed_at": _iso(attempt.completed_at),
        "failure": attempt.failure,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine, session_factory = create_session_factory(settings.database_url)
    registry = TemplateRegistry(settings.template_root)
    cache = PackageCache(settings.package_cache_root, settings.package_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.template_root.mkdir(parents=True, exist_ok=True)
        settings.package_cache_root.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)
        cache.prune()
        yield
        engine.dispose()

    app = FastAPI(title="HiStrA Job Server", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.registry = registry
    app.state.cache = cache

    def db() -> Session:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        if settings.api_token is None:
            return
        if authorization != f"Bearer {settings.api_token}":
            raise HTTPException(status_code=401, detail="invalid bearer token")

    def translate(exc: Exception) -> HTTPException:
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, (ConflictError, IdentityError)):
            return HTTPException(409, str(exc))
        if isinstance(exc, InvalidStateError):
            return HTTPException(422, str(exc))
        raise exc

    @app.get("/health/live")
    def live():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/health/ready")
    def ready(session: Session = Depends(db)):
        try:
            session.execute(text("SELECT 1"))
            settings.template_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise HTTPException(503, f"not ready: {exc}") from exc
        return {"status": "ready"}

    @app.post("/jobs", dependencies=[Depends(authenticate)])
    def submit_job(
        document: dict[str, Any],
        priority: int = Query(default=0, ge=-1000, le=1000),
        max_attempts: int | None = Query(default=None, ge=1, le=100),
        session: Session = Depends(db),
    ):
        try:
            job, created = create_job(
                session,
                document,
                priority=priority,
                max_attempts=max_attempts or settings.default_max_attempts,
            )
        except ConflictError as exc:
            raise translate(exc)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        payload = _job_json(job, include_document=True)
        return JSONResponse(payload, status_code=201 if created else 200)

    @app.get("/jobs", dependencies=[Depends(authenticate)])
    def list_jobs(
        job_status: str | None = Query(default=None, alias="status"),
        session: Session = Depends(db),
    ):
        statement = select(Job).order_by(Job.created_at.desc())
        if job_status:
            statement = statement.where(Job.status == job_status)
        return {"items": [_job_json(job) for job in session.scalars(statement).all()]}

    @app.get("/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def read_job(job_id: str, session: Session = Depends(db)):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job does not exist")
        payload = _job_json(job, include_document=True)
        payload["attempts"] = [_attempt_json(attempt) for attempt in job.attempts]
        return payload

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(authenticate)])
    def cancel(job_id: str, session: Session = Depends(db)):
        try:
            return _job_json(cancel_job(session, job_id, cache))
        except Exception as exc:
            raise translate(exc)

    @app.post("/runners/register", dependencies=[Depends(authenticate)])
    def register(payload: RunnerRegistration, session: Session = Depends(db)):
        runner = register_runner(session, payload)
        return {
            "runner_id": runner.id,
            "name": runner.name,
            "version": runner.version,
            "capabilities": runner.capabilities,
            "registered_at": _iso(runner.registered_at),
        }

    @app.post("/claims", dependencies=[Depends(authenticate)])
    def claim(payload: ClaimRequest, request: Request, session: Session = Depends(db)):
        try:
            claimed = claim_job(
                session,
                runner_id=payload.runner_id,
                registry=registry,
                cache=cache,
                lease_seconds=settings.lease_seconds,
            )
        except Exception as exc:
            raise translate(exc)
        if claimed is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        job, attempt = claimed
        package_path = f"/jobs/{job.id}/attempts/{attempt.id}/package"
        return {
            "job_id": job.id,
            "attempt_id": attempt.id,
            "job_sha256": attempt.job_sha256,
            "hrx_sha256": attempt.hrx_sha256,
            "lease_expires_at": _iso(attempt.lease_expires_at),
            "package_url": str(request.base_url).rstrip("/") + package_path,
        }

    @app.get(
        "/jobs/{job_id}/attempts/{attempt_id}/package",
        dependencies=[Depends(authenticate)],
    )
    def package(
        job_id: str,
        attempt_id: str,
        x_runner_id: str = Header(alias="X-Runner-ID"),
        session: Session = Depends(db),
    ):
        try:
            data = get_package(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                runner_id=x_runner_id,
                registry=registry,
                cache=cache,
            )
        except Exception as exc:
            raise translate(exc)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{attempt_id}.zip"'},
        )

    @app.post(
        "/jobs/{job_id}/attempts/{attempt_id}/heartbeat",
        dependencies=[Depends(authenticate)],
    )
    def heartbeat_route(
        job_id: str,
        attempt_id: str,
        payload: HeartbeatRequest,
        session: Session = Depends(db),
    ):
        try:
            attempt = heartbeat(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                runner_id=payload.runner_id,
                lease_seconds=settings.lease_seconds,
            )
        except Exception as exc:
            raise translate(exc)
        return {"lease_expires_at": _iso(attempt.lease_expires_at)}

    @app.post(
        "/jobs/{job_id}/attempts/{attempt_id}/results",
        dependencies=[Depends(authenticate)],
    )
    def results(
        job_id: str,
        attempt_id: str,
        payload: ResultsUpload,
        session: Session = Depends(db),
    ):
        try:
            attempt = complete_attempt(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                payload=payload,
                cache=cache,
            )
        except Exception as exc:
            raise translate(exc)
        return _attempt_json(attempt)

    @app.post(
        "/jobs/{job_id}/attempts/{attempt_id}/failed",
        dependencies=[Depends(authenticate)],
    )
    def failed(
        job_id: str,
        attempt_id: str,
        payload: FailureUpload,
        session: Session = Depends(db),
    ):
        try:
            attempt = fail_attempt(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                payload=payload,
                cache=cache,
            )
        except Exception as exc:
            raise translate(exc)
        return _attempt_json(attempt)

    return app


app = create_app()

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from histra_builder import (
    JobSpec,
    TemplateRegistry,
    compile_job,
    generate_variants,
    inspect_hrx,
    job_from_hrx,
    preview_job,
)

from . import __version__
from .cache import PackageCache
from .config import Settings
from .db import Base, create_session_factory
from .models import Attempt, Job, Runner, utcnow
from .results import extract_numeric_series
from .schemas import (
    ClaimRequest,
    FailureUpload,
    HeartbeatRequest,
    ResultsUpload,
    RunnerRegistration,
    SubmitBatchRequest,
    VariantBatchRequest,
)
from .serialization import attempt_json, job_json, runner_json
from .service import (
    ConflictError,
    IdentityError,
    InvalidStateError,
    NotFoundError,
    cancel_job,
    claim_job,
    complete_attempt,
    create_job,
    create_jobs_batch,
    fail_attempt,
    get_package,
    heartbeat,
    register_runner,
)

_STATIC_ROOT = Path(__file__).with_name("static")


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

    app = FastAPI(
        title="HiStrA Job Server",
        version=__version__,
        description="Canonical JOB orchestration, dashboard and HRX/JOB authoring API.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.registry = registry
    app.state.cache = cache

    if _STATIC_ROOT.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_ROOT), name="static")

    def db():
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

    @app.get("/", include_in_schema=False)
    def index():
        if settings.dashboard_enabled:
            return RedirectResponse("/dashboard")
        if settings.builder_ui_enabled:
            return RedirectResponse("/builder")
        return RedirectResponse("/docs")

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/dashboard/", include_in_schema=False)
    def dashboard_page():
        if not settings.dashboard_enabled:
            raise HTTPException(404, "dashboard is disabled")
        return FileResponse(_STATIC_ROOT / "dashboard.html")

    @app.get("/builder", include_in_schema=False)
    @app.get("/builder/", include_in_schema=False)
    def builder_page():
        if not settings.builder_ui_enabled:
            raise HTTPException(404, "builder UI is disabled")
        return FileResponse(_STATIC_ROOT / "builder.html")

    @app.get("/viewer", include_in_schema=False)
    @app.get("/viewer/", include_in_schema=False)
    def viewer_page():
        if not settings.builder_ui_enabled:
            raise HTTPException(404, "viewer UI is disabled")
        return FileResponse(_STATIC_ROOT / "viewer.html")

    @app.get("/health/live")
    def live():
        return {
            "status": "ok",
            "version": __version__,
            "dashboard_enabled": settings.dashboard_enabled,
            "builder_ui_enabled": settings.builder_ui_enabled,
        }

    @app.get("/health/ready")
    def ready(session: Session = Depends(db)):
        try:
            session.execute(text("SELECT 1"))
            settings.template_root.mkdir(parents=True, exist_ok=True)
            settings.package_cache_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise HTTPException(503, f"not ready: {exc}") from exc
        return {"status": "ready", "version": __version__}

    # Canonical v1 runner/server API. Existing runners remain compatible.
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
        return JSONResponse(job_json(job, include_document=True), status_code=201 if created else 200)

    @app.get("/jobs", dependencies=[Depends(authenticate)])
    def list_jobs(
        job_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=500, ge=1, le=5000),
        session: Session = Depends(db),
    ):
        statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if job_status:
            statement = statement.where(Job.status == job_status)
        return {"items": [job_json(job) for job in session.scalars(statement).all()]}

    @app.get("/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def read_job(job_id: str, session: Session = Depends(db)):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job does not exist")
        return job_json(job, include_document=True, include_attempts=True)

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(authenticate)])
    def cancel(job_id: str, session: Session = Depends(db)):
        try:
            return job_json(cancel_job(session, job_id, cache))
        except Exception as exc:
            raise translate(exc)

    @app.post("/runners/register", dependencies=[Depends(authenticate)])
    def register(payload: RunnerRegistration, session: Session = Depends(db)):
        return runner_json(register_runner(session, payload))

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
            "lease_expires_at": attempt_json(attempt)["lease_expires_at"],
            "package_url": str(request.base_url).rstrip("/") + package_path,
        }

    @app.get("/jobs/{job_id}/attempts/{attempt_id}/package", dependencies=[Depends(authenticate)])
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

    @app.post("/jobs/{job_id}/attempts/{attempt_id}/heartbeat", dependencies=[Depends(authenticate)])
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
        return {"lease_expires_at": attempt_json(attempt)["lease_expires_at"]}

    @app.post("/jobs/{job_id}/attempts/{attempt_id}/results", dependencies=[Depends(authenticate)])
    def results(
        job_id: str,
        attempt_id: str,
        payload: ResultsUpload,
        session: Session = Depends(db),
    ):
        try:
            attempt = complete_attempt(
                session, job_id=job_id, attempt_id=attempt_id, payload=payload, cache=cache
            )
        except Exception as exc:
            raise translate(exc)
        return attempt_json(attempt)

    @app.post("/jobs/{job_id}/attempts/{attempt_id}/failed", dependencies=[Depends(authenticate)])
    def failed(
        job_id: str,
        attempt_id: str,
        payload: FailureUpload,
        session: Session = Depends(db),
    ):
        try:
            attempt = fail_attempt(
                session, job_id=job_id, attempt_id=attempt_id, payload=payload, cache=cache
            )
        except Exception as exc:
            raise translate(exc)
        return attempt_json(attempt)

    # Dashboard read API.
    @app.get("/api/ui/dashboard/summary", dependencies=[Depends(authenticate)])
    def dashboard_summary(session: Session = Depends(db)):
        status_rows = session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
        statuses = {name: count for name, count in status_rows}
        cutoff = utcnow() - timedelta(seconds=settings.runner_online_seconds)
        total_runners = session.scalar(select(func.count(Runner.id))) or 0
        online_runners = session.scalar(select(func.count(Runner.id)).where(Runner.last_seen_at >= cutoff)) or 0
        total_attempts = session.scalar(select(func.count(Attempt.id))) or 0
        return {
            "jobs": {"total": sum(statuses.values()), "by_status": statuses},
            "runners": {"total": total_runners, "online": online_runners, "online_window_seconds": settings.runner_online_seconds},
            "attempts": {"total": total_attempts},
            "refresh_seconds": settings.ui_refresh_seconds,
        }

    @app.get("/api/ui/dashboard/jobs", dependencies=[Depends(authenticate)])
    def dashboard_jobs(
        job_status: str | None = Query(default=None, alias="status"),
        search: str | None = None,
        limit: int = Query(default=200, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(db),
    ):
        filters = []
        if job_status:
            filters.append(Job.status == job_status)
        if search:
            filters.append(or_(Job.id.ilike(f"%{search}%"), Job.job_sha256.ilike(f"%{search}%")))
        count_statement = select(func.count(Job.id))
        statement = select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit)
        for condition in filters:
            count_statement = count_statement.where(condition)
            statement = statement.where(condition)
        return {
            "total": session.scalar(count_statement) or 0,
            "items": [job_json(job) for job in session.scalars(statement).all()],
        }

    @app.get("/api/ui/dashboard/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def dashboard_job(job_id: str, session: Session = Depends(db)):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job does not exist")
        return job_json(job, include_document=True, include_attempts=True)

    @app.get("/api/ui/dashboard/jobs/{job_id}/series", dependencies=[Depends(authenticate)])
    def dashboard_job_series(job_id: str, session: Session = Depends(db)):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job does not exist")
        return {"job_id": job_id, "series": extract_numeric_series(job.result or {})}

    @app.get("/api/ui/dashboard/runners", dependencies=[Depends(authenticate)])
    def dashboard_runners(session: Session = Depends(db)):
        cutoff = utcnow() - timedelta(seconds=settings.runner_online_seconds)
        runners = session.scalars(select(Runner).order_by(Runner.last_seen_at.desc())).all()
        def online(item: Runner) -> bool:
            seen = item.last_seen_at
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            return seen >= cutoff
        return {"items": [runner_json(item, online=online(item)) for item in runners]}

    # Builder/authoring API. Imported templates are immutable and persisted in HISTRA_TEMPLATE_ROOT.
    @app.get("/api/ui/builder/templates", dependencies=[Depends(authenticate)])
    def builder_templates():
        return {"items": [asset.as_dict() for asset in registry.list()]}

    @app.get("/api/ui/builder/templates/{template_id}", dependencies=[Depends(authenticate)])
    def builder_template_download(template_id: str):
        path = registry.path_for(template_id)
        if not path.exists():
            raise HTTPException(404, "template does not exist")
        return FileResponse(path, media_type="application/xml", filename=path.name)

    @app.post("/api/ui/builder/import", dependencies=[Depends(authenticate)])
    async def builder_import(
        file: UploadFile = File(...),
        job_id: str = Form(...),
        template_id: str = Form(...),
    ):
        data = await file.read(settings.max_hrx_upload_bytes + 1)
        if len(data) > settings.max_hrx_upload_bytes:
            raise HTTPException(413, f"HRX exceeds {settings.max_hrx_upload_bytes} bytes")
        if not data:
            raise HTTPException(422, "uploaded HRX is empty")
        try:
            job = job_from_hrx(
                data,
                job_id=job_id,
                template_id=template_id,
                registry=registry,
                metadata={"source_filename": file.filename or "model.hrx"},
            )
            artifact = compile_job(job, registry)
            preview = inspect_hrx(artifact.hrx_bytes).as_dict(include_geometry=True)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "job": job.model_dump(mode="json"),
            "preview": preview,
            "roundtrip": {
                "exact": artifact.hrx_bytes == data,
                "source_size_bytes": len(data),
                "compiled_size_bytes": len(artifact.hrx_bytes),
                "provenance": artifact.provenance,
            },
        }

    @app.post("/api/ui/builder/preview", dependencies=[Depends(authenticate)])
    def builder_preview(document: dict[str, Any]):
        try:
            return preview_job(document, registry)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/ui/builder/compile", dependencies=[Depends(authenticate)])
    def builder_compile(document: dict[str, Any]):
        try:
            artifact = compile_job(document, registry)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        filename = Path(artifact.output_path).name
        return Response(
            content=artifact.hrx_bytes,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-HRX-SHA256": artifact.hrx_sha256,
                "X-JOB-SHA256": str(artifact.provenance["job_sha256"]),
            },
        )

    @app.post("/api/ui/builder/variants", dependencies=[Depends(authenticate)])
    def builder_variants(payload: VariantBatchRequest):
        try:
            jobs = generate_variants(payload.base_job, payload.variants)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"items": [job.model_dump(mode="json") for job in jobs]}

    @app.post("/api/ui/builder/submit-batch", dependencies=[Depends(authenticate)])
    def builder_submit_batch(payload: SubmitBatchRequest, session: Session = Depends(db)):
        try:
            results = create_jobs_batch(
                session,
                payload.jobs,
                priority=payload.priority,
                max_attempts=payload.max_attempts or settings.default_max_attempts,
            )
        except ConflictError as exc:
            raise translate(exc)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "items": [
                {**job_json(job, include_document=True), "created": created}
                for job, created in results
            ]
        }

    return app


app = create_app()

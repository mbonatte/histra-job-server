from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.router import router
from .config import Settings, get_settings
from .db import build_engine, build_session_factory
from .services.jobs import requeue_expired_attempts
from .storage import Storage

logger = logging.getLogger(__name__)


async def _lease_reaper(app: FastAPI) -> None:
    interval = app.state.settings.lease_reaper_interval_seconds
    while interval > 0:
        await asyncio.sleep(interval)
        try:
            with app.state.session_factory() as session:
                changed = requeue_expired_attempts(session)
                if changed:
                    logger.warning("Requeued %s expired job attempt(s)", changed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lease reaper failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=getattr(logging, resolved_settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        app.state.settings = resolved_settings
        app.state.engine = build_engine(resolved_settings)
        app.state.session_factory = build_session_factory(app.state.engine)
        app.state.storage = Storage(resolved_settings.storage_root)
        reaper_task = None
        if resolved_settings.lease_reaper_interval_seconds > 0:
            reaper_task = asyncio.create_task(_lease_reaper(app))
        yield
        if reaper_task is not None:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass
        app.state.engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description=(
            "Queue, lease, distribute and collect HiStrA numerical-analysis jobs. "
            "Version 0.2.0 includes an integrated analytical dashboard. "
            "Authentication is not implemented yet."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)

    if resolved_settings.dashboard_enabled:
        static_root = Path(__file__).resolve().parent / "static"
        app.mount("/dashboard", StaticFiles(directory=static_root, html=True), name="dashboard")

        @app.get("/", include_in_schema=False)
        def dashboard_redirect():
            return RedirectResponse(url="/dashboard/")

    return app


app = create_app()

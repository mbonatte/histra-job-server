from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def live(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(status="ok", version=settings.app_version)


@router.get("/health/ready", response_model=HealthResponse)
def ready(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        session.execute(text("SELECT 1"))
    return HealthResponse(status="ready", version=settings.app_version)

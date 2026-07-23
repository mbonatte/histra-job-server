from __future__ import annotations

import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..services.dashboard import (
    dashboard_catalog,
    dashboard_summary,
    descriptive_statistics,
    grouped_statistics,
    job_detail,
    job_row,
    load_jobs,
    metric_observations,
    worker_rows,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(request: Request, session: Session = Depends(get_session)):
    return dashboard_summary(
        session,
        online_seconds=request.app.state.settings.dashboard_worker_online_seconds,
    )


@router.get("/catalog")
def catalog(session: Session = Depends(get_session)):
    return dashboard_catalog(session)


@router.get("/jobs")
def jobs(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    scenario: str | None = None,
    worker_id: str | None = None,
    search: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    sort: Annotated[
        str,
        Query(pattern="^(created_at|completed_at|duration_seconds|status|scenario_id|id)$"),
    ] = "created_at",
    direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
):
    loaded = load_jobs(
        session,
        status=status_filter,
        scenario=scenario,
        worker_id=worker_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    rows = [job_row(job) for job in loaded]

    def sort_key(row: dict[str, Any]):
        value = row.get(sort)
        if sort in {"status", "scenario_id", "id"}:
            return str(value or "").lower()
        if sort in {"created_at", "completed_at"}:
            return value.timestamp() if value is not None else float("-inf")
        return float(value) if isinstance(value, (int, float)) else float("-inf")

    rows.sort(key=sort_key, reverse=direction == "desc")
    total = len(rows)
    return {
        "items": rows[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}")
def job(job_id: str, attempt_id: str | None = None, session: Session = Depends(get_session)):
    detail = job_detail(session, job_id, attempt_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return detail


@router.get("/workers")
def workers(request: Request, session: Session = Depends(get_session)):
    return {
        "items": worker_rows(
            session,
            online_seconds=request.app.state.settings.dashboard_worker_online_seconds,
        )
    }


def _statistics_payload(
    session: Session,
    *,
    metric: str,
    analysis: str | None,
    component: str | None,
    model_point_id: int | None,
    group_by: str | None,
    status_filter: str | None,
    scenario: str | None,
    worker_id: str | None,
    search: str | None,
    created_from: str | None,
    created_to: str | None,
) -> dict[str, Any]:
    loaded = load_jobs(
        session,
        status=status_filter or "completed",
        scenario=scenario,
        worker_id=worker_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    observations = metric_observations(
        loaded,
        metric=metric,
        analysis=analysis,
        component=component,
        model_point_id=model_point_id,
    )
    return {
        "metric": metric,
        "analysis": analysis,
        "component": component,
        "model_point_id": model_point_id,
        "summary": descriptive_statistics(
            row["value"] for row in observations if isinstance(row.get("value"), (int, float))
        ),
        "groups": grouped_statistics(observations, group_by),
        "observations": observations,
    }


@router.get("/statistics")
def statistics_endpoint(
    metric: str = "duration_seconds",
    analysis: str | None = None,
    component: str | None = None,
    model_point_id: int | None = None,
    group_by: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    scenario: str | None = None,
    worker_id: str | None = None,
    search: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    session: Session = Depends(get_session),
):
    allowed_metrics = {
        "duration_seconds",
        "reaction_final",
        "reaction_peak_abs",
        "reaction_minimum",
        "reaction_maximum",
        "displacement_final",
        "displacement_peak_abs",
        "displacement_minimum",
        "displacement_maximum",
    }
    if metric not in allowed_metrics:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported metric: {metric}",
        )
    return _statistics_payload(
        session,
        metric=metric,
        analysis=analysis,
        component=component,
        model_point_id=model_point_id,
        group_by=group_by,
        status_filter=status_filter,
        scenario=scenario,
        worker_id=worker_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )


@router.get("/statistics.csv")
def statistics_csv(
    metric: str = "duration_seconds",
    analysis: str | None = None,
    component: str | None = None,
    model_point_id: int | None = None,
    group_by: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    scenario: str | None = None,
    worker_id: str | None = None,
    search: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    session: Session = Depends(get_session),
):
    payload = _statistics_payload(
        session,
        metric=metric,
        analysis=analysis,
        component=component,
        model_point_id=model_point_id,
        group_by=group_by,
        status_filter=status_filter,
        scenario=scenario,
        worker_id=worker_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    buffer = io.StringIO()
    fields = [
        "job_id",
        "attempt_id",
        "scenario_id",
        "worker_id",
        "worker_name",
        "analysis",
        "component",
        "model_point_id",
        "step",
        "value",
        "created_at",
        "completed_at",
        "metadata",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in payload["observations"]:
        serialised = dict(row)
        serialised["metadata"] = "; ".join(
            f"{key}={value}" for key, value in sorted((row.get("metadata") or {}).items())
        )
        writer.writerow(serialised)
    headers = {"Content-Disposition": f'attachment; filename="histra-{metric}.csv"'}
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers=headers)

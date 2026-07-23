from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Job, JobAttempt, Worker


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_date_boundary(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return ensure_utc(parsed)
    except ValueError:
        parsed_date = date.fromisoformat(value)
        boundary = time.max if end else time.min
        return datetime.combine(parsed_date, boundary, tzinfo=UTC)


def latest_attempt(job: Job) -> JobAttempt | None:
    if not job.attempts:
        return None
    if job.current_attempt_id:
        for attempt in job.attempts:
            if attempt.id == job.current_attempt_id:
                return attempt
    return max(job.attempts, key=lambda item: item.created_at)


def attempt_duration_seconds(attempt: JobAttempt | None) -> float | None:
    if attempt is None:
        return None
    run = attempt.run_json or {}
    value = run.get("duration_seconds")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    started = ensure_utc(attempt.started_at or attempt.created_at)
    finished = ensure_utc(attempt.finished_at)
    if started and finished:
        return max(0.0, (finished - started).total_seconds())
    return None


def flatten_numeric(value: Any, prefix: str = "") -> dict[str, float]:
    result: dict[str, float] = {}
    if isinstance(value, bool):
        return result
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        if prefix:
            result[prefix] = float(value)
        return result
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten_numeric(child, child_prefix))
    return result


def job_numeric_metadata(job: Job, attempt: JobAttempt | None) -> dict[str, float]:
    metadata: dict[str, float] = {}
    definition_metadata = (job.job_definition or {}).get("metadata")
    if isinstance(definition_metadata, dict):
        metadata.update(flatten_numeric(definition_metadata))
    run_metadata = (attempt.run_json or {}).get("metadata") if attempt else None
    if isinstance(run_metadata, dict):
        metadata.update(flatten_numeric(run_metadata))
    return metadata


def apply_job_filters(
    query: Select,
    *,
    status: str | None = None,
    scenario: str | None = None,
    worker_id: str | None = None,
    search: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> Select:
    if status:
        values = [item.strip() for item in status.split(",") if item.strip()]
        if values:
            query = query.where(Job.status.in_(values))
    if scenario:
        query = query.where(Job.scenario_id == scenario)
    if worker_id:
        query = query.where(
            Job.id.in_(select(JobAttempt.job_id).where(JobAttempt.worker_id == worker_id))
        )
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Job.id.ilike(pattern),
                Job.scenario_id.ilike(pattern),
                Job.model_filename.ilike(pattern),
            )
        )
    start = parse_date_boundary(created_from)
    end = parse_date_boundary(created_to, end=True)
    if start:
        query = query.where(Job.created_at >= start)
    if end:
        query = query.where(Job.created_at <= end)
    return query


def load_jobs(
    session: Session,
    *,
    status: str | None = None,
    scenario: str | None = None,
    worker_id: str | None = None,
    search: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> list[Job]:
    query = select(Job).options(
        selectinload(Job.attempts).selectinload(JobAttempt.worker),
    )
    query = apply_job_filters(
        query,
        status=status,
        scenario=scenario,
        worker_id=worker_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    return list(session.scalars(query.order_by(Job.created_at.desc())).unique().all())


def job_row(job: Job) -> dict[str, Any]:
    attempt = latest_attempt(job)
    worker = attempt.worker if attempt else None
    analyses = (attempt.results_json or {}).get("analyses") if attempt else None
    analysis_count = (
        len(analyses)
        if isinstance(analyses, dict)
        else len((job.job_definition or {}).get("analyses") or [])
    )
    return {
        "id": job.id,
        "scenario_id": job.scenario_id,
        "status": job.status,
        "priority": job.priority,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "current_attempt_id": job.current_attempt_id,
        "worker_id": worker.id if worker else None,
        "worker_name": worker.name if worker else None,
        "analysis_count": analysis_count,
        "duration_seconds": attempt_duration_seconds(attempt),
        "model_filename": job.model_filename,
        "model_size_bytes": job.model_size_bytes,
        "error_message": job.error_message,
        "created_at": ensure_utc(job.created_at),
        "updated_at": ensure_utc(job.updated_at),
        "completed_at": ensure_utc(job.completed_at),
    }


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def descriptive_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std_dev": None,
            "coefficient_of_variation": None,
            "minimum": None,
            "q1": None,
            "q3": None,
            "maximum": None,
            "p05": None,
            "p95": None,
        }
    mean = statistics.fmean(clean)
    std_dev = statistics.stdev(clean) if len(clean) > 1 else 0.0
    return {
        "count": len(clean),
        "mean": mean,
        "median": statistics.median(clean),
        "std_dev": std_dev,
        "coefficient_of_variation": (std_dev / abs(mean)) if mean else None,
        "minimum": min(clean),
        "q1": quantile(clean, 0.25),
        "q3": quantile(clean, 0.75),
        "maximum": max(clean),
        "p05": quantile(clean, 0.05),
        "p95": quantile(clean, 0.95),
    }


def _step_value(row: dict[str, Any]) -> float:
    value = row.get("Step")
    return float(value) if isinstance(value, (int, float)) else -math.inf


def _metric_observations_for_job(
    job: Job,
    attempt: JobAttempt,
    *,
    metric: str,
    analysis_filter: str | None,
    component: str | None,
    model_point_id: int | None,
) -> list[dict[str, Any]]:
    worker = attempt.worker
    base = {
        "job_id": job.id,
        "attempt_id": attempt.id,
        "scenario_id": job.scenario_id,
        "worker_id": worker.id if worker else None,
        "worker_name": worker.name if worker else None,
        "created_at": ensure_utc(job.created_at),
        "completed_at": ensure_utc(job.completed_at),
        "metadata": job_numeric_metadata(job, attempt),
    }
    if metric == "duration_seconds":
        duration = attempt_duration_seconds(attempt)
        if duration is None:
            return []
        return [
            {
                **base,
                "analysis": None,
                "component": None,
                "model_point_id": None,
                "value": duration,
            }
        ]

    results = attempt.results_json or {}
    analyses = results.get("analyses")
    if not isinstance(analyses, dict):
        return []
    observations: list[dict[str, Any]] = []
    for analysis_name, analysis_data in analyses.items():
        if analysis_filter and analysis_name != analysis_filter:
            continue
        outputs = analysis_data.get("outputs") if isinstance(analysis_data, dict) else None
        if not isinstance(outputs, dict):
            continue

        if metric.startswith("reaction_"):
            selected_component = component or "R1"
            rows = outputs.get("reactions")
            if not isinstance(rows, list):
                continue
            valid = [
                row
                for row in rows
                if isinstance(row, dict) and isinstance(row.get(selected_component), (int, float))
            ]
            if not valid:
                continue
            if metric == "reaction_final":
                chosen = max(valid, key=_step_value)
                value = float(chosen[selected_component])
            elif metric == "reaction_peak_abs":
                chosen = max(valid, key=lambda row: abs(float(row[selected_component])))
                value = abs(float(chosen[selected_component]))
            elif metric == "reaction_minimum":
                chosen = min(valid, key=lambda row: float(row[selected_component]))
                value = float(chosen[selected_component])
            elif metric == "reaction_maximum":
                chosen = max(valid, key=lambda row: float(row[selected_component]))
                value = float(chosen[selected_component])
            else:
                continue
            observations.append(
                {
                    **base,
                    "analysis": analysis_name,
                    "component": selected_component,
                    "model_point_id": None,
                    "step": chosen.get("Step"),
                    "value": value,
                }
            )

        if metric.startswith("displacement_"):
            selected_component = component or "Uy"
            rows = outputs.get("displacements")
            if not isinstance(rows, list):
                continue
            by_point: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                point = row.get("IdElement")
                value = row.get(selected_component)
                if not isinstance(point, int) or not isinstance(value, (int, float)):
                    continue
                if model_point_id is not None and point != model_point_id:
                    continue
                by_point[point].append(row)
            for point, point_rows in by_point.items():
                if metric == "displacement_final":
                    chosen = max(point_rows, key=_step_value)
                    value = float(chosen[selected_component])
                elif metric == "displacement_peak_abs":
                    chosen = max(point_rows, key=lambda row: abs(float(row[selected_component])))
                    value = abs(float(chosen[selected_component]))
                elif metric == "displacement_minimum":
                    chosen = min(point_rows, key=lambda row: float(row[selected_component]))
                    value = float(chosen[selected_component])
                elif metric == "displacement_maximum":
                    chosen = max(point_rows, key=lambda row: float(row[selected_component]))
                    value = float(chosen[selected_component])
                else:
                    continue
                observations.append(
                    {
                        **base,
                        "analysis": analysis_name,
                        "component": selected_component,
                        "model_point_id": point,
                        "parent_key": chosen.get("ParentKey"),
                        "step": chosen.get("Step"),
                        "value": value,
                    }
                )
    return observations


def metric_observations(
    jobs: Iterable[Job],
    *,
    metric: str,
    analysis: str | None = None,
    component: str | None = None,
    model_point_id: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        attempt = latest_attempt(job)
        if attempt is None or attempt.status != "completed":
            continue
        rows.extend(
            _metric_observations_for_job(
                job,
                attempt,
                metric=metric,
                analysis_filter=analysis,
                component=component,
                model_point_id=model_point_id,
            )
        )
    return rows


def grouped_statistics(
    observations: list[dict[str, Any]],
    group_by: str | None,
) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in observations:
        value = row.get("value")
        if not isinstance(value, (int, float)):
            continue
        if group_by == "scenario":
            key = str(row.get("scenario_id") or "Unspecified")
        elif group_by == "worker":
            key = str(row.get("worker_name") or "Unassigned")
        elif group_by == "analysis":
            key = str(row.get("analysis") or "Run")
        elif group_by == "model_point":
            key = str(row.get("model_point_id") or "N/A")
        elif group_by and group_by.startswith("metadata:"):
            path = group_by.split(":", 1)[1]
            metadata = row.get("metadata") or {}
            raw = metadata.get(path)
            key = "Unspecified" if raw is None else str(raw)
        else:
            key = "All observations"
        groups[key].append(float(value))
    return [
        {"group": key, **descriptive_statistics(values)}
        for key, values in sorted(groups.items(), key=lambda item: item[0])
    ]


def dashboard_summary(session: Session, *, online_seconds: int = 300) -> dict[str, Any]:
    jobs = load_jobs(session)
    workers = list(session.scalars(select(Worker).order_by(Worker.name)).all())
    counts = Counter(job.status for job in jobs)
    terminal = counts.get("completed", 0) + counts.get("failed", 0) + counts.get("cancelled", 0)
    durations = [
        duration
        for job in jobs
        if job.status == "completed"
        and (duration := attempt_duration_seconds(latest_attempt(job))) is not None
    ]
    now = datetime.now(UTC)
    online_cutoff = now - timedelta(seconds=online_seconds)
    active_workers = sum(
        1
        for worker in workers
        if worker.enabled
        and (ensure_utc(worker.last_seen_at) or datetime.min.replace(tzinfo=UTC)) >= online_cutoff
    )
    days = [(now.date() - timedelta(days=offset)) for offset in range(13, -1, -1)]
    throughput_counts = Counter(
        ensure_utc(job.completed_at).date()
        for job in jobs
        if job.completed_at is not None and ensure_utc(job.completed_at).date() in days
    )
    throughput = [
        {"date": day.isoformat(), "completed": throughput_counts.get(day, 0)} for day in days
    ]
    recent = [job_row(job) for job in jobs[:10]]
    duration_stats = descriptive_statistics(durations)
    return {
        "generated_at": now,
        "jobs": {
            "total": len(jobs),
            "by_status": dict(sorted(counts.items())),
            "completion_rate": (counts.get("completed", 0) / terminal) if terminal else None,
            "active": sum(
                counts.get(status, 0)
                for status in (
                    "leased",
                    "downloading",
                    "running",
                    "extracting",
                    "uploading",
                    "validating",
                )
            ),
        },
        "duration_seconds": duration_stats,
        "workers": {
            "total": len(workers),
            "enabled": sum(1 for worker in workers if worker.enabled),
            "online": active_workers,
            "capacity": sum(worker.max_parallel_jobs for worker in workers if worker.enabled),
        },
        "throughput": throughput,
        "recent_jobs": recent,
    }


def worker_rows(session: Session, *, online_seconds: int = 300) -> list[dict[str, Any]]:
    workers = list(
        session.scalars(
            select(Worker)
            .options(selectinload(Worker.attempts).selectinload(JobAttempt.job))
            .order_by(Worker.name)
        )
        .unique()
        .all()
    )
    cutoff = datetime.now(UTC) - timedelta(seconds=online_seconds)
    rows: list[dict[str, Any]] = []
    for worker in workers:
        statuses = Counter(attempt.status for attempt in worker.attempts)
        completed_durations = [
            duration
            for attempt in worker.attempts
            if attempt.status == "completed"
            and (duration := attempt_duration_seconds(attempt)) is not None
        ]
        terminal = (
            statuses.get("completed", 0) + statuses.get("failed", 0) + statuses.get("expired", 0)
        )
        active = sum(
            statuses.get(status, 0)
            for status in (
                "leased",
                "downloading",
                "running",
                "extracting",
                "uploading",
                "validating",
            )
        )
        rows.append(
            {
                "id": worker.id,
                "name": worker.name,
                "enabled": worker.enabled,
                "online": (ensure_utc(worker.last_seen_at) or datetime.min.replace(tzinfo=UTC))
                >= cutoff,
                "max_parallel_jobs": worker.max_parallel_jobs,
                "active_attempts": active,
                "completed_attempts": statuses.get("completed", 0),
                "failed_attempts": statuses.get("failed", 0) + statuses.get("expired", 0),
                "success_rate": statuses.get("completed", 0) / terminal if terminal else None,
                "average_duration_seconds": (
                    statistics.fmean(completed_durations) if completed_durations else None
                ),
                "worker_version": worker.worker_version,
                "solver_version": worker.solver_version,
                "metadata": worker.metadata_json,
                "created_at": ensure_utc(worker.created_at),
                "last_seen_at": ensure_utc(worker.last_seen_at),
            }
        )
    return rows


def dashboard_catalog(session: Session) -> dict[str, Any]:
    jobs = load_jobs(session)
    statuses = sorted({job.status for job in jobs})
    scenarios = sorted({job.scenario_id for job in jobs if job.scenario_id})
    analyses: set[str] = set()
    model_points: set[int] = set()
    reaction_components: set[str] = set()
    displacement_components: set[str] = set()
    metadata_paths: set[str] = set()
    for job in jobs:
        attempt = latest_attempt(job)
        metadata_paths.update(job_numeric_metadata(job, attempt))
        if not attempt or not isinstance(attempt.results_json, dict):
            continue
        result_analyses = attempt.results_json.get("analyses")
        if not isinstance(result_analyses, dict):
            continue
        for analysis_name, analysis_data in result_analyses.items():
            analyses.add(str(analysis_name))
            outputs = analysis_data.get("outputs") if isinstance(analysis_data, dict) else None
            if not isinstance(outputs, dict):
                continue
            for row in outputs.get("reactions") or []:
                if isinstance(row, dict):
                    reaction_components.update(
                        key
                        for key in row
                        if key != "Step" and isinstance(row.get(key), (int, float))
                    )
            for row in outputs.get("displacements") or []:
                if isinstance(row, dict):
                    if isinstance(row.get("IdElement"), int):
                        model_points.add(row["IdElement"])
                    displacement_components.update(
                        key
                        for key in row
                        if key not in {"Step", "IdElement", "ParentKey"}
                        and isinstance(row.get(key), (int, float))
                    )
    workers = list(session.scalars(select(Worker).order_by(Worker.name)).all())
    return {
        "statuses": statuses,
        "scenarios": scenarios,
        "workers": [{"id": worker.id, "name": worker.name} for worker in workers],
        "analyses": sorted(analyses),
        "model_points": sorted(model_points),
        "reaction_components": sorted(reaction_components),
        "displacement_components": sorted(displacement_components),
        "metadata_paths": sorted(metadata_paths),
        "metrics": [
            {"id": "duration_seconds", "label": "Run duration", "family": "run"},
            {"id": "reaction_final", "label": "Final reaction", "family": "reaction"},
            {"id": "reaction_peak_abs", "label": "Peak absolute reaction", "family": "reaction"},
            {"id": "reaction_minimum", "label": "Minimum reaction", "family": "reaction"},
            {"id": "reaction_maximum", "label": "Maximum reaction", "family": "reaction"},
            {"id": "displacement_final", "label": "Final displacement", "family": "displacement"},
            {
                "id": "displacement_peak_abs",
                "label": "Peak absolute displacement",
                "family": "displacement",
            },
            {
                "id": "displacement_minimum",
                "label": "Minimum displacement",
                "family": "displacement",
            },
            {
                "id": "displacement_maximum",
                "label": "Maximum displacement",
                "family": "displacement",
            },
        ],
    }


def job_detail(
    session: Session,
    job_id: str,
    attempt_id: str | None = None,
) -> dict[str, Any] | None:
    job = session.scalar(
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.attempts).selectinload(JobAttempt.worker),
            selectinload(Job.artifacts),
        )
    )
    if job is None:
        return None
    selected = None
    if attempt_id:
        selected = next((attempt for attempt in job.attempts if attempt.id == attempt_id), None)
    if selected is None:
        selected = latest_attempt(job)
    attempts = []
    for attempt in sorted(job.attempts, key=lambda item: item.created_at, reverse=True):
        attempts.append(
            {
                "id": attempt.id,
                "status": attempt.status,
                "worker_id": attempt.worker_id,
                "worker_name": attempt.worker.name if attempt.worker else None,
                "lease_expires_at": ensure_utc(attempt.lease_expires_at),
                "last_heartbeat_at": ensure_utc(attempt.last_heartbeat_at),
                "progress": attempt.progress_json,
                "exit_code": attempt.exit_code,
                "failure_reason": attempt.failure_reason,
                "duration_seconds": attempt_duration_seconds(attempt),
                "created_at": ensure_utc(attempt.created_at),
                "started_at": ensure_utc(attempt.started_at),
                "finished_at": ensure_utc(attempt.finished_at),
            }
        )
    artifacts = [
        {
            "id": artifact.id,
            "attempt_id": artifact.attempt_id,
            "kind": artifact.kind,
            "filename": artifact.filename,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "created_at": ensure_utc(artifact.created_at),
            "download_url": f"/api/v1/artifacts/{artifact.id}",
        }
        for artifact in sorted(job.artifacts, key=lambda item: item.created_at, reverse=True)
    ]
    return {
        "job": {
            **job_row(job),
            "job_definition": job.job_definition,
            "model_sha256": job.model_sha256,
        },
        "attempts": attempts,
        "selected_attempt": (
            {
                "id": selected.id,
                "status": selected.status,
                "worker_id": selected.worker_id,
                "worker_name": selected.worker.name if selected.worker else None,
                "duration_seconds": attempt_duration_seconds(selected),
                "results": selected.results_json,
                "run": selected.run_json,
                "validation": selected.validation_json,
                "failure_reason": selected.failure_reason,
            }
            if selected
            else None
        ),
        "artifacts": artifacts,
    }

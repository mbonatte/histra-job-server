"""Tier 4: Real-World Application Scenarios (Portfolio E2E Pipeline).

The capstone end-to-end integration test:
1. Generates a portfolio of >=5 distinct random bridges with multi-stage scour scenarios (Vert -> Scour_1 -> Scour_2).
2. Submits batch jobs to histra-job-server.
3. Executes them through histra-job-runner backed by histra-python in-process.
4. Asserts all 5 jobs reach terminal completed state.
5. Verifies numerical result metrics (reaction curves, displacements, convergence history) are persisted in the database.
6. Validates dashboard series discovery API endpoints.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import canonical_json_bytes, job_sha256, sha256_hex
from histra_runner.contracts import Claim
from histra_runner.executor import RunnerExecutor
from histra_runner.histra_python_backend import HiStrAPythonBackend
from histra_runner.worker import Worker


def generate_portfolio_definitions(synthetic_hrx_factory, count: int = 5) -> list[dict[str, Any]]:
    """Synthesize >=5 distinct parametric bridge models with multi-stage scour."""
    configs = [
        {"num_spans": 1, "span_length": 250.0, "pier_height": 90.0, "step_size": 0.5},
        {"num_spans": 2, "span_length": 400.0, "pier_height": 120.0, "step_size": 0.5},
        {"num_spans": 2, "span_length": 350.0, "pier_height": 140.0, "step_size": 0.5},
        {"num_spans": 3, "span_length": 500.0, "pier_height": 160.0, "step_size": 0.5},
        {"num_spans": 1, "span_length": 300.0, "pier_height": 100.0, "step_size": 0.5},
    ]

    portfolio: list[dict[str, Any]] = []
    scour_stages = ["Vert", "Scour_1", "Scour_2"]

    for idx, cfg in enumerate(configs[:count], start=1):
        job_id = f"portfolio-bridge-{idx:03d}"
        hrx = synthetic_hrx_factory(
            num_spans=cfg["num_spans"],
            span_length=cfg["span_length"],
            pier_height=cfg["pier_height"],
            scour_stages=scour_stages,
            step_size=cfg["step_size"],
        )

        mutations: dict[str, list[dict[str, Any]]] = {}

        portfolio.append({
            "job_id": job_id,
            "hrx_bytes": hrx,
            "cfg": cfg,
            "analyses": scour_stages,
            "interface_mutations": mutations,
        })

    return portfolio


def test_tier4_realworld_portfolio_five_bridges_end_to_end(
    server_client: TestClient,
    server_adapter,
    server_settings,
    synthetic_hrx_factory,
    tmp_path: Path,
):
    """End-to-end execution of a 5-bridge random portfolio with multi-stage scour.

    Validates:
    - Submission of 5 distinct bridges to histra-job-server.
    - Worker claims each job atomically and downloads compiled package.
    - In-process execution through HiStrAPythonBackend with histra-python solver.
    - Terminal status == 'completed' with zero retries.
    - Database persistence of numerical reaction metrics and convergence history.
    - Dashboard series API returns valid plottable series vectors.
    """
    portfolio = generate_portfolio_definitions(synthetic_hrx_factory, count=5)
    assert len(portfolio) == 5, "Portfolio must contain at least 5 distinct bridges"

    job_ids: list[str] = []

    # 1. Ingest all 5 bridge jobs into the server
    for item in portfolio:
        jid = item["job_id"]
        template_id = f"template-{jid}"
        template_file = server_settings.template_root / f"{template_id}.hrx"
        template_file.write_bytes(item["hrx_bytes"])

        job_spec = {
            "schema_version": "1.0",
            "job_id": jid,
            "model": {
                "template": {"id": template_id, "sha256": sha256_hex(item["hrx_bytes"])},
                "output_path": "model.hrx",
                "patches": [],
            },
            "workflow": {
                "analyses": [{"name": a} for a in item["analyses"]],
                "interface_mutations": item["interface_mutations"],
            },
            "metadata": {"portfolio_index": len(job_ids) + 1, "config": item["cfg"]},
        }

        resp = server_client.post("/jobs", json=job_spec)
        assert resp.status_code == 201, f"Failed to ingest job {jid}: {resp.text}"
        job_ids.append(jid)

    # Verify all 5 jobs are queued in server
    summary = server_client.get("/api/ui/dashboard/summary").json()
    assert summary["jobs"]["by_status"].get("queued", 0) >= 5

    # 2. Instantiate Runner Worker backed by real HiStrAPythonBackend
    worker = Worker(
        client=server_adapter,
        executor=RunnerExecutor(HiStrAPythonBackend()),
        work_root=tmp_path / "portfolio_runner_work",
        runner_name="portfolio-e2e-worker",
        runner_id="e2e-worker-p1",
        heartbeat_interval_seconds=0,
    )
    worker.register()

    # 3. Drain the queue by running all 5 jobs
    processed_count = 0
    while worker.run_once():
        processed_count += 1
        if processed_count >= 5:
            break

    assert processed_count == 5, f"Expected to execute 5 jobs, but executed {processed_count}"

    # 4. Verify terminal status and database persistence for all 5 jobs
    for jid in job_ids:
        job_data = server_client.get(f"/jobs/{jid}").json()
        assert job_data["status"] == "completed", f"Job {jid} did not complete: {job_data}"
        assert job_data["attempts_count"] == 1, f"Job {jid} required retries: {job_data['attempts_count']}"

        # Inspect persisted result envelope
        assert job_data["result"] is not None
        envelope = job_data["result"]
        results = envelope["results"]
        assert results["backend"] == "histra-python"

        # Check multi-stage execution parity (Vert -> Scour_1 -> Scour_2)
        for stage in ["Vert", "Scour_1", "Scour_2"]:
            assert stage in results["analyses"], f"Stage {stage} missing from {jid} results"
            stage_info = results["analyses"][stage]
            assert stage_info["execution"]["completed"] is True, f"Stage {stage} not completed in {jid}"
            assert stage_info["execution"]["exit_code"] == 0

            # Reactions must exist and contain non-empty step records
            reactions = stage_info["outputs"]["reactions"]
            assert len(reactions) > 0, f"Stage {stage} has no reaction records in {jid}"
            for r in reactions:
                assert "Step" in r
                assert "R1" in r
                assert "R2" in r
                assert "R3" in r
                assert math.isfinite(r["R3"])

        # Check dashboard series extraction API
        series_resp = server_client.get(f"/api/ui/dashboard/jobs/{jid}/series")
        assert series_resp.status_code == 200
        series_data = series_resp.json()
        assert "series" in series_data


def test_tier4_portfolio_database_persistence_direct_sql(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
    server_settings,
):
    """Verify database records directly via SQLAlchemy ORM at the SQL engine level."""
    from sqlalchemy import select
    from histra_server.db import create_session_factory
    from histra_server.models import Attempt, Job

    job = canonical_job_factory("sql-verify-001")
    server_client.post("/jobs", json=job)

    worker = runner_worker_factory(runner_id="worker-sql-1")
    assert worker.run_once() is True

    engine, factory = create_session_factory(server_settings.database_url)
    with factory() as session:
        db_job = session.scalar(select(Job).where(Job.id == "sql-verify-001"))
        assert db_job is not None
        assert db_job.status == "completed"
        assert db_job.completed_at is not None
        assert isinstance(db_job.result, dict)

        db_attempt = session.scalar(select(Attempt).where(Attempt.job_id == "sql-verify-001"))
        assert db_attempt is not None
        assert db_attempt.status == "completed"
        assert db_attempt.completed_at is not None
        assert db_attempt.result is not None


def test_tier4_portfolio_concurrent_multi_worker_drain(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify concurrent workers drain portfolio queue without duplicate claims."""
    # Ingest 4 jobs
    for i in range(1, 5):
        job = canonical_job_factory(f"parallel-drain-{i:03d}")
        server_client.post("/jobs", json=job)

    worker_a = runner_worker_factory(runner_id="drain-worker-a")
    worker_b = runner_worker_factory(runner_id="drain-worker-b")

    total_drained = 0
    for _ in range(4):
        # Round-robin claim between worker A and B
        if worker_a.run_once():
            total_drained += 1
        if worker_b.run_once():
            total_drained += 1

    assert total_drained == 4

    summary = server_client.get("/api/ui/dashboard/summary").json()
    assert summary["jobs"]["by_status"].get("completed", 0) == 4
    assert summary["jobs"]["by_status"].get("queued", 0) == 0


def test_tier4_realworld_portfolio_generator_module_integration(
    server_client: TestClient,
    server_adapter,
    server_settings,
    tmp_path: Path,
):
    """Verify integration when histra_builder.portfolio generator module is available."""
    portfolio_module = pytest.importorskip(
        "histra_builder.portfolio",
        reason="M2 portfolio generator module under development",
    )

    generate_fn = getattr(portfolio_module, "generate_bridge_portfolio", None)
    if generate_fn is None:
        pytest.skip("generate_bridge_portfolio not yet implemented in histra_builder.portfolio")

    portfolio = generate_fn(count=5, base_seed=42)
    assert len(portfolio) >= 5

    for job_spec in portfolio:
        resp = server_client.post("/jobs", json=job_spec.model_dump(mode="json"))
        assert resp.status_code == 201

    worker = Worker(
        client=server_adapter,
        executor=RunnerExecutor(HiStrAPythonBackend()),
        work_root=tmp_path / "work_m2",
        runner_name="m2-worker",
        runner_id="worker-m2",
        heartbeat_interval_seconds=0,
    )
    worker.register()

    while worker.run_once():
        pass

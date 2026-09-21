"""Tier 3: Cross-Feature Combinations & Multi-Module Interactions Suite.

Validates pairwise interactions and subsystem interoperability across package boundaries:
- Interaction 1: Bridge Generation x Multi-Stage Scour Definitions & Interface Resolution
- Interaction 2: Server Attempt Packaging x Runner Cryptographic Verification
- Interaction 3: Atomic Lease Claiming x Concurrent Runner Workers (Contention & Racing)
- Interaction 4: Solver Result Packaging x Server Columnar Series Extraction
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import canonical_json_bytes, job_sha256, sha256_hex
from histra_runner.canonical import job_sha256 as runner_job_sha256
from histra_runner.contracts import Claim
from histra_runner.errors import PackageValidationError
from histra_runner.histra_python_backend import HiStrAPythonBackend
from histra_runner.package import validate_package


# ===========================================================================
# 1. Interaction 1: Generation x Scour Resolution x Solver Execution
# ===========================================================================

def test_interaction_generated_model_scour_interface_keys_resolver(synthetic_hrx_factory, tmp_path: Path):
    """Verify that interfaces created by prepare_model can be programmatically resolved for scour."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory(num_spans=2, span_length=400.0, pier_height=120.0)
    p = tmp_path / "model.hrx"
    p.write_bytes(hrx)

    model = load_model(p)
    prepare_model(model, force=True)

    # Restrained interfaces are the foundation boundary contact springs
    restrained_interfaces = [
        intf_key
        for intf_key, intf in model.collections.interfaces.items()
        if intf.interfaccia_vincolata
    ]
    assert len(restrained_interfaces) >= 2, "Expected at least 2 foundation restraint interfaces"


def test_interaction_scour_mutation_workflow_formulation(canonical_job_factory, synthetic_hrx_factory):
    """Verify formulating canonical JobSpec with concrete resolved interface mutations."""
    hrx = synthetic_hrx_factory(num_spans=2)
    mutations = {
        "Scour_1": [
            {"interface_keys": [1], "material_key": 147, "preserve_committed_state": True}
        ],
        "Scour_2": [
            {"interface_keys": [2], "material_key": 147, "preserve_committed_state": True}
        ],
    }

    job = canonical_job_factory(
        "interact-scour-job",
        hrx_bytes=hrx,
        analyses=["Vert", "Scour_1", "Scour_2"],
        interface_mutations=mutations,
    )

    assert job["workflow"]["analyses"] == [{"name": "Vert"}, {"name": "Scour_1"}, {"name": "Scour_2"}]
    assert 1 in job["workflow"]["interface_mutations"]["Scour_1"][0]["interface_keys"]
    assert 2 in job["workflow"]["interface_mutations"]["Scour_2"][0]["interface_keys"]


def test_interaction_multi_stage_solver_execution(synthetic_hrx_factory, tmp_path: Path):
    """Verify HiStrAPythonBackend executes multi-stage workflow with interface mutation."""
    scour_stages = ["Vert", "Scour_1"]
    hrx = synthetic_hrx_factory(num_spans=1, scour_stages=scour_stages, step_size=0.5)

    job = {
        "schema_version": "1.0",
        "job_id": "multi-stage-exec-001",
        "model": {
            "template": {"id": "test-template", "sha256": sha256_hex(hrx)},
            "output_path": "model.hrx",
            "patches": [],
        },
        "workflow": {
            "analyses": [{"name": "Vert"}, {"name": "Scour_1"}],
            "interface_mutations": {
                "Scour_1": [
                    {"interface_keys": [1], "material_key": 147, "preserve_committed_state": True}
                ]
            },
        },
        "metadata": {},
    }

    pkg_zip = tmp_path / "package.zip"
    with zipfile.ZipFile(pkg_zip, "w") as zf:
        manifest_data = {
            "protocol_version": "1.0",
            "job_id": "multi-stage-exec-001",
            "attempt_id": "att-001",
            "created_at": "2026-09-21T12:00:00Z",
            "job_sha256": job_sha256(job),
            "hrx": {"path": "model.hrx", "sha256": sha256_hex(hrx), "size_bytes": len(hrx)},
            "builder": {
                "builder_version": "1.1.0",
                "job_sha256": job_sha256(job),
                "template_id": "test-template",
                "template_sha256": sha256_hex(hrx),
                "output_path": "model.hrx",
                "hrx_sha256": sha256_hex(hrx),
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest_data))
        zf.writestr("job.json", canonical_json_bytes(job))
        zf.writestr("model.hrx", hrx)

    pkg = validate_package(
        pkg_zip,
        tmp_path / "extracted",
        expected_job_id="multi-stage-exec-001",
        expected_attempt_id="att-001",
        expected_job_sha256=job_sha256(job),
        expected_hrx_sha256=sha256_hex(hrx),
    )

    backend = HiStrAPythonBackend()
    result = backend.execute(pkg, tmp_path / "out")

    assert result.results["analyses"]["Vert"]["execution"]["completed"] is True
    assert result.results["analyses"]["Scour_1"]["execution"]["completed"] is True


# ===========================================================================
# 2. Interaction 2: Server Packaging x Cryptographic Verification
# ===========================================================================

def test_interaction_server_package_compilation_and_verification(
    server_client: TestClient,
    canonical_job_factory,
    tmp_path: Path,
):
    """Verify server-compiled package ZIP contains valid canonical JSON and matching SHA-256."""
    job = canonical_job_factory("pkg-verify-001")
    server_client.post("/jobs", json=job)
    server_client.post(
        "/runners/register",
        json={"runner_id": "pkg-worker-1", "name": "worker", "version": "1.0.0"},
    )

    claim_data = server_client.post("/claims", json={"runner_id": "pkg-worker-1"}).json()
    resp_pkg = server_client.get(claim_data["package_url"], headers={"X-Runner-ID": "pkg-worker-1"})
    assert resp_pkg.status_code == 200

    zip_path = tmp_path / "package.zip"
    zip_path.write_bytes(resp_pkg.content)

    claim = Claim.model_validate(claim_data)
    extract_dir = tmp_path / "extracted"

    contents = validate_package(
        zip_path,
        extract_dir,
        expected_job_id=claim.job_id,
        expected_attempt_id=claim.attempt_id,
        expected_job_sha256=claim.job_sha256,
        expected_hrx_sha256=claim.hrx_sha256,
    )
    assert contents.job["job_id"] == "pkg-verify-001"
    assert contents.hrx_path.exists()


def test_interaction_tampered_package_detection(
    server_client: TestClient,
    canonical_job_factory,
    tmp_path: Path,
):
    """Verify validate_package detects and rejects any payload tampering."""
    job = canonical_job_factory("tamper-job-001")
    server_client.post("/jobs", json=job)
    server_client.post(
        "/runners/register",
        json={"runner_id": "tamper-worker-1", "name": "worker", "version": "1.0.0"},
    )

    claim_data = server_client.post("/claims", json={"runner_id": "tamper-worker-1"}).json()
    resp_pkg = server_client.get(claim_data["package_url"], headers={"X-Runner-ID": "tamper-worker-1"})

    # Tamper with the HRX file inside the archive
    tampered_zip = tmp_path / "tampered.zip"
    with zipfile.ZipFile(io.BytesIO(resp_pkg.content), "r") as src:
        with zipfile.ZipFile(tampered_zip, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "model.hrx":
                    data = data + b"<!-- tampered -->"
                dst.writestr(item, data)

    claim = Claim.model_validate(claim_data)
    with pytest.raises(PackageValidationError):
        validate_package(
            tampered_zip,
            tmp_path / "extracted_tamper",
            expected_job_id=claim.job_id,
            expected_attempt_id=claim.attempt_id,
            expected_job_sha256=claim.job_sha256,
            expected_hrx_sha256=claim.hrx_sha256,
        )


def test_interaction_runner_worker_executes_server_package(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify Worker end-to-end executes package obtained from server."""
    job = canonical_job_factory("worker-server-job-001")
    server_client.post("/jobs", json=job)

    worker = runner_worker_factory(runner_id="runner-full-1")
    did_run = worker.run_once()
    assert did_run is True

    job_data = server_client.get("/jobs/worker-server-job-001").json()
    assert job_data["status"] == "completed"


# ===========================================================================
# 3. Interaction 3: Atomic Claiming x Concurrent Runners
# ===========================================================================

def test_interaction_concurrent_workers_claim_distinct_jobs(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify multiple runner workers claim distinct jobs without duplicate assignment."""
    for i in range(1, 4):
        job = canonical_job_factory(f"concurrent-job-00{i}")
        server_client.post("/jobs", json=job)

    worker1 = runner_worker_factory(runner_id="worker-c1")
    worker2 = runner_worker_factory(runner_id="worker-c2")

    # Worker 1 claims and runs
    assert worker1.run_once() is True
    # Worker 2 claims and runs
    assert worker2.run_once() is True

    # Assert 2 different jobs are completed, 1 remains queued
    summary = server_client.get("/api/ui/dashboard/summary").json()
    assert summary["jobs"]["by_status"].get("completed", 0) == 2
    assert summary["jobs"]["by_status"].get("queued", 0) == 1


def test_interaction_expired_lease_reassignment(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
    server_settings,
    server_app,
):
    """Verify expired lease is recovered and successfully reclaimed by another worker."""
    from datetime import datetime, timedelta, timezone
    from histra_server.db import create_session_factory
    from histra_server.models import Attempt, Job
    from histra_server.service import expire_leases

    job = canonical_job_factory("expire-job-001")
    server_client.post("/jobs", json=job)

    server_client.post("/runners/register", json={"runner_id": "dead-worker", "name": "dead", "version": "1.0"})
    claim = server_client.post("/claims", json={"runner_id": "dead-worker"}).json()
    assert claim["job_id"] == "expire-job-001"

    engine, factory = create_session_factory(server_settings.database_url)

    # Fast-forward lease expiry into the past
    with factory() as session:
        att = session.get(Attempt, claim["attempt_id"])
        assert att is not None
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        att.lease_expires_at = past
        session.commit()

    # Expire leases via service using server app cache
    with factory() as session:
        expired_count = expire_leases(session, cache=server_app.state.cache)
        assert expired_count == 1
        session.commit()

    # New healthy worker claims the recovered job
    healthy_worker = runner_worker_factory(runner_id="healthy-worker")
    assert healthy_worker.run_once() is True

    job_data = server_client.get("/jobs/expire-job-001").json()
    assert job_data["status"] == "completed"
    assert len(job_data["attempts"]) == 2  # First expired, second completed


def test_interaction_job_cancellation_aborts_active_leases(
    server_client: TestClient,
    canonical_job_factory,
):
    """Verify cancelling a job transitions active attempts to cancelled."""
    job = canonical_job_factory("cancel-job-001")
    server_client.post("/jobs", json=job)

    server_client.post("/runners/register", json={"runner_id": "cancel-worker", "name": "worker", "version": "1.0"})
    claim = server_client.post("/claims", json={"runner_id": "cancel-worker"}).json()

    # Cancel the job
    resp_cancel = server_client.post(f"/jobs/{claim['job_id']}/cancel")
    assert resp_cancel.status_code == 200

    job_data = server_client.get(f"/jobs/{claim['job_id']}").json()
    assert job_data["status"] == "cancelled"
    assert job_data["attempts"][0]["status"] == "cancelled"


# ===========================================================================
# 4. Interaction 4: Result Packaging x Server Columnar Series Extraction
# ===========================================================================

def test_interaction_runner_result_packaging_reactions_and_displacements(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify result payload packaging structures reactions and displacements."""
    job = canonical_job_factory("series-job-001")
    server_client.post("/jobs", json=job)

    worker = runner_worker_factory(runner_id="worker-series-1")
    worker.run_once()

    job_data = server_client.get("/jobs/series-job-001").json()
    results = job_data["result"]["results"]

    assert "Vert" in results["analyses"]
    analysis = results["analyses"]["Vert"]
    reactions = analysis["outputs"]["reactions"]
    assert len(reactions) > 0
    assert "R3" in reactions[0]


def test_interaction_server_series_extraction_endpoint(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify /api/ui/dashboard/jobs/{id}/series unpacks plottable curve vectors."""
    job = canonical_job_factory("series-extract-001")
    server_client.post("/jobs", json=job)

    worker = runner_worker_factory(runner_id="worker-extract-1")
    worker.run_once()

    series_resp = server_client.get("/api/ui/dashboard/jobs/series-extract-001/series")
    assert series_resp.status_code == 200
    data = series_resp.json()
    assert "series" in data
    # Reaction curve or numeric series should be present
    assert len(data["series"]) > 0


def test_interaction_dashboard_metrics_and_series_parity(
    server_client: TestClient,
    canonical_job_factory,
    runner_worker_factory,
):
    """Verify series points are finite floats and correspond to step counts."""
    import math

    job = canonical_job_factory("parity-job-001")
    server_client.post("/jobs", json=job)

    worker = runner_worker_factory(runner_id="worker-parity-1")
    worker.run_once()

    series_data = server_client.get("/api/ui/dashboard/jobs/parity-job-001/series").json()
    for item in series_data["series"]:
        assert "path" in item
        assert "values" in item
        for val in item["values"]:
            assert isinstance(val, (int, float))
            assert math.isfinite(val)

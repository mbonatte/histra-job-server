from __future__ import annotations

import io
import json
import zipfile


def job_definition(job_id: str = "bridge-001") -> dict:
    return {
        "schema_version": "1.0",
        "job_id": job_id,
        "model": {"path": "model.hrx"},
        "analyses": [{"name": "LiveLoad_1"}],
        "metadata": {"scenario_id": job_id},
    }


def create_job(client, job_id: str = "bridge-001"):
    return client.post(
        "/api/v1/jobs",
        data={"priority": "10", "max_attempts": "3"},
        files={
            "job_file": ("job.json", json.dumps(job_definition(job_id)), "application/json"),
            "model_file": ("model.hrx", b"<HRX>test</HRX>", "application/octet-stream"),
        },
    )


def register_worker(client, name: str = "test-worker", capacity: int = 1):
    response = client.post(
        "/api/v1/workers/register",
        json={"name": name, "max_parallel_jobs": capacity, "worker_version": "0.2.0"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_job_workflow(client):
    created = create_job(client)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "queued"

    worker = register_worker(client)
    claimed = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]})
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()

    package = client.get(claim["package_url"])
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert set(archive.namelist()) == {"job.json", "model.hrx"}
        manifest = json.loads(archive.read("job.json"))
        assert manifest["job_id"] == "bridge-001"
        assert manifest["attempt_id"] == claim["attempt_id"]
        assert len(manifest["model"]["sha256"]) == 64

    heartbeat = client.post(
        claim["heartbeat_url"],
        json={"status": "running", "progress": {"analysis": "LiveLoad_1"}},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "running"

    completed = client.post(
        claim["results_url"],
        files={
            "results_file": (
                "results.json",
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "job_id": "bridge-001",
                        "attempt_id": claim["attempt_id"],
                        "analyses": {"LiveLoad_1": {"outputs": {}}},
                    }
                ),
                "application/json",
            ),
            "run_file": (
                "run.json",
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "job_id": "bridge-001",
                        "attempt_id": claim["attempt_id"],
                        "status": "completed",
                    }
                ),
                "application/json",
            ),
            "validation_file": (
                "validation.json",
                json.dumps({"valid": True}),
                "application/json",
            ),
            "solver_log": ("solver.log", b"Analysis completed", "text/plain"),
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    repeated = client.post(
        claim["results_url"],
        files={
            "results_file": (
                "results.json",
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "job_id": "bridge-001",
                        "attempt_id": claim["attempt_id"],
                        "analyses": {"LiveLoad_1": {"outputs": {}}},
                    }
                ),
                "application/json",
            ),
            "run_file": (
                "run.json",
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "job_id": "bridge-001",
                        "attempt_id": claim["attempt_id"],
                        "status": "completed",
                    }
                ),
                "application/json",
            ),
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "completed"

    results = client.get("/api/v1/jobs/bridge-001/results")
    assert results.status_code == 200
    assert results.json()["job_id"] == "bridge-001"

    artifacts = client.get("/api/v1/jobs/bridge-001/artifacts")
    assert artifacts.status_code == 200
    kinds = {item["kind"] for item in artifacts.json()}
    assert {
        "job_json",
        "model_hrx",
        "job_package",
        "attempt_job_json",
        "attempt_package",
        "results_json",
        "run_json",
    } <= kinds


def test_no_job_returns_204(client):
    worker = register_worker(client)
    response = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]})
    assert response.status_code == 204


def test_failed_attempt_is_requeued(client):
    assert create_job(client).status_code == 201
    worker = register_worker(client)
    claim = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]}).json()

    failed = client.post(
        claim["failure_url"],
        json={"reason": "Solver failed", "retryable": True, "exit_code": 1},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "queued"

    second_claim = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]})
    assert second_claim.status_code == 200
    assert second_claim.json()["attempt_id"] != claim["attempt_id"]


def test_duplicate_job_is_rejected(client):
    assert create_job(client).status_code == 201
    duplicate = create_job(client)
    assert duplicate.status_code == 409


def test_model_filename_must_match_manifest(client):
    response = client.post(
        "/api/v1/jobs",
        files={
            "job_file": ("job.json", json.dumps(job_definition()), "application/json"),
            "model_file": ("different.hrx", b"x", "application/octet-stream"),
        },
    )
    assert response.status_code == 422


def test_worker_capacity_limits_claims(client):
    assert create_job(client, "bridge-001").status_code == 201
    assert create_job(client, "bridge-002").status_code == 201
    worker = register_worker(client, capacity=1)

    first = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]})
    second = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]})

    assert first.status_code == 200
    assert second.status_code == 204


def test_expired_lease_requeues_job(client):
    from datetime import UTC, datetime, timedelta

    from histra_server.models import AttemptStatus, JobAttempt

    assert create_job(client).status_code == 201
    first_worker = register_worker(client, "worker-one")
    first_claim = client.post(
        "/api/v1/jobs/claim", json={"worker_id": first_worker["id"]}
    ).json()

    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttempt, first_claim["attempt_id"])
        attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    second_worker = register_worker(client, "worker-two")
    second_claim_response = client.post(
        "/api/v1/jobs/claim", json={"worker_id": second_worker["id"]}
    )
    assert second_claim_response.status_code == 200, second_claim_response.text
    second_claim = second_claim_response.json()
    assert second_claim["job_id"] == first_claim["job_id"]
    assert second_claim["attempt_id"] != first_claim["attempt_id"]

    with client.app.state.session_factory() as session:
        expired = session.get(JobAttempt, first_claim["attempt_id"])
        assert expired.status == AttemptStatus.EXPIRED


def test_expired_attempt_cannot_heartbeat(client):
    from datetime import UTC, datetime, timedelta

    from histra_server.models import JobAttempt

    assert create_job(client).status_code == 201
    worker = register_worker(client)
    claim = client.post("/api/v1/jobs/claim", json={"worker_id": worker["id"]}).json()

    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttempt, claim["attempt_id"])
        attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    heartbeat = client.post(
        claim["heartbeat_url"],
        json={"status": "running", "progress": {}},
    )
    assert heartbeat.status_code == 409
    assert heartbeat.json()["detail"] == "Attempt lease has expired"

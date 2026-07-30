from datetime import timedelta

from histra_server.models import Attempt, Job, utcnow
from histra_server.service import expire_leases


def result_payload(claim, runner):
    return {
        "runner_id": runner["runner_id"],
        "job_id": claim["job_id"],
        "attempt_id": claim["attempt_id"],
        "job_sha256": claim["job_sha256"],
        "hrx_sha256": claim["hrx_sha256"],
        "results": {"maximum_displacement": 0.0042},
        "run": {"solver": "fake", "exit_code": 0},
        "logs": "finished",
    }


def test_heartbeat_extends_lease(client, claim, runner):
    response = client.post(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/heartbeat",
        json={"runner_id": runner["runner_id"]},
    )
    assert response.status_code == 200
    assert response.json()["lease_expires_at"] > claim["lease_expires_at"]


def test_complete_attempt_persists_results_and_deletes_package(client, claim, runner, app):
    payload = result_payload(claim, runner)
    response = client.post(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/results", json=payload
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert not app.state.cache.path_for(claim["attempt_id"]).exists()
    detail = client.get("/jobs/job-001").json()
    assert detail["status"] == "completed"
    assert detail["result"]["results"]["maximum_displacement"] == 0.0042
    # Exact retry is idempotent.
    assert client.post(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/results", json=payload
    ).status_code == 200


def test_result_provenance_must_match(client, claim, runner):
    payload = result_payload(claim, runner)
    payload["hrx_sha256"] = "0" * 64
    response = client.post(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/results", json=payload
    )
    assert response.status_code == 409


def test_failure_requeues_until_max_attempts(client, claim, runner):
    endpoint = f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/failed"
    payload = {"runner_id": runner["runner_id"], "error_type": "SolverError", "message": "boom"}
    assert client.post(endpoint, json=payload).json()["status"] == "failed"
    assert client.get("/jobs/job-001").json()["status"] == "queued"
    second = client.post("/claims", json={"runner_id": runner["runner_id"]}).json()
    second_endpoint = f"/jobs/{second['job_id']}/attempts/{second['attempt_id']}/failed"
    assert client.post(second_endpoint, json=payload).status_code == 200
    assert client.get("/jobs/job-001").json()["status"] == "failed"


def test_expired_lease_requeues_job(client, claim, app):
    with app.state.session_factory() as session:
        attempt = session.get(Attempt, claim["attempt_id"])
        attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
        assert expire_leases(session, app.state.cache) == 1
        assert session.get(Attempt, claim["attempt_id"]).status == "expired"
        assert session.get(Job, "job-001").status == "queued"

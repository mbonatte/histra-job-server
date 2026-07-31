from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from fastapi.testclient import TestClient
from histra_server.main import create_app


def lease(client, auth, job):
    assert client.post("/jobs", headers=auth, json=job).status_code in {200, 201}
    assert client.post("/runners/register", headers=auth, json={"runner_id": "r1", "name": "R", "version": "1", "capabilities": {}}).status_code == 200
    response = client.post("/claims", headers=auth, json={"runner_id": "r1"})
    assert response.status_code == 200, response.text
    return response.json()


def result_payload(claim, *, runner="r1", job="bridge-1", values=None):
    return {
        "runner_id": runner,
        "job_id": job,
        "attempt_id": claim["attempt_id"],
        "job_sha256": claim["job_sha256"],
        "hrx_sha256": claim["hrx_sha256"],
        "results": {"curve": values or [0, 1]},
        "run": {"exit_code": 0},
        "logs": "log",
    }


def test_job_conflicts_filters_missing_and_cancel(client, auth, imported):
    job = imported["job"]
    assert client.post("/jobs", headers=auth, json={}).status_code == 422
    assert client.post("/jobs", headers=auth, json=job).status_code == 201
    assert client.post("/jobs", headers=auth, json=job).status_code == 200
    changed = {**job, "metadata": {**job["metadata"], "changed": True}}
    assert client.post("/jobs", headers=auth, json=changed).status_code == 409
    assert client.get("/jobs?status=queued&limit=1", headers=auth).json()["items"][0]["job_id"] == "bridge-1"
    assert client.get("/jobs/missing", headers=auth).status_code == 404
    assert client.post("/jobs/missing/cancel", headers=auth).status_code == 404
    assert client.post("/jobs/bridge-1/cancel", headers=auth).json()["status"] == "cancelled"
    assert client.post("/jobs/bridge-1/cancel", headers=auth).json()["status"] == "cancelled"


def test_runner_update_unknown_claim_and_empty_queue(client, auth):
    generated = client.post("/runners/register", headers=auth, json={"name": "auto", "version": "1", "capabilities": {}})
    assert generated.status_code == 200 and generated.json()["runner_id"]
    first = client.post("/runners/register", headers=auth, json={"runner_id": "r1", "name": "one", "version": "1", "capabilities": {}})
    second = client.post("/runners/register", headers=auth, json={"runner_id": "r1", "name": "two", "version": "2", "capabilities": {"x": 1}})
    assert first.status_code == second.status_code == 200
    assert second.json()["name"] == "two"
    assert client.post("/claims", headers=auth, json={"runner_id": "missing"}).status_code == 404
    assert client.post("/claims", headers=auth, json={"runner_id": "r1"}).status_code == 204


def test_compilation_failure_is_visible(client, auth, imported):
    job = imported["job"]
    bad = {**job, "job_id": "missing-template", "model": {**job["model"], "template": {"id": "none", "sha256": "0" * 64}}}
    assert client.post("/jobs", headers=auth, json=bad).status_code == 201
    client.post("/runners/register", headers=auth, json={"runner_id": "r1", "name": "R", "version": "1", "capabilities": {}})
    claim = client.post("/claims", headers=auth, json={"runner_id": "r1"})
    assert claim.status_code == 422
    detail = client.get("/jobs/missing-template", headers=auth).json()
    assert detail["status"] == "failed"
    assert detail["attempts"][0]["failure"]["error_type"]


def test_package_identity_regeneration_heartbeat_and_completed_state(client, auth, imported):
    claim = lease(client, auth, imported["job"])
    attempt = claim["attempt_id"]
    url = f"/jobs/bridge-1/attempts/{attempt}/package"
    assert client.get(url, headers={**auth, "X-Runner-ID": "other"}).status_code == 409
    assert client.get("/jobs/bridge-1/attempts/missing/package", headers={**auth, "X-Runner-ID": "r1"}).status_code == 404
    client.app.state.cache.delete(attempt)
    regenerated = client.get(url, headers={**auth, "X-Runner-ID": "r1"})
    assert regenerated.status_code == 200
    heartbeat = client.post(f"/jobs/bridge-1/attempts/{attempt}/heartbeat", headers=auth, json={"runner_id": "r1"})
    assert heartbeat.status_code == 200
    assert client.post(f"/jobs/bridge-1/attempts/{attempt}/heartbeat", headers=auth, json={"runner_id": "wrong"}).status_code == 409
    payload = result_payload(claim)
    assert client.post(f"/jobs/bridge-1/attempts/{attempt}/results", headers=auth, json=payload).status_code == 200
    assert client.get(url, headers={**auth, "X-Runner-ID": "r1"}).status_code == 422
    assert client.post(f"/jobs/bridge-1/attempts/{attempt}/heartbeat", headers=auth, json={"runner_id": "r1"}).status_code == 422
    assert client.post(f"/jobs/bridge-1/attempts/{attempt}/results", headers=auth, json=payload).status_code == 200
    different = result_payload(claim, values=[9, 10])
    assert client.post(f"/jobs/bridge-1/attempts/{attempt}/results", headers=auth, json=different).status_code == 409


def test_result_identity_failures(client, auth, imported):
    claim = lease(client, auth, imported["job"])
    attempt = claim["attempt_id"]
    url = f"/jobs/bridge-1/attempts/{attempt}/results"
    cases = [
        {**result_payload(claim), "runner_id": "wrong"},
        {**result_payload(claim), "job_id": "wrong"},
        {**result_payload(claim), "job_sha256": "0" * 64},
    ]
    for payload in cases:
        assert client.post(url, headers=auth, json=payload).status_code == 409
    assert client.post("/jobs/bridge-1/attempts/missing/results", headers=auth, json={**result_payload(claim), "attempt_id": "missing"}).status_code == 404


def test_failed_attempt_requeues_then_reaches_final_failure(client, auth, imported):
    job = imported["job"]
    assert client.post("/jobs?max_attempts=2", headers=auth, json=job).status_code == 201
    client.post("/runners/register", headers=auth, json={"runner_id": "r1", "name": "R", "version": "1", "capabilities": {}})
    first = client.post("/claims", headers=auth, json={"runner_id": "r1"}).json()
    fail_url = f"/jobs/bridge-1/attempts/{first['attempt_id']}/failed"
    failure = {"runner_id": "r1", "error_type": "SolverError", "message": "failed", "details": {"step": 2}, "logs": "trace"}
    assert client.post(fail_url, headers=auth, json={**failure, "runner_id": "wrong"}).status_code == 409
    assert client.post(fail_url, headers=auth, json=failure).status_code == 200
    assert client.get("/jobs/bridge-1", headers=auth).json()["status"] == "queued"
    second = client.post("/claims", headers=auth, json={"runner_id": "r1"}).json()
    second_url = f"/jobs/bridge-1/attempts/{second['attempt_id']}/failed"
    assert client.post(second_url, headers=auth, json=failure).status_code == 200
    assert client.get("/jobs/bridge-1", headers=auth).json()["status"] == "failed"
    assert client.post(second_url, headers=auth, json=failure).status_code == 422


def test_dashboard_filters_missing_and_builder_errors(client, auth, imported):
    client.post("/jobs", headers=auth, json=imported["job"])
    assert client.get("/api/ui/dashboard/jobs?status=queued&search=bridge", headers=auth).json()["total"] == 1
    assert client.get("/api/ui/dashboard/jobs/missing", headers=auth).status_code == 404
    assert client.get("/api/ui/dashboard/jobs/missing/series", headers=auth).status_code == 404
    assert client.get("/api/ui/builder/templates/missing", headers=auth).status_code == 404
    assert client.post("/api/ui/builder/preview", headers=auth, json={}).status_code == 422
    assert client.post("/api/ui/builder/compile", headers=auth, json={}).status_code == 422
    invalid_variants = {"base_job": imported["job"], "variants": {"variants": [{"job_id": "bad id"}]}}
    assert client.post("/api/ui/builder/variants", headers=auth, json=invalid_variants).status_code == 422
    changed = {**imported["job"], "metadata": {"different": True}}
    response = client.post("/api/ui/builder/submit-batch", headers=auth, json={"jobs": [changed]})
    assert response.status_code == 409


def test_empty_and_too_large_upload(settings, auth):
    small = replace(settings, max_hrx_upload_bytes=4)
    with TestClient(create_app(small)) as client:
        assert client.post("/api/ui/builder/import", headers=auth, data={"job_id": "x", "template_id": "x"}, files={"file": ("x.hrx", b"", "application/xml")}).status_code == 422
        assert client.post("/api/ui/builder/import", headers=auth, data={"job_id": "x", "template_id": "x"}, files={"file": ("x.hrx", b"12345", "application/xml")}).status_code == 413


def test_disabled_pages(settings):
    disabled = replace(settings, dashboard_enabled=False, builder_ui_enabled=False)
    with TestClient(create_app(disabled)) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/docs"
        assert client.get("/dashboard").status_code == 404
        assert client.get("/builder").status_code == 404

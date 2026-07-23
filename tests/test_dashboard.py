from __future__ import annotations

from datetime import UTC, datetime, timedelta

from histra_server.models import Job, JobAttempt, Worker


def seed_dashboard(client):
    now = datetime.now(UTC)
    with client.app.state.session_factory() as session:
        worker = Worker(
            name="test-worker",
            max_parallel_jobs=2,
            worker_version="0.3.0",
            solver_version="2025.1",
            last_seen_at=now,
        )
        session.add(worker)
        session.flush()
        definition = {
            "schema_version": "1.0",
            "job_id": "bridge-001",
            "model": {"path": "model.hrx"},
            "analyses": [{"name": "LiveLoad_1"}],
            "metadata": {"span": 5.0, "scour": 0.25},
        }
        job = Job(
            id="bridge-001",
            scenario_id="scenario-a",
            status="completed",
            priority=0,
            max_attempts=3,
            attempt_count=1,
            job_definition=definition,
            model_filename="model.hrx",
            model_sha256="a" * 64,
            model_size_bytes=1024,
            package_relative_path="jobs/bridge-001/input/package.zip",
            created_at=now - timedelta(minutes=10),
            updated_at=now,
            completed_at=now,
        )
        session.add(job)
        session.flush()
        attempt = JobAttempt(
            job_id=job.id,
            worker_id=worker.id,
            status="completed",
            lease_expires_at=now + timedelta(minutes=5),
            last_heartbeat_at=now,
            created_at=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=9),
            finished_at=now,
            results_json={
                "schema_version": "1.0",
                "job_id": job.id,
                "attempt_id": "placeholder",
                "analyses": {
                    "LiveLoad_1": {
                        "analysis_key": 2,
                        "outputs": {
                            "reactions": [
                                {"Step": 1, "R1": 10.0, "R2": 20.0, "R3": 30.0},
                                {"Step": 2, "R1": 15.0, "R2": 25.0, "R3": 35.0},
                            ],
                            "displacements": [
                                {
                                    "IdElement": 101,
                                    "ParentKey": 1,
                                    "Step": 1,
                                    "Ux": 0.1,
                                    "Uy": -0.2,
                                    "Uz": 0.0,
                                },
                                {
                                    "IdElement": 101,
                                    "ParentKey": 1,
                                    "Step": 2,
                                    "Ux": 0.2,
                                    "Uy": -0.5,
                                    "Uz": 0.1,
                                },
                            ],
                        },
                    }
                },
            },
            run_json={
                "schema_version": "1.0",
                "job_id": job.id,
                "attempt_id": "placeholder",
                "status": "completed",
                "duration_seconds": 540.0,
                "metadata": {"span": 5.0, "scour": 0.25},
            },
        )
        session.add(attempt)
        session.flush()
        job.current_attempt_id = attempt.id

        failed = Job(
            id="bridge-failed",
            scenario_id="scenario-b",
            status="failed",
            priority=0,
            max_attempts=1,
            attempt_count=1,
            job_definition={
                "schema_version": "1.0",
                "job_id": "bridge-failed",
                "model": {"path": "model.hrx"},
                "analyses": [{"name": "LiveLoad_1"}],
                "metadata": {},
            },
            model_filename="model.hrx",
            model_sha256="b" * 64,
            model_size_bytes=2048,
            package_relative_path="jobs/bridge-failed/input/package.zip",
            created_at=now - timedelta(minutes=5),
            updated_at=now,
            error_message="Solver failed",
        )
        session.add(failed)
        session.commit()


def test_dashboard_static_page_and_redirect(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/dashboard/"
    page = client.get("/dashboard/")
    assert page.status_code == 200
    assert "HiStrA Analysis Dashboard" in page.text


def test_dashboard_summary_catalog_and_jobs(client):
    seed_dashboard(client)
    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["jobs"]["total"] == 2
    assert payload["jobs"]["by_status"]["completed"] == 1
    assert payload["workers"]["online"] == 1
    assert payload["duration_seconds"]["median"] == 540.0

    catalog = client.get("/api/v1/dashboard/catalog").json()
    assert "LiveLoad_1" in catalog["analyses"]
    assert 101 in catalog["model_points"]
    assert "span" in catalog["metadata_paths"]

    jobs = client.get("/api/v1/dashboard/jobs", params={"status": "completed"}).json()
    assert jobs["total"] == 1
    assert jobs["items"][0]["worker_name"] == "test-worker"


def test_dashboard_job_detail_and_statistics(client):
    seed_dashboard(client)
    detail = client.get("/api/v1/dashboard/jobs/bridge-001")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["selected_attempt"]["results"]["analyses"]["LiveLoad_1"]
    assert payload["attempts"][0]["duration_seconds"] == 540.0

    stats = client.get(
        "/api/v1/dashboard/statistics",
        params={
            "metric": "reaction_peak_abs",
            "analysis": "LiveLoad_1",
            "component": "R1",
            "group_by": "scenario",
        },
    )
    assert stats.status_code == 200
    data = stats.json()
    assert data["summary"]["count"] == 1
    assert data["summary"]["maximum"] == 15.0
    assert data["groups"][0]["group"] == "scenario-a"

    displacement = client.get(
        "/api/v1/dashboard/statistics",
        params={
            "metric": "displacement_peak_abs",
            "analysis": "LiveLoad_1",
            "component": "Uy",
            "model_point_id": 101,
        },
    ).json()
    assert displacement["summary"]["maximum"] == 0.5

    csv_response = client.get(
        "/api/v1/dashboard/statistics.csv",
        params={"metric": "duration_seconds"},
    )
    assert csv_response.status_code == 200
    assert "bridge-001" in csv_response.text

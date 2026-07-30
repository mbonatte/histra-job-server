"""Acceptance test crossing Server, Builder, and Runner package boundaries."""

from histra_runner.backends import ExecutionResult
from histra_runner.contracts import Claim
from histra_runner.executor import RunnerExecutor
from histra_runner.worker import Worker


class StructuralBackend:
    def execute(self, package, output_dir):
        assert package.job["workflow"]["analyses"] == [{"id": "static"}]
        assert b'length="27.5"' in package.hrx_path.read_bytes()
        return ExecutionResult(
            results={"maximum_displacement": 0.0042, "status": "converged"},
            run={"solver": "acceptance-fake", "solver_version": "1"},
            logs="accepted",
        )


class TestClientServerAdapter:
    """Expose the real FastAPI application through the Runner client protocol."""

    def __init__(self, client):
        self.client = client

    def register(self, **payload):
        response = self.client.post("/runners/register", json=payload)
        response.raise_for_status()
        return response.json()["runner_id"]

    def claim(self, runner_id):
        response = self.client.post("/claims", json={"runner_id": runner_id})
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return Claim.model_validate(response.json())

    def download_package(self, claim, runner_id):
        response = self.client.get(claim.package_url, headers={"X-Runner-ID": runner_id})
        response.raise_for_status()
        return response.content

    def heartbeat(self, claim, runner_id):
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/heartbeat",
            json={"runner_id": runner_id},
        )
        response.raise_for_status()
        return response.json()["lease_expires_at"]

    def submit_results(self, claim, envelope):
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/results", json=envelope
        )
        response.raise_for_status()
        return response.json()

    def submit_failure(self, claim, **payload):
        error = payload.pop("error")
        body = {
            "runner_id": payload["runner_id"],
            "error_type": type(error).__name__,
            "message": str(error),
            "details": payload.get("details", {}),
            "logs": payload.get("logs", ""),
        }
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/failed", json=body
        )
        response.raise_for_status()
        return response.json()


def test_job_to_hrx_to_runner_to_result(client, job_document, tmp_path, app):
    assert client.post("/jobs", json=job_document).status_code == 201

    worker = Worker(
        client=TestClientServerAdapter(client),
        executor=RunnerExecutor(StructuralBackend()),
        work_root=tmp_path / "runner-work",
        runner_name="acceptance-runner",
        runner_id="acceptance-runner-1",
        heartbeat_interval_seconds=0,
    )
    worker.register()
    assert worker.run_once() is True

    completed = client.get("/jobs/job-001").json()
    assert completed["status"] == "completed"
    assert completed["result"]["results"]["status"] == "converged"
    attempt = completed["attempts"][0]
    assert attempt["job_sha256"] == completed["job_sha256"]
    assert attempt["hrx_sha256"]
    assert list(app.state.cache.root.glob("*.zip")) == []

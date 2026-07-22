from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from histra_server.config import Settings
from histra_server.main import create_app


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    with tempfile.TemporaryDirectory() as temporary_directory:
        app = create_app(
            Settings(
                database_url=database_url,
                storage_root=Path(temporary_directory) / "data",
                lease_seconds=60,
                lease_reaper_interval_seconds=0,
            )
        )
        with TestClient(app) as client:
            definition = {
                "schema_version": "1.0",
                "job_id": "ci-postgres-smoke",
                "model": {"path": "model.hrx"},
                "analyses": [{"name": "LiveLoad_1"}],
            }
            created = client.post(
                "/api/v1/jobs",
                files={
                    "job_file": (
                        "job.json",
                        json.dumps(definition),
                        "application/json",
                    ),
                    "model_file": (
                        "model.hrx",
                        b"<HRX>CI</HRX>",
                        "application/octet-stream",
                    ),
                },
            )
            created.raise_for_status()
            worker = client.post(
                "/api/v1/workers/register",
                json={"name": "ci-worker", "max_parallel_jobs": 1},
            )
            worker.raise_for_status()
            claim = client.post(
                "/api/v1/jobs/claim",
                json={"worker_id": worker.json()["id"]},
            )
            claim.raise_for_status()
            attempt_id = claim.json()["attempt_id"]
            completed = client.post(
                claim.json()["results_url"],
                files={
                    "results_file": (
                        "results.json",
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "job_id": definition["job_id"],
                                "attempt_id": attempt_id,
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
                                "job_id": definition["job_id"],
                                "attempt_id": attempt_id,
                                "status": "completed",
                            }
                        ),
                        "application/json",
                    ),
                },
            )
            completed.raise_for_status()
            assert completed.json()["status"] == "completed"
    print("PostgreSQL API smoke test passed")


if __name__ == "__main__":
    main()

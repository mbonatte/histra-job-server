import io
import json
import zipfile

from histra_server.models import Attempt, Job


def package_files(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return set(archive.namelist()), json.loads(archive.read("manifest.json")), archive.read("job.json")


def test_claim_compiles_three_file_ephemeral_package(client, claim, runner, app):
    response = client.get(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/package",
        headers={"X-Runner-ID": runner["runner_id"]},
    )
    assert response.status_code == 200
    names, manifest, job_bytes = package_files(response.content)
    assert names == {"manifest.json", "job.json", "models/job-001.hrx"}
    assert manifest["protocol_version"] == "1.0"
    assert manifest["job_sha256"] == claim["job_sha256"]
    assert manifest["hrx"]["sha256"] == claim["hrx_sha256"]
    assert b'"job_id":"job-001"' in job_bytes

    # The database contains canonical JOB/provenance, never HRX bytes or a package path.
    with app.state.session_factory() as session:
        job = session.get(Job, "job-001")
        attempt = session.get(Attempt, claim["attempt_id"])
        assert not hasattr(job, "hrx")
        assert not hasattr(job, "package_relative_path")
        assert attempt.package_manifest["hrx"]["sha256"] == claim["hrx_sha256"]


def test_package_is_bound_to_runner(client, claim):
    response = client.get(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/package",
        headers={"X-Runner-ID": "someone-else"},
    )
    assert response.status_code == 409


def test_missing_cache_is_regenerated_identically(client, claim, runner, app):
    path = app.state.cache.path_for(claim["attempt_id"])
    original = path.read_bytes()
    path.unlink()
    response = client.get(
        f"/jobs/{claim['job_id']}/attempts/{claim['attempt_id']}/package",
        headers={"X-Runner-ID": runner["runner_id"]},
    )
    assert response.status_code == 200
    assert response.content == original
    assert path.exists()


def test_compilation_failure_marks_job_failed(client, runner, job_document):
    job_document["model"]["template"]["sha256"] = "0" * 64
    assert client.post("/jobs", json=job_document).status_code == 201
    response = client.post("/claims", json={"runner_id": runner["runner_id"]})
    assert response.status_code == 422
    assert client.get("/jobs/job-001").json()["status"] == "failed"

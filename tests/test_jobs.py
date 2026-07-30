import copy


def test_submit_job_is_idempotent(client, job_document):
    first = client.post("/jobs?priority=7&max_attempts=4", json=job_document)
    second = client.post("/jobs?priority=99", json=job_document)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["job_sha256"] == second.json()["job_sha256"]
    assert second.json()["priority"] == 7
    assert second.json()["max_attempts"] == 4


def test_same_id_with_different_content_conflicts(client, job_document):
    assert client.post("/jobs", json=job_document).status_code == 201
    changed = copy.deepcopy(job_document)
    changed["metadata"]["campaign"] = "different"
    response = client.post("/jobs", json=changed)
    assert response.status_code == 409


def test_invalid_job_is_rejected(client, job_document):
    del job_document["model"]["template"]["sha256"]
    response = client.post("/jobs", json=job_document)
    assert response.status_code == 422


def test_list_read_and_cancel(client, submitted):
    listed = client.get("/jobs").json()["items"]
    assert [item["job_id"] for item in listed] == ["job-001"]
    detail = client.get("/jobs/job-001").json()
    assert detail["job"]["workflow"]["analyses"][0]["id"] == "static"
    cancelled = client.post("/jobs/job-001/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/claims", json={"runner_id": "missing"}).status_code == 404

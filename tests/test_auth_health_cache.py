import os
import time

from fastapi.testclient import TestClient

from histra_server.config import Settings
from histra_server.main import create_app


def test_health_endpoints_are_public(client):
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").json()["status"] == "ready"


def test_optional_bearer_auth(settings, job_document):
    protected = Settings(**{**settings.__dict__, "api_token": "secret"})
    with TestClient(create_app(protected)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.post("/jobs", json=job_document).status_code == 401
        response = client.post(
            "/jobs", json=job_document, headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 201


def test_cache_prune_removes_expired_file(app):
    path = app.state.cache.put("old", b"zip")
    old = time.time() - app.state.cache.ttl_seconds - 1
    os.utime(path, (old, old))
    assert app.state.cache.prune() == 1
    assert not path.exists()

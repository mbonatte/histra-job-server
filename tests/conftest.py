from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import sha256_hex
from histra_server.config import Settings
from histra_server.main import create_app


TEMPLATE = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<HiStrAProject><Span id="main" length="25"/></HiStrAProject>\n'
)


@pytest.fixture
def hrx_bytes() -> bytes:
    return (Path(__file__).parent / "fixtures" / "simple.hrx").read_bytes()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "base.hrx").write_bytes(TEMPLATE)
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        template_root=templates,
        package_cache_root=tmp_path / "cache",
        api_token="test-token",
        lease_seconds=120,
        package_ttl_seconds=3600,
        default_max_attempts=2,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(app, auth):
    # Keep the retained v1.0 protocol tests readable while exercising the same
    # bearer-authenticated application used by the v1.1 UI tests.
    with TestClient(app, headers=auth) as value:
        yield value


@pytest.fixture
def raw_client(app):
    """Unauthenticated client for explicit authentication tests."""
    with TestClient(app) as value:
        yield value


@pytest.fixture
def job_document() -> dict:
    return {
        "schema_version": "1.0",
        "job_id": "job-001",
        "model": {
            "output_path": "models/job-001.hrx",
            "template": {"id": "base", "sha256": sha256_hex(TEMPLATE)},
            "patches": [
                {
                    "op": "set_attribute",
                    "xpath": "//Span[@id='main']",
                    "attribute": "length",
                    "value": 27.5,
                }
            ],
        },
        "workflow": {"analyses": [{"id": "static"}]},
        "metadata": {"campaign": "tests"},
    }


@pytest.fixture
def submitted(client, job_document):
    response = client.post("/jobs", json=job_document)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def runner(client):
    response = client.post(
        "/runners/register",
        json={"runner_id": "runner-1", "name": "test", "version": "1.0.0"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def claim(client, submitted, runner):
    response = client.post("/claims", json={"runner_id": runner["runner_id"]})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def imported(client, hrx_bytes):
    response = client.post(
        "/api/ui/builder/import",
        data={"job_id": "bridge-1", "template_id": "bridge-1"},
        files={"file": ("bridge.hrx", hrx_bytes, "application/xml")},
    )
    assert response.status_code == 200, response.text
    return response.json()

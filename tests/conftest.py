import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import sha256_hex
from histra_server.config import Settings
from histra_server.main import create_app


TEMPLATE = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<HiStrAProject><Span id=\"main\" length=\"25\"/></HiStrAProject>\n"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "base.hrx").write_bytes(TEMPLATE)
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'server.db'}",
        template_root=templates,
        package_cache_root=tmp_path / "cache",
        lease_seconds=60,
        package_ttl_seconds=3600,
        default_max_attempts=2,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def job_document():
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
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def runner(client):
    response = client.post(
        "/runners/register",
        json={"runner_id": "runner-1", "name": "test", "version": "1.0.0"},
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def claim(client, submitted, runner):
    response = client.post("/claims", json={"runner_id": runner["runner_id"]})
    assert response.status_code == 200
    return response.json()

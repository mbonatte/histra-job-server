from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from histra_server.config import Settings
from histra_server.main import create_app

@pytest.fixture
def hrx_bytes():
    return (Path(__file__).parent / "fixtures" / "simple.hrx").read_bytes()

@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path/'test.db'}",
        template_root=tmp_path/"templates",
        package_cache_root=tmp_path/"cache",
        api_token="test-token",
        lease_seconds=120,
        package_ttl_seconds=3600,
        default_max_attempts=3,
    )

@pytest.fixture
def client(settings):
    app=create_app(settings)
    with TestClient(app) as value:
        yield value

@pytest.fixture
def auth():
    return {"Authorization":"Bearer test-token"}

@pytest.fixture
def imported(client,auth,hrx_bytes):
    response=client.post("/api/ui/builder/import",headers=auth,data={"job_id":"bridge-1","template_id":"bridge-1"},files={"file":("bridge.hrx",hrx_bytes,"application/xml")})
    assert response.status_code==200,response.text
    return response.json()

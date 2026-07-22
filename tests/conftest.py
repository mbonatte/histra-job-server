from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histra_server.config import Settings
from histra_server.db import Base
from histra_server.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        storage_root=tmp_path / "data",
        lease_seconds=60,
        lease_reaper_interval_seconds=0,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client
        Base.metadata.drop_all(app.state.engine)

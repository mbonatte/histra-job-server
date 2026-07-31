from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import select

from histra_server.cache import PackageCache
from histra_server.config import Settings
from histra_server.db import Base, create_session_factory, session_scope
from histra_server.models import Attempt, Job, Runner, utcnow
from histra_server.package import read_manifest
from histra_server.results import extract_numeric_series
from histra_server.schemas import ClaimRequest, FailureUpload, ResultsUpload, RunnerRegistration
from histra_server.service import (
    ConflictError,
    IdentityError,
    InvalidStateError,
    NotFoundError,
    cancel_job,
    claim_job,
    complete_attempt,
    create_job,
    expire_leases,
    fail_attempt,
    get_package,
    heartbeat,
    register_runner,
)
from histra_builder import TemplateRegistry, job_from_hrx


def test_settings_from_env(monkeypatch):
    values = {
        "HISTRA_DATABASE_URL": "sqlite:///x.db", "HISTRA_TEMPLATE_ROOT": "/tmp/t",
        "HISTRA_PACKAGE_CACHE_ROOT": "/tmp/c", "HISTRA_API_TOKEN": "token",
        "HISTRA_LEASE_SECONDS": "10", "HISTRA_PACKAGE_TTL_SECONDS": "20",
        "HISTRA_DEFAULT_MAX_ATTEMPTS": "4", "HISTRA_DASHBOARD_ENABLED": "false",
        "HISTRA_BUILDER_UI_ENABLED": "0", "HISTRA_RUNNER_ONLINE_SECONDS": "30",
        "HISTRA_MAX_HRX_UPLOAD_BYTES": "40", "HISTRA_UI_REFRESH_SECONDS": "6",
    }
    for key, value in values.items(): monkeypatch.setenv(key, value)
    settings = Settings.from_env()
    assert settings.database_url == "sqlite:///x.db" and settings.api_token == "token"
    assert settings.dashboard_enabled is False and settings.builder_ui_enabled is False
    assert settings.max_hrx_upload_bytes == 40
    monkeypatch.setenv("HISTRA_LEASE_SECONDS", "0")
    with pytest.raises(ValueError): Settings.from_env()


def test_cache_lifecycle(tmp_path):
    cache = PackageCache(tmp_path, 1)
    assert cache.get("missing") is None
    path = cache.put("a", b"data")
    assert path.read_bytes() == b"data" and cache.get("a") == b"data"
    old = time.time() - 5
    os.utime(path, (old, old))
    assert cache.get("a") is None
    cache.put("b", b"x"); path = cache.path_for("b"); os.utime(path, (old, old))
    assert cache.prune() == 1
    cache.delete("b")


def test_db_scope_and_numeric_series(tmp_path):
    engine, factory = create_session_factory(f"sqlite:///{tmp_path/'db.sqlite'}")
    Base.metadata.create_all(engine)
    generator = session_scope(factory)
    session = next(generator)
    assert session.execute(select(Job)).all() == []
    with pytest.raises(StopIteration): next(generator)
    series = extract_numeric_series({"a": [1, 2], "nested": [{"b": [3.0, 4.0]}, "x"], "bad": [1, "x"]})
    assert [item["path"] for item in series] == ["/a", "/nested/0/b"]
    assert extract_numeric_series([1, 2], max_series=0) == []
    engine.dispose()


def service_context(tmp_path, hrx_bytes, *, max_attempts=2):
    engine, factory = create_session_factory(f"sqlite:///{tmp_path/'db.sqlite'}")
    Base.metadata.create_all(engine)
    session = factory()
    registry = TemplateRegistry(tmp_path / "templates")
    cache = PackageCache(tmp_path / "cache", 3600)
    job_spec = job_from_hrx(hrx_bytes, job_id="job", template_id="job", registry=registry)
    job, _ = create_job(session, job_spec.model_dump(mode="json"), priority=0, max_attempts=max_attempts)
    runner = register_runner(session, RunnerRegistration(runner_id="runner", name="Runner", version="1", capabilities={}))
    return engine, session, registry, cache, job, runner


def test_direct_service_error_states(tmp_path, hrx_bytes):
    engine, session, registry, cache, job, runner = service_context(tmp_path, hrx_bytes)
    claimed = claim_job(session, runner_id="runner", registry=registry, cache=cache, lease_seconds=10)
    assert claimed is not None
    _, attempt = claimed
    with pytest.raises(NotFoundError): get_package(session, job_id="job", attempt_id="missing", runner_id="runner", registry=registry, cache=cache)
    with pytest.raises(IdentityError): heartbeat(session, job_id="job", attempt_id=attempt.id, runner_id="other", lease_seconds=10)
    attempt.status = "failed"; session.commit()
    with pytest.raises(InvalidStateError): heartbeat(session, job_id="job", attempt_id=attempt.id, runner_id="runner", lease_seconds=10)
    with pytest.raises(InvalidStateError): get_package(session, job_id="job", attempt_id=attempt.id, runner_id="runner", registry=registry, cache=cache)
    with pytest.raises(NotFoundError): cancel_job(session, "missing", cache)
    session.close(); engine.dispose()


def test_expire_lease_and_package_manifest(tmp_path, hrx_bytes):
    engine, session, registry, cache, job, runner = service_context(tmp_path, hrx_bytes)
    _, attempt = claim_job(session, runner_id="runner", registry=registry, cache=cache, lease_seconds=10)
    package = cache.get(attempt.id)
    assert read_manifest(package)["job_id"] == "job"
    attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    session.commit()
    assert expire_leases(session, cache) == 1
    assert session.get(Attempt, attempt.id).status == "expired"
    assert session.get(Job, "job").status == "queued"
    assert expire_leases(session, cache) == 0
    session.close(); engine.dispose()

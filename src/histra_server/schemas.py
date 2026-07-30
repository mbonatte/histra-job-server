from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunnerRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=64)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str


class ResultsUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str
    job_id: str
    attempt_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hrx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: dict[str, Any]
    run: dict[str, Any]
    logs: str = ""


class FailureUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str
    error_type: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)
    logs: str = ""

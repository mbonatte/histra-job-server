from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ModelDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value in {".", ".."}:
            raise ValueError("model.path must name an HRX file")
        if "/" in value or "\\" in value:
            raise ValueError("model.path must be a filename, not a directory path")
        if not value.lower().endswith(".hrx"):
            raise ValueError("model.path must end with .hrx")
        return value


class JobDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["1.0"]
    job_id: str
    model: ModelDefinition
    analyses: list[dict[str, Any]] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("analyses")
    @classmethod
    def validate_analyses(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names: list[str] = []
        for index, analysis in enumerate(value):
            name = analysis.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"analyses[{index}].name must be a non-empty string")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("analysis names must be unique")
        return value

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if not JOB_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "job_id must start with an alphanumeric character and contain only "
                "letters, numbers, dots, underscores, or hyphens"
            )
        return value


class WorkerRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    max_parallel_jobs: int = Field(default=1, ge=1, le=64)
    worker_version: str | None = Field(default=None, max_length=50)
    solver_version: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeatRequest(BaseModel):
    max_parallel_jobs: int | None = Field(default=None, ge=1, le=64)
    worker_version: str | None = Field(default=None, max_length=50)
    solver_version: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] | None = None


class WorkerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    enabled: bool
    max_parallel_jobs: int
    worker_version: str | None
    solver_version: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    last_seen_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str | None
    status: str
    priority: int
    max_attempts: int
    attempt_count: int
    current_attempt_id: str | None
    model_filename: str
    model_sha256: str
    model_size_bytes: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    worker_id: str
    status: str
    lease_expires_at: datetime
    last_heartbeat_at: datetime
    progress_json: dict[str, Any]
    exit_code: int | None
    failure_reason: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobDetailResponse(JobResponse):
    job_definition: dict[str, Any]
    attempts: list[AttemptResponse] = Field(default_factory=list)


class JobPageResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class ClaimRequest(BaseModel):
    worker_id: str


class ClaimResponse(BaseModel):
    job_id: str
    attempt_id: str
    lease_expires_at: datetime
    package_url: str
    heartbeat_url: str
    results_url: str
    failure_url: str


AttemptActiveStatus = Literal[
    "leased", "downloading", "running", "extracting", "uploading", "validating"
]


class AttemptHeartbeatRequest(BaseModel):
    status: AttemptActiveStatus
    progress: dict[str, Any] = Field(default_factory=dict)


class AttemptFailureRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=10_000)
    retryable: bool = True
    exit_code: int | None = None
    run: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None


class RetryJobRequest(BaseModel):
    additional_attempts: int = Field(default=1, ge=1, le=100)


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    attempt_id: str | None
    kind: str
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    created_at: datetime
    download_url: str | None = None

    @model_validator(mode="after")
    def set_download_url(self):
        if self.download_url is None:
            self.download_url = f"/api/v1/artifacts/{self.id}"
        return self


class HealthResponse(BaseModel):
    status: str
    version: str

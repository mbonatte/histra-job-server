"""Canonical identities for immutable JOB documents and attempt packages."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PACKAGE_PROTOCOL_VERSION = "1.1"
SUPPORTED_JOB_SCHEMA_VERSIONS = {"1.0"}


def canonical_job_bytes(value: dict[str, Any]) -> bytes:
    cloned = json.loads(json.dumps(value))
    cloned.pop("attempt_id", None)
    metadata = cloned.setdefault("metadata", {})
    for key in (
        "job_sha256",
        "provenance",
        "submission_protocol",
        "idempotency_key",
        "import_validation",
    ):
        metadata.pop(key, None)
    return json.dumps(
        cloned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def job_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_job_bytes(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def job_metadata(value: dict[str, Any]) -> dict[str, Any]:
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def campaign_fields(value: dict[str, Any]) -> dict[str, Any]:
    campaign = value.get("campaign")
    return campaign if isinstance(campaign, dict) else {}


def assert_supported_job(value: dict[str, Any]) -> None:
    schema = str(value.get("schema_version", ""))
    if schema not in SUPPORTED_JOB_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_JOB_SCHEMA_VERSIONS))
        raise ValueError(f"Unsupported JOB schema {schema!r}; supported: {supported}")
    model = value.get("model")
    if not isinstance(model, dict) or not str(model.get("path", "")).lower().endswith(".hrx"):
        raise ValueError("JOB model.path must name the generated HRX file")
    if model.get("template_path") or model.get("imported"):
        raise ValueError("Persistent JOBs must not depend on an imported HRX template")

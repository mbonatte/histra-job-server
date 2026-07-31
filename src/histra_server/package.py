from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any
from histra_builder import TemplateRegistry, canonical_json_bytes, compile_job

PACKAGE_PROTOCOL_VERSION = "1.0"


def _zip_entry(name: str, data: bytes):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info, data


def build_attempt_package(job: dict[str, Any], *, attempt_id: str, created_at: datetime, registry: TemplateRegistry):
    artifact = compile_job(job, registry)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    timestamp = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "protocol_version": PACKAGE_PROTOCOL_VERSION,
        "job_id": job["job_id"],
        "attempt_id": attempt_id,
        "created_at": timestamp,
        "job_sha256": artifact.provenance["job_sha256"],
        "hrx": {"path": artifact.output_path, "sha256": artifact.hrx_sha256, "size_bytes": len(artifact.hrx_bytes)},
        "builder": artifact.provenance,
    }
    entries = [
        _zip_entry("manifest.json", canonical_json_bytes(manifest)),
        _zip_entry("job.json", canonical_json_bytes(job)),
        _zip_entry(artifact.output_path, artifact.hrx_bytes),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue(), manifest


def read_manifest(package_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        return json.loads(archive.read("manifest.json"))

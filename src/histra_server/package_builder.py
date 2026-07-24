"""Build disposable runner packages from authoritative JOB JSON."""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .contracts import PACKAGE_PROTOCOL_VERSION, bytes_sha256, job_sha256


@dataclass(frozen=True)
class BuiltPackage:
    path: Path
    job_sha256: str
    hrx_sha256: str
    hrx_size_bytes: int
    builder_version: str


class EphemeralPackageBuilder:
    """Compile an HRX and cache only a reproducible, disposable attempt ZIP."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.package_cache_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, digest: str, attempt_id: str) -> Path:
        safe_attempt = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        if not safe_attempt:
            raise ValueError("Unsafe attempt ID")
        return self.root / digest / f"{safe_attempt}.zip"

    def _fresh(self, path: Path) -> bool:
        return path.is_file() and (
            time.time() - path.stat().st_mtime <= self.settings.package_cache_ttl_seconds
        )

    @staticmethod
    def _read_manifest(path: Path) -> BuiltPackage:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        return BuiltPackage(
            path=path,
            job_sha256=str(manifest["job_sha256"]),
            hrx_sha256=str(manifest["hrx_sha256"]),
            hrx_size_bytes=int(manifest["hrx_size_bytes"]),
            builder_version=str(manifest["builder_version"]),
        )

    def build(
        self,
        job_definition: dict[str, Any],
        *,
        job_id: str,
        attempt_id: str,
    ) -> BuiltPackage:
        self.purge_expired()
        digest = job_sha256(job_definition)
        destination = self._cache_path(digest, attempt_id)
        if self._fresh(destination):
            return self._read_manifest(destination)

        attempt_job = json.loads(json.dumps(job_definition))
        attempt_job["attempt_id"] = attempt_id
        attempt_job.setdefault("metadata", {})["job_sha256"] = digest

        url = f"{self.settings.builder_url.rstrip('/')}/api/jobs/generate/hrx"
        with httpx.Client(timeout=self.settings.builder_timeout_seconds) as client:
            response = client.post(url, json=attempt_job)
        if response.status_code != 200:
            raise RuntimeError(
                f"Builder returned HTTP {response.status_code} for {job_id}: "
                + response.text[:4000]
            )

        hrx = response.content
        if not hrx:
            raise RuntimeError("Builder returned an empty HRX")
        expected = response.headers.get("X-Job-SHA256")
        if expected and expected.lower() != digest.lower():
            raise RuntimeError("Builder JOB hash does not match the stored JOB")

        model_filename = str(attempt_job["model"]["path"])
        hrx_digest = bytes_sha256(hrx)
        builder_version = response.headers.get("X-Builder-Version", "unknown")
        attempt_job.setdefault("metadata", {})["provenance"] = {
            "job_sha256": digest,
            "hrx_sha256": hrx_digest,
            "builder_version": builder_version,
            "package_protocol_version": PACKAGE_PROTOCOL_VERSION,
        }
        manifest = {
            "manifest_version": "1.0",
            "protocol_version": PACKAGE_PROTOCOL_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "attempt_id": attempt_id,
            "job_schema_version": str(attempt_job.get("schema_version")),
            "job_sha256": digest,
            "hrx_path": model_filename,
            "hrx_sha256": hrx_digest,
            "hrx_size_bytes": len(hrx),
            "builder_version": builder_version,
            "target_solver_version": attempt_job.get("metadata", {}).get("target_solver_version"),
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "job.json",
                json.dumps(attempt_job, indent=2, ensure_ascii=False).encode("utf-8"),
            )
            archive.writestr(model_filename, hrx)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(buffer.getvalue())
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return BuiltPackage(destination, digest, hrx_digest, len(hrx), builder_version)

    def purge_expired(self) -> int:
        removed = 0
        cutoff = time.time() - self.settings.package_cache_ttl_seconds
        for path in self.root.glob("*/*.zip"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

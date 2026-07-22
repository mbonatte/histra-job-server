from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status


@dataclass(frozen=True)
class SavedFile:
    relative_path: str
    filename: str
    size_bytes: int
    sha256: str
    content_type: str | None


class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Storage path escaped the storage root")
        return candidate

    def job_dir(self, job_id: str) -> Path:
        return self.resolve(f"jobs/{job_id}")

    def attempt_dir(self, job_id: str, attempt_id: str) -> Path:
        return self.resolve(f"jobs/{job_id}/attempts/{attempt_id}")

    async def save_upload(
        self,
        upload: UploadFile,
        relative_path: str,
        *,
        max_bytes: int,
        filename: str,
    ) -> SavedFile:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"{filename} exceeds the {max_bytes}-byte limit",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        finally:
            await upload.close()

        return SavedFile(
            relative_path=str(destination.relative_to(self.root).as_posix()),
            filename=filename,
            size_bytes=size,
            sha256=digest.hexdigest(),
            content_type=upload.content_type,
        )

    def write_json(self, relative_path: str, data: dict, *, filename: str) -> SavedFile:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".json-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return SavedFile(
            relative_path=str(destination.relative_to(self.root).as_posix()),
            filename=filename,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type="application/json",
        )

    def _build_zip(
        self,
        *,
        package: Path,
        manifest: Path,
        model: Path,
        model_filename: str,
    ) -> SavedFile:
        package.parent.mkdir(parents=True, exist_ok=True)
        temporary = package.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest, arcname="job.json")
            archive.write(model, arcname=model_filename)
        os.replace(temporary, package)
        payload = package.read_bytes()
        return SavedFile(
            relative_path=str(package.relative_to(self.root).as_posix()),
            filename="package.zip",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type="application/zip",
        )

    def build_package(self, job_id: str, model_filename: str) -> SavedFile:
        job_dir = self.job_dir(job_id)
        return self._build_zip(
            package=job_dir / "input" / "package.zip",
            manifest=job_dir / "input" / "job.json",
            model=job_dir / "input" / model_filename,
            model_filename=model_filename,
        )

    def build_attempt_package(
        self, job_id: str, attempt_id: str, model_filename: str
    ) -> SavedFile:
        job_dir = self.job_dir(job_id)
        attempt_dir = self.attempt_dir(job_id, attempt_id)
        return self._build_zip(
            package=attempt_dir / "input" / "package.zip",
            manifest=attempt_dir / "input" / "job.json",
            model=job_dir / "input" / model_filename,
            model_filename=model_filename,
        )

    def delete_job(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

from __future__ import annotations

import os
import time
from pathlib import Path


class PackageCache:
    """Regenerable TTL cache. It is never an authoritative artifact store."""
    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, attempt_id: str) -> Path:
        return self.root / f"{attempt_id}.zip"

    def put(self, attempt_id: str, data: bytes) -> Path:
        target = self.path_for(attempt_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        return target

    def get(self, attempt_id: str) -> bytes | None:
        path = self.path_for(attempt_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        if time.time() - stat.st_mtime > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return path.read_bytes()

    def delete(self, attempt_id: str) -> None:
        self.path_for(attempt_id).unlink(missing_ok=True)

    def prune(self) -> int:
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        for path in self.root.glob("*.zip"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

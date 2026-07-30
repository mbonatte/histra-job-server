from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _optional(value: str | None) -> str | None:
    return value if value else None


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./histra.db"
    template_root: Path = Path("./templates")
    package_cache_root: Path = Path("./.package-cache")
    api_token: str | None = None
    lease_seconds: int = 900
    package_ttl_seconds: int = 3600
    default_max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("HISTRA_DATABASE_URL", cls.database_url),
            template_root=Path(os.getenv("HISTRA_TEMPLATE_ROOT", "./templates")),
            package_cache_root=Path(
                os.getenv("HISTRA_PACKAGE_CACHE_ROOT", "./.package-cache")
            ),
            api_token=_optional(os.getenv("HISTRA_API_TOKEN")),
            lease_seconds=int(os.getenv("HISTRA_LEASE_SECONDS", "900")),
            package_ttl_seconds=int(os.getenv("HISTRA_PACKAGE_TTL_SECONDS", "3600")),
            default_max_attempts=int(os.getenv("HISTRA_DEFAULT_MAX_ATTEMPTS", "3")),
        )

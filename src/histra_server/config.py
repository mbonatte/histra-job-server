from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _optional(value: str | None) -> str | None:
    return value if value else None


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./histra.db"
    template_root: Path = Path("./templates")
    package_cache_root: Path = Path("./.package-cache")
    api_token: str | None = None
    lease_seconds: int = 900
    package_ttl_seconds: int = 3600
    default_max_attempts: int = 3
    dashboard_enabled: bool = True
    builder_ui_enabled: bool = True
    runner_online_seconds: int = 300
    max_hrx_upload_bytes: int = 100 * 1024 * 1024
    ui_refresh_seconds: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("HISTRA_DATABASE_URL", cls.database_url),
            template_root=Path(os.getenv("HISTRA_TEMPLATE_ROOT", "./templates")),
            package_cache_root=Path(os.getenv("HISTRA_PACKAGE_CACHE_ROOT", "./.package-cache")),
            api_token=_optional(os.getenv("HISTRA_API_TOKEN")),
            lease_seconds=_positive_int("HISTRA_LEASE_SECONDS", 900),
            package_ttl_seconds=_positive_int("HISTRA_PACKAGE_TTL_SECONDS", 3600),
            default_max_attempts=_positive_int("HISTRA_DEFAULT_MAX_ATTEMPTS", 3),
            dashboard_enabled=_bool("HISTRA_DASHBOARD_ENABLED", True),
            builder_ui_enabled=_bool("HISTRA_BUILDER_UI_ENABLED", True),
            runner_online_seconds=_positive_int("HISTRA_RUNNER_ONLINE_SECONDS", 300),
            max_hrx_upload_bytes=_positive_int("HISTRA_MAX_HRX_UPLOAD_BYTES", 100 * 1024 * 1024),
            ui_refresh_seconds=_positive_int("HISTRA_UI_REFRESH_SECONDS", 5),
        )

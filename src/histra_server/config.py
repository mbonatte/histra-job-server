from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "HiStrA Job Server"
    app_version: str = "0.1.5"
    database_url: str = "sqlite:///./histra-server.sqlite3"
    storage_root: Path = Path("./data")
    lease_seconds: int = Field(default=300, ge=30, le=86_400)
    lease_reaper_interval_seconds: int = Field(default=30, ge=0, le=3600)
    max_job_json_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    max_model_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_result_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_log_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    sql_echo: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

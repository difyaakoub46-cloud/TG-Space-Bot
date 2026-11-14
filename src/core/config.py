"""Typed application settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    webhook_url: str = ""

    supabase_url: str
    supabase_key: str
    supabase_table: str = "files"

    request_timeout: float = 30.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)


def load_settings(path: Path | None = None) -> Settings:
    return Settings(_env_file=path or Path(".env"))

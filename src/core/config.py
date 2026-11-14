"""Application settings loaded from environment variables / ``.env``."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.filetypes import FileCategory


class Settings(BaseSettings):
    """Strongly typed runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────
    bot_token: SecretStr

    # ── Supabase (PostgREST) ────────────────────────
    supabase_url: str
    supabase_key: SecretStr
    supabase_table: str = "files"

    # ── Telegram storage backends ───────────────────
    storage_chat_documents: int
    storage_chat_media: int
    storage_chat_audio: int
    storage_chat_archives: int
    storage_chat_default: int

    # ── Access & behaviour ──────────────────────────
    allowed_user_ids: str = ""
    page_size: int = Field(default=6, ge=1, le=20)
    request_timeout: float = Field(default=15.0, gt=0)
    log_level: str = "INFO"

    @field_validator("supabase_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("SUPABASE_URL must start with http:// or https://")
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_level(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def allowed_users(self) -> frozenset[int]:
        """Parsed allowlist; an empty set means "everyone is allowed"."""
        ids: set[int] = set()
        for raw in self.allowed_user_ids.replace(" ", "").split(","):
            if not raw:
                continue
            try:
                ids.add(int(raw))
            except ValueError:
                continue
        return frozenset(ids)

    def is_user_allowed(self, user_id: int | None) -> bool:
        if not self.allowed_users:
            return True
        return user_id is not None and user_id in self.allowed_users

    def storage_chats(self) -> dict[FileCategory, int]:
        """Map every file category to its dedicated Telegram storage chat."""
        return {
            FileCategory.DOCUMENT: self.storage_chat_documents,
            FileCategory.IMAGE: self.storage_chat_media,
            FileCategory.VIDEO: self.storage_chat_media,
            FileCategory.AUDIO: self.storage_chat_audio,
            FileCategory.ARCHIVE: self.storage_chat_archives,
            FileCategory.OTHER: self.storage_chat_default,
        }

    def storage_chat_for(self, category: FileCategory) -> int:
        return self.storage_chats()[category]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()  # type: ignore[call-arg]

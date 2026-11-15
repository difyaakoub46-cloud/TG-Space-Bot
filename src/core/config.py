"""Typed application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.filetypes import FileCategory

DEFAULT_WEB_PORT = 8000


def _blank_to_none(value: object) -> object:
    """Treat empty values (common in .env files) as "not configured"."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if value in (0, "0"):
        return None
    return value


OptionalChatId = Annotated[int | None, BeforeValidator(_blank_to_none)]


class ConfigurationError(RuntimeError):
    """Raised when a required runtime setting is missing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str = Field(
        validation_alias=AliasChoices("TG_SPACE_BOT_TOKEN", "BOT_TOKEN")
    )
    webhook_url: str = Field(
        "",
        validation_alias=AliasChoices("TG_SPACE_BOT_WEBHOOK_URL", "WEBHOOK_URL"),
    )

    supabase_url: str = Field(validation_alias=AliasChoices("SUPABASE_URL"))
    supabase_key: SecretStr = Field(
        validation_alias=AliasChoices(
            "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_KEY"
        )
    )
    supabase_table: str = Field(
        "files", validation_alias=AliasChoices("SUPABASE_TABLE")
    )

    storage_chat_document: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_DOCUMENT")
    )
    storage_chat_image: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_IMAGE")
    )
    storage_chat_video: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_VIDEO")
    )
    storage_chat_audio: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_AUDIO")
    )
    storage_chat_archive: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_ARCHIVE")
    )
    storage_chat_other: OptionalChatId = Field(
        None, validation_alias=AliasChoices("TG_STORAGE_CHAT_OTHER")
    )

    request_timeout: float = 30.0

    server_host: str = Field(
        "0.0.0.0", validation_alias=AliasChoices("TG_SPACE_BOT_HOST", "HOST")
    )
    server_port: int = Field(
        DEFAULT_WEB_PORT,
        validation_alias=AliasChoices("PORT", "TG_SPACE_BOT_PORT"),
    )

    def storage_chats(self) -> dict[FileCategory, int]:
        """Configured storage group per category (missing categories are omitted)."""
        mapping: dict[FileCategory, int | None] = {
            FileCategory.document: self.storage_chat_document,
            FileCategory.image: self.storage_chat_image,
            FileCategory.video: self.storage_chat_video,
            FileCategory.audio: self.storage_chat_audio,
            FileCategory.archive: self.storage_chat_archive,
            FileCategory.other: self.storage_chat_other,
        }
        return {
            category: chat_id
            for category, chat_id in mapping.items()
            if chat_id is not None
        }

    def storage_chat_for(self, category: FileCategory) -> int:
        chat_id = self.storage_chats().get(category)
        if chat_id is None:
            raise ConfigurationError(
                f"لا توجد مجموعة تخزين مهيأة للفئة «{category.value}». "
                "أضف المتغير TG_STORAGE_CHAT_<CATEGORY> إلى إعدادات البيئة."
            )
        return chat_id


def load_settings(path: Path | None = None) -> Settings:
    return Settings(_env_file=path or Path(".env"))

"""Typed application settings."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.filetypes import FileCategory

DEFAULT_WEB_PORT = 8000
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 20

logger = logging.getLogger(__name__)


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
OptionalStr = Annotated[str | None, BeforeValidator(_blank_to_none)]


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
        "", validation_alias=AliasChoices("TG_SPACE_BOT_WEBHOOK_URL", "WEBHOOK_URL")
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
        None,
        validation_alias=AliasChoices(
            "TG_STORAGE_CHAT_DOCUMENT", "TG_STORAGE_CHAT_DOCUMENTS"
        ),
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
        None,
        validation_alias=AliasChoices(
            "TG_STORAGE_CHAT_ARCHIVE", "TG_STORAGE_CHAT_ARCHIVES"
        ),
    )
    storage_chat_other: OptionalChatId = Field(
        None,
        validation_alias=AliasChoices(
            "TG_STORAGE_CHAT_OTHER", "TG_STORAGE_CHAT_DEFAULT"
        ),
    )

    # The emergency group: any file whose registration in Supabase fails is copied
    # here instead of the library, and «🔧 إصلاح» finishes it later. It never
    # replaces a TG_STORAGE_CHAT_* value.
    storage_chat_fallback: OptionalChatId = Field(
        None,
        validation_alias=AliasChoices(
            "TG_STORAGE_CHAT_FALLBACK",
            "TG_STORAGE_CHAT_FALLBACK_ID",
            "FALLBACK_STORAGE_CHAT",
        ),
    )

    # Optional extra id for the storage-group gate (see the access router); the
    # gate itself is driven by ALLOWED_USER_IDS alone, with no password step.
    owner_id: OptionalChatId = Field(
        None,
        validation_alias=AliasChoices("OWNER_ID", "OWNER_TELEGRAM_ID", "TG_OWNER_ID"),
    )

    # Guard for the storage groups: invite links become join requests that the bot
    # approves only for the allowed ids, and anybody else who gets in is removed.
    join_guard: bool = Field(
        True, validation_alias=AliasChoices("JOIN_GUARD", "JOIN_APPROVAL")
    )
    # How long an invite link stays valid. Keep it short: the link itself is the
    # secret, so a leaked link must die quickly on its own.
    join_link_ttl_minutes: int = Field(
        10, validation_alias=AliasChoices("JOIN_LINK_TTL_MINUTES", "JOIN_LINK_TTL")
    )

    request_timeout: float = 30.0
    # Files larger than this are saved without a content fingerprint (Telegram
    # does not let a bot download more than 20 MB anyway).
    hash_max_bytes: int = Field(
        20 * 1024 * 1024, validation_alias=AliasChoices("HASH_MAX_BYTES")
    )
    log_level: str = Field("INFO", validation_alias=AliasChoices("LOG_LEVEL"))
    page_size: int = Field(
        DEFAULT_PAGE_SIZE, validation_alias=AliasChoices("PAGE_SIZE")
    )
    allowed_user_ids_raw: str = Field(
        "", validation_alias=AliasChoices("ALLOWED_USER_IDS")
    )

    def allowed_users(self) -> frozenset[int]:
        """Telegram user ids allowed to use the bot (empty = everybody)."""
        ids: set[int] = set()
        for part in re.split(r"[,\s]+", self.allowed_user_ids_raw.strip()):
            if not part:
                continue
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("Ignoring invalid ALLOWED_USER_IDS entry: %r", part)
        return frozenset(ids)

    def results_per_page(self) -> int:
        return max(1, min(self.page_size, MAX_PAGE_SIZE))

    def join_guard_enabled(self) -> bool:
        """Whether invite links require the bot's approval and strangers are removed."""
        return bool(self.join_guard)

    def join_link_ttl(self) -> int:
        """Invite-link lifetime in seconds, kept between one minute and one day."""
        try:
            minutes = int(self.join_link_ttl_minutes)
        except (TypeError, ValueError):
            minutes = 10
        return max(1, min(minutes, 1440)) * 60

    # Where the emergency registry lives. Point it at a persistent disk so the
    # list of held files survives a restart.
    pending_store_path: str = Field(
        "data/pending_files.json",
        validation_alias=AliasChoices("PENDING_STORE_PATH", "PENDING_PATH"),
    )

    # Where each user's language is remembered. English is the default, so the
    # file may be missing, empty or unreadable without breaking anything.
    preferences_path: str = Field(
        "data/user_preferences.json",
        validation_alias=AliasChoices("PREFERENCES_PATH", "USER_PREFERENCES_PATH"),
    )

    server_host: str = Field(
        "0.0.0.0", validation_alias=AliasChoices("TG_SPACE_BOT_HOST", "HOST")
    )
    server_port: int = Field(
        DEFAULT_WEB_PORT, validation_alias=AliasChoices("PORT", "TG_SPACE_BOT_PORT")
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
        """The group of one category.

        The fallback group is not a substitute: it only holds files whose
        registration failed, so a category without a group of its own is refused.
        """
        chat_id = self.storage_chats().get(category)
        if chat_id is None:
            raise ConfigurationError(
                f"لا توجد مجموعة تخزين مهيأة للفئة «{category.value}». "
                "أضف المتغير TG_STORAGE_CHAT_<CATEGORY> إلى إعدادات البيئة "
                "(المجموعة الاحتياطية لا تحل محل مجموعة الفئة)."
            )
        return chat_id

    def uncovered_categories(self) -> list[FileCategory]:
        """Categories with no storage group of their own (the fallback does not
        count: it is an emergency store, not a library group)."""
        configured = self.storage_chats()
        return [category for category in FileCategory if category not in configured]


def load_settings(path: Path | None = None) -> Settings:
    return Settings(_env_file=path or Path(".env"))

"""Dependency container for the bot."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot

from src.core.config import Settings
from src.db.repository import FileRepository
from src.services.files import FileStorageServices
from src.services.storage import StorageService


class Services:
    """Single composition root shared by the middleware and every handler."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = FileRepository(settings)
        self.files = FileStorageServices(settings, self.repository)
        self.storage: StorageService | None = None
        self.bot: Bot | None = None
        self.started_at = datetime.now(timezone.utc)

    def bind_bot(self, bot: Bot) -> None:
        """Attach the Telegram bot once it exists (needed by the storage backend)."""
        self.bot = bot
        self.storage = StorageService(bot, self.settings)
        self.files.bind_storage(self.storage)
        self.files.bind_bot(bot)

    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


def build_services(settings: Settings) -> Services:
    return Services(settings)

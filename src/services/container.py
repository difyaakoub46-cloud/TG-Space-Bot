"""Dependency container for the bot."""

from __future__ import annotations

from src.core.config import Settings
from src.db.repository import FileRepository
from src.services.storage import FileStorageServices


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.files = FileStorageServices(settings)


def build_services(settings: Settings) -> Services:
    return Services(settings)

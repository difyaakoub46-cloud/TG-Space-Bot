"""File operations service."""

from __future__ import annotations

from src.core.config import Settings
from src.db.repository import FileRepository
from src.services.storage import FileStorageServices


def create_services(settings: Settings) -> tuple[FileRepository, FileStorageServices]:
    repository = FileRepository(settings)
    storage = FileStorageServices(settings)
    return repository, storage

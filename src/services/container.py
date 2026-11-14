"""Dependency container shared by handlers."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.config import Settings
from src.db.repository import FileRepository
from src.services.files import FileService
from src.services.storage import StorageService


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    repository: FileRepository
    storage: StorageService
    files: FileService

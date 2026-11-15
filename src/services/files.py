"""File operations service: storage, metadata and library queries."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import Message

from src.core.config import Settings
from src.db.models import StoredFile
from src.db.repository import FileRepository
from src.services.storage import StorageService
from src.utils.filetypes import FileCandidate, FileCategory, parse_category

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 100


class FileStorageServices:
    """High level operations shared by the upload, browse and search routers."""

    def __init__(
        self,
        settings: Settings,
        repository: FileRepository,
        storage: StorageService | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._storage = storage

    def bind_storage(self, storage: StorageService) -> None:
        self._storage = storage

    @property
    def storage(self) -> StorageService:
        if self._storage is None:
            raise RuntimeError("StorageService is not bound to a bot yet")
        return self._storage

    async def save_from_message(
        self, message: Message, candidate: FileCandidate
    ) -> StoredFile:
        """Copy the file into its category group and persist its metadata."""
        storage = self.storage
        chat_id, message_id = await storage.upload(message, candidate)

        uploader_id = message.from_user.id if message.from_user else None
        record = await self._repository.create(
            file_id=candidate.file_id,
            file_unique_id=candidate.file_unique_id,
            file_name=candidate.file_name,
            file_type=candidate.mime_type,
            category=candidate.category.value,
            file_size=candidate.file_size,
            user_id=uploader_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        logger.info(
            "Stored %s (%s) as %s/%s",
            record.file_name,
            record.category.value,
            record.storage_chat_id,
            record.storage_message_id,
        )
        return record

    async def get(self, file_id: str) -> StoredFile | None:
        return await self._repository.get(file_id)

    async def search(
        self,
        query: str | None = None,
        *,
        category: FileCategory | str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[StoredFile]:
        parsed_category = (
            parse_category(category) if category is not None else None
        )
        results, _total = await self._repository.search(
            query=query, category=parsed_category, limit=limit
        )
        return results

    async def by_category(
        self, category: FileCategory | str, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[StoredFile]:
        return await self.search(category=category, limit=limit)

    async def category_counts(self) -> dict[FileCategory, int]:
        return await self._repository.category_counts()

    async def stats(self) -> dict[str, Any]:
        counts = await self.category_counts()
        total_bytes = await self._repository.total_size()
        return {
            "total": sum(counts.values()),
            "total_bytes": total_bytes or 0,
            "categories": counts,
        }

    async def deliver(self, record: StoredFile, target_chat_id: int) -> int:
        """Send the stored copy back to a user and return the new message id."""
        _chat_id, message_id = await self.storage.deliver(
            record.storage_chat_id, record.storage_message_id, target_chat_id
        )
        return message_id

    async def delete(self, file_id: str) -> bool:
        """Delete the stored Telegram copy and its metadata row."""
        record = await self._repository.delete(file_id)
        if record is None:
            return False
        await self.storage.remove(record.storage_chat_id, record.storage_message_id)
        return True


__all__ = ["FileStorageServices"]

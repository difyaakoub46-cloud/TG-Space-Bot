"""File domain service: orchestrates Supabase metadata and Telegram storage."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

from aiogram.types import Message

from src.core.config import Settings
from src.db.models import StoredFile
from src.db.repository import FileRepository
from src.services.storage import StorageService
from src.utils.filetypes import FileCandidate, FileCategory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FilePage:
    """One page of search/browse results."""

    items: list[StoredFile]
    total: int
    page: int
    pages: int
    page_size: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


@dataclass(frozen=True, slots=True)
class LibraryStats:
    counts: dict[str, int]
    total: int
    total_bytes: int | None


class FileService:
    """High level file operations used by the handlers."""

    def __init__(self, repository: FileRepository, storage: StorageService, settings: Settings) -> None:
        self._repository = repository
        self._storage = storage
        self._settings = settings

    async def save_from_message(self, message: Message, candidate: FileCandidate) -> StoredFile:
        """Store the file in Telegram, then persist its metadata in Supabase."""
        storage_chat_id, storage_message_id = await self._storage.upload(message, candidate)
        try:
            record = await self._repository.create(
                file_id=candidate.file_id,
                file_unique_id=candidate.file_unique_id,
                file_name=candidate.file_name,
                category=candidate.category.value,
                mime_type=candidate.mime_type,
                size_bytes=candidate.size_bytes,
                uploader_id=message.from_user.id if message.from_user else None,
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message_id,
            )
        except Exception:
            logger.exception("Saving metadata failed, rolling back the stored copy")
            try:
                await self._storage.remove(storage_chat_id, storage_message_id)
            except Exception:  # noqa: BLE001 - rollback is best effort
                logger.exception("Rollback of storage message failed")
            raise
        return record

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        page: int = 1,
    ) -> FilePage:
        page_size = self._settings.page_size
        page = max(1, page)
        items, total = await self._repository.search(
            query=query,
            category=category,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        pages = max(1, math.ceil(total / page_size))
        if page > pages:
            # The requested page vanished (e.g. files were deleted): fall back to the last one.
            page = pages
            items, total = await self._repository.search(
                query=query,
                category=category,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        return FilePage(
            items=items,
            total=total,
            page=page,
            pages=pages,
            page_size=page_size,
        )

    async def get(self, file_id: str) -> StoredFile | None:
        return await self._repository.get(file_id)

    async def send_to_user(self, record: StoredFile, chat_id: int) -> None:
        await self._storage.deliver(
            record.storage_chat_id, record.storage_message_id, chat_id
        )

    async def delete(self, file_id: str) -> StoredFile:
        """Remove both the Telegram copy and the Supabase row."""
        record = await self._repository.get(file_id)
        if record is None:
            raise LookupError(file_id)

        try:
            await self._storage.remove(record.storage_chat_id, record.storage_message_id)
        except Exception:  # noqa: BLE001 - metadata removal must still proceed
            logger.exception("Could not delete the Telegram copy of %s", file_id)

        deleted = await self._repository.delete(file_id)
        return deleted or record

    async def category_counts(self) -> dict[str, int]:
        return await self._repository.category_counts()

    async def stats(self) -> LibraryStats:
        counts, total, total_bytes = await asyncio.gather(
            self._repository.category_counts(),
            self._repository.count(),
            self._repository.total_size(),
        )
        return LibraryStats(counts=counts, total=total, total_bytes=total_bytes)

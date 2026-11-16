"""File operations service: storage, metadata and library queries."""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.db.repository import FileRepository, SupabaseError
from src.services.storage import StorageService
from src.utils.filetypes import FileCandidate, FileCategory, parse_category
from src.utils.sorting import SortSpec, database_order, sort_files

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 100
# The cap of a category listing; the ordering is applied on the fetched rows.
CATEGORY_FETCH_LIMIT = 300


def _is_duplicate(exc: SupabaseError) -> bool:
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


class DuplicateFileError(Exception):
    """Raised when the exact same Telegram file is already in the library."""

    def __init__(self, record: StoredFile) -> None:
        super().__init__(f"الملف موجود أصلاً: {record.file_name}")
        self.record = record


class FileStorageServices:
    """High level operations shared by the upload, browse and search routers."""

    def __init__(
        self,
        settings: Settings,
        repository: FileRepository,
        storage: StorageService | None = None,
        bot: Bot | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._storage = storage
        self._bot = bot

    def bind_bot(self, bot: Bot) -> None:
        """The bot is needed to read a file's bytes when fingerprinting it."""
        self._bot = bot

    def bind_storage(self, storage: StorageService) -> None:
        self._storage = storage

    @property
    def storage(self) -> StorageService:
        if self._storage is None:
            raise RuntimeError("StorageService is not bound to a bot yet")
        return self._storage

    async def content_hash(self, candidate: FileCandidate) -> str | None:
        """SHA-256 of the file bytes, or ``None`` when they cannot be read.

        Telegram refuses downloads above 20 MB, so the fingerprint is best effort:
        a missing hash never blocks an upload, it only leaves the file without it.
        """
        if self._bot is None or not candidate.file_id:
            return None
        if candidate.file_size and candidate.file_size > self._settings.hash_max_bytes:
            logger.info(
                "%s is larger than the hashing limit; stored without a fingerprint",
                candidate.file_name,
            )
            return None

        buffer = BytesIO()
        try:
            await self._bot.download(candidate.file_id, destination=buffer)
        except (TelegramAPIError, OSError):
            logger.warning(
                "Could not read %s to fingerprint it",
                candidate.file_name,
                exc_info=True,
            )
            return None
        data = buffer.getvalue()
        return hashlib.sha256(data).hexdigest() if data else None

    async def save_from_message(
        self, message: Message, candidate: FileCandidate
    ) -> StoredFile:
        """Copy the file into its storage group and persist its metadata.

        The same Telegram file is never stored twice, thanks to the unique
        ``file_unique_id`` column; a different file with the same name is stored
        and told apart by its content fingerprint.
        """
        existing = await self._repository.find_by_unique_id(
            candidate.file_unique_id or ""
        )
        if existing is not None:
            raise DuplicateFileError(existing)

        fingerprint = await self.content_hash(candidate)

        storage = self.storage
        chat_id, message_id = await storage.upload(message, candidate)

        uploader_id = message.from_user.id if message.from_user else None
        try:
            record = await self._repository.create(
                file_id=candidate.file_id,
                file_unique_id=candidate.file_unique_id,
                file_name=candidate.file_name,
                file_type=candidate.mime_type,
                category=candidate.category.value,
                file_size=candidate.file_size,
                content_hash=fingerprint,
                user_id=uploader_id,
                chat_id=chat_id,
                message_id=message_id,
            )
        except SupabaseError as exc:
            if not _is_duplicate(exc):
                raise
            # The unique index caught a concurrent copy of the same file: undo the
            # extra copy we just made and report the file already on record.
            await storage.remove(chat_id, message_id)
            duplicate = await self._repository.find_by_unique_id(
                candidate.file_unique_id or ""
            )
            if duplicate is None:
                raise
            raise DuplicateFileError(duplicate) from exc

        logger.info(
            "Stored %s (%s) as %s/%s%s",
            record.file_name,
            record.category.value,
            record.storage_chat_id,
            record.storage_message_id,
            f" hash={record.content_hash[:12]}" if record.content_hash else "",
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
        self,
        category: FileCategory | str,
        sort: SortSpec | None = None,
        limit: int = CATEGORY_FETCH_LIMIT,
    ) -> list[StoredFile]:
        """Files of one category, ordered by the requested sort."""
        spec = sort or SortSpec()
        results, _total = await self._repository.search(
            category=parse_category(category),
            limit=limit,
            order=database_order(spec),
        )
        return sort_files(results, spec)

    async def category_counts(self) -> dict[FileCategory, int]:
        return await self._repository.category_counts()

    async def category_totals(self) -> dict[FileCategory, tuple[int, int]]:
        """Files and bytes per category (one round trip, thanks to the view)."""
        return await self._repository.category_totals()

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

    async def rename(self, file_id: str, name: str) -> StoredFile | None:
        """Rename a file in the library (the Telegram copy keeps its own name)."""
        return await self._repository.rename(file_id, name)

    async def folders(self, user_id: int) -> list[Folder]:
        """Folders of one user, always in case-insensitive alphabetical order."""
        return await self._repository.list_folders(user_id)

    async def folder(self, folder_id: str, user_id: int) -> Folder | None:
        return await self._repository.get_folder(folder_id, user_id)

    async def favorite_folders(self, user_id: int) -> list[Folder]:
        """The shortcuts of the library screen, in their manual order.

        A favourite without an explicit position keeps the alphabetical fallback,
        so folders starred before the ordering existed still show up.
        """
        folders = [
            folder for folder in await self.folders(user_id) if folder.favorite
        ]
        return sorted(folders, key=lambda folder: folder.favorite_sort_key)

    async def set_favorite(
        self, folder_id: str, user_id: int, favorite: bool
    ) -> Folder | None:
        """Star a folder at the end of the favourites, or unstar it."""
        order: int | None = None
        if favorite:
            favorites = await self.favorite_folders(user_id)
            order = max(
                (folder.favorite_order or 0 for folder in favorites), default=-1
            ) + 1

        await self._repository.set_folder_favorite(
            folder_id, user_id, favorite, order
        )
        return await self._repository.get_folder(folder_id, user_id)

    async def move_favorite(
        self, folder_id: str, user_id: int, step: int
    ) -> Folder | None:
        """Move a favourite one place up (``step=-1``) or down (``step=1``)."""
        favorites = await self.favorite_folders(user_id)
        ids = [folder.id for folder in favorites]
        if folder_id not in ids:
            return None

        index = ids.index(folder_id)
        target = max(0, min(index + step, len(favorites) - 1))
        if target == index:
            return await self._repository.get_folder(folder_id, user_id)

        favorites[index], favorites[target] = favorites[target], favorites[index]
        for position, folder in enumerate(favorites):
            await self._repository.set_folder_order(folder.id, user_id, position)
        return await self._repository.get_folder(folder_id, user_id)

    async def create_folder(self, user_id: int, name: str) -> Folder:
        return await self._repository.create_folder(user_id, name)

    async def folder_files(
        self, folder_id: str, sort: SortSpec | None = None
    ) -> list[StoredFile]:
        """Files of one folder, ordered by the requested sort."""
        files = await self._repository.files_in_folder(folder_id)
        return sort_files(files, sort or SortSpec())

    async def delete_folder(self, folder_id: str, user_id: int) -> bool:
        """Remove the folder label only: its files are kept in the library."""
        return await self._repository.delete_folder(folder_id, user_id)

    async def add_to_folder(self, folder_id: str, file_id: str) -> None:
        await self._repository.add_to_folder(folder_id, file_id)

    async def file_folders(self, file_id: str) -> list[str]:
        return await self._repository.folders_of_file(file_id)

    async def delete(self, file_id: str) -> bool:
        """Delete the stored Telegram copy and its metadata row."""
        record = await self._repository.delete(file_id)
        if record is None:
            return False
        await self.storage.remove(record.storage_chat_id, record.storage_message_id)
        return True


__all__ = ["DuplicateFileError", "FileStorageServices"]

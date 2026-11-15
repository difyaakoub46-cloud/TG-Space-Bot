"""Telegram group storage backend: upload, deliver and delete stored copies."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramMigrateToChat,
)
from aiogram.types import Message

from src.core.config import Settings
from src.utils.filetypes import FileCandidate
from src.utils.telegram import call_with_retry

logger = logging.getLogger(__name__)

_MISSING_MESSAGE_MARKERS = (
    "message to delete not found",
    "message can't be deleted",
    "message_id_invalid",
)


class StorageService:
    """Moves bytes between the user's private chat and the storage groups."""

    def __init__(self, bot: Bot, settings: Settings) -> None:
        self._bot = bot
        self._settings = settings
        # Old basic-group id -> supergroup id, learned from Telegram on first use.
        # Telegram upgrades groups to supergroups on its own and then rejects the
        # previous id, so the mapping is followed instead of failing every upload.
        self._migrated: dict[int, int] = {}

    @property
    def bot(self) -> Bot:
        return self._bot

    def _current(self, chat_id: int) -> int:
        return self._migrated.get(chat_id, chat_id)

    async def _call(self, method, chat_id: int, **kwargs: Any) -> tuple[Any, int]:
        """Run a chat method, following a group-to-supergroup migration once."""
        target = self._current(chat_id)
        try:
            return await call_with_retry(method, chat_id=target, **kwargs), target
        except TelegramMigrateToChat as exc:
            new_id = exc.migrate_to_chat_id
            logger.warning(
                "Storage chat %s was upgraded to the supergroup %s; using the new "
                "id from now on. Update TG_STORAGE_CHAT_* in the environment to "
                "make it permanent.",
                target,
                new_id,
            )
            self._migrated[chat_id] = new_id
            self._migrated[target] = new_id
            result = await call_with_retry(method, chat_id=new_id, **kwargs)
            return result, new_id

    async def upload(
        self, message: Message, candidate: FileCandidate
    ) -> tuple[int, int]:
        """Copy a user's file into its category group; returns ``(chat_id, message_id)``."""
        target_chat_id = self._settings.storage_chat_for(candidate.category)
        stored, used_chat_id = await self._call(
            self._bot.copy_message,
            target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        return used_chat_id, stored.message_id

    async def deliver(
        self, storage_chat_id: int, storage_message_id: int, target_chat_id: int
    ) -> tuple[int, int]:
        """Send the stored copy back to a user without downloading anything."""
        stored, used_chat_id = await self._call(
            self._bot.copy_message,
            target_chat_id,
            from_chat_id=self._current(storage_chat_id),
            message_id=storage_message_id,
        )
        return used_chat_id, stored.message_id

    async def remove(self, storage_chat_id: int, storage_message_id: int) -> bool:
        """Delete the stored copy. Returns ``False`` when it was already gone."""
        try:
            await self._call(
                self._bot.delete_message,
                storage_chat_id,
                message_id=storage_message_id,
            )
            return True
        except TelegramBadRequest as exc:
            if any(marker in str(exc).lower() for marker in _MISSING_MESSAGE_MARKERS):
                logger.warning(
                    "Storage message %s/%s is already missing",
                    storage_chat_id,
                    storage_message_id,
                )
                return False
            raise

    async def verify_chats(self) -> list[int]:
        """Check that the bot can reach every configured storage group."""
        unreachable: list[int] = []
        for chat_id in self._unique_chats():
            try:
                chat = await self._bot.get_chat(self._current(chat_id))
            except TelegramAPIError as exc:
                logger.error(
                    "Storage chat %s is unreachable (%s). Add the bot as an admin.",
                    chat_id,
                    exc,
                )
                unreachable.append(chat_id)
            else:
                logger.info(
                    "Storage chat ready: %s (%s)", chat_id, chat.title or chat.type
                )
        return unreachable

    def _unique_chats(self) -> Iterable[int]:
        return dict.fromkeys(self._settings.storage_chats().values())

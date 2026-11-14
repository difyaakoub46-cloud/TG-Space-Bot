"""Telegram group storage backend: upload, deliver and delete stored copies."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
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

    async def upload(self, message: Message, candidate: FileCandidate) -> tuple[int, int]:
        """Copy a user's file into its category group; returns ``(chat_id, message_id)``."""
        target_chat_id = self._settings.storage_chat_for(candidate.category)
        # chat_id_out is captured so we keep the (possibly migrated) destination id.
        chat_id_out: list[int] = [target_chat_id]
        stored = await call_with_retry(
            self._bot.copy_message,
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            chat_id_out=chat_id_out,
        )
        # aiogram returns a MessageId here, not a full Message, so we cannot read
        # ``stored.chat``. We rely on the destination chat id that was actually used.
        return chat_id_out[0], stored.message_id

    async def deliver(
        self, storage_chat_id: int, storage_message_id: int, target_chat_id: int
    ) -> tuple[int, int]:
        """Send the stored copy back to a user without downloading anything.

        Returns ``(chat_id, message_id)`` of the delivered copy. aiogram returns a
        ``MessageId`` (not a full ``Message``) from ``copy_message``, so we can't read
        ``.chat`` and instead report the destination chat id that was actually used.
        """
        chat_id_out: list[int] = [target_chat_id]
        stored = await call_with_retry(
            self._bot.copy_message,
            chat_id=target_chat_id,
            from_chat_id=storage_chat_id,
            message_id=storage_message_id,
            chat_id_out=chat_id_out,
        )
        return chat_id_out[0], stored.message_id

    async def remove(self, storage_chat_id: int, storage_message_id: int) -> bool:
        """Delete the stored copy. Returns ``False`` when it was already gone."""
        try:
            await call_with_retry(
                self._bot.delete_message,
                chat_id=storage_chat_id,
                message_id=storage_message_id,
            )
            return True
        except TelegramBadRequest as exc:
            if any(marker in str(exc).lower() for marker in _MISSING_MESSAGE_MARKERS):
                logger.warning(
                    "Storage message %s/%s is already missing", storage_chat_id, storage_message_id
                )
                return False
            raise

    async def verify_chats(self) -> list[int]:
        """Check that the bot can reach every configured storage group."""
        unreachable: list[int] = []
        for chat_id in self._unique_chats():
            try:
                chat = await self._bot.get_chat(chat_id)
            except TelegramAPIError as exc:
                logger.error(
                    "Storage chat %s is unreachable (%s). Add the bot as an admin.", chat_id, exc
                )
                unreachable.append(chat_id)
            else:
                logger.info("Storage chat ready: %s (%s)", chat_id, chat.title or chat.type)
        return unreachable

    def _unique_chats(self) -> Iterable[int]:
        return dict.fromkeys(self._settings.storage_chats().values())

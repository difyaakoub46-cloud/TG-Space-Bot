"""Telegram group storage backend: upload, deliver and delete stored copies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramMigrateToChat,
)
from aiogram.types import Message

from src.core.config import ConfigurationError, Settings
from src.utils.filetypes import FileCandidate, FileCategory
from src.utils.telegram import call_with_retry

logger = logging.getLogger(__name__)


# The rights the bot must hold in every storage group, named as Telegram names
# them. None is cosmetic: without ``can_invite_users`` no join request is sent at
# all, without ``can_restrict_members`` an unlisted account stays in the group, and
# without any right it grants the owner the promotion is refused as a whole.
REQUIRED_RIGHTS: tuple[tuple[str, str, str], ...] = (
    ("can_invite_users", "Invite users via link", "دعوة المستخدمين عبر رابط"),
    ("can_restrict_members", "Ban users", "حظر الأعضاء"),
    ("can_delete_messages", "Delete messages", "حذف الرسائل"),
    ("can_promote_members", "Add new admins", "إضافة مشرفين"),
    ("can_change_info", "Change group info", "تغيير معلومات المجموعة"),
    ("can_pin_messages", "Pin messages", "تثبيت الرسائل"),
    ("can_manage_video_chats", "Manage video chats", "إدارة محادثات الفيديو"),
)

ALL_RIGHT_NAMES: tuple[str, ...] = tuple(name for name, _en, _ar in REQUIRED_RIGHTS)


@dataclass(frozen=True, slots=True)
class StorageChatCheck:
    """Result of reaching one storage group at startup."""

    chat_id: int
    categories: frozenset[FileCategory]
    reachable: bool
    title: str | None = None
    reason: str | None = None
    is_fallback: bool = False
    # None means the rights were never read (an unreachable group).
    is_admin: bool | None = None
    missing_rights: tuple[str, ...] = ()


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

    async def hold(self, message: Message) -> tuple[int, int]:
        """Copy a file into the emergency group; returns ``(chat_id, message_id)``.

        Used when the registration in Supabase fails: the copy must not stay in
        the library, so the file is held here until «🔧 إصلاح» can register it.
        """
        fallback = self._settings.storage_chat_fallback
        if fallback is None:
            raise ConfigurationError(
                "لا توجد مجموعة احتياطية مهيّأة (TG_STORAGE_CHAT_FALLBACK) "
                "لحفظ الملفات التي يفشل تسجيلها."
            )
        stored, used_chat_id = await self._call(
            self._bot.copy_message,
            int(fallback),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        return used_chat_id, stored.message_id

    async def move(
        self, storage_chat_id: int, storage_message_id: int, target_chat_id: int
    ) -> tuple[int, int]:
        """Copy a held file into its real group; the caller removes the old copy."""
        stored, used_chat_id = await self._call(
            self._bot.copy_message,
            target_chat_id,
            from_chat_id=self._current(storage_chat_id),
            message_id=storage_message_id,
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

    async def check_chats(self) -> list[StorageChatCheck]:
        """Reach every configured storage group, one entry per unique chat id.

        The report is built here so the owner learns about a wrong id or a missing
        bot membership at startup instead of at the first upload.
        """
        grouped: dict[int, set[FileCategory]] = {}
        for category, chat_id in self._settings.storage_chats().items():
            grouped.setdefault(self._current(int(chat_id)), set()).add(category)

        # The fallback group holds files whose registration failed, so it is
        # checked too (an empty category set marks it as the emergency group).
        fallback = self._settings.storage_chat_fallback
        fallback_id = self._current(int(fallback)) if fallback is not None else None
        if fallback_id is not None and fallback_id not in grouped:
            grouped[fallback_id] = set()

        checks: list[StorageChatCheck] = []
        for chat_id, categories in grouped.items():
            try:
                chat = await self._bot.get_chat(chat_id)
            except TelegramAPIError as exc:
                # ``str(exc)`` of an aiogram API error is prefixed with
                # "Telegram server says"; the owner only needs the cause itself.
                detail = getattr(exc, "message", None) or str(exc)
                reason = " ".join(str(detail).split())[:200] or type(exc).__name__
                logger.error(
                    "Storage chat %s is unreachable (%s). Add the bot as an admin "
                    "or fix the id in the environment.",
                    chat_id,
                    reason,
                )
                checks.append(
                    StorageChatCheck(
                        chat_id=chat_id,
                        categories=frozenset(categories),
                        reachable=False,
                        reason=reason,
                        is_fallback=chat_id == fallback_id,
                    )
                )
            else:
                logger.info(
                    "Storage chat ready: %s (%s)", chat_id, chat.title or chat.type
                )
                is_admin, missing = await self.check_rights(chat_id)
                checks.append(
                    StorageChatCheck(
                        chat_id=chat_id,
                        categories=frozenset(categories),
                        reachable=True,
                        title=chat.title,
                        is_fallback=chat_id == fallback_id,
                        is_admin=is_admin,
                        missing_rights=missing,
                    )
                )
        return checks

    async def check_rights(self, chat_id: int) -> tuple[bool, tuple[str, ...]]:
        """Whether the bot is an administrator here, and which rights it lacks.

        Reported per right rather than as one failure: «Ban users» off reopens the
        invite-link hole while every upload keeps working, which is the kind of
        breakage nothing else on the screen would show.
        """
        try:
            member = await self._bot.get_chat_member(
                chat_id=self._current(chat_id), user_id=self._bot.id
            )
        except TelegramAPIError:
            logger.warning(
                "Could not read the bot's rights in %s", chat_id, exc_info=True
            )
            return False, ALL_RIGHT_NAMES

        status = getattr(member, "status", None)
        if status == ChatMemberStatus.CREATOR:
            return True, ()
        if status != ChatMemberStatus.ADMINISTRATOR:
            return False, ALL_RIGHT_NAMES

        missing = tuple(
            name for name in ALL_RIGHT_NAMES if not getattr(member, name, False)
        )
        return True, missing

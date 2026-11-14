"""Access control and dependency-injection middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.services.container import Services

logger = logging.getLogger(__name__)

DENIED_TEXT = (
    "⛔️ عذراً، هذا البوت خاص.\n"
    "اطلب من المالك إضافة معرّفك إلى قائمة المستخدمين المسموح لهم."
)


class ContextMiddleware(BaseMiddleware):
    """Injects services into handlers and enforces the user allowlist."""

    def __init__(self, services: Services) -> None:
        self._services = services

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        user_id = getattr(user, "id", None)

        if not self._services.settings.is_user_allowed(user_id):
            logger.warning("Blocked an unauthorized update from user %s", user_id)
            await self._reject(event)
            return None

        data["services"] = self._services
        data["settings"] = self._services.settings
        return await handler(event, data)

    @staticmethod
    async def _reject(event: TelegramObject) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(DENIED_TEXT, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(DENIED_TEXT)
        except Exception:  # noqa: BLE001 - never let a rejection crash the dispatcher
            logger.debug("Could not deliver the access-denied notice", exc_info=True)

"""Middlewares: dependency injection and the user allow-list."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from src.services.container import Services

logger = logging.getLogger(__name__)

DENIED_TEXT = "⛔ هذا البوت خاص. لا يُسمح لك باستخدامه."


class AccessMiddleware(BaseMiddleware):
    """Reject every update coming from a user outside ALLOWED_USER_IDS."""

    def __init__(self, services: Services) -> None:
        self._settings = services.settings
        self._allowed = services.settings.allowed_users()
        if self._allowed:
            logger.info("Access limited to %s user(s)", len(self._allowed))

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if not self._allowed:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user is not None and user.id in self._allowed:
            return await handler(event, data)

        logger.warning(
            "Blocked update from user %s", getattr(user, "id", "unknown")
        )
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(DENIED_TEXT, show_alert=True)
            else:
                await event.answer(DENIED_TEXT)
        except Exception:  # noqa: BLE001 - never fail inside the guard
            logger.debug("Could not answer the blocked user", exc_info=True)
        return None


class ContextMiddleware(BaseMiddleware):
    def __init__(self, services: Services) -> None:
        self._services = services

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        data["services"] = self._services
        data["settings"] = self._services.settings
        data["bot"] = self._services.bot
        return await handler(event, data)

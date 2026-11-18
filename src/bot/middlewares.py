"""Middlewares: the access guard, the language of the update, and injection.

The language is resolved once per update from the user's stored preference and
installed in the context that the text builders read. English is the default, so
an unknown user (or a fresh deployment) simply sees English.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from src.core.i18n import Language, pick, reset_language, set_language
from src.services.container import Services

logger = logging.getLogger(__name__)


def denied_text() -> str:
    return pick(
        "⛔ This bot is private. You are not allowed to use it.",
        "⛔ هذا البوت خاص. لا يُسمح لك باستخدامه.",
    )


def _user_id(event: Message | CallbackQuery) -> int | None:
    user = getattr(event, "from_user", None)
    return int(user.id) if user is not None else None


class AccessMiddleware(BaseMiddleware):
    """Install the user's language, then reject anybody outside ALLOWED_USER_IDS."""

    def __init__(self, services: Services) -> None:
        self._settings = services.settings
        self._preferences = services.preferences
        self._allowed = services.settings.allowed_users()
        if self._allowed:
            logger.info("Access limited to %s user(s)", len(self._allowed))

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user_id = _user_id(event)
        language = self._preferences.language_for(user_id)
        set_language(language)
        try:
            if not self._allowed:
                return await handler(event, data)

            if user_id is not None and user_id in self._allowed:
                return await handler(event, data)

            logger.warning("Blocked update from user %s", user_id or "unknown")
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(denied_text(), show_alert=True)
                else:
                    await event.answer(denied_text())
            except Exception:  # noqa: BLE001 - never fail inside the guard
                logger.debug("Could not answer the blocked user", exc_info=True)
            return None
        finally:
            reset_language()


class AckMiddleware(BaseMiddleware):
    """Stop the button spinner before the screen starts being fetched.

    Telegram keeps a pressed button "loading" until its callback query is
    acknowledged, and the handlers acknowledged only after their queries had run:
    that is why a tap looked frozen for seconds. This answers first; any text a
    handler wants to show later is delivered through :func:`src.bot.views.ack`.
    """

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except TelegramAPIError:
                logger.debug("Could not acknowledge the callback query", exc_info=True)
        return await handler(event, data)


class ContextMiddleware(BaseMiddleware):
    """Inject the services, the settings and the bot into every handler."""

    def __init__(self, services: Services) -> None:
        self._services = services

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        # The access guard already installed the language; this keeps the
        # middleware usable on its own (chat_member updates skip the guard).
        if not isinstance(event, (Message, CallbackQuery)):
            user = getattr(event, "from_user", None)
            set_language(
                self._services.preferences.language_for(
                    int(user.id) if user is not None else None
                )
            )
        data["services"] = self._services
        data["settings"] = self._services.settings
        data["bot"] = self._services.bot
        return await handler(event, data)


__all__ = [
    "AccessMiddleware",
    "AckMiddleware",
    "ContextMiddleware",
    "Language",
    "denied_text",
]

"""Context middleware: inject dependencies into handler data."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from src.services.container import Services


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
        # Shared bot instance used by the single-screen session helpers.
        data["bot"] = self._services.storage._bot
        return await handler(event, data)

    @staticmethod
    def _bot_from_services(services: Services):
        return services.storage._bot

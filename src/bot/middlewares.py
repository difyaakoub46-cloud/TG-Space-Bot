"""Context middleware: inject dependencies into handler data."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

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
        data["bot"] = self._services.bot
        return await handler(event, data)

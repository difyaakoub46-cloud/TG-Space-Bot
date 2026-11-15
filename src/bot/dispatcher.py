"""Dispatcher wiring: middlewares, routers and a global error handler."""

from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message

from src.bot.middlewares import ContextMiddleware
from src.bot.routers import browse, commands, fallback, search, upload
from src.services.container import Services

logger = logging.getLogger(__name__)

ERROR_TEXT = "⚠️ تعذّر إكمال العملية حالياً. حاول مرة أخرى بعد قليل."


async def on_error(event: ErrorEvent) -> None:
    """Keep the bot usable when a handler fails (database or Telegram outage)."""
    logger.error(
        "Unhandled error while processing update %s",
        event.update.update_id,
        exc_info=event.exception,
    )

    update = event.update
    callback = update.callback_query
    message = update.message or (callback.message if callback else None)

    try:
        if callback is not None:
            await callback.answer(ERROR_TEXT, show_alert=True)
        elif isinstance(message, Message):
            await message.answer(ERROR_TEXT)
    except Exception:  # noqa: BLE001 - never fail inside the error handler
        logger.debug("Could not notify the user about the failure", exc_info=True)


def build_dispatcher(services: Services) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    middleware = ContextMiddleware(services)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)

    # Order matters: commands first, the catch-all fallback last.
    dispatcher.include_routers(
        commands.router,
        upload.router,
        search.router,
        browse.router,
        fallback.router,
    )

    dispatcher.errors.register(on_error)
    return dispatcher

"""Dispatcher wiring: middlewares, routers and a global error handler."""

from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message

from src.bot.middlewares import AckMiddleware, AccessMiddleware, ContextMiddleware
from src.bot.routers import (
    access,
    browse,
    commands,
    fallback,
    file_list,
    folders,
    pending,
    search,
    sort,
    status,
    upload,
)
from src.core.i18n import pick
from src.services.container import Services

logger = logging.getLogger(__name__)


def error_text() -> str:
    """The one message a failed handler shows, in the active language."""
    return pick(
        "⚠️ Could not finish that just now. Please try again in a moment.",
        "⚠️ تعذّر إكمال العملية حالياً. حاول مرة أخرى بعد قليل.",
    )


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
            # The query was already acknowledged (see ``AckMiddleware``), so the
            # alert is delivered as a message instead of a popup.
            try:
                await callback.answer(error_text(), show_alert=True)
            except TelegramAPIError:
                if callback.message is not None:
                    await callback.message.answer(error_text())
        elif isinstance(message, Message):
            await message.answer(error_text())
    except Exception:  # noqa: BLE001 - never fail inside the error handler
        logger.debug("Could not notify the user about the failure", exc_info=True)


def build_dispatcher(services: Services) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    access_guard = AccessMiddleware(services)
    ack = AckMiddleware()
    middleware = ContextMiddleware(services)
    # Order matters: the allow-list runs before anything else touches the update,
    # and the tap is acknowledged right after it (before any query starts) so the
    # spinner never waits for the screen.
    dispatcher.message.outer_middleware(access_guard)
    dispatcher.callback_query.outer_middleware(access_guard)
    dispatcher.callback_query.outer_middleware(ack)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    # Member and join-request updates only need the services: the owner check and
    # the allow-list live in the handlers.
    dispatcher.chat_member.outer_middleware(middleware)
    dispatcher.chat_join_request.outer_middleware(middleware)

    # Order matters: commands first, the catch-all fallback last.
    dispatcher.include_routers(
        commands.router,
        upload.router,
        search.router,
        file_list.router,
        browse.router,
        folders.router,
        sort.router,
        status.router,
        pending.router,
        access.router,
        fallback.router,
    )

    dispatcher.errors.register(on_error)
    return dispatcher

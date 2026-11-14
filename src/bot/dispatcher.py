"""Dispatcher wiring: middlewares and routers."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.middlewares import ContextMiddleware
from src.bot.routers import browse, commands, fallback, search, upload
from src.services.container import Services


def build_dispatcher(services: Services) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Workflow data available to every handler.
    dispatcher["services"] = services
    dispatcher["settings"] = services.settings

    middleware = ContextMiddleware(services)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)

    # Order matters: commands first, catch-all last.
    dispatcher.include_routers(
        commands.router,
        upload.router,
        search.router,
        browse.router,
        fallback.router,
    )
    return dispatcher

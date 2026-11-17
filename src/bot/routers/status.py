"""General status router: one screen for the bot, the database and the groups.

The storage check that runs at startup is available here on demand, so a broken
group can be confirmed at any time without restarting the bot or uploading a file.
The statistics and the reserve section live under this screen.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import STATUS_TEXTS, MenuCB
from src.services.container import Services

logger = logging.getLogger(__name__)

router = Router(name="status")

STATUS_SCREEN: dict[str, object] = {"k": "status"}


@router.message(Command("status"))
@router.message(F.text.in_(STATUS_TEXTS))
async def cmd_status(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message, state, bot, services, dict(STATUS_SCREEN)
    )


@router.callback_query(MenuCB.filter(F.action == "status"))
async def menu_status_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, dict(STATUS_SCREEN)
    )


@router.callback_query(MenuCB.filter(F.action == "status_check"))
async def status_refresh_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Reach the storage groups again and refresh every number in place."""
    await navigation.navigate_callback(
        callback, state, bot, services, dict(STATUS_SCREEN), push=False
    )

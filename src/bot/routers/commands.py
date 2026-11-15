"""Command router: start, help, cancel, stats and the shared menu callbacks."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import BTN_HELP, MenuCB
from src.services.container import Services

router = Router(name="commands")


async def _main_menu(
    message: Message, state: FSMContext, bot, services: Services
) -> None:
    """Wipe the whole log and leave a single fresh welcome screen behind."""
    await views.clear_tracked(bot, message.chat.id, state, upto=message.message_id)
    await views.reset_flow(state, keep_nav=False)
    await state.update_data(trail=[], screen_key=dict(navigation.MAIN))
    await navigation.navigate_message(
        message, state, bot, services, navigation.MAIN, push=False
    )
    await views.remember(state, message.message_id)


@router.message(CommandStart())
async def cmd_start(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await _main_menu(message, state, bot, services)


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(message, state, bot, services, {"k": "help"})


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await _main_menu(message, state, bot, services)


@router.callback_query(MenuCB.filter(F.action == "main"))
async def menu_main_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    if callback.message is not None:
        await views.clear_tracked(
            bot,
            callback.message.chat.id,
            state,
            upto=callback.message.message_id,
        )
    await views.reset_flow(state, keep_nav=False)
    await state.update_data(trail=[], screen_key=dict(navigation.MAIN))
    await navigation.navigate_callback(
        callback, state, bot, services, navigation.MAIN, push=False
    )


@router.callback_query(MenuCB.filter(F.action == "back"))
async def menu_back_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    # Going back only changes the screen: nothing is deleted.
    await views.reset_flow(state)
    await navigation.go_back(callback, state, services)


@router.callback_query(MenuCB.filter(F.action == "help"))
async def menu_help_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "help"}
    )


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def menu_cancel_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        navigation.MAIN,
        notice="✅ تم إلغاء العملية.",
        push=False,
    )


@router.callback_query(MenuCB.filter(F.action == "stats"))
async def menu_stats_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "stats"}
    )


@router.message(Command("stats"))
async def cmd_stats(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(message, state, bot, services, {"k": "stats"})

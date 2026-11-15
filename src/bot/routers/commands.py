"""Command router: start, help, cancel, stats and the shared menu callbacks."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import BTN_HELP, MenuCB, back_to_menu_kb, main_menu_kb
from src.services.container import Services

router = Router(name="commands")


async def _main_menu(message: Message, state: FSMContext, bot) -> None:
    await views.reset_flow(state)
    await views.set_screen(bot, message, state, views.welcome_text(), main_menu_kb())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot) -> None:
    await _main_menu(message, state, bot)


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext, bot) -> None:
    await views.reset_flow(state)
    await views.set_screen(bot, message, state, views.help_text(), back_to_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, bot) -> None:
    await _main_menu(message, state, bot)


@router.callback_query(MenuCB.filter(F.action == "main"))
async def menu_main_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await views.reset_flow(state)
    await views.set_screen_cb(callback, state, views.welcome_text(), main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "help"))
async def menu_help_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await views.reset_flow(state)
    await views.set_screen_cb(callback, state, views.help_text(), back_to_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def menu_cancel_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await views.reset_flow(state)
    await views.set_screen_cb(
        callback, state, "✅ تم إلغاء العملية.", main_menu_kb()
    )


@router.callback_query(MenuCB.filter(F.action == "stats"))
async def menu_stats_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    stats = await services.files.stats()
    await views.set_screen_cb(
        callback, state, views.stats_text(stats), back_to_menu_kb()
    )


@router.message(Command("stats"))
async def cmd_stats(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    stats = await services.files.stats()
    await views.set_screen(
        bot, message, state, views.stats_text(stats), back_to_menu_kb()
    )

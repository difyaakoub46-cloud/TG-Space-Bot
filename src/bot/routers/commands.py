"""Start/help commands and the main menu navigation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import (
    BTN_HELP,
    MenuCB,
    main_menu_kb,
    main_reply_kb,
)
from src.core.config import Settings

router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await message.answer(views.welcome_text(settings), reply_markup=main_reply_kb())
    await message.answer("اختر ما تريد 👇", reply_markup=main_menu_kb())


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(views.help_text(settings), reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("تم إلغاء العملية الحالية.", reply_markup=main_reply_kb())


@router.callback_query(MenuCB.filter(F.action == "menu"))
async def back_to_menu(callback: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(None)
    await views.show(callback, views.welcome_text(settings), main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "help"))
async def help_callback(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    await views.show(callback, views.help_text(settings), main_menu_kb())

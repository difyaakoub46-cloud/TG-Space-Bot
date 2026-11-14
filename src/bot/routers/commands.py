"""Command routers: start, help, library, search, stats."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from src.bot import views
from src.bot.keyboards import BTN_HELP, MenuCB, main_menu_kb
from src.core.config import Settings

router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings, state: FSMContext, bot) -> None:
    await state.clear()
    await views.set_screen(bot, message, state, views.welcome_text(settings), main_menu_kb())


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext, bot) -> None:
    await views.set_screen(bot, message, state, views.help_text(), main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "main_menu"))
async def main_menu_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await views.set_screen_cb(callback, state, views.welcome_text(), main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def cancel_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await views.set_screen_cb(callback, state, "✅ تم الإلغاء.", main_menu_kb())


@router.message(Command("library"))
@router.message(F.text == "📚 المكتبة")
async def cmd_library(message: Message, services, state: FSMContext, bot) -> None:
    counts = await services.files.category_counts()
    await views.set_screen(bot, message, state, views.categories_text(counts), categories_kb(counts))


@router.message(Command("stats"))
@router.message(F.text == "📊 إحصائيات")
async def cmd_stats(message: Message, services, state: FSMContext, bot) -> None:
    stats = await services.files.stats()
    await views.set_screen(bot, message, state, views.stats_text(stats), back_to_menu_kb())


@router.message(Command("search"))
@router.message(F.text == "🔍 بحث")
async def cmd_search(message: Message, state: FSMContext, bot) -> None:
    await state.set_state(None)
    await views.set_screen(bot, message, state, views.search_prompt_text(), search_prompt_kb())


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    return builder.as_markup()


def categories_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category, count in counts.items():
        builder.button(
            text=f"{category.capitalize()} ({count})",
            callback_data=f"category_{category}",
        )
    builder.row(InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    return builder.as_markup()


def search_prompt_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ إلغاء", callback_data="search_cancel"))
    return builder.as_markup()

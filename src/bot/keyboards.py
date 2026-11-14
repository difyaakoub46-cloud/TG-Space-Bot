"""Keyboard builders for the bot."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot import views
from src.db.models import StoredFile

router = Router(name="keyboards")


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        views.welcome_text(),
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "✅ تم الإلغاء.",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


BTN_CANCEL = "❌ إلغاء"
BTN_LIBRARY = "📚 المكتبة"
BTN_SEARCH = "🔍 بحث"
BTN_STATS = "📊 إحصائيات"
BTN_HELP = "❓ مساعدة"


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=BTN_LIBRARY, callback_data="menu_categories"),
        InlineKeyboardButton(text=BTN_SEARCH, callback_data="menu_search"),
        InlineKeyboardButton(text=BTN_STATS, callback_data="menu_stats"),
        InlineKeyboardButton(text=BTN_HELP, callback_data="menu_help"),
    )
    builder.row(InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_back"))
    return builder.as_markup()


def main_reply_kb() -> ReplyKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=BTN_LIBRARY, callback_data="menu_categories"),
        InlineKeyboardButton(text=BTN_SEARCH, callback_data="menu_search"),
    )
    builder.row(
        InlineKeyboardButton(text=BTN_STATS, callback_data="menu_stats"),
        InlineKeyboardButton(text=BTN_HELP, callback_data="menu_help"),
    )
    builder.row(InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_back"))
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    return builder.as_markup()


MenuCB = CallbackQuery


def categories_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category, count in counts.items():
        builder.button(
            text=f"{category.capitalize()} ({count})",
            callback_data=f"category_{category}",
        )
    builder.row(InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    return builder.as_markup()


def category_files_kb(files: list[StoredFile], category: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in files:
        builder.button(
            text=file.file_name,
            callback_data=f"file_{file.id}",
        )
    builder.row(
        InlineKeyboardButton(text="🔙 رجوع للفئات", callback_data="menu_categories"),
        InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"),
    )
    return builder.as_markup()


FileCB = CallbackQuery
CategoryCB = CallbackQuery
SearchPageCB = CallbackQuery
SearchFilterCB = CallbackQuery


def search_list_kb(results: list[StoredFile], page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in results:
        builder.button(
            text=file.file_name,
            callback_data=f"file_{file.id}",
        )
    builder.row(
        InlineKeyboardButton(text="◀ السابق", callback_data=f"search_page_{page-1}"),
        InlineKeyboardButton(text="التالي ▶", callback_data=f"search_page_{page+1}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"),
        InlineKeyboardButton(text="🔍 بحث جديد", callback_data="menu_search"),
    )
    return builder.as_markup()


def file_actions_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 إرسال", callback_data=f"file_send_{file_id}"),
        InlineKeyboardButton(text="🗑 حذف", callback_data=f"file_delete_{file_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"),
    )
    return builder.as_markup()


def delete_confirm_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ نعم، احذف", callback_data=f"file_delete_confirm_{file_id}"),
        InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_main"),
    )
    return builder.as_markup()


def cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[ReplyKeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message) -> None:
    await message.answer("✅ تم الإلغاء.", reply_markup=main_menu_kb())


@router.message(Command("start"))
async def start_command(message: Message) -> None:
    await message.answer(views.welcome_text(), reply_markup=main_menu_kb())


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_command(message: Message) -> None:
    await message.answer(views.help_text(), reply_markup=main_menu_kb())


@router.message(Command("library"))
@router.message(F.text == BTN_LIBRARY)
async def library_command(message: Message) -> None:
    await message.answer("📚 المكتبة قيد التطوير.", reply_markup=main_menu_kb())


@router.message(Command("search"))
@router.message(F.text == BTN_SEARCH)
async def search_command(message: Message) -> None:
    await message.answer("🔍 اكتب اسم الملف للبحث.", reply_markup=cancel_reply_kb())


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def stats_command(message: Message) -> None:
    await message.answer("📊 إحصائيات المكتبة قيد التطوير.", reply_markup=main_menu_kb())

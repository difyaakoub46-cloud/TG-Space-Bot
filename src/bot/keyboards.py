"""Inline keyboard builders and callback-data factories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot import views
from src.db.models import StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number

BTN_LIBRARY = "📚 المكتبة"
BTN_SEARCH = "🔍 بحث"
BTN_STATS = "📊 إحصائيات"
BTN_HELP = "❓ مساعدة"
BTN_CANCEL = "❌ إلغاء"
BTN_MAIN = "🔙 القائمة الرئيسية"
BTN_BACK = "↩️ رجوع"

_MAX_BUTTON_TEXT = 55


class MenuCB(CallbackData, prefix="menu"):
    action: str


class CategoryCB(CallbackData, prefix="cat"):
    name: str


class FileCB(CallbackData, prefix="file"):
    action: str
    file_id: str


class SearchCB(CallbackData, prefix="search"):
    action: str
    value: str = ""


def _short(text: str, limit: int = _MAX_BUTTON_TEXT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _main_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=BTN_MAIN, callback_data=MenuCB(action="main").pack()
    )


def _back_button() -> InlineKeyboardButton:
    """Go back one screen; unlike the main menu it deletes nothing."""
    return InlineKeyboardButton(
        text=BTN_BACK, callback_data=MenuCB(action="back").pack()
    )


def nav_row() -> tuple[InlineKeyboardButton, InlineKeyboardButton]:
    """The shared [رجوع] [القائمة الرئيسية] row."""
    return _back_button(), _main_menu_button()


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BTN_LIBRARY, callback_data=MenuCB(action="categories").pack()
        ),
        InlineKeyboardButton(
            text=BTN_SEARCH, callback_data=MenuCB(action="search").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BTN_STATS, callback_data=MenuCB(action="stats").pack()
        ),
        InlineKeyboardButton(
            text=BTN_HELP, callback_data=MenuCB(action="help").pack()
        ),
    )
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*nav_row())
    return builder.as_markup()


def categories_kb(counts: Mapping[FileCategory, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category, count in counts.items():
        builder.button(
            text=f"{views.category_label(category)} ({format_number(count)})",
            callback_data=CategoryCB(name=category.value).pack(),
        )
    builder.adjust(2)
    builder.row(*nav_row())
    return builder.as_markup()


def category_files_kb(
    files: Sequence[StoredFile], category: FileCategory | str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in files:
        builder.button(
            text=_short(file.file_name),
            callback_data=FileCB(action="view", file_id=file.id).pack(),
        )
    builder.adjust(1)
    builder.row(*nav_row())
    return builder.as_markup()


def file_actions_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📤 إرسال",
            callback_data=FileCB(action="send", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 حذف",
            callback_data=FileCB(action="delete", file_id=file_id).pack(),
        ),
    )
    builder.row(*nav_row())
    return builder.as_markup()


def delete_confirm_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ نعم، احذف",
            callback_data=FileCB(action="confirm", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text=BTN_CANCEL, callback_data=MenuCB(action="main").pack()
        ),
    )
    return builder.as_markup()


def search_list_kb(
    results: Sequence[StoredFile],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in results:
        builder.button(
            text=_short(file.file_name),
            callback_data=FileCB(action="view", file_id=file.id).pack(),
        )
    builder.adjust(1)

    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="◀ السابق",
                callback_data=SearchCB(action="page", value=str(page - 1)).pack(),
            )
        )
    if page < total_pages:
        navigation.append(
            InlineKeyboardButton(
                text="التالي ▶",
                callback_data=SearchCB(action="page", value=str(page + 1)).pack(),
            )
        )
    if navigation:
        builder.row(*navigation)

    builder.row(
        InlineKeyboardButton(
            text="🔍 بحث جديد", callback_data=MenuCB(action="search").pack()
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()


def search_prompt_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BTN_CANCEL, callback_data=MenuCB(action="cancel").pack()
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()

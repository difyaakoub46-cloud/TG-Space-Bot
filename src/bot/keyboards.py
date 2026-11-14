"""All keyboards and callback-data factories."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.db.models import StoredFile
from src.utils.filetypes import CATEGORY_LABELS, FileCategory
from src.utils.formatters import clip_button, human_size

BTN_SEARCH = "🔍 بحث"
BTN_LIBRARY = "📂 مكتبتي"
BTN_STATS = "📊 الإحصائيات"
BTN_HELP = "❓ مساعدة"
BTN_CANCEL = "❌ إلغاء"


class MenuCB(CallbackData, prefix="menu"):
    action: str


class CategoryCB(CallbackData, prefix="cat"):
    category: str
    page: int = 1


class FileCB(CallbackData, prefix="file"):
    action: str
    file_id: str


class SearchPageCB(CallbackData, prefix="spage"):
    page: int


class SearchFilterCB(CallbackData, prefix="sflt"):
    category: str


# ── reply keyboards ─────────────────────────────
def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_LIBRARY)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل ملفاً لحفظه، أو ابحث بالاسم…",
    )


def cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        input_field_placeholder="اكتب اسم الملف…",
    )


# ── inline keyboards ────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 بحث في الملفات", callback_data=MenuCB(action="search"))
    builder.button(text="📂 تصفح الفئات", callback_data=MenuCB(action="categories"))
    builder.button(text="📊 الإحصائيات", callback_data=MenuCB(action="stats"))
    builder.button(text="❓ مساعدة", callback_data=MenuCB(action="help"))
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def categories_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in FileCategory:
        builder.button(
            text=f"{CATEGORY_LABELS[category]} · {counts.get(category.value, 0)}",
            callback_data=CategoryCB(category=category.value, page=1),
        )
    builder.button(text="⬅️ القائمة الرئيسية", callback_data=MenuCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def _file_button(record: StoredFile) -> InlineKeyboardButton:
    label = f"{clip_button(record.file_name, 38)} · {human_size(record.size_bytes)}"
    return InlineKeyboardButton(
        text=label,
        callback_data=FileCB(action="view", file_id=record.id).pack(),
    )


def browse_list_kb(
    items: list[StoredFile], category: str, page: int, pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for record in items:
        builder.row(_file_button(record))

    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ السابق",
                callback_data=CategoryCB(category=category, page=page - 1).pack(),
            )
        )
    if page < pages:
        navigation.append(
            InlineKeyboardButton(
                text="التالي ➡️",
                callback_data=CategoryCB(category=category, page=page + 1).pack(),
            )
        )
    if navigation:
        builder.row(*navigation)

    builder.row(
        InlineKeyboardButton(
            text="📂 الفئات", callback_data=MenuCB(action="categories").pack()
        ),
        InlineKeyboardButton(
            text="🏠 الرئيسية", callback_data=MenuCB(action="menu").pack()
        ),
    )
    return builder.as_markup()


def search_list_kb(
    items: list[StoredFile], page: int, pages: int, active_category: str | None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for record in items:
        builder.row(_file_button(record))

    filters: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text="✅ الكل" if active_category is None else "الكل",
            callback_data=SearchFilterCB(category="all").pack(),
        )
    ]
    for category in FileCategory:
        is_active = active_category == category.value
        filters.append(
            InlineKeyboardButton(
                text=f"{'✅ ' if is_active else ''}{CATEGORY_LABELS[category].split()[0]}",
                callback_data=SearchFilterCB(category=category.value).pack(),
            )
        )
    builder.row(*filters)

    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ السابق", callback_data=SearchPageCB(page=page - 1).pack()
            )
        )
    if page < pages:
        navigation.append(
            InlineKeyboardButton(
                text="التالي ➡️", callback_data=SearchPageCB(page=page + 1).pack()
            )
        )
    if navigation:
        builder.row(*navigation)

    builder.row(
        InlineKeyboardButton(
            text="🔍 بحث جديد", callback_data=MenuCB(action="search").pack()
        ),
        InlineKeyboardButton(
            text="🏠 الرئيسية", callback_data=MenuCB(action="menu").pack()
        ),
    )
    return builder.as_markup()


def file_actions_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬇️ إرسال الملف", callback_data=FileCB(action="send", file_id=file_id)
    )
    builder.button(
        text="🗑 حذف الملف", callback_data=FileCB(action="delete", file_id=file_id)
    )
    builder.button(text="🏠 الرئيسية", callback_data=MenuCB(action="menu"))
    builder.adjust(2, 1)
    return builder.as_markup()


def delete_confirm_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ نعم، احذف نهائياً",
        callback_data=FileCB(action="confirm_delete", file_id=file_id),
    )
    builder.button(
        text="↩️ تراجع", callback_data=FileCB(action="view", file_id=file_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 القائمة الرئيسية", callback_data=MenuCB(action="menu"))
    return builder.as_markup()

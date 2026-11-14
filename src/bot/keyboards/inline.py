from typing import List, Optional, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from src.database.models import FileCategory
from src.utils.formatters import get_category_emoji, format_bytes


def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="📁 تصفح الملفات حسب الفئة", callback_data="categories_menu"),
            InlineKeyboardButton(text="🔍 بحث سريع", callback_data="start_search")
        ],
        [
            InlineKeyboardButton(text="📊 إحصائيات التخزين", callback_data="storage_stats"),
            InlineKeyboardButton(text="ℹ️ مساعدة وتعليمات", callback_data="help_info")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def categories_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="📄 المستندات (Documents)", callback_data="cat:documents:1"),
            InlineKeyboardButton(text="🎬 الوسائط (Media)", callback_data="cat:media:1")
        ],
        [
            InlineKeyboardButton(text="🎵 الصوتيات (Audio)", callback_data="cat:audio:1"),
            InlineKeyboardButton(text="📦 الملفات المضغوطة (Archives)", callback_data="cat:archives:1")
        ],
        [
            InlineKeyboardButton(text="📁 كافة الملفات (All Files)", callback_data="cat:all:1")
        ],
        [
            InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def files_pagination_keyboard(
    files: List[Dict[str, Any]],
    current_page: int,
    total_count: int,
    page_size: int,
    category: str
) -> InlineKeyboardMarkup:
    keyboard = []

    for f in files:
        f_name = f.get("file_name", "Untitled")
        if len(f_name) > 28:
            f_name = f_name[:25] + "..."
        f_size = format_bytes(f.get("file_size", 0))
        f_cat = f.get("category", "other")
        emoji = get_category_emoji(f_cat)
        btn_text = f"{emoji} {f_name} ({f_size})"
        keyboard.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"file:{f['id']}")
        ])

    nav_buttons = []
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    if current_page > 1:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ السابق", callback_data=f"cat:{category}:{current_page - 1}")
        )

    nav_buttons.append(
        InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop")
    )

    if current_page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(text="التالي ➡️", callback_data=f"cat:{category}:{current_page + 1}")
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton(text="🔙 قائمة الفئات", callback_data="categories_menu"),
        InlineKeyboardButton(text="🏠 الرئيسية", callback_data="main_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def file_actions_keyboard(file_uuid: str, category: str = "all") -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="📥 استرجاع وتحميل الملف", callback_data=f"download:{file_uuid}"),
            InlineKeyboardButton(text="🗑 حذف الملف نهائياً", callback_data=f"delete_confirm:{file_uuid}")
        ],
        [
            InlineKeyboardButton(text="🔙 العودة للملفات", callback_data=f"cat:{category}:1"),
            InlineKeyboardButton(text="🏠 الرئيسية", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def delete_confirmation_keyboard(file_uuid: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="⚠️ نعم، احذف الملف الآن", callback_data=f"delete_do:{file_uuid}"),
            InlineKeyboardButton(text="❌ إلغاء", callback_data=f"file:{file_uuid}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="🏠 العودة للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

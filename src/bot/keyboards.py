"""Inline keyboard builders and callback-data factories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot import views
from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number
from src.utils.sorting import SortDirection, SortField, SortSpec

BTN_LIBRARY = "📚 المكتبة"
BTN_SEARCH = "🔍 بحث"
BTN_STATS = "📊 إحصائيات"
BTN_HELP = "❓ مساعدة"
BTN_CANCEL = "❌ إلغاء"
BTN_MAIN = "🔙 القائمة الرئيسية"
BTN_BACK = "↩️ رجوع"
BTN_FOLDERS = "📁 المجلدات"
BTN_NEW_FOLDER = "➕ مجلد جديد"
BTN_JOIN = "🔗 انضمام للمجموعات"
BTN_STATUS = "🩺 الحالة العامة"
BTN_STATUS_CHECK = "🔍 فحص المجموعات وتحديث الحالة"
BTN_SORT = "🔃 فرز / ترتيب"

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


class FolderCB(CallbackData, prefix="fld"):
    action: str
    value: str = ""


class JoinCB(CallbackData, prefix="join"):
    action: str
    value: str = ""


class SortCB(CallbackData, prefix="srt"):
    """Sorting panel: menu / set a field and direction / close."""

    action: str
    field: str = ""
    direction: str = ""


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
    # Second section: the virtual folders that group files of the library above.
    builder.row(
        InlineKeyboardButton(
            text=BTN_FOLDERS, callback_data=MenuCB(action="folders").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BTN_STATUS, callback_data=MenuCB(action="status").pack()
        )
    )
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*nav_row())
    return builder.as_markup()


def help_kb() -> InlineKeyboardMarkup:
    """Help screen: the navigation row first, then the owner-only gate."""
    builder = InlineKeyboardBuilder()
    builder.row(*nav_row())
    builder.row(
        InlineKeyboardButton(
            text=BTN_JOIN, callback_data=MenuCB(action="join").pack()
        )
    )
    return builder.as_markup()


def groups_kb(groups: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """One button per joinable group: ``(JoinCB value, button label)``."""
    builder = InlineKeyboardBuilder()
    for value, label in groups:
        builder.button(
            text=label,
            callback_data=JoinCB(action="link", value=value).pack(),
        )
    builder.adjust(2)
    builder.row(*nav_row())
    return builder.as_markup()


def categories_kb(
    totals: Mapping[FileCategory, tuple[int, int]],
    favorites: Sequence[Folder] = (),
    folder_count: int = 0,
) -> InlineKeyboardMarkup:
    """Library keyboard: categories, then favourite folders, then all folders."""
    builder = InlineKeyboardBuilder()
    # The order lives in views so the text and the buttons always agree.
    for category in views.CATEGORY_ORDER:
        if category not in totals:
            continue
        count = totals[category][0]
        builder.button(
            text=f"{views.category_label(category)} ({format_number(count)})",
            callback_data=CategoryCB(name=category.value).pack(),
        )
    builder.adjust(2)

    # One tap per favourite folder, in the manual order kept in the database.
    for folder in favorites:
        builder.row(
            InlineKeyboardButton(
                text=f"⭐ {_short(folder.name, 30)}",
                callback_data=FolderCB(action="open", value=folder.id).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=(
                f"{BTN_FOLDERS} ({format_number(folder_count)})"
                if folder_count
                else BTN_FOLDERS
            ),
            callback_data=MenuCB(action="folders").pack(),
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()


def _sort_button(sort: SortSpec) -> InlineKeyboardButton:
    """The single entry point of the sorting panel."""
    return InlineKeyboardButton(
        text=f"{BTN_SORT} · {views.sort_short(sort)}",
        callback_data=SortCB(action="menu").pack(),
    )


def category_files_kb(
    files: Sequence[StoredFile],
    category: FileCategory | str,
    sort: SortSpec | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in files:
        builder.button(
            text=_short(file.file_name),
            callback_data=FileCB(action="view", file_id=file.id).pack(),
        )
    builder.adjust(1)
    if files:
        builder.row(_sort_button(sort or SortSpec()))
    builder.row(*nav_row())
    return builder.as_markup()


def sort_menu_kb(sort: SortSpec) -> InlineKeyboardMarkup:
    """Every ordering as one button, the active one marked with ✅.

    Each field gets a row with its two directions, so the user never has to
    combine a field and a separate «عكس الترتيب» step.
    """
    builder = InlineKeyboardBuilder()
    options = (
        (SortField.size, SortDirection.asc, "📦 الأصغر أولاً"),
        (SortField.size, SortDirection.desc, "📦 الأكبر أولاً"),
        (SortField.date, SortDirection.asc, "🕐 الأقدم أولاً"),
        (SortField.date, SortDirection.desc, "🕐 الأحدث أولاً"),
        (SortField.name, SortDirection.asc, "🔤 أ→ي / A→Z"),
        (SortField.name, SortDirection.desc, "🔤 ي→أ / Z→A"),
    )
    for field, direction, label in options:
        active = sort.field is field and sort.direction is direction
        builder.button(
            text=f"{'✅ ' if active else ''}{label}",
            callback_data=SortCB(
                action="set", field=field.value, direction=direction.value
            ).pack(),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="↩️ رجوع إلى القائمة",
            callback_data=SortCB(action="close").pack(),
        )
    )
    return builder.as_markup()


def status_kb() -> InlineKeyboardMarkup:
    """General status: one button re-runs the check and refreshes every number."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BTN_STATUS_CHECK,
            callback_data=MenuCB(action="status_check").pack(),
        )
    )
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
    builder.row(
        InlineKeyboardButton(
            text="✏️ إعادة تسمية",
            callback_data=FileCB(action="rename", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text="📁 إضافة إلى مجلد",
            callback_data=FileCB(action="folder", file_id=file_id).pack(),
        ),
    )
    builder.row(*nav_row())
    return builder.as_markup()


def folders_kb(
    folders: Sequence[Folder], page: int = 1, total_pages: int = 1
) -> InlineKeyboardMarkup:
    """One page of folders plus first/previous/next/last navigation."""
    builder = InlineKeyboardBuilder()
    for folder in folders:
        star = "⭐" if folder.favorite else "📁"
        builder.button(
            text=f"{star} {_short(folder.name, 25)} ({format_number(folder.count)})",
            callback_data=FolderCB(action="open", value=folder.id).pack(),
        )
    builder.adjust(2)

    if total_pages > 1:
        builder.row(
            InlineKeyboardButton(
                text=f"صفحة {page} من {total_pages}",
                callback_data=FolderCB(action="noop").pack(),
            )
        )
        controls: list[InlineKeyboardButton] = []
        if page > 1:
            controls.append(
                InlineKeyboardButton(
                    text="⏮ الأولى",
                    callback_data=FolderCB(action="page", value="1").pack(),
                )
            )
            controls.append(
                InlineKeyboardButton(
                    text="◀ السابق",
                    callback_data=FolderCB(action="page", value=str(page - 1)).pack(),
                )
            )
        if page < total_pages:
            controls.append(
                InlineKeyboardButton(
                    text="التالي ▶",
                    callback_data=FolderCB(action="page", value=str(page + 1)).pack(),
                )
            )
            controls.append(
                InlineKeyboardButton(
                    text="الأخيرة ⏭",
                    callback_data=FolderCB(
                        action="page", value=str(total_pages)
                    ).pack(),
                )
            )
        if controls:
            builder.row(*controls)

    builder.row(
        InlineKeyboardButton(
            text=BTN_NEW_FOLDER, callback_data=FolderCB(action="new").pack()
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()


def folder_files_kb(
    files: Sequence[StoredFile],
    folder_id: str | None = None,
    favorite: bool = False,
    sort: SortSpec | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for file in files:
        builder.button(
            text=_short(file.file_name),
            callback_data=FileCB(action="view", file_id=file.id).pack(),
        )
    builder.adjust(1)
    if files:
        builder.row(_sort_button(sort or SortSpec()))
    if folder_id:
        if favorite:
            # Manual order of the favourites, exactly as they appear in the library.
            builder.row(
                InlineKeyboardButton(
                    text="⬆️ تقديم",
                    callback_data=FolderCB(
                        action="move_up", value=folder_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="⬇️ تأخير",
                    callback_data=FolderCB(
                        action="move_down", value=folder_id
                    ).pack(),
                ),
            )
        builder.row(
            InlineKeyboardButton(
                text=(
                    "🌟 إزالة من المفضلة"
                    if favorite
                    else "⭐ إضافة إلى المفضلة"
                ),
                callback_data=FolderCB(
                    action="unfavorite" if favorite else "favorite",
                    value=folder_id,
                ).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🗑 حذف المجلد",
                callback_data=FolderCB(action="delete", value=folder_id).pack(),
            )
        )
    builder.row(*nav_row())
    return builder.as_markup()


def folder_delete_confirm_kb(folder_id: str) -> InlineKeyboardMarkup:
    """Confirm the deletion of a folder, keeping every file inside it."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ نعم، احذف المجلد",
            callback_data=FolderCB(action="confirm_delete", value=folder_id).pack(),
        ),
        InlineKeyboardButton(
            text=BTN_CANCEL,
            callback_data=FolderCB(action="open", value=folder_id).pack(),
        ),
    )
    return builder.as_markup()


def pick_folder_kb(folders: Sequence[Folder]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for folder in folders:
        builder.button(
            text=f"📁 {_short(folder.name, 25)}",
            callback_data=FolderCB(action="put", value=folder.id).pack(),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text=BTN_NEW_FOLDER, callback_data=FolderCB(action="new").pack()
        )
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

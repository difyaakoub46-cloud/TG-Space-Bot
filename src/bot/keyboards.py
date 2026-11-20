"""Inline keyboard builders and callback-data factories.

The menu hierarchy is:

    📚 Library                         (one wide button)
    🔍 Search   |   ⚙️ Settings
    🩺 General status                  (one wide button)

and each of the two sub-screens groups its own actions:

* Settings → 🌐 Change language, 🔗 Join the groups, ❓ Help
* General status → 🔍 Check and update status, 📊 Statistics,
  🛡 Group permissions, 🛟 Reserve and pending files

Labels are functions, not constants: they must be built while the update's
language is active. The ``*_TEXTS`` sets exist so the command handlers can match a
typed button in either language.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot import library_views, views
from src.core.i18n import Language, pick
from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number, truncate_name
from src.utils.sorting import SORT_CHOICES, SortSpec

_MAX_BUTTON_TEXT = 55


# --------------------------------------------------------------------------- #
# Labels (both languages, so a typed button is recognised either way)
# --------------------------------------------------------------------------- #
def library_label() -> str:
    return pick("📚 Library", "📚 المكتبة")


def search_label() -> str:
    return pick("🔍 Search", "🔍 بحث")


def settings_label() -> str:
    return pick("⚙️ Settings", "⚙️ الإعدادات")


def links_label() -> str:
    return views.links_label()


def status_label() -> str:
    return pick("🩺 General status", "🩺 الحالة العامة")


def status_check_label() -> str:
    return pick("🔍 Check and update status", "🔍 الفحص والتحديث الحالة")


def stats_label() -> str:
    return pick("📊 Statistics", "📊 الإحصائيات")


def permissions_label() -> str:
    return pick("🛡 Group permissions", "🛡 الصلاحيات للمجموعات")


def permissions_check_label() -> str:
    return pick("🔍 Re-check permissions", "🔍 إعادة فحص الصلاحيات")


def pending_label() -> str:
    return pick("🛟 Reserve and pending files", "🛟 قسم الاحتياط والملفات المعلقة")


def language_label() -> str:
    return pick("🌐 Change language", "🌐 تغيير اللغة")


def join_label() -> str:
    return pick("🔗 Join the groups", "🔗 الانضمام إلى المجموعات")


def help_label() -> str:
    return pick("❓ Help", "❓ مساعدة")


def cancel_label() -> str:
    return pick("❌ Cancel", "❌ إلغاء")


def main_label() -> str:
    return pick("🔙 Main menu", "🔙 القائمة الرئيسية")


def back_label() -> str:
    return pick("↩️ Back", "↩️ رجوع")


def folders_label() -> str:
    return pick("📁 Folders", "📁 المجلدات")


def new_folder_label() -> str:
    return pick("➕ New folder", "➕ مجلد جديد")


def sort_label() -> str:
    return pick("🔃 Sort", "🔃 فرز / ترتيب")


def repair_label() -> str:
    return pick("🔧 Repair", "🔧 إصلاح")


# A typed button may arrive in either language, whatever the current setting is.
LIBRARY_TEXTS = frozenset({"📚 Library", "📚 المكتبة"})
SEARCH_TEXTS = frozenset({"🔍 Search", "🔍 بحث"})
LINKS_TEXTS = frozenset({"🔗 Links", "🔗 الروابط"})
SETTINGS_TEXTS = frozenset({"⚙️ Settings", "⚙️ الإعدادات"})
STATUS_TEXTS = frozenset({"🩺 General status", "🩺 الحالة العامة"})
HELP_TEXTS = frozenset({"❓ Help", "❓ مساعدة"})


# --------------------------------------------------------------------------- #
# Callback data
# --------------------------------------------------------------------------- #
class MenuCB(CallbackData, prefix="menu"):
    action: str
    value: str = ""


class CategoryCB(CallbackData, prefix="cat"):
    """Open a category, or one of its pages (``page=1`` is the first one)."""

    name: str
    page: int = 1


class FileCB(CallbackData, prefix="file"):
    action: str
    file_id: str


class FolderCB(CallbackData, prefix="fld"):
    action: str
    value: str = ""


class JoinCB(CallbackData, prefix="join"):
    action: str
    value: str = ""


class SavedCB(CallbackData, prefix="svd"):
    """Walk through the files of one upload batch: previous / next."""

    action: str
    value: str = ""


class SortCB(CallbackData, prefix="srt"):
    """Sorting panel: menu / set a field and direction / close.

    Sorting is applied to the whole category in the database, so an ordering
    change always restarts from the first page.
    """

    action: str
    field: str = ""
    direction: str = ""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _short(text: str, limit: int = _MAX_BUTTON_TEXT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _main_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=main_label(), callback_data=MenuCB(action="main").pack()
    )


def _back_button() -> InlineKeyboardButton:
    """Go back one screen; unlike the main menu it deletes nothing."""
    return InlineKeyboardButton(
        text=back_label(), callback_data=MenuCB(action="back").pack()
    )


def nav_row() -> tuple[InlineKeyboardButton, InlineKeyboardButton]:
    """The shared [Back] [Main menu] row."""
    return _back_button(), _main_menu_button()


# --------------------------------------------------------------------------- #
# Main menu and its two sub-screens
# --------------------------------------------------------------------------- #
def main_menu_kb() -> InlineKeyboardMarkup:
    """Library on top, search + settings in the middle, status at the bottom."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=library_label(), callback_data=MenuCB(action="categories").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=links_label(), callback_data=MenuCB(action="links").pack()
        ),
        InlineKeyboardButton(
            text=search_label(), callback_data=MenuCB(action="search").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=settings_label(), callback_data=MenuCB(action="settings").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=status_label(), callback_data=MenuCB(action="status").pack()
        )
    )
    return builder.as_markup()


def saved_screen_label(show: bool = True) -> str:
    """The saved-file card toggle, with its current state on the button."""
    state = pick("✅ On", "✅ مفعّلة") if show else pick("⛔ Off", "⛔ معطّلة")
    return pick(
        f"📨 Saved-file screen: {state}",
        f"📨 شاشة «تم حفظ الملف»: {state}",
    )


def settings_kb(
    show_saved: bool = True, keep_sent: bool = False
) -> InlineKeyboardMarkup:
    """Settings: language, both screen toggles, the group gate and help."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=language_label(), callback_data=MenuCB(action="language").pack()
        )
    )
    # The two toggles and the two links share a row each: five actions used to
    # cost five rows, which made the panel taller than the text above it.
    builder.row(
        InlineKeyboardButton(
            text=saved_screen_label(show_saved),
            callback_data=MenuCB(action="toggle_saved").pack(),
        ),
        InlineKeyboardButton(
            text=library_views.keep_sent_label(keep_sent),
            callback_data=MenuCB(action="toggle_keep_sent").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=join_label(), callback_data=MenuCB(action="join").pack()
        ),
        InlineKeyboardButton(
            text=help_label(), callback_data=MenuCB(action="help").pack()
        ),
    )
    builder.row(*nav_row())
    return builder.as_markup()


def language_kb(current: Language) -> InlineKeyboardMarkup:
    """The two languages, the active one marked with ✅."""
    builder = InlineKeyboardBuilder()
    for language, label in (
        (Language.en, "English"),
        (Language.ar, "العربية"),
    ):
        mark = "✅ " if current is language else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=MenuCB(action="set_lang", value=language.value).pack(),
            )
        )
    builder.row(*nav_row())
    return builder.as_markup()


def status_kb() -> InlineKeyboardMarkup:
    """General status: refresh, statistics, permissions and the reserve section."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=status_check_label(),
            callback_data=MenuCB(action="status_check").pack(),
        )
    )
    # Statistics and permissions are two lists of the same groups: side by side.
    builder.row(
        InlineKeyboardButton(
            text=stats_label(), callback_data=MenuCB(action="stats").pack()
        ),
        InlineKeyboardButton(
            text=permissions_label(),
            callback_data=MenuCB(action="permissions").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=pending_label(), callback_data=MenuCB(action="pending").pack()
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()


def permissions_kb() -> InlineKeyboardMarkup:
    """«Group permissions»: re-read the groups next to the shared back row."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=permissions_check_label(),
            callback_data=MenuCB(action="permissions_check").pack(),
        ),
        *nav_row(),
    )
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*nav_row())
    return builder.as_markup()


def help_kb() -> InlineKeyboardMarkup:
    """Help is a leaf now: the group gate lives in Settings."""
    builder = InlineKeyboardBuilder()
    builder.row(*nav_row())
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


def pending_kb(total: int = 0) -> InlineKeyboardMarkup:
    """The reserve screen: repair only makes sense when files are waiting."""
    builder = InlineKeyboardBuilder()
    if total:
        builder.row(
            InlineKeyboardButton(
                text=repair_label(), callback_data=MenuCB(action="repair").pack()
            ),
            *nav_row(),
        )
    else:
        builder.row(*nav_row())
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Library screens
# --------------------------------------------------------------------------- #
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
                f"{folders_label()} ({format_number(folder_count)})"
                if folder_count
                else folders_label()
            ),
            callback_data=MenuCB(action="folders").pack(),
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()


def _sort_button(sort: SortSpec) -> InlineKeyboardButton:
    """The single entry point of the sorting panel of a folder."""
    return InlineKeyboardButton(
        text=f"{sort_label()} · {views.sort_short(sort)}",
        callback_data=SortCB(action="menu").pack(),
    )


def sort_menu_kb(sort: SortSpec) -> InlineKeyboardMarkup:
    """Every ordering as one button, the active one marked with ✅."""
    builder = InlineKeyboardBuilder()
    for choice in SORT_CHOICES:
        active = choice.spec == sort
        builder.button(
            text=f"{'✅ ' if active else ''}{pick(*choice.labels)}",
            callback_data=SortCB(
                action="set",
                field=choice.spec.field.value,
                direction=choice.spec.direction.value,
            ).pack(),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=pick("↩️ Back to the list", "↩️ رجوع إلى القائمة"),
            callback_data=SortCB(action="close").pack(),
        )
    )
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# File screens
# --------------------------------------------------------------------------- #
def file_actions_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=pick("📤 Send", "📤 إرسال"),
            callback_data=FileCB(action="send", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text=pick("🗑 Delete", "🗑 حذف"),
            callback_data=FileCB(action="delete", file_id=file_id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=pick("✏️ Rename", "✏️ إعادة تسمية"),
            callback_data=FileCB(action="rename", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text=pick("📁 Add to a folder", "📁 إضافة إلى مجلد"),
            callback_data=FileCB(action="folder", file_id=file_id).pack(),
        ),
    )
    builder.row(*nav_row())
    return builder.as_markup()


def saved_nav_kb(
    files: Sequence[StoredFile],
    index: int = 0,
    duplicates: Sequence[str] = (),
    show_pending: bool = False,
) -> InlineKeyboardMarkup:
    """One uploaded file at a time, with «Next ▶» to reach the rest of the batch.

    The batch is walked file by file so every file keeps its own card and its own
    buttons (send, delete, rename, folders); the file name in the header says
    where the walk stands (``file 2 of 6``).
    """
    builder = InlineKeyboardBuilder()
    record = files[index]
    duplicate = record.id in set(duplicates)
    if not duplicate:
        builder.row(
            InlineKeyboardButton(
                text=pick("📤 Send", "📤 إرسال"),
                callback_data=FileCB(action="send", file_id=record.id).pack(),
            ),
            InlineKeyboardButton(
                text=pick("🗑 Delete", "🗑 حذف"),
                callback_data=FileCB(action="delete", file_id=record.id).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=pick("✏️ Rename", "✏️ إعادة تسمية"),
            callback_data=FileCB(action="rename", file_id=record.id).pack(),
        ),
        InlineKeyboardButton(
            text=pick("📁 Add to a folder", "📁 إضافة إلى مجلد"),
            callback_data=FileCB(action="folder", file_id=record.id).pack(),
        ),
    )

    steps: list[InlineKeyboardButton] = []
    if index > 0:
        steps.append(
            InlineKeyboardButton(
                text=pick("◀ Previous", "◀ السابق"),
                callback_data=SavedCB(action="go", value=str(index - 1)).pack(),
            )
        )
    if index + 1 < len(files):
        steps.append(
            InlineKeyboardButton(
                text=pick("Next ▶", "التالي ▶"),
                callback_data=SavedCB(action="go", value=str(index + 1)).pack(),
            )
        )
    if steps:
        builder.row(*steps)

    if show_pending:
        builder.row(
            InlineKeyboardButton(
                text=pending_label(), callback_data=MenuCB(action="pending").pack()
            )
        )
    builder.row(*nav_row())
    return builder.as_markup()


def delete_confirm_kb(file_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=pick("✅ Yes, delete", "✅ نعم، احذف"),
            callback_data=FileCB(action="confirm", file_id=file_id).pack(),
        ),
        InlineKeyboardButton(
            text=cancel_label(), callback_data=MenuCB(action="main").pack()
        ),
    )
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #
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
                text=pick(
                    f"Page {page} of {total_pages}", f"صفحة {page} من {total_pages}"
                ),
                callback_data=FolderCB(action="noop").pack(),
            )
        )
        controls: list[InlineKeyboardButton] = []
        if page > 1:
            controls.append(
                InlineKeyboardButton(
                    text=pick("⏮ First", "⏮ الأولى"),
                    callback_data=FolderCB(action="page", value="1").pack(),
                )
            )
            controls.append(
                InlineKeyboardButton(
                    text=pick("◀ Previous", "◀ السابق"),
                    callback_data=FolderCB(action="page", value=str(page - 1)).pack(),
                )
            )
        if page < total_pages:
            controls.append(
                InlineKeyboardButton(
                    text=pick("Next ▶", "التالي ▶"),
                    callback_data=FolderCB(action="page", value=str(page + 1)).pack(),
                )
            )
            controls.append(
                InlineKeyboardButton(
                    text=pick("Last ⏭", "⏭ الأخيرة"),
                    callback_data=FolderCB(
                        action="page", value=str(total_pages)
                    ).pack(),
                )
            )
        if controls:
            builder.row(*controls)

    builder.row(
        InlineKeyboardButton(
            text=new_folder_label(), callback_data=FolderCB(action="new").pack()
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
            # The same label as the shared file list: name, size and time, with the
            # name cut in the middle so the button never wraps.
            text=(
                f"📄 {truncate_name(file.file_name)} - {file.size_human} - "
                f"{file.created_at:%H:%M}"
            ),
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
                    text=pick("⬆️ Move up", "⬆️ تقديم"),
                    callback_data=FolderCB(
                        action="move_up", value=folder_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=pick("⬇️ Move down", "⬇️ تأخير"),
                    callback_data=FolderCB(
                        action="move_down", value=folder_id
                    ).pack(),
                ),
            )
        # The two folder actions share one row, so the panel stays compact.
        builder.row(
            InlineKeyboardButton(
                text=(
                    pick("🌟 Remove from favourites", "🌟 إزالة من المفضلة")
                    if favorite
                    else pick("⭐ Add to favourites", "⭐ إضافة إلى المفضلة")
                ),
                callback_data=FolderCB(
                    action="unfavorite" if favorite else "favorite",
                    value=folder_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=pick("🗑 Delete the folder", "🗑 حذف المجلد"),
                callback_data=FolderCB(action="delete", value=folder_id).pack(),
            ),
        )
    builder.row(*nav_row())
    return builder.as_markup()


def folder_delete_confirm_kb(folder_id: str) -> InlineKeyboardMarkup:
    """Confirm the deletion of a folder, keeping every file inside it."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=pick("✅ Yes, delete the folder", "✅ نعم، احذف المجلد"),
            callback_data=FolderCB(action="confirm_delete", value=folder_id).pack(),
        ),
        InlineKeyboardButton(
            text=cancel_label(),
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
            text=new_folder_label(), callback_data=FolderCB(action="new").pack()
        )
    )
    builder.row(*nav_row())
    return builder.as_markup()

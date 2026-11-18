"""The merged «🔍 Search» screen: «History uploads» plus a typed query.

Search is not a second browser. It opens the upload history itself — the same
period filters, eight files per page, the same page turns and the same date jumps
— and whatever the user types narrows that history to the files whose names match.
Matching folders are offered in the same keyboard, so one screen finds both, and
«✖️ Clear search» puts the plain history back.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards import FolderCB, SearchCB, history_kb
from src.bot.library_views import history_period_label
from src.core.i18n import pick
from src.db.models import Folder
from src.services.files import HistoryPage
from src.utils.formatters import format_number

_MAX_LABEL = 48


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _folder_label(folder: Folder) -> str:
    name = folder.name
    if len(name) > _MAX_LABEL:
        name = f"{name[: _MAX_LABEL - 1]}…"
    return f"📁 {name} ({format_number(folder.count)})"


def search_text(
    page: HistoryPage, query: str = "", folder_total: int = 0
) -> str:
    """The screen under the filters: the period, what was found and the hint."""
    if query:
        title = pick(
            f"🔍 <b>Search: «{_escape(query)}»</b>",
            f"🔍 <b>البحث عن «{_escape(query)}»</b>",
        )
    else:
        title = pick("🔍 <b>Search</b>", "🔍 <b>البحث</b>")

    lines = [title, _escape(history_period_label(page))]
    if query:
        lines.append(
            pick(
                f"📄 {format_number(page.total)} file(s) · "
                f"📁 {format_number(folder_total)} folder(s)",
                f"📄 {format_number(page.total)} ملف · "
                f"📁 {format_number(folder_total)} مجلد",
            )
        )
    lines.append("")

    if query and not page.files and not folder_total:
        lines.append(
            pick(
                "No matching files or folders.",
                "لا توجد ملفات أو مجلدات مطابقة.",
            )
        )
        lines.append(
            pick(
                "Type another message to search again.",
                "أرسل رسالة أخرى للبحث من جديد.",
            )
        )
    elif not query and not page.files:
        lines.append(
            pick("No uploads in this period.", "لا توجد ملفات في هذه الفترة.")
        )
        lines.append(
            pick(
                "Type a message to search the files and the folders.",
                "اكتب رسالة للبحث في الملفات والمجلدات.",
            )
        )
    elif query:
        if folder_total:
            lines.append(
                pick(
                    "The matching folders are under the file buttons.",
                    "المجلدات المطابقة أسفل أزرار الملفات.",
                )
            )
        lines.append(
            pick(
                "Type another message to search again.",
                "أرسل رسالة أخرى للبحث من جديد.",
            )
        )
    else:
        lines.append(
            pick(
                "Type a message to search the files and the folders.",
                "اكتب رسالة للبحث في الملفات والمجلدات.",
            )
        )
    return "\n".join(lines)


def search_kb(
    page: HistoryPage,
    folders: Sequence[Folder] = (),
    query: str = "",
) -> InlineKeyboardMarkup:
    """The history keyboard, with the matching folders and «Clear» added.

    The history rows stay exactly as they are (filters, the eight files, the page
    controls, Back / Main menu); the folder results and the clear button are
    inserted right under the file buttons, so the layout never moves the filters.
    """
    markup = history_kb(page)
    rows = [list(row) for row in markup.inline_keyboard]
    after_files = 2 + len(page.files)  # two filter rows, then one row per file

    folder_rows = [
        [
            InlineKeyboardButton(
                text=_folder_label(folder),
                callback_data=FolderCB(action="open", value=folder.id).pack(),
            )
        ]
        for folder in folders
    ]
    rows[after_files:after_files] = folder_rows

    if query:
        rows.insert(
            after_files + len(folder_rows),
            [
                InlineKeyboardButton(
                    text=pick("✖️ Clear search", "✖️ مسح البحث"),
                    callback_data=SearchCB(action="clear").pack(),
                )
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)

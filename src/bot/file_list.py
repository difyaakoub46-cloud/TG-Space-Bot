"""The shared file-list component: one screen for both ways into the library.

«🔍 Search» and a library category are the same list with a different *view*: the
first narrows the files by a typed name, the second by their category. Everything
else — the eight files a page, the five period filters, the six orderings, the
page turns, the quick period jump and the period picker — is written once here and
shared.

The screen key holds the whole position so a rebuild needs nothing else::

    {"k": "list", "v": "search"|"category", "val": ..., "f": ..., "srt": ...,
     "p": ..., "d": ..., "u": ...}

and every button repeats it in :class:`FileListCB`, so a tap never depends on the
screen the chat happens to be on.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot import views
from src.bot.keyboards import FileCB, FolderCB, nav_row
from src.bot.library_views import history_period_label
from src.core.i18n import pick
from src.db.models import Folder
from src.services.files import FileListPage, history_date
from src.utils.formatters import format_number, truncate_name
from src.utils.sorting import sort_from_key

VIEWS: tuple[str, ...] = ("search", "category")

# The five filters in one row: «All time» first, then the growing periods.
_FILTERS: tuple[str, ...] = ("all", "day", "week", "month", "year")

_FILTER_LABELS: dict[str, tuple[str, str]] = {
    "all": ("📁 All time", "📁 كل الوقت"),
    "day": ("📅 Day", "📅 يوم"),
    "week": ("📅 Week", "📅 أسبوع"),
    "month": ("📅 Month", "📅 شهر"),
    "year": ("📅 Year", "📅 سنة"),
}

# A callback is capped at 64 bytes by Telegram; the typed query can be longer, so
# only its beginning travels in the button and the full text is recovered from the
# screen key it was typed on.
_MAX_CALLBACK_VALUE = 16
_MAX_LABEL = 48

_SORT_LABELS: dict[str, tuple[str, str]] = {
    "newest": ("🕐 Newest first", "🕐 الأحدث أولاً"),
    "oldest": ("🕐 Oldest first", "🕐 الأقدم أولاً"),
    "size_asc": ("📦 Smallest first", "📦 الأصغر أولاً"),
    "size_desc": ("📦 Largest first", "📦 الأكبر أولاً"),
    "name_asc": ("🔤 A→Z / أ→ي", "🔤 أ→ي / A→Z"),
    "name_desc": ("🔤 Z→A / ي→أ", "🔤 ي→أ / Z→A"),
}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


# --------------------------------------------------------------------------- #
# Context: where the list came from, and what it is showing
# --------------------------------------------------------------------------- #
class FileListCB(CallbackData, prefix="fl"):
    """The whole position of the list, carried by every button.

    ``view`` is the entry point (``search`` or ``category``) and ``value`` its
    context (the typed query or the category name); ``period`` is the time filter,
    and ``sort``, ``page`` and ``anchor`` are the list's own state.
    """

    action: str = "open"
    view: str = "search"
    value: str = ""
    period: str = "all"
    sort: str = "newest"
    page: int = 1
    anchor: str = ""


@dataclass(frozen=True)
class FileListContext:
    """The state of one shared file list."""

    view: str = "search"
    value: str = ""
    period: str = "all"
    sort: str = "newest"
    page: int = 1
    anchor: date | None = None

    @classmethod
    def from_screen(cls, screen: dict[str, object] | None) -> "FileListContext":
        """Read a screen key back into the context that rendered it."""
        screen = screen or {}
        view = str(screen.get("v") or "search")
        try:
            page = max(1, int(screen.get("p") or 1))
        except (TypeError, ValueError):
            page = 1
        return cls(
            view=view if view in VIEWS else "search",
            value=" ".join(str(screen.get("val") or "").split()),
            period=str(screen.get("f") or "all"),
            sort=str(screen.get("srt") or "newest"),
            page=page,
            anchor=history_date(screen.get("d")),
        )

    def to_screen(self, base: dict[str, object] | None = None) -> dict[str, object]:
        """The screen key of this context, keeping unrelated fields (the user id)."""
        screen = dict(base or {})
        screen.update(
            {
                "k": "list",
                "v": self.view,
                "val": self.value,
                "f": self.period,
                "srt": self.sort,
                "p": max(1, self.page),
                "d": (self.anchor or history_date()).isoformat(),
            }
        )
        return screen


def new_screen(
    view: str, value: str = "", user_id: int = 0, *, span: str = "all"
) -> dict[str, object]:
    """The first page of a list, opened from its own entry point today."""
    return FileListContext(
        view=view if view in VIEWS else "search",
        value=" ".join(str(value or "").split()),
        period=span,
        anchor=history_date(),
    ).to_screen({"u": int(user_id or 0)})


def callback_data(
    ctx: FileListContext,
    action: str = "open",
    *,
    page: int | None = None,
    sort: str | None = None,
    anchor: date | None = None,
) -> str:
    """Pack the context (plus what the tap changes) into a button's data."""
    return FileListCB(
        action=action,
        view=ctx.view,
        value=ctx.value[:_MAX_CALLBACK_VALUE],
        period=ctx.period,
        sort=sort or ctx.sort,
        page=max(1, page if page is not None else ctx.page),
        anchor=(anchor or ctx.anchor or history_date()).isoformat(),
    ).pack()


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
def file_list_text(
    ctx: FileListContext, page: FileListPage, folder_total: int = 0
) -> str:
    """The header of the list: where it comes from, its date, its size and sort."""
    if ctx.view == "category":
        title = f"<b>{_escape(views.category_label(ctx.value))}</b>"
    elif ctx.value:
        title = pick(
            f"🔍 <b>Search: «{_escape(ctx.value)}»</b>",
            f"🔍 <b>البحث عن «{_escape(ctx.value)}»</b>",
        )
    else:
        title = pick("🔍 <b>Search</b>", "🔍 <b>البحث</b>")

    # The date of the period is always the first thing under the title.
    lines = [title, _escape(history_period_label(page))]

    summary = pick(
        f"📄 {format_number(page.total)} file(s)",
        f"📄 {format_number(page.total)} ملف",
    )
    if ctx.view == "search" and ctx.value:
        summary += pick(
            f" · 📁 {format_number(folder_total)} folder(s)",
            f" · 📁 {format_number(folder_total)} مجلد",
        )
    if page.pages > 1:
        summary += pick(
            f" · 📄 Page {page.page} of {page.pages}",
            f" · 📄 الصفحة {page.page} من {page.pages}",
        )
    lines.append(summary)
    lines.append(
        pick(
            f"🔃 Ordering: {views.sort_summary(sort_from_key(ctx.sort))}",
            f"🔃 الترتيب: {views.sort_summary(sort_from_key(ctx.sort))}",
        )
    )

    if not page.files:
        lines.append("")
        if ctx.view == "search" and ctx.value:
            lines.append(
                pick(
                    "No matching files or folders.",
                    "لا توجد ملفات أو مجلدات مطابقة.",
                )
            )
        elif ctx.view == "search":
            lines.append(pick("There are no uploads yet.", "لا توجد ملفات بعد."))
        else:
            lines.append(
                pick("No files in this period.", "لا توجد ملفات في هذه الفترة.")
            )
    elif ctx.view == "search" and ctx.value and folder_total:
        lines.append("")
        lines.append(
            pick(
                "The matching folders are under the file buttons.",
                "المجلدات المطابقة أسفل أزرار الملفات.",
            )
        )

    if ctx.view == "search":
        lines.append("")
        lines.append(
            pick(
                "Type another message to search again."
                if ctx.value
                else "Type a message to search the files and the folders.",
                "أرسل رسالة أخرى للبحث من جديد."
                if ctx.value
                else "اكتب رسالة للبحث في الملفات والمجلدات.",
            )
        )
    return "\n".join(lines)


def sort_menu_text(ctx: FileListContext) -> str:
    """The ordering panel: the current choice and what a tap does."""
    return "\n".join(
        [
            pick("🔃 <b>Sort the files</b>", "🔃 <b>فرز وترتيب الملفات</b>"),
            "",
            pick(
                f"Current ordering: <b>{views.sort_summary(sort_from_key(ctx.sort))}</b>",
                f"الترتيب الحالي: <b>{views.sort_summary(sort_from_key(ctx.sort))}</b>",
            ),
            "",
            pick(
                "Pick an ordering; the list is refreshed in place. The ordering "
                "covers every file of the list, not the page, and returns you to "
                "the first page because the page numbers change.",
                "اختر ترتيباً؛ تتحدّث القائمة في مكانها. الفرز يشمل كل ملفات "
                "القائمة لا الصفحة المعروضة، ويعود بك إلى الصفحة الأولى لأن "
                "ترقيم الصفحات يتغيّر.",
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# Keyboard
# --------------------------------------------------------------------------- #
def _file_label(file) -> str:
    """One file button: its name, its size and the time it was saved.

    Every file button of the bot has exactly this shape. The name is cut in the
    middle (first 12 characters, «...», last 8), so a long name can never wrap the
    button onto a second line and change the height of the panel.
    """
    return (
        f"📄 {truncate_name(file.file_name)} - {file.size_human} - "
        f"{file.created_at:%H:%M}"
    )


def _folder_label(folder: Folder) -> str:
    name = folder.name
    if len(name) > _MAX_LABEL:
        name = f"{name[: _MAX_LABEL - 1]}…"
    return f"📁 {name} ({format_number(folder.count)})"


def _jump_label(span: str) -> str:
    """«Next Day ⏭️» and friends, matching the active period filter."""
    return {
        "day": pick("Next Day ⏭️", "اليوم التالي ⏭️"),
        "week": pick("Next Week ⏭️", "الأسبوع التالي ⏭️"),
        "month": pick("Next Month ⏭️", "الشهر التالي ⏭️"),
        "year": pick("Next Year ⏭️", "السنة التالية ⏭️"),
    }.get(span, pick("Next ⏭️", "التالي ⏭️"))


def _filter_buttons(
    ctx: FileListContext, page: FileListPage
) -> list[InlineKeyboardButton]:
    """The five period filters, side by side in one row.

    A tap applies the filter; tapping the filter that is already active opens the
    period picker instead, because the same filter pressed twice asks for a date.
    """
    return [
        InlineKeyboardButton(
            text=f"{'✅ ' if span == page.span else ''}{pick(*_FILTER_LABELS[span])}",
            callback_data=FileListCB(
                action="filter",
                view=ctx.view,
                value=ctx.value[:_MAX_CALLBACK_VALUE],
                period=span,
                sort=ctx.sort,
                page=1,
                anchor=page.anchor.isoformat(),
            ).pack(),
        )
        for span in _FILTERS
    ]


def _controls(
    ctx: FileListContext, page: FileListPage
) -> list[InlineKeyboardButton]:
    """Previous / next page, then the quick jump to the closest older period.

    «Previous» always stays in the row so the layout never shifts; on the first
    page it is inert, and so is «Next» on the last page of the period.
    """
    buttons = [
        InlineKeyboardButton(
            text=pick("◀️ Previous", "◀️ السابق"),
            callback_data=(
                callback_data(ctx, page=page.newer_page)
                if page.newer_page is not None
                else FileListCB(action="noop").pack()
            ),
        ),
        InlineKeyboardButton(
            text=pick("Next ▶️", "التالي ▶️"),
            callback_data=(
                callback_data(ctx, page=page.older_page)
                if page.older_page is not None
                else FileListCB(action="noop").pack()
            ),
        ),
    ]
    if page.span != "all":
        buttons.append(
            InlineKeyboardButton(
                text=_jump_label(page.span),
                callback_data=(
                    callback_data(ctx, page=1, anchor=page.next_anchor)
                    if page.next_anchor is not None
                    else FileListCB(action="noop").pack()
                ),
            )
        )
    return buttons


def file_list_kb(
    ctx: FileListContext,
    page: FileListPage,
    folders: Sequence[Folder] = (),
) -> InlineKeyboardMarkup:
    """Filters, up to eight files, the page controls, sorting and the menu row."""
    builder = InlineKeyboardBuilder()

    # The five period filters share one row on top of the list.
    builder.row(*_filter_buttons(ctx, page))

    for file in page.files:
        builder.row(
            InlineKeyboardButton(
                text=_file_label(file),
                callback_data=FileCB(action="view", file_id=file.id).pack(),
            )
        )

    for folder in folders:
        builder.row(
            InlineKeyboardButton(
                text=_folder_label(folder),
                callback_data=FolderCB(action="open", value=folder.id).pack(),
            )
        )

    controls = _controls(ctx, page)
    if controls:
        builder.row(*controls)

    builder.row(
        InlineKeyboardButton(
            text=f"{pick('🔃 Sort', '🔃 ترتيب')} · {views.sort_short(sort_from_key(ctx.sort))}",
            callback_data=callback_data(ctx, "sort"),
        )
    )

    if ctx.view == "search" and ctx.value:
        builder.row(
            InlineKeyboardButton(
                text=pick("✖️ Clear search", "✖️ مسح البحث"),
                callback_data=callback_data(ctx, "clear", page=1),
            )
        )

    builder.row(*nav_row())
    return builder.as_markup()


def _period_option_label(
    span: str, anchor: date, *, active: bool, count: int | None = None
) -> str:
    """One picker button: the day, week, month or year, then its file count."""
    if span == "week":
        first = anchor - timedelta(days=anchor.weekday())
        last = first + timedelta(days=6)
        text = f"{first:%d %b} – {last:%d %b}"
    elif span == "month":
        text = f"{anchor:%Y-%m}"
    elif span == "year":
        text = f"{anchor:%Y}"
    else:
        text = anchor.isoformat()
    if count is not None:
        text = f"{text} · {format_number(count)}"
    return f"{'✅ ' if active else ''}{text}"


def period_picker_text(
    ctx: FileListContext, periods: Sequence[tuple[date, int]]
) -> str:
    """The picker panel: the periods that actually hold a file."""
    unit = {
        "week": pick("week", "أسبوع"),
        "month": pick("month", "شهر"),
        "year": pick("year", "سنة"),
    }.get(ctx.period, pick("day", "يوم"))
    lines = [
        pick("📅 <b>Pick a period</b>", "📅 <b>اختر فترة</b>"),
        "",
        pick(
            f"Every {unit} that holds files, newest first, with its number of "
            "files. Pick one to jump straight to it; the one on screen is marked "
            "✅.",
            f"كل {unit} فيه ملفات، الأحدث أولاً، ومعه عدد ملفاته. اختر واحدة "
            "للانتقال إليها مباشرة؛ الفترة المعروضة معلّمة ✅.",
        ),
    ]
    if not periods:
        lines.extend(
            [
                "",
                pick(
                    "There are no files to pick from.",
                    "لا توجد ملفات للاختيار منها.",
                ),
            ]
        )
    return "\n".join(lines)


def period_picker_kb(
    ctx: FileListContext, periods: Sequence[tuple[date, int]]
) -> InlineKeyboardMarkup:
    """The period grid, two buttons a row, above a way back to the list."""
    builder = InlineKeyboardBuilder()
    for anchor, count in periods:
        builder.button(
            text=_period_option_label(
                ctx.period, anchor, active=anchor == ctx.anchor, count=count
            ),
            callback_data=FileListCB(
                action="anchor",
                view=ctx.view,
                value=ctx.value[:_MAX_CALLBACK_VALUE],
                period=ctx.period,
                sort=ctx.sort,
                page=1,
                anchor=anchor.isoformat(),
            ).pack(),
        )
    if periods:
        builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=pick("↩️ Back to the list", "↩️ رجوع إلى القائمة"),
            callback_data=callback_data(ctx, "back"),
        )
    )
    return builder.as_markup()


def sort_menu_kb(ctx: FileListContext) -> InlineKeyboardMarkup:
    """Every ordering as one button, the active one marked with ✅."""
    builder = InlineKeyboardBuilder()
    for key in _SORT_LABELS:
        label = pick(*_SORT_LABELS[key])
        active = key == ctx.sort
        builder.button(
            text=f"{'✅ ' if active else ''}{label}",
            callback_data=callback_data(ctx, "sorted", page=1, sort=key),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=pick("↩️ Back to the list", "↩️ رجوع إلى القائمة"),
            callback_data=callback_data(ctx, "back"),
        )
    )
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# The one entry point both sections call
# --------------------------------------------------------------------------- #
async def screen(services, screen: dict[str, object]) -> tuple[str, InlineKeyboardMarkup]:
    """Build the shared file list for a screen key.

    Both entry points end here: «🔍 Search» passes its typed query, a library
    category its name, and the service applies the view's ``WHERE``, the period
    window, the ``ORDER BY`` and the eight-file ``LIMIT``/``OFFSET`` slice.
    """
    ctx = FileListContext.from_screen(screen)
    page = await services.files.file_list_page(
        ctx.view,
        ctx.value,
        span=ctx.period,
        sort=ctx.sort,
        anchor=ctx.anchor,
        page=ctx.page,
    )
    # A clamped page or a normalised filter must be what the next tap carries.
    ctx = replace(ctx, page=page.page, period=page.span, sort=page.sort, anchor=page.anchor)

    folders: list[Folder] = []
    folder_total = 0
    if ctx.view == "search" and ctx.value:
        folders, folder_total = await services.files.search_folders(
            ctx.value, int(screen.get("u") or 0)
        )
    return (
        file_list_text(ctx, page, folder_total),
        file_list_kb(ctx, page, folders),
    )


__all__ = [
    "FileListCB",
    "FileListContext",
    "callback_data",
    "file_list_kb",
    "file_list_text",
    "new_screen",
    "period_picker_kb",
    "period_picker_text",
    "screen",
    "sort_menu_kb",
    "sort_menu_text",
]

"""View helpers: the single persistent screen plus all text builders.

Every user-facing string goes through :func:`src.core.i18n.pick`, so the same
screen exists in English (the default) and in Arabic. Both versions sit next to
each other: a screen is never half translated.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.core.i18n import Language, language_name, pick, plural
from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import escape_text, format_number, human_size, human_total
from src.utils.sorting import SortDirection, SortField, SortSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.services.pending import PendingFile

_NOT_MODIFIED = "message is not modified"
_MAX_TRACKED_MESSAGES = 100
# When the main menu is opened, everything in the recent window is removed as
# well: after a restart the bot no longer knows the ids of older messages, so the
# window is what guarantees that delivered and uploaded files really disappear.
# Every id here is a blind deletion, and one already-gone id makes Telegram reject
# the whole batch, which then costs a call per message. The tracked ids cover the
# screens the bot knows, so the window only has to cover one busy flow without it:
# the ten files of an album, their status bubble, the result screen and the notices
# around them — fifteen messages, with room to spare.
_WIPE_WINDOW = 20
_MAX_IDS_PER_CALL = 100

logger = logging.getLogger(__name__)

# The order of the category buttons (and of their lines) on the library screen.
CATEGORY_ORDER: tuple[FileCategory, ...] = (
    FileCategory.image,
    FileCategory.video,
    FileCategory.document,
    FileCategory.audio,
    FileCategory.archive,
    FileCategory.other,
)


def category_label(category: FileCategory | str) -> str:
    """The name of a category, in the active language."""
    try:
        parsed = category if isinstance(category, FileCategory) else FileCategory(str(category))
    except ValueError:
        return str(category)
    return {
        FileCategory.document: pick("📄 Documents", "📄 مستندات"),
        FileCategory.image: pick("🖼 Images", "🖼 صور"),
        FileCategory.video: pick("🎬 Video", "🎬 فيديو"),
        FileCategory.audio: pick("🎧 Audio", "🎧 صوت"),
        FileCategory.archive: pick("🗜 Archives", "🗜 أرشيف"),
        FileCategory.other: pick("📦 Other", "📦 أخرى"),
    }.get(parsed, parsed.value)


def fallback_label() -> str:
    """One label for the emergency group (a store, never a library category)."""
    return pick("🛟 Emergency", "🛟 احتياطية")


# The two nouns every count on a screen is made of, in both languages.
_FILES = ("file", "files", ("ملف واحد", "ملفان", "ملفات", "ملفاً"))
_FOLDERS = ("folder", "folders", ("مجلد واحد", "مجلدان", "مجلدات", "مجلداً"))


def count_files(count: int) -> str:
    """``1 file`` / ``2 files``, and their Arabic forms."""
    return plural(count, *_FILES)


def count_folders(count: int) -> str:
    """``1 folder`` / ``2 folders``, and their Arabic forms."""
    return plural(count, *_FOLDERS)


def sort_summary(sort: SortSpec) -> str:
    """Human description of the current ordering."""
    return {
        (SortField.size, SortDirection.asc): pick(
            "📦 Size — smallest first", "📦 الحجم — الأصغر أولاً"
        ),
        (SortField.size, SortDirection.desc): pick(
            "📦 Size — largest first", "📦 الحجم — الأكبر أولاً"
        ),
        (SortField.date, SortDirection.asc): pick(
            "🕐 Date — oldest first", "🕐 التاريخ — الأقدم أولاً"
        ),
        (SortField.date, SortDirection.desc): pick(
            "🕐 Date — newest first", "🕐 التاريخ — الأحدث أولاً"
        ),
        (SortField.name, SortDirection.asc): pick(
            "🔤 Name — A→Z / أ→ي", "🔤 الاسم — أ→ي / A→Z"
        ),
        (SortField.name, SortDirection.desc): pick(
            "🔤 Name — Z→A / ي→أ", "🔤 الاسم — ي→أ / Z→A"
        ),
    }.get((sort.field, sort.direction), "")


def sort_short(sort: SortSpec) -> str:
    """Compact ordering for the «Sort» button label."""
    arrow = "⬆" if sort.direction is SortDirection.asc else "⬇"
    name = {
        SortField.size: pick("Size", "الحجم"),
        SortField.date: pick("Date", "التاريخ"),
        SortField.name: pick("Name", "الاسم"),
    }[sort.field]
    return f"{name} {arrow}"


def sort_menu_text(sort: SortSpec) -> str:
    """The sorting panel: current choice plus what each button does."""
    return "\n".join(
        [
            pick("🔃 <b>Sort the files</b>", "🔃 <b>فرز وترتيب الملفات</b>"),
            "",
            pick(
                f"Current ordering: <b>{sort_summary(sort)}</b>",
                f"الترتيب الحالي: <b>{sort_summary(sort)}</b>",
            ),
            "",
            pick(
                "Pick an ordering; the list is refreshed in place.",
                "اختر ترتيباً؛ تتحدّث القائمة في مكانها دون إعادة فتحها.",
            ),
            pick(
                "The ordering covers every file of the category, not the page, and "
                "returns you to the first page because the page numbers change.",
                "الفرز يشمل كل ملفات الفئة لا الصفحة المعروضة، ويعود بك إلى "
                "الصفحة الأولى لأن ترقيم الصفحات يتغيّر مع كل ترتيب.",
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# Single-screen helpers
# --------------------------------------------------------------------------- #
async def delete_screen_message(bot: Bot, chat_id: int, message_id: int) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass


async def remember(state: FSMContext, *message_ids: int) -> None:
    """Track messages the main menu should wipe together with the results."""
    ids = [int(mid) for mid in message_ids if mid]
    if not ids:
        return
    data = await state.get_data()
    tracked = list(data.get("cleanup_ids") or [])
    tracked.extend(ids)
    await state.update_data(cleanup_ids=tracked[-_MAX_TRACKED_MESSAGES:])


async def _delete_many(bot: Bot, chat_id: int, message_ids: Sequence[int]) -> None:
    """Delete a list of messages in as few requests as possible."""
    ids = [int(mid) for mid in message_ids if mid]
    for start in range(0, len(ids), _MAX_IDS_PER_CALL):
        chunk = ids[start : start + _MAX_IDS_PER_CALL]
        if len(chunk) == 1:
            await delete_screen_message(bot, chat_id, chunk[0])
            continue
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
        except TelegramAPIError:
            # Mixed batches (already deleted, too old, foreign) may be rejected as
            # a whole; fall back to removing them one by one.
            for message_id in chunk:
                await delete_screen_message(bot, chat_id, message_id)


async def clear_tracked(
    bot: Bot, chat_id: int, state: FSMContext, upto: int | None = None
) -> None:
    """Delete every tracked message of this chat, leaving an empty log."""
    data = await state.get_data()
    tracked = {int(mid) for mid in (data.get("cleanup_ids") or []) if mid}
    screen_id = data.get("screen_message_id")
    if screen_id:
        tracked.add(int(screen_id))

    if upto:
        upto = int(upto)
        tracked.add(upto)
        tracked.update(range(max(1, upto - _WIPE_WINDOW + 1), upto + 1))

    await _delete_many(bot, chat_id, sorted(tracked))
    await state.update_data(cleanup_ids=[], screen_message_id=None)


async def _render_screen(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> bool:
    """Edit one message in place. Returns True when the screen is up to date."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True
    except TelegramBadRequest as exc:
        if _NOT_MODIFIED in str(exc).lower():
            return True
        return False
    except TelegramAPIError:
        return False


async def reset_flow(state: FSMContext, *, keep_nav: bool = True) -> None:
    """End the current workflow, keeping the screen, the cleanup list and the trail."""
    data = await state.get_data()
    kept = {
        "screen_message_id": data.get("screen_message_id"),
        "cleanup_ids": data.get("cleanup_ids"),
    }
    if keep_nav:
        kept["trail"] = data.get("trail")
        kept["screen_key"] = data.get("screen_key")
        # A copy sent from a file screen waits for «↩️ Back» to remove it, so it
        # must survive the resets of the flows in between; the main menu drops it
        # with ``keep_nav=False`` and wipes the copy through ``cleanup_ids``.
        kept["delivered_ids"] = data.get("delivered_ids")
    await state.clear()
    await state.update_data(**{name: value for name, value in kept.items() if value})


async def set_screen(
    bot: Bot,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Render the screen by editing the tracked message, or send a first one."""
    chat_id = message.chat.id
    screen_id = (await state.get_data()).get("screen_message_id")

    if screen_id and await _render_screen(bot, chat_id, screen_id, text, reply_markup):
        return

    if screen_id:
        await delete_screen_message(bot, chat_id, screen_id)

    sent = await message.answer(
        text, reply_markup=reply_markup, disable_web_page_preview=True
    )
    await state.update_data(screen_message_id=sent.message_id)
    await remember(state, sent.message_id)


async def ack(
    callback: CallbackQuery, text: str | None = None, *, show_alert: bool = False
) -> None:
    """Show a short notice for a button press, if there is one to show.

    ``AckMiddleware`` already acknowledged the query when it arrived, so the
    spinner is gone before the screen is fetched and there is nothing left to
    acknowledge here: Telegram rejects a second acknowledgement, which used to
    cost a rejected API call on every tap. A call without text therefore does
    nothing at all. The text is shown as an alert, and when the query is too old
    to carry a popup it is delivered as a normal message instead, so a notice the
    user must see is never lost.
    """
    if not text:
        return
    try:
        await callback.answer(text, show_alert=show_alert)
        return
    except TelegramAPIError:
        logger.debug("Could not acknowledge the callback query", exc_info=True)
    try:
        if callback.message is not None:
            await callback.message.answer(text)
    except TelegramAPIError:
        logger.debug("Could not deliver the notice", exc_info=True)


async def _safe_answer(
    callback: CallbackQuery, text: str | None = None, alert: bool = False
) -> None:
    """Backwards-compatible wrapper around :func:`ack`."""
    await ack(callback, text, show_alert=alert)


async def set_screen_cb(
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Same as :func:`set_screen` for callback queries.

    The tap itself was already acknowledged by ``AckMiddleware``, before any
    query ran, so rendering does not acknowledge it again: a second
    acknowledgement is rejected by Telegram and would only cost a request.
    """
    if callback.message is None:
        await _safe_answer(
            callback,
            pick(
                "This message has expired, use /start.",
                "انتهت صلاحية هذه الرسالة، استخدم /start.",
            ),
            True,
        )
        return

    bot = callback.bot
    chat_id = callback.message.chat.id
    screen_id = (await state.get_data()).get("screen_message_id")

    if screen_id and await _render_screen(bot, chat_id, screen_id, text, reply_markup):
        return

    if screen_id:
        await delete_screen_message(bot, chat_id, screen_id)

    # No tracked screen: reuse the message the button belongs to when possible.
    if await _render_screen(
        bot, chat_id, callback.message.message_id, text, reply_markup
    ):
        await state.update_data(screen_message_id=callback.message.message_id)
        await remember(state, callback.message.message_id)
        return

    sent = await callback.message.answer(
        text, reply_markup=reply_markup, disable_web_page_preview=True
    )
    await state.update_data(screen_message_id=sent.message_id)
    await remember(state, sent.message_id)


# --------------------------------------------------------------------------- #
# Text builders
# --------------------------------------------------------------------------- #
def welcome_text(total_files: int = 0, total_bytes: int = 0) -> str:
    return "\n".join(
        [
            pick("👋 Welcome to TG-Space-Bot", "👋 مرحباً بك في TG-Space-Bot"),
            pick(
                f"🗂 {count_files(total_files)} · {escape_text(human_total(total_bytes))} "
                "— send a file to save it, or use the buttons below.",
                f"🗂 {count_files(total_files)} · {escape_text(human_total(total_bytes))} "
                "— أرسل ملفاً لحفظه، أو استخدم الأزرار أدناه.",
            ),
        ]
    )


def settings_text() -> str:
    current = pick("English", "الإنجليزية")
    return "\n".join(
        [
            pick("⚙️ <b>Settings</b>", "⚙️ <b>الإعدادات</b>"),
            pick(f"🌐 Language: <b>{current}</b>", f"🌐 اللغة: <b>{current}</b>"),
            pick(
                "🔗 Join the groups — owner only",
                "🔗 الانضمام إلى المجموعات — للمالك فقط",
            ),
            pick("❓ Help — commands and screens", "❓ مساعدة — الأوامر والشاشات"),
        ]
    )


def language_text(current: Language) -> str:
    """The language picker; the active language is marked in its own name."""
    return "\n".join(
        [
            pick("🌐 <b>Interface language</b>", "🌐 <b>لغة الواجهة</b>"),
            "",
            pick(
                f"Current language: <b>{language_name(current)}</b>",
                f"اللغة الحالية: <b>{language_name(current)}</b>",
            ),
            "",
            pick(
                "Pick a language; every screen is redrawn in it immediately and the "
                "choice is remembered for your account.",
                "اختر لغة؛ تُعاد كل الشاشات بها فوراً ويُحفظ اختيارك لحسابك.",
            ),
        ]
    )


def language_changed_text(language: Language) -> str:
    return pick(
        f"✅ Language changed to <b>{language_name(language)}</b>.",
        f"✅ تغيّرت اللغة إلى <b>{language_name(language)}</b>.",
    )


def help_text() -> str:
    return "\n".join(
        [
            pick("📖 <b>Help</b>", "📖 <b>مساعدة</b>"),
            pick(
                "• Send any file to save it in its category group.",
                "• أرسل أي ملف ليُحفظ في مجموعة فئته.",
            ),
            pick(
                "• /library · /search · /stats · /status · /cancel",
                "• /library · /search · /stats · /status · /cancel",
            ),
            pick(
                "• A file's page: send, delete, rename, or add to a folder.",
                "• صفحة الملف: إرسال، حذف، إعادة تسمية، أو إضافة إلى مجلد.",
            ),
            pick(
                "• «🔃 Sort» orders by size, date or name, both ways.",
                "• «🔃 فرز / ترتيب» يرتّب حسب الحجم أو التاريخ أو الاسم، تصاعدياً أو تنازلياً.",
            ),
            pick(
                "• A file whose registration fails waits in the reserve; «🔧 Repair» "
                "registers it.",
                "• الملف الذي يفشل تسجيله يبقى في الاحتياط، و«🔧 إصلاح» يسجّله.",
            ),
        ]
    )


def _storage_check_label(check: Any) -> str:
    """The name of a storage group: its categories, or the emergency group."""
    if getattr(check, "is_fallback", False):
        return fallback_label()
    categories = getattr(check, "categories", ()) or ()
    if not categories:
        return "—"
    return "، ".join(
        category_label(category)
        for category in sorted(categories, key=lambda item: item.value)
    )


def stats_text(stats: Mapping[str, Any], checks: Sequence[Any] | None = None) -> str:
    """The «Statistics» screen: the counts, each next to the group holding it.

    The group list used to live in «General status»; it is merged here so one line
    answers both questions — how many files of a category there are, and which
    group stores them.
    """
    total = int(stats.get("total", 0))
    total_bytes = int(stats.get("total_bytes", 0))
    categories = stats.get("categories") or {}
    checks = list(checks or ())

    by_category: dict[FileCategory, Any] = {}
    for check in checks:
        for category in getattr(check, "categories", ()) or ():
            by_category[category] = check

    lines = [
        pick("📊 <b>Library statistics</b>", "📊 <b>إحصائيات المكتبة</b>"),
        "",
        pick(
            f"Total files: <b>{format_number(total)}</b>",
            f"إجمالي الملفات: <b>{format_number(total)}</b>",
        ),
        pick(
            f"Total size: <b>{escape_text(human_size(total_bytes))}</b>",
            f"الحجم الإجمالي: <b>{escape_text(human_size(total_bytes))}</b>",
        ),
    ]
    if categories:
        lines.append("")
        for category, count in categories.items():
            parsed: FileCategory | None
            try:
                parsed = (
                    category
                    if isinstance(category, FileCategory)
                    else FileCategory(str(category))
                )
            except ValueError:
                parsed = None
            check = by_category.get(parsed) if parsed is not None else None
            line = f"{category_label(category)}: {format_number(int(count))}"
            if check is None:
                lines.append(line)
                continue
            lines.append(
                f"{'✅' if check.reachable else '⛔'} {line} "
                f"— <code>{check.chat_id}</code>"
            )

    # Groups that hold no category of their own (the emergency group) still
    # belong here: their id was part of the same list.
    extras = [check for check in checks if not getattr(check, "categories", ())]
    if extras:
        lines.append("")
        for check in extras:
            lines.append(
                f"{'✅' if check.reachable else '⛔'} {_storage_check_label(check)} "
                f"— <code>{check.chat_id}</code>"
            )

    for check in checks:
        if check.reachable or not check.reason:
            continue
        lines.append(
            f"   ⚠️ {_storage_check_label(check)}: {escape_text(check.reason[:160])}"
        )
    return "\n".join(lines)


def categories_text(
    totals: Mapping[FileCategory, tuple[int, int]],
    favorites: Sequence[Folder] = (),
    folder_count: int = 0,
) -> str:
    """The library: the categories, then the favourite folders, then the rest.

    The full folder list keeps its own screen, where it is sorted alphabetically
    and paged, so only the favourites appear here.
    """
    total_files = sum(count for count, _size in totals.values())
    total_bytes = sum(size for _count, size in totals.values())

    lines = [
        pick("📚 <b>Library</b>", "📚 <b>المكتبة</b>"),
        "",
        pick(
            f"Total: {count_files(total_files)} · {escape_text(human_total(total_bytes))}",
            f"الإجمالي: {count_files(total_files)} · {escape_text(human_total(total_bytes))}",
        ),
        "",
        pick("<b>Categories</b>", "<b>الفئات</b>"),
    ]
    ordered = [category for category in CATEGORY_ORDER if category in totals]
    ordered += [category for category in totals if category not in ordered]
    for category in ordered:
        count, size = totals[category]
        lines.append(
            f"{category_label(category)} — {count_files(count)} · "
            f"{escape_text(human_total(size))}"
        )

    lines.append("")
    if favorites:
        lines.append(pick("<b>⭐ Favourite folders</b>", "<b>⭐ المجلدات المفضلة</b>"))
        for folder in favorites:
            lines.append(
                f"⭐ {escape_text(folder.name)} — {count_files(folder.count)}"
            )
    else:
        lines.append(
            pick(
                "⭐ No favourite folders yet — open a folder and press "
                "«⭐ Add to favourites» to pin it here.",
                "⭐ لا توجد مجلدات مفضلة — افتح أي مجلد ثم اضغط "
                "«⭐ إضافة إلى المفضلة» ليظهر هنا.",
            )
        )
    lines.append("")
    lines.append(
        pick(
            f"📁 Folders: {format_number(folder_count)} — open the full list with "
            "«📁 Folders».",
            f"📁 المجلدات: {format_number(folder_count)} — افتح القائمة الكاملة بزر "
            "«📁 المجلدات».",
        )
    )
    return "\n".join(lines)


def category_text(
    category: FileCategory | str,
    files: Sequence[StoredFile],
    totals: tuple[int, int] | None = None,
    sort: SortSpec | None = None,
    page: int = 1,
    pages: int = 1,
) -> str:
    """One page of a category: totals, the ordering and where the page stands."""
    label = category_label(category)
    count, size = (
        totals
        if totals is not None
        else (len(files), sum(file.size_bytes for file in files))
    )
    lines = [
        f"{label} — {count_files(count)} · {escape_text(human_total(size))}",
        "",
    ]
    if not files:
        lines.append(pick("There are no files in this category.", "لا توجد ملفات في هذه الفئة."))
    else:
        if pages > 1:
            lines.append(
                pick(f"📄 Page {page} of {pages}", f"📄 الصفحة {page} من {pages}")
            )
        lines.append(
            pick(
                f"🔃 Ordering: {sort_summary(sort or SortSpec())}",
                f"🔃 الترتيب: {sort_summary(sort or SortSpec())}",
            )
        )
        lines.append(
            pick(
                "Pick a file to open it, or change the ordering with the button below.",
                "اختر ملفاً لعرضه، أو غيّر الترتيب من الزر أدناه.",
            )
        )
    return "\n".join(lines).rstrip()


def pending_text(
    total: int,
    by_category: Mapping[FileCategory, int] | None = None,
    latest_name: str | None = None,
    latest_reason: str | None = None,
) -> str:
    """The reserve screen: what is waiting and how to repair it."""
    lines = [
        pick(
            "🛟 <b>Reserve and pending files</b>",
            "🛟 <b>قسم الاحتياط والملفات المعلقة</b>",
        ),
        "",
        pick(
            "A file whose registration in the database fails is stored here instead "
            "of being lost, and it does not enter the library until its row exists.",
            "الملف الذي تعذّر تسجيله في قاعدة البيانات يُحفظ هنا بدل ضياعه، "
            "ولا يدخل المكتبة حتى يُسجَّل صفّه.",
        ),
        "",
    ]
    if not total:
        lines.append(
            pick("✅ There are no pending files right now.", "✅ لا توجد ملفات معلّقة الآن.")
        )
        lines.append("")
        lines.append(
            pick(
                "Any file that fails to register appears here, ready for «🔧 Repair».",
                "سيظهر هنا أي ملف يفشل تسجيله، مع إمكانية إصلاحه بزر «🔧 إصلاح».",
            )
        )
        return "\n".join(lines)

    lines.append(
        pick(
            f"⏳ Pending files: <b>{format_number(total)}</b>",
            f"⏳ ملفات معلّقة: <b>{format_number(total)}</b>",
        )
    )
    counts = by_category or {}
    ordered = [category for category in CATEGORY_ORDER if category in counts]
    ordered += [category for category in counts if category not in CATEGORY_ORDER]
    if ordered:
        lines.append("")
        lines.append(pick("<b>By type</b>", "<b>حسب النوع</b>"))
        for category in ordered:
            lines.append(
                f"{category_label(category)}: {format_number(counts[category])}"
            )
    if latest_name:
        lines.append("")
        lines.append(pick(f"Latest file: {escape_text(latest_name)}", f"آخر ملف: {escape_text(latest_name)}"))
    if latest_reason:
        lines.append(pick(f"Reason: {escape_text(latest_reason)}", f"سبب الفشل: {escape_text(latest_reason)}"))
    lines.append("")
    lines.append(
        pick(
            "Once the database answers again press «🔧 Repair»: every file is "
            "registered, then moved into its correct group.",
            "بعد التأكد من عمل قاعدة البيانات اضغط «🔧 إصلاح»: يُسجَّل كل ملف "
            "ثم يُنقل إلى مجموعته الصحيحة حسب نوعه.",
        )
    )
    return "\n".join(lines)


def quarantined_text(
    file_name: str, category: FileCategory | str, reason: str = ""
) -> str:
    """A file that could not be registered and was held in the fallback group."""
    lines = [
        pick("🛟 <b>The file was held in the reserve</b>", "🛟 <b>حُفظ الملف في الاحتياط</b>"),
        "",
        pick(
            "It could not be registered in the database, so it did not enter the library.",
            "تعذّر تسجيل الملف في قاعدة البيانات، فلم يُنقل إلى المكتبة.",
        ),
        pick(f"File: {escape_text(file_name)}", f"الملف: {escape_text(file_name)}"),
        pick(f"Category: {category_label(category)}", f"الفئة: {category_label(category)}"),
    ]
    if reason:
        lines.append(pick(f"Reason: {escape_text(reason)}", f"السبب: {escape_text(reason)}"))
    lines.append("")
    lines.append(
        pick(
            "Once the database is back, open «🛟 Reserve and pending files» under "
            "General status and press «🔧 Repair».",
            "بعد عودة قاعدة البيانات افتح «🛟 قسم الاحتياط والملفات المعلقة» داخل "
            "الحالة العامة واضغط «🔧 إصلاح» ليُسجَّل الملف ثم يُنقل إلى مجموعته.",
        )
    )
    return "\n".join(lines)


def search_prompt_text() -> str:
    return "\n".join(
        [
            pick("🔍 <b>Search the library</b>", "🔍 <b>بحث في المكتبة</b>"),
            "",
            pick(
                "Type the file name, or part of it, and send it.",
                "اكتب اسم الملف أو جزءاً منه، ثم أرسله.",
            ),
            pick("You can cancel at any time.", "يمكنك الإلغاء في أي وقت."),
        ]
    )


def search_results_text(
    results: Sequence[StoredFile],
    query: str,
    page: int,
    total_pages: int,
    total: int | None = None,
) -> str:
    headline = pick(
        f"🔍 <b>Results for «{escape_text(query)}»</b>",
        f"🔍 <b>نتائج البحث عن «{escape_text(query)}»</b>",
    )
    if not results:
        return f"{headline}\n\n{pick('No matching files.', 'لا توجد نتائج مطابقة.')}"

    lines = [headline]
    if total is not None:
        lines.append(pick(f"Results: {format_number(total)}", f"عدد النتائج: {format_number(total)}"))
    lines.append("")
    for file in results:
        lines.append(f"• {escape_text(file.file_name)} — {escape_text(file.size_human)}")
    if total_pages > 1:
        lines.append("")
        lines.append(pick(f"Page {page} of {total_pages}", f"الصفحة {page} من {total_pages}"))
    return "\n".join(lines)


def file_saved_text(record: StoredFile) -> str:
    lines = [
        pick("✅ <b>The file was saved</b>", "✅ <b>تم حفظ الملف</b>"),
        "",
        pick(f"Name: {escape_text(record.file_name)}", f"الاسم: {escape_text(record.file_name)}"),
        pick(f"Category: {category_label(record.category)}", f"الفئة: {category_label(record.category)}"),
        pick(f"Size: {escape_text(record.size_human)}", f"الحجم: {escape_text(record.size_human)}"),
    ]
    if record.hash_short:
        lines.append(
            pick(
                f"Fingerprint: <code>{escape_text(record.hash_short)}</code>",
                f"بصمة المحتوى: <code>{escape_text(record.hash_short)}</code>",
            )
        )
    return "\n".join(lines)


def files_saved_text(
    records: Sequence[StoredFile],
    duplicates: Sequence[StoredFile] = (),
    quarantined: Sequence[PendingFile] = (),
    failures: Sequence[tuple[str, str]] = (),
) -> str:
    """The result of a whole album: every file by name, then what did not land.

    An album is saved item by item, so one card would hide the other files. This
    text lists all of them, and the keyboard keeps one row per file with its own
    buttons.
    """
    total = len(records) + len(duplicates)
    lines = [
        pick(
            f"✅ <b>{format_number(total)} files were saved</b>",
            f"✅ <b>تم حفظ {format_number(total)} ملفات</b>",
        ),
        "",
    ]
    for index, record in enumerate(records, start=1):
        lines.append(
            f"{index}. 📄 {escape_text(record.file_name)} — "
            f"{category_label(record.category)} · {escape_text(record.size_human)}"
        )

    if duplicates:
        lines.extend(
            [
                "",
                pick(
                    "♻️ <b>Already stored (no second copy):</b>",
                    "♻️ <b>موجودة أصلاً (دون نسخة ثانية):</b>",
                ),
            ]
        )
        for record in duplicates:
            lines.append(f"• 📄 {escape_text(record.file_name)}")

    if quarantined:
        lines.extend(
            [
                "",
                pick(
                    "🛟 <b>Held in the reserve group:</b>",
                    "🛟 <b>محفوظة في مجموعة الاحتياط:</b>",
                ),
            ]
        )
        for pending in quarantined:
            lines.append(f"• 📄 {escape_text(pending.file_name)}")

    if failures:
        lines.extend(
            [
                "",
                pick("❌ <b>Not saved:</b>", "❌ <b>لم تُحفظ:</b>"),
            ]
        )
        for file_name, reason in failures:
            lines.append(f"• {escape_text(file_name)} — <code>{reason}</code>")

    if records or duplicates:
        lines.extend(
            [
                "",
                pick(
                    "Open a file to use its own buttons, or send it straight to a "
                    "folder with «📁 Folder».",
                    "افتح الملف لتستعمل أزراره الخاصة، أو أرسله مباشرة إلى مجلد "
                    "بزر «📁 مجلد».",
                ),
            ]
        )
    return "\n".join(lines)


def duplicate_file_text(record: StoredFile) -> str:
    """The file is already in the library: no second copy was stored."""
    return "\n".join(
        [
            pick("♻️ <b>The file is already stored</b>", "♻️ <b>الملف موجود أصلاً</b>"),
            "",
            pick(f"Name: {escape_text(record.file_name)}", f"الاسم: {escape_text(record.file_name)}"),
            pick(f"Size: {escape_text(record.size_human)}", f"الحجم: {escape_text(record.size_human)}"),
            pick(
                f"Saved on: {escape_text(record.created_human)}",
                f"تاريخ الحفظ: {escape_text(record.created_human)}",
            ),
            "",
            pick(
                "No new copy was made; this is the file already stored in its group.",
                "لم تُحفظ نسخة جديدة، وهذا هو الملف المحفوظ سابقاً في مجموعته.",
            ),
        ]
    )


def file_text(record: StoredFile, folders: Sequence[str] = ()) -> str:
    lines = [
        f"📄 <b>{escape_text(record.file_name)}</b>",
        "",
        pick(f"Category: {category_label(record.category)}", f"الفئة: {category_label(record.category)}"),
        pick(f"Size: {escape_text(record.size_human)}", f"الحجم: {escape_text(record.size_human)}"),
        pick(
            f"Saved on: {escape_text(record.created_human)}",
            f"تاريخ الحفظ: {escape_text(record.created_human)}",
        ),
        pick(
            f"Folders: {escape_text('، '.join(folders)) if folders else '—'}",
            f"المجلدات: {escape_text('، '.join(folders)) if folders else '—'}",
        ),
        (
            pick(
                f"Fingerprint: <code>{escape_text(record.hash_short)}</code>",
                f"بصمة المحتوى: <code>{escape_text(record.hash_short)}</code>",
            )
            if record.hash_short
            else pick(
                "Fingerprint: — (not computed, the file is above the hashing limit)",
                "بصمة المحتوى: — (لم تُحسب، الملف أكبر من حدّ الحساب)",
            )
        ),
    ]
    return "\n".join(lines)


def folders_text(
    folders: Sequence[Folder],
    page: int = 1,
    total_pages: int = 1,
    total: int | None = None,
) -> str:
    """One page of the folders, already sorted alphabetically."""
    lines = [
        pick("📁 <b>Folders</b>", "📁 <b>المجلدات</b>"),
        "",
        pick(
            "Virtual folders in alphabetical order — the files themselves stay in "
            "their Telegram groups.",
            "مجلدات افتراضية مرتّبة أبجدياً — الملفات نفسها تبقى في مجموعات تيليغرام.",
        ),
        "",
    ]
    if folders:
        lines.append(pick("Pick a folder to see its files:", "اختر مجلداً لعرض ملفاته:"))
        lines.append("")
        for folder in folders:
            star = "⭐" if folder.favorite else "📁"
            lines.append(
                f"{star} <b>{escape_text(folder.name)}</b> — {count_files(folder.count)} "
                f"· {escape_text(human_total(folder.bytes))}"
            )
    else:
        lines.append(
            pick(
                "No folders yet. Create the first one with «➕ New folder».",
                "لا توجد مجلدات بعد. أنشئ أول مجلد بـ«➕ مجلد جديد».",
            )
        )
    if total_pages > 1:
        lines.append("")
        if total is not None:
            lines.append(pick(f"Folders: {format_number(total)}", f"عدد المجلدات: {format_number(total)}"))
        lines.append(pick(f"Page {page} of {total_pages}", f"الصفحة {page} من {total_pages}"))
    return "\n".join(lines)


def folder_delete_text(folder: Folder) -> str:
    """Confirm deleting the label; the files are never part of this action."""
    return "\n".join(
        [
            pick("🗑 <b>Delete the folder</b>", "🗑 <b>حذف المجلد</b>"),
            "",
            pick(
                f"Folder: «{escape_text(folder.name)}» — {count_files(folder.count)}",
                f"المجلد: «{escape_text(folder.name)}» — {count_files(folder.count)}",
            ),
            "",
            pick(
                "Only the label is deleted: every file stays in the library and in "
                "its storage group unchanged.",
                "سيُحذف المجلد التنظيمي فقط، وتبقى جميع الملفات في المكتبة "
                "وفي مجموعات التخزين دون أي تغيير.",
            ),
            "",
            pick("Do you want to continue?", "هل تريد المتابعة؟"),
        ]
    )


def folder_text(
    folder: Folder,
    files: Sequence[StoredFile],
    sort: SortSpec | None = None,
) -> str:
    count = folder.count or len(files)
    size = folder.bytes or sum(file.size_bytes for file in files)
    lines = [
        pick(
            f"📁 <b>{escape_text(folder.name)}</b> — {count_files(count)} · "
            f"{escape_text(human_total(size))}",
            f"📁 <b>{escape_text(folder.name)}</b> — {count_files(count)} · "
            f"{escape_text(human_total(size))}",
        ),
        "",
        pick("⭐ In the favourites", "⭐ في المفضلة")
        if folder.favorite
        else pick("⭐ Not in the favourites", "⭐ ليست في المفضلة"),
        "",
    ]
    if files:
        lines.append(
            pick(
                f"🔃 Ordering: {sort_summary(sort or SortSpec())}",
                f"🔃 الترتيب: {sort_summary(sort or SortSpec())}",
            )
        )
        lines.append("")
        lines.append(pick("Pick a file to open it:", "اختر ملفاً لعرضه:"))
    else:
        lines.append(
            pick("No files in this folder yet.", "لا توجد ملفات في هذا المجلد بعد.")
        )
        lines.append(
            pick(
                "Add one from its page with «📁 Add to a folder».",
                "أضف ملفاً من صفحته عبر «📁 إضافة إلى مجلد».",
            )
        )
    return "\n".join(lines)


def pick_folder_text(record: StoredFile) -> str:
    return "\n".join(
        [
            pick("📁 <b>Add to a folder</b>", "📁 <b>إضافة إلى مجلد</b>"),
            "",
            pick(f"File: {escape_text(record.file_name)}", f"الملف: {escape_text(record.file_name)}"),
            "",
            pick("Pick the folder to link this file to:", "اختر المجلد الذي تريد ربط الملف به:"),
        ]
    )


def new_folder_text() -> str:
    return "\n".join(
        [
            pick("➕ <b>New folder</b>", "➕ <b>مجلد جديد</b>"),
            "",
            pick(
                "Send the folder name now (for example: lectures, family photos).",
                "أرسل اسم المجلد الآن (مثال: محاضرات، صور العائلة، مشروع التخرج).",
            ),
        ]
    )


def join_groups_text() -> str:
    return "\n".join(
        [
            pick(
                "🔗 <b>Joining the storage groups</b>",
                "🔗 <b>الدخول إلى مجموعات التخزين</b>",
            ),
            "",
            pick(
                "Pick a group and the bot creates an invite link valid for "
                "<b>one use only</b>.",
                "اختر مجموعة، وسيُنشئ البوت رابط دعوة صالحاً <b>لاستخدام واحد فقط</b>.",
            ),
            pick(
                "As soon as you join, the bot promotes you to admin in that group.",
                "بمجرد انضمامك سيرقّيك البوت مشرفاً تلقائياً في تلك المجموعة.",
            ),
        ]
    )


def invite_text(link: str, label: str, *, join_request: bool = False) -> str:
    """The invite screen; with the guard on the link is a join request, not a single use."""
    hint = (
        pick(
            "The link only asks to join, and it stops working after its first use:",
            "الرابط طلب انضمام فقط، ويتوقف عمله بعد أول استخدام:",
        )
        if join_request
        else pick(
            "The link works once, then it stops working:",
            "الرابط صالح لاستخدام واحد فقط ثم يصبح معطّلاً:",
        )
    )
    return "\n".join(
        [
            pick("✅ <b>New invite link</b>", "✅ <b>رابط دعوة جديد</b>"),
            "",
            pick(f"Group: {escape_text(label)}", f"المجموعة: {escape_text(label)}"),
            hint,
            "",
            f"<code>{escape_text(link)}</code>",
        ]
    )


def rename_prompt_text(record: StoredFile) -> str:
    return "\n".join(
        [
            pick("✏️ <b>Rename the file</b>", "✏️ <b>إعادة تسمية الملف</b>"),
            "",
            pick(
                f"Current name: {escape_text(record.file_name)}",
                f"الاسم الحالي: {escape_text(record.file_name)}",
            ),
            "",
            pick("Send the new name:", "أرسل الاسم الجديد كما تريده:"),
        ]
    )


def file_caption(record: StoredFile) -> str:
    return f"{escape_text(record.file_name)} — {escape_text(record.size_human)}"


def file_sent_text(record: StoredFile) -> str:
    return pick(
        f"✅ «{escape_text(record.file_name)}» was sent to the chat.",
        f"✅ تم إرسال «{escape_text(record.file_name)}» إلى المحادثة.",
    )


def deletion_text(record: StoredFile) -> str:
    return "\n".join(
        [
            pick("🗑 <b>Delete the file</b>", "🗑 <b>حذف الملف</b>"),
            "",
            pick(f"Name: {escape_text(record.file_name)}", f"الاسم: {escape_text(record.file_name)}"),
            pick(f"Size: {escape_text(record.size_human)}", f"الحجم: {escape_text(record.size_human)}"),
            "",
            pick(
                "Are you sure? This cannot be undone.",
                "هل أنت متأكد من الحذف؟ لا يمكن التراجع.",
            ),
        ]
    )

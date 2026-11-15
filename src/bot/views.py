"""View helpers: the single persistent screen plus all text builders."""

from __future__ import annotations

import html
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number, human_size, human_total

_NOT_MODIFIED = "message is not modified"
_MAX_TRACKED_MESSAGES = 100
# When the main menu is opened, everything in the recent window is removed as
# well: after a restart the bot no longer knows the ids of older messages, so the
# window is what guarantees that delivered and uploaded files really disappear.
_WIPE_WINDOW = 80
_MAX_IDS_PER_CALL = 100

logger = logging.getLogger(__name__)

CATEGORY_LABELS: dict[FileCategory, str] = {
    FileCategory.document: "📄 مستندات",
    FileCategory.image: "🖼 صور",
    FileCategory.video: "🎬 فيديو",
    FileCategory.audio: "🎧 صوت",
    FileCategory.archive: "🗜 أرشيف",
    FileCategory.other: "📦 أخرى",
}


def category_label(category: FileCategory | str) -> str:
    if isinstance(category, FileCategory):
        return CATEGORY_LABELS.get(category, category.value)
    try:
        return CATEGORY_LABELS[FileCategory(str(category))]
    except ValueError:
        return str(category)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


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


async def _safe_answer(
    callback: CallbackQuery, text: str | None = None, alert: bool = False
) -> None:
    """Acknowledge a button press.

    Telegram rejects the acknowledgement of a query older than a few minutes (it
    answers ``query is too old``), which happens whenever a button from before a
    bot restart is pressed. The screen itself is already rendered at that point,
    so a failed acknowledgement must never surface as a user-visible error.
    """
    try:
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text, show_alert=alert)
    except TelegramAPIError:
        logger.debug("Could not acknowledge a stale callback query", exc_info=True)


async def set_screen_cb(
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Same as :func:`set_screen` for callback queries, then clears the spinner."""
    if callback.message is None:
        await _safe_answer(callback, "انتهت صلاحية هذه الرسالة، استخدم /start.", True)
        return

    bot = callback.bot
    chat_id = callback.message.chat.id
    screen_id = (await state.get_data()).get("screen_message_id")

    if screen_id and await _render_screen(bot, chat_id, screen_id, text, reply_markup):
        await _safe_answer(callback)
        return

    if screen_id:
        await delete_screen_message(bot, chat_id, screen_id)

    # No tracked screen: reuse the message the button belongs to when possible.
    if await _render_screen(
        bot, chat_id, callback.message.message_id, text, reply_markup
    ):
        await state.update_data(screen_message_id=callback.message.message_id)
        await remember(state, callback.message.message_id)
        await _safe_answer(callback)
        return

    sent = await callback.message.answer(
        text, reply_markup=reply_markup, disable_web_page_preview=True
    )
    await state.update_data(screen_message_id=sent.message_id)
    await remember(state, sent.message_id)
    await _safe_answer(callback)


# --------------------------------------------------------------------------- #
# Text builders
# --------------------------------------------------------------------------- #
def welcome_text(total_files: int = 0, total_bytes: int = 0) -> str:
    return "\n".join(
        [
            "👋 مرحباً بك في TG-Space-Bot",
            "",
            "هذا البوت يحفظ ملفاتك في مجموعات تخزين منظمة تلقائياً.",
            "أرسل أي ملف الآن، أو تنقّل عبر الأزرار أدناه.",
            "",
            f"🗂 <b>إجمالي المكتبة</b>: {format_number(total_files)} ملف · "
            f"{_escape(human_total(total_bytes))}",
            "",
            "📚 المكتبة — تصفح الملفات حسب الفئة",
            "🔍 بحث — البحث بالاسم داخل المكتبة",
            "📊 إحصائيات — عدد الملفات والحجم الإجمالي",
            "📁 المجلدات — مجموعات افتراضية للتنظيم السريع",
        ]
    )


def help_text() -> str:
    return (
        "📖 <b>مساعدة</b>\n\n"
        "• أرسل أي ملف (مستند، صورة، فيديو، صوت، أرشيف) وسيُحفظ في مجموعته المناسبة.\n"
        "• /library لتصفح المكتبة حسب الفئة.\n"
        "• /search للبحث عن ملف محفوظ بالاسم.\n"
        "• /stats لعرض الإحصائيات.\n"
        "• /cancel لإلغاء أي عملية جارية.\n\n"
        "من صفحة أي ملف يمكنك: إرساله، حذفه، إعادة تسميته، أو إضافته إلى مجلد.\n"
        "المجلدات تسميات تنظيمية فقط؛ الملف يبقى في مجموعة تيليغرام الخاصة بفئته."
    )


def stats_text(stats: Mapping[str, Any]) -> str:
    total = int(stats.get("total", 0))
    total_bytes = int(stats.get("total_bytes", 0))
    categories = stats.get("categories") or {}

    lines = [
        "📊 <b>إحصائيات المكتبة</b>",
        "",
        f"إجمالي الملفات: <b>{format_number(total)}</b>",
        f"الحجم الإجمالي: <b>{_escape(human_size(total_bytes))}</b>",
    ]
    if categories:
        lines.append("")
        for category, count in categories.items():
            label = category_label(category)
            lines.append(f"{label}: {format_number(int(count))}")
    return "\n".join(lines)


def categories_text(
    totals: Mapping[FileCategory, tuple[int, int]],
    folders: Sequence[Folder] = (),
) -> str:
    """The library: every category with its count and total size, plus folders.

    The folders are listed here (not only in their own screen) so the library is the
    single place that shows everything the user has organised.
    """
    total_files = sum(count for count, _size in totals.values())
    total_bytes = sum(size for _count, size in totals.values())

    lines = [
        "📚 <b>المكتبة</b>",
        "",
        f"الإجمالي: {format_number(total_files)} ملف · "
        f"{_escape(human_total(total_bytes))}",
        "",
        "<b>الفئات</b>",
    ]
    for category, (count, size) in totals.items():
        lines.append(
            f"{category_label(category)} — {format_number(count)} ملف · "
            f"{_escape(human_total(size))}"
        )

    lines.append("")
    lines.append("<b>📁 المجلدات</b>")
    if folders:
        for folder in folders:
            lines.append(
                f"📁 {_escape(folder.name)} — {format_number(folder.count)} ملف · "
                f"{_escape(human_total(folder.bytes))}"
            )
    else:
        lines.append("لا توجد مجلدات بعد — أنشئ واحداً بـ«➕ مجلد جديد».")
    return "\n".join(lines)


def category_text(
    category: FileCategory | str,
    files: Sequence[StoredFile],
    totals: tuple[int, int] | None = None,
) -> str:
    """One category: its file count and total size, then the files themselves."""
    label = category_label(category)
    count, size = (
        totals
        if totals is not None
        else (len(files), sum(file.size_bytes for file in files))
    )
    lines = [
        f"{label} — {format_number(count)} ملف · {_escape(human_total(size))}",
        "",
    ]
    if not files:
        lines.append("لا توجد ملفات في هذه الفئة.")
    return "\n".join(lines).rstrip()


def search_prompt_text() -> str:
    return (
        "🔍 <b>بحث في المكتبة</b>\n\n"
        "اكتب اسم الملف أو جزءاً منه، ثم أرسله.\n"
        "يمكنك الإلغاء في أي وقت."
    )


def search_results_text(
    results: Sequence[StoredFile],
    query: str,
    page: int,
    total_pages: int,
    total: int | None = None,
) -> str:
    headline = f"🔍 <b>نتائج البحث عن «{_escape(query)}»</b>"
    if not results:
        return f"{headline}\n\nلا توجد نتائج مطابقة."

    lines = [headline]
    if total is not None:
        lines.append(f"عدد النتائج: {format_number(total)}")
    lines.append("")
    for file in results:
        lines.append(
            f"• {_escape(file.file_name)} — {_escape(file.size_human)}"
        )
    if total_pages > 1:
        lines.append("")
        lines.append(f"الصفحة {page} من {total_pages}")
    return "\n".join(lines)


def file_saved_text(record: StoredFile) -> str:
    return (
        "✅ <b>تم حفظ الملف</b>\n\n"
        f"الاسم: {_escape(record.file_name)}\n"
        f"الفئة: {category_label(record.category)}\n"
        f"الحجم: {_escape(record.size_human)}"
    )


def file_text(record: StoredFile, folders: Sequence[str] = ()) -> str:
    lines = [
        f"📄 <b>{_escape(record.file_name)}</b>",
        "",
        f"الفئة: {category_label(record.category)}",
        f"الحجم: {_escape(record.size_human)}",
        f"تاريخ الحفظ: {_escape(record.created_human)}",
        f"المجلدات: {_escape('، '.join(folders)) if folders else '—'}",
    ]
    return "\n".join(lines)


def folders_text(folders: Sequence[Folder]) -> str:
    lines = [
        "📁 <b>المجلدات</b>",
        "",
        "مجلدات افتراضية للتنظيم والاختصار — الملفات نفسها تبقى في مجموعات تيليغرام.",
        "",
    ]
    if folders:
        lines.append("اختر مجلداً لعرض ملفاته:")
        lines.append("")
        for folder in folders:
            lines.append(
                f"📁 <b>{_escape(folder.name)}</b> — "
                f"{format_number(folder.count)} ملف · "
                f"{_escape(human_total(folder.bytes))}"
            )
    else:
        lines.append("لا توجد مجلدات بعد. أنشئ أول مجلد بـ«➕ مجلد جديد».")
    return "\n".join(lines)


def folder_text(folder: Folder, files: Sequence[StoredFile]) -> str:
    count = folder.count or len(files)
    size = folder.bytes or sum(file.size_bytes for file in files)
    lines = [
        f"📁 <b>{_escape(folder.name)}</b> — {format_number(count)} ملف · "
        f"{_escape(human_total(size))}",
        "",
    ]
    if files:
        lines.append("اختر ملفاً لعرضه:")
    else:
        lines.append("لا توجد ملفات في هذا المجلد بعد.")
        lines.append("أضف ملفاً من صفحته عبر «📁 إضافة إلى مجلد».")
    return "\n".join(lines)


def pick_folder_text(record: StoredFile) -> str:
    return (
        "📁 <b>إضافة إلى مجلد</b>\n\n"
        f"الملف: {_escape(record.file_name)}\n\n"
        "اختر المجلد الذي تريد ربط الملف به:"
    )


def new_folder_text() -> str:
    return (
        "➕ <b>مجلد جديد</b>\n\n"
        "أرسل اسم المجلد الآن (مثال: محاضرات، صور العائلة، مشروع التخرج)."
    )


def rename_prompt_text(record: StoredFile) -> str:
    return (
        "✏️ <b>إعادة تسمية الملف</b>\n\n"
        f"الاسم الحالي: {_escape(record.file_name)}\n\n"
        "أرسل الاسم الجديد كما تريده:"
    )


def file_caption(record: StoredFile) -> str:
    return f"{_escape(record.file_name)} — {_escape(record.size_human)}"


def file_sent_text(record: StoredFile) -> str:
    return f"✅ تم إرسال «{_escape(record.file_name)}» إلى المحادثة."


def deletion_text(record: StoredFile) -> str:
    return (
        "🗑 <b>حذف الملف</b>\n\n"
        f"الاسم: {_escape(record.file_name)}\n"
        f"الحجم: {_escape(record.size_human)}\n\n"
        "هل أنت متأكد من الحذف؟ لا يمكن التراجع."
    )

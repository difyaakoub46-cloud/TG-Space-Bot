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

from src.db.models import StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number, human_size

_NOT_MODIFIED = "message is not modified"
_MAX_TRACKED_MESSAGES = 100

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


async def clear_tracked(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Delete every tracked message of this chat, leaving an empty log."""
    data = await state.get_data()
    tracked = list(dict.fromkeys(data.get("cleanup_ids") or []))
    screen_id = data.get("screen_message_id")
    if screen_id and screen_id not in tracked:
        tracked.append(screen_id)

    for message_id in tracked:
        await delete_screen_message(bot, chat_id, message_id)

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


async def reset_flow(state: FSMContext) -> None:
    """End the current workflow, keeping the screen and the cleanup list."""
    data = await state.get_data()
    screen_id = data.get("screen_message_id")
    cleanup_ids = data.get("cleanup_ids")
    await state.clear()
    if screen_id:
        await state.update_data(screen_message_id=screen_id)
    if cleanup_ids:
        await state.update_data(cleanup_ids=cleanup_ids)


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
def welcome_text() -> str:
    return (
        "👋 مرحباً بك في TG-Space-Bot\n\n"
        "هذا البوت يحفظ ملفاتك في مجموعات تخزين منظمة تلقائياً.\n"
        "أرسل أي ملف الآن، أو تنقّل عبر الأزرار أدناه.\n\n"
        "📚 المكتبة — تصفح الملفات حسب الفئة\n"
        "🔍 بحث — البحث بالاسم داخل المكتبة\n"
        "📊 إحصائيات — عدد الملفات والحجم الإجمالي"
    )


def help_text() -> str:
    return (
        "📖 <b>مساعدة</b>\n\n"
        "• أرسل أي ملف (مستند، صورة، فيديو، صوت، أرشيف) وسيُحفظ في مجموعته المناسبة.\n"
        "• /library لتصفح المكتبة حسب الفئة.\n"
        "• /search للبحث عن ملف محفوظ بالاسم.\n"
        "• /stats لعرض الإحصائيات.\n"
        "• /cancel لإلغاء أي عملية جارية.\n\n"
        "كل التنقل يتم عبر الأزرار وتعديل الرسالة نفسها، دون تراكم رسائل."
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


def categories_text(counts: Mapping[FileCategory | str, int]) -> str:
    lines = ["📚 <b>المكتبة</b>", "", "اختر فئة لعرض ملفاتها:"]
    if not counts:
        lines.append("لا توجد ملفات محفوظة بعد.")
    return "\n".join(lines)


def category_text(category: FileCategory | str, files: Sequence[StoredFile]) -> str:
    label = category_label(category)
    lines = [f"{label} — <b>{format_number(len(files))}</b> ملف", ""]
    if not files:
        lines.append("لا توجد ملفات في هذه الفئة.")
    return "\n".join(lines)


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


def file_text(record: StoredFile) -> str:
    return (
        f"📄 <b>{_escape(record.file_name)}</b>\n\n"
        f"الفئة: {category_label(record.category)}\n"
        f"الحجم: {_escape(record.size_human)}\n"
        f"تاريخ الحفظ: {_escape(record.created_human)}"
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

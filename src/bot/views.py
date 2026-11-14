"""View helpers: single persistent screen and text builders."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.core.config import Settings
from src.db.models import StoredFile


async def delete_screen_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass


async def set_screen(
    bot: Bot,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    screen_id = (await state.get_data()).get("screen_message_id")
    try:
        if screen_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=screen_id,
                text=text,
                reply_markup=reply_markup,
            )
            await state.update_data(screen_message_id=screen_id)
            return
    except TelegramAPIError:
        await delete_screen_message(bot, message.chat.id, screen_id or 0)

    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data(screen_message_id=sent.message_id)


async def set_screen_cb(
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    screen_id = (await state.get_data()).get("screen_message_id")
    try:
        if screen_id:
            await callback.message.edit_text(
                text=text,
                reply_markup=reply_markup,
            )
            await state.update_data(screen_message_id=screen_id)
            await callback.answer()
            return
    except TelegramAPIError:
        await delete_screen_message(callback.bot, callback.message.chat.id, screen_id or 0)

    sent = await callback.message.answer(text, reply_markup=reply_markup)
    await state.update_data(screen_message_id=sent.message_id)
    await callback.answer()


def welcome_text(settings: Settings | None = None) -> str:
    return (
        "👋 مرحباً بك في TG-Space-Bot\n\n"
        "هذا البوت يحفظ ملفاتك في مجموعات تخزين منظمة.\n"
        "يمكنك تصفح المكتبة، البحث عن ملفات، ورؤية الإحصائيات.\n\n"
        "استخدم الأزرار أدناه للبدء."
    )


def help_text() -> str:
    return (
        "📖 <b>مساعدة</b>\n\n"
        "- أرسِل أي ملف وسيتم حفظه تلقائياً في مجموعة التخزين المناسبة.\n"
        "- استخدم /library لتصفح المكتبة.\n"
        "- استخدم /search للبحث عن ملفات محفوظة.\n"
        "- استخدم /stats لرؤية إحصائيات المكتبة.\n\n"
        "كل التنقل يتم عبر الأزرارinline لتقليل الرسائل."
    )


def stats_text(stats: dict[str, int]) -> str:
    total = stats.get("total", 0)
    return (
        f"📊 <b>إحصائيات المكتبة</b>\n\n"
        f"إجمالي الملفات: {total}\n"
    )


def categories_text(counts: dict[str, int]) -> str:
    lines = ["📚 <b>المكتبة</b>"]
    for category, count in counts.items():
        lines.append(f"- {category.capitalize()}: {count}")
    return "\n".join(lines)


def search_prompt_text() -> str:
    return (
        "🔍 <b>أدخل اسم الملف</b>\n\n"
        "اكتب اسم الملف أو جزء منه للبحث.\n"
        "يمكنك إلغاء العملية بأي وقت."
    )


def deletion_text(record: StoredFile) -> str:
    return (
        f"🗑 <b>حذف الملف</b>\n\n"
        f"اسم الملف: {record.file_name}\n"
        f"الحجم: {record.size_bytes} بايت\n\n"
        "هل أنت متأكد من الحذف؟"
    )


async def show(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramAPIError:
        pass

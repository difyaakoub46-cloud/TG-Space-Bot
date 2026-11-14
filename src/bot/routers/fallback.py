"""Catch-all handlers for unsupported input."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import main_reply_kb

router = Router(name="fallback")


@router.message(F.chat.type == ChatType.PRIVATE)
async def unknown_message(message: Message) -> None:
    await message.answer(
        "🤔 لم أفهم هذه الرسالة.\n"
        "أرسل ملفاً لحفظه في المكتبة، أو استخدم /search للبحث عن ملف محفوظ.",
        reply_markup=main_reply_kb(),
    )


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("هذا الزر لم يعد صالحاً، استخدم /start.", show_alert=False)

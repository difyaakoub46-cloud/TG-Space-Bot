"""Catch-all handlers for unsupported input."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import main_menu_kb

router = Router(name="fallback")


@router.message(F.chat.type == ChatType.PRIVATE)
async def unknown_message(message: Message) -> None:
    await message.answer(
        "🤔 لم أفهم هذه الرسالة.\n"
        "أرسل ملفاً لحفظه في المكتبة، أو استخدم الأزرار أدناه.",
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("هذا الزر لم يعد صالحاً، استخدم /start.", show_alert=False)

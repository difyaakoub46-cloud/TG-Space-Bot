"""Catch-all handlers for unsupported input."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import main_menu_kb
from src.core.i18n import pick

router = Router(name="fallback")


@router.message(F.chat.type == ChatType.PRIVATE)
async def unknown_message(message: Message, state: FSMContext) -> None:
    sent = await message.answer(
        pick(
            "🤔 I did not understand that message.\n"
            "Send a file to save it in the library, or use the buttons below.",
            "🤔 لم أفهم هذه الرسالة.\n"
            "أرسل ملفاً لحفظه في المكتبة، أو استخدم الأزرار أدناه.",
        ),
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )
    # Keep the hint in the tracked list so the main menu removes it as well.
    await views.remember(state, sent.message_id)


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await views.ack(callback,
        pick(
            "This button is no longer valid, use /start.",
            "هذا الزر لم يعد صالحاً، استخدم /start.",
        ),
        show_alert=False,
    )

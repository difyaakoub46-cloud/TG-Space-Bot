"""File ingestion: route every incoming file to its Telegram storage group."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot import views
from src.bot.keyboards import file_actions_kb, main_menu_kb
from src.services.container import Services
from src.utils.filetypes import extract_candidate
from src.utils.telegram import safe_edit_text

logger = logging.getLogger(__name__)

router = Router(name="upload")

SUPPORTED_MEDIA = (
    F.document | F.photo | F.video | F.audio | F.voice | F.animation | F.video_note
)


@router.message(F.chat.type == ChatType.PRIVATE, SUPPORTED_MEDIA)
async def handle_upload(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    candidate = extract_candidate(message)
    if candidate is None:
        return

    await views.reset_flow(state)
    status = await message.answer("⏳ جارٍ رفع الملف إلى مجموعة التخزين…")

    try:
        record = await services.files.save_from_message(message, candidate)
    except Exception:  # noqa: BLE001 - every failure must be reported to the user
        logger.exception("Failed to store %s", candidate.file_name)
        await safe_edit_text(
            bot,
            status.chat.id,
            status.message_id,
            "❌ فشل حفظ الملف. تأكد من إعدادات مجموعات التخزين وصلاحيات البوت.",
            reply_markup=main_menu_kb(),
        )
        return

    await safe_edit_text(
        bot,
        status.chat.id,
        status.message_id,
        views.file_saved_text(record),
        reply_markup=file_actions_kb(record.id),
    )

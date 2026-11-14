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
async def handle_upload(message: Message, services: Services, state: FSMContext) -> None:
    candidate = extract_candidate(message)
    if candidate is None:
        return

    await state.clear()
    status = await message.answer("⏳ جارٍ رفع الملف إلى مجموعة التخزين…")

    try:
        record = await services.files.save_from_message(message, candidate)
    except Exception:  # noqa: BLE001 - report every failure to the user
        logger.exception("Failed to store %s", candidate.file_name)
        await safe_edit_text(
            services.storage._bot,
            status.chat.id,
            status.message_id,
            "❌ فشل حفظ الملف.",
        )
        return

    await safe_edit_text(
        services.storage._bot,
        status.chat.id,
        status.message_id,
        views.file_saved_text(record),
        reply_markup=file_actions_kb(record.id),
    )


async def start_search(message: Message, state: FSMContext, bot) -> None:
    await state.set_state(None)
    await views.set_screen(bot, message, state, views.search_prompt_text(), search_prompt_kb())


async def cancel_search(state: FSMContext, bot, message: Message) -> None:
    await state.clear()
    await views.set_screen(bot, message, state, "✅ تم الإلغاء.", main_menu_kb())

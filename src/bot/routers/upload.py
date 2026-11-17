"""File ingestion: move every incoming file into its storage group."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot import navigation, views
from src.bot.keyboards import main_menu_kb
from src.core.i18n import pick
from src.services.container import Services
from src.services.files import DuplicateFileError, QuarantinedFileError
from src.utils.filetypes import extract_candidate
from src.utils.telegram import safe_delete_message, safe_edit_text

logger = logging.getLogger(__name__)

router = Router(name="upload")

SUPPORTED_MEDIA = (
    F.document | F.photo | F.video | F.audio | F.voice | F.animation | F.video_note
)

def failed_text() -> str:
    """The failure line is built per update, so it follows the user's language."""
    return pick(
        "❌ Could not save the file. Check the storage-group settings and the bot's rights.",
        "❌ فشل حفظ الملف. تأكد من إعدادات مجموعات التخزين وصلاحيات البوت.",
    )


def _reason(exc: BaseException) -> str:
    """One short line describing why the copy failed, for fast diagnosis."""
    text = " ".join(str(exc).split())
    return html.escape(text[:200] or type(exc).__name__)


@router.message(F.chat.type == ChatType.PRIVATE, SUPPORTED_MEDIA)
async def handle_upload(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    candidate = extract_candidate(message)
    if candidate is None:
        return

    await views.reset_flow(state)
    # Track the incoming file too, so a copy that survives any failure is removed
    # the next time the main menu is opened.
    await views.remember(state, message.message_id)
    status = await message.answer(
        pick(
            "⏳ Moving the file into its storage group…",
            "⏳ جارٍ نقل الملف إلى مجموعة التخزين…",
        )
    )

    try:
        record = await services.files.save_from_message(message, candidate)
    except QuarantinedFileError as exc:
        # The row could not be written: the file was held in the fallback group
        # instead of the library, and «🔧 إصلاح» will finish the job later.
        await safe_delete_message(bot, message.chat.id, message.message_id)
        await safe_delete_message(bot, status.chat.id, status.message_id)
        await navigation.navigate_message(
            message,
            state,
            bot,
            services,
            {"k": "pending"},
            notice=views.quarantined_text(
                exc.pending.file_name,
                exc.pending.category,
                exc.pending.reason,
            ),
        )
        return
    except DuplicateFileError as exc:
        # The same Telegram file is already stored: no second copy, just its page.
        await safe_delete_message(bot, message.chat.id, message.message_id)
        await safe_delete_message(bot, status.chat.id, status.message_id)
        await navigation.navigate_message(
            message,
            state,
            bot,
            services,
            {"k": "file", "id": exc.record.id},
            notice=views.duplicate_file_text(exc.record),
        )
        return
    except Exception as exc:  # noqa: BLE001 - every failure must be reported
        logger.exception("Failed to store %s", candidate.file_name)
        await safe_edit_text(
            bot,
            status.chat.id,
            status.message_id,
            f"{failed_text()}\n\n<code>{_reason(exc)}</code>",
            reply_markup=main_menu_kb(),
        )
        return

    # The copy now lives in its storage group, so the original message and the
    # status bubble are removed. The result is rendered on the single persistent
    # screen instead, which keeps the chat free of one message per upload.
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await safe_delete_message(bot, status.chat.id, status.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "file", "id": record.id},
        notice=views.file_saved_text(record),
    )

"""File ingestion: move every incoming file into its storage group.

Telegram delivers an album (a media group) as several separate messages and never
says which one is the last. Handling each message on its own made every copy
rewrite the same single screen, so an album of six files ended up as one saved
card — the last file — and the actions on it belonged to that file only. An album
is therefore collected for a moment and handled as one batch, and the batch screen
keeps one button per file so each file can be opened, or sent into its own folder,
on its own.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot import navigation, views
from src.bot.keyboards import main_menu_kb, saved_files_kb
from src.core.i18n import pick
from src.db.models import StoredFile
from src.services.container import Services
from src.services.files import DuplicateFileError, QuarantinedFileError
from src.services.pending import PendingFile
from src.utils.filetypes import extract_candidate
from src.utils.telegram import safe_delete_message, safe_edit_text

logger = logging.getLogger(__name__)

router = Router(name="upload")

SUPPORTED_MEDIA = (
    F.document | F.photo | F.video | F.audio | F.voice | F.animation | F.video_note
)

# How long to wait for the remaining items of an album. Telegram sends them
# within milliseconds, but no update announces that the group is complete.
ALBUM_WINDOW = 1.5

# Albums being collected: ``(chat_id, media_group_id)`` → their messages.
_albums: dict[tuple[int, str], list[Message]] = {}


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


@dataclass(slots=True)
class _Batch:
    """What an album produced, so the summary can report every item by name."""

    saved: list[StoredFile] = field(default_factory=list)
    duplicates: list[StoredFile] = field(default_factory=list)
    quarantined: list[PendingFile] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.saved or self.duplicates or self.quarantined or self.failures)


@router.message(F.chat.type == ChatType.PRIVATE, SUPPORTED_MEDIA)
async def handle_upload(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    if extract_candidate(message) is None:
        return

    album_id = message.media_group_id
    if not album_id:
        await _handle_one(message, services, state, bot)
        return

    key = (message.chat.id, str(album_id))
    collected = _albums.get(key)
    if collected is not None:
        # A later item of an album already being collected: the first handler
        # owns the batch and will save every item once the window closes.
        collected.append(message)
        return

    collected = _albums[key] = [message]
    await asyncio.sleep(ALBUM_WINDOW)
    _albums.pop(key, None)
    await _handle_album(collected, services, state, bot)


async def _handle_one(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    """One file on its own: the usual status bubble, then its single card."""
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


async def _store(
    message: Message, candidate, services: Services, batch: _Batch
) -> None:
    """Save one album item, recording how it ended instead of raising."""
    try:
        batch.saved.append(await services.files.save_from_message(message, candidate))
    except QuarantinedFileError as exc:
        batch.quarantined.append(exc.pending)
    except DuplicateFileError as exc:
        batch.duplicates.append(exc.record)
    except Exception as exc:  # noqa: BLE001 - every failure must be reported
        logger.exception("Failed to store %s", candidate.file_name)
        batch.failures.append((candidate.file_name, _reason(exc)))


async def _handle_album(
    messages: list[Message], services: Services, state: FSMContext, bot
) -> None:
    """Save every file of an album, then report the whole batch at once."""
    first = messages[0]
    total = len(messages)

    await views.reset_flow(state)
    await views.remember(state, *(message.message_id for message in messages))
    status = await first.answer(
        pick(
            f"⏳ Moving {total} files into their storage groups…",
            f"⏳ جارٍ نقل {total} ملفات إلى مجموعات التخزين…",
        )
    )

    batch = _Batch()
    done = 0
    for message in messages:
        candidate = extract_candidate(message)
        if candidate is None:
            continue
        done += 1
        if done > 1:
            await safe_edit_text(
                bot,
                status.chat.id,
                status.message_id,
                pick(
                    f"⏳ Moving file {done} of {total}…",
                    f"⏳ نقل الملف {done} من {total}…",
                ),
            )
        await _store(message, candidate, services, batch)

    for message in messages:
        await safe_delete_message(bot, message.chat.id, message.message_id)
    await safe_delete_message(bot, status.chat.id, status.message_id)

    if batch.empty:
        return

    # An album that produced exactly one stored file keeps the normal card, as if
    # it had been sent alone.
    if len(batch.saved) == 1 and not (
        batch.duplicates or batch.quarantined or batch.failures
    ):
        record = batch.saved[0]
        await navigation.navigate_message(
            first,
            state,
            bot,
            services,
            {"k": "file", "id": record.id},
            notice=views.file_saved_text(record),
        )
        return

    user_id = int(first.from_user.id) if first.from_user else 0
    await navigation.navigate_message(
        first,
        state,
        bot,
        services,
        {"k": "categories", "u": user_id},
        notice=views.files_saved_text(
            batch.saved,
            batch.duplicates,
            batch.quarantined,
            batch.failures,
        ),
        markup=saved_files_kb(
            batch.saved,
            batch.duplicates,
            show_pending=bool(batch.quarantined),
        ),
    )

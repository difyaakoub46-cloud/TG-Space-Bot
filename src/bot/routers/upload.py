"""File ingestion: move every incoming file into its storage group.

Telegram delivers an album (a media group) as several separate messages and never
says which one is the last. The desktop client is worse: files picked together are
sent as separate messages with no album id at all. Handling each message on its own
made every copy rewrite the same single screen, so six files uploaded together
ended up as one saved card — the last file — and the actions on it belonged to that
file only. Incoming files are therefore collected for a moment and handled as one
batch, and the batch screen keeps one button per file so each file can be opened,
or sent into its own folder, on its own.
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
from src.bot.keyboards import main_menu_kb
from src.core.i18n import pick
from src.db.models import StoredFile
from src.services.container import Services
from src.services.files import DuplicateFileError, QuarantinedFileError
from src.services.pending import PendingFile
from src.utils.filetypes import extract_candidate
from src.utils.telegram import safe_delete_messages, safe_edit_text

logger = logging.getLogger(__name__)

router = Router(name="upload")

SUPPORTED_MEDIA = (
    F.document | F.photo | F.video | F.audio | F.voice | F.animation | F.video_note
)

# How long to wait for the remaining items of an upload. Telegram sends the items
# of an album within milliseconds, but no update announces that the group is
# complete; a selection of files the desktop client sends as separate messages
# gets a shorter window so a single file is not kept waiting for long.
ALBUM_WINDOW = 0.5
SINGLE_WINDOW = 0.4

# How many files of one batch are stored at the same time. The copies are
# independent Telegram calls, so a small window keeps a batch fast without
# running into the flood limits.
UPLOAD_CONCURRENCY = 3

# Uploads being collected: ``(chat_id, media_group_id or "single")`` → messages.
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
    key = (message.chat.id, str(album_id) if album_id else "single")
    collected = _albums.get(key)
    if collected is not None:
        # A later file of an upload already being collected: the first handler
        # owns the batch and will save every file once the window closes.
        collected.append(message)
        return

    collected = _albums[key] = [message]
    await asyncio.sleep(ALBUM_WINDOW if album_id else SINGLE_WINDOW)
    _albums.pop(key, None)

    if not album_id and len(collected) == 1:
        # One file on its own keeps its own card and its own failure text.
        await _handle_one(message, services, state, bot)
        return
    await _handle_album(collected, services, state, bot)


async def _handle_one(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    """One file on its own: the usual status bubble, then its single card."""
    candidate = extract_candidate(message)
    if candidate is None:
        return

    # «Settings → 📨 Saved-file screen»: when it is off, an upload leaves no card
    # behind (a held or failed file is still reported).
    user_id = int(message.from_user.id) if message.from_user else None
    show_saved = services.preferences.show_saved_screen(user_id)

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
        await safe_delete_messages(
            bot, message.chat.id, [message.message_id, status.message_id]
        )
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
        await safe_delete_messages(
            bot, message.chat.id, [message.message_id, status.message_id]
        )
        if not show_saved:
            return
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
    await safe_delete_messages(
        bot, message.chat.id, [message.message_id, status.message_id]
    )

    if not show_saved:
        return

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "file", "id": record.id},
        notice=views.file_saved_text(record),
    )


async def _store(
    message: Message,
    candidate,
    services: Services,
    limit: asyncio.Semaphore,
) -> tuple[str, object]:
    """Save one album item and say how it ended, instead of raising.

    The outcome is returned rather than written into the batch, so several copies
    can run at the same time and the batch is still reported in the order the
    files arrived.
    """
    async with limit:
        try:
            return "saved", await services.files.save_from_message(message, candidate)
        except QuarantinedFileError as exc:
            return "quarantined", exc.pending
        except DuplicateFileError as exc:
            return "duplicate", exc.record
        except Exception as exc:  # noqa: BLE001 - every failure must be reported
            logger.exception("Failed to store %s", candidate.file_name)
            return "failed", (candidate.file_name, _reason(exc))


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

    # The files of one batch are independent: store a few at a time instead of one
    # after the other, and keep one status bubble for the whole batch.
    batch = _Batch()
    limit = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    candidates = [
        (message, candidate)
        for message in messages
        if (candidate := extract_candidate(message)) is not None
    ]
    outcomes = await asyncio.gather(
        *(
            _store(message, candidate, services, limit)
            for message, candidate in candidates
        )
    )
    # Messages of the files that reached the library, so the chat is kept free
    # only for the copies that no longer need the user's attention.
    settled: list[int] = []
    for (message, _candidate), (kind, payload) in zip(candidates, outcomes):
        if kind == "saved":
            batch.saved.append(payload)  # type: ignore[arg-type]
            settled.append(message.message_id)
        elif kind == "quarantined":
            batch.quarantined.append(payload)  # type: ignore[arg-type]
        elif kind == "duplicate":
            batch.duplicates.append(payload)  # type: ignore[arg-type]
            settled.append(message.message_id)
        else:
            batch.failures.append(payload)  # type: ignore[arg-type]

    # One request for the whole batch instead of two per file. A held or failed
    # file keeps its message: removing it would leave the user with a summary and
    # no way to read the reason or resend the same file. The status bubble is
    # always removed.
    await safe_delete_messages(bot, first.chat.id, [*settled, status.message_id])

    if batch.empty:
        return

    user_id = int(first.from_user.id) if first.from_user else 0
    if not services.preferences.show_saved_screen(user_id):
        # Silent mode: nothing is drawn for a clean batch. A held or failed file
        # still deserves a warning, so it keeps the reserve screen.
        if batch.quarantined or batch.failures:
            await navigation.navigate_message(
                first,
                state,
                bot,
                services,
                {"k": "pending"},
                notice=views.files_saved_text(
                    [], [], batch.quarantined, batch.failures
                ),
            )
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

    # Every saved (and already stored) file is reachable one by one: the batch
    # screen starts on the first file and «Next ▶» walks to the last one.
    batch_files = [*batch.saved, *batch.duplicates]
    if not batch_files:
        # Nothing reached the library (held or failed): the reserve screen
        # already lists those files, so there is no batch to walk.
        await navigation.navigate_message(
            first,
            state,
            bot,
            services,
            {"k": "pending"},
            notice=views.files_saved_text(
                [], [], batch.quarantined, batch.failures
            ),
        )
        return
    await navigation.navigate_message(
        first,
        state,
        bot,
        services,
        {
            "k": "saved",
            "ids": ",".join(record.id for record in batch_files),
            "dup": ",".join(record.id for record in batch.duplicates),
            "i": 0,
            "p": len(batch.quarantined),
            "x": len(batch.failures),
        },
        push=False,
    )

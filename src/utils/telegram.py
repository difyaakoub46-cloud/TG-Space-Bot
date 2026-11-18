"""Telegram utility helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import Message

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def call_with_retry(
    method: Callable[..., Awaitable[T]],
    /,
    *args: Any,
    retries: int = 3,
    **kwargs: Any,
) -> T:
    """Call an aiogram API method, honouring flood waits and retrying network blips."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await method(*args, **kwargs)
        except TelegramRetryAfter as exc:
            last_error = exc
            if attempt == retries:
                break
            logger.warning("Flood wait: retrying in %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except TelegramNetworkError as exc:
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


async def safe_edit_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Any | None = None,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except TelegramAPIError:
        return False


async def safe_delete_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramAPIError as exc:
        # Deleting the user's copy is cosmetic: never fail the upload because of
        # it, but keep the reason visible in the logs.
        logger.warning(
            "Could not delete message %s in chat %s: %s",
            message_id,
            chat_id,
            exc,
        )
        return False


async def safe_delete_messages(
    bot: Bot,
    chat_id: int,
    message_ids: Sequence[int],
) -> bool:
    """Delete several messages in one request, falling back to one by one.

    An upload used to pay a Telegram call per message it cleaned up; the batch is
    deleted as a whole, and a mixed batch (one message already gone) is retried
    individually rather than reported as a failure.
    """
    ids = [int(message_id) for message_id in message_ids if message_id]
    if not ids:
        return True
    if len(ids) == 1:
        return await safe_delete_message(bot, chat_id, ids[0])
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=ids)
        return True
    except TelegramAPIError as exc:
        logger.warning(
            "Could not delete messages %s in chat %s: %s", ids, chat_id, exc
        )
    removed = True
    for message_id in ids:
        removed = await safe_delete_message(bot, chat_id, message_id) and removed
    return removed


async def answer_or_edit(
    message: Message,
    text: str,
    reply_markup: Any | None = None,
) -> None:
    await message.answer(text, reply_markup=reply_markup)

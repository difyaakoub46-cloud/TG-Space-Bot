"""Telegram resilience helpers: flood-control aware retries and message editing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiogram.exceptions import (
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

T = TypeVar("T")

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0


async def call_with_retry(
    call: Callable[..., Awaitable[T]],
    /,
    *args: Any,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    chat_id_out: list[int] | None = None,
    **kwargs: Any,
) -> T:
    """Call a Telegram API method, surviving flood limits, network errors and chat migration.

    ``TelegramRetryAfter`` is honored with the exact server-provided delay, network
    failures use exponential backoff, and ``TelegramMigrateToChat`` transparently
    retargets the call to the upgraded supergroup id.

    When ``chat_id_out`` is given, it is updated with the ``chat_id`` actually used
    for the successful call (important after a group → supergroup migration).
    """
    for attempt in range(1, attempts + 1):
        if chat_id_out is not None:
            chat_id_out[0] = kwargs.get("chat_id", chat_id_out[0])
        try:
            return await call(*args, **kwargs)
        except TelegramRetryAfter as exc:
            if attempt >= attempts:
                raise
            delay = float(exc.retry_after) + 0.5
            logger.warning("Flood control: sleeping %.0fs (attempt %s/%s)", delay, attempt, attempts)
            await asyncio.sleep(delay)
        except TelegramMigrateToChat as exc:
            if attempt >= attempts:
                raise
            logger.warning("Chat migrated to %s, retrying", exc.migrate_to_chat_id)
            kwargs["chat_id"] = exc.migrate_to_chat_id
            if chat_id_out is not None:
                chat_id_out[0] = exc.migrate_to_chat_id
        except TelegramNetworkError as exc:
            if attempt >= attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("Network error (%s), retrying in %.1fs", exc, delay)
            await asyncio.sleep(delay)

    raise RuntimeError("retry loop exhausted without result")  # pragma: no cover


async def safe_edit_text(
    message: Any,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    disable_preview: bool = True,
) -> None:
    """Edit a message, tolerating identical content and deleted messages."""
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_preview,
        )
    except Exception as exc:  # noqa: BLE001 - editing is best effort
        if "message is not modified" in str(exc).lower():
            return
        logger.debug("edit_text failed (%s), sending a new message instead", exc)
        await message.answer(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_preview,
        )

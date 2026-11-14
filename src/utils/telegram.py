"""Telegram utility helpers."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message


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
    except TelegramAPIError:
        return False


async def answer_or_edit(
    message: Message,
    text: str,
    reply_markup: Any | None = None,
) -> None:
    await message.answer(text, reply_markup=reply_markup)

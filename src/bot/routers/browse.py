"""Browse router: library, categories and per-file actions."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import (
    BTN_LIBRARY,
    CategoryCB,
    FileCB,
    MenuCB,
    back_to_menu_kb,
    categories_kb,
    category_files_kb,
    delete_confirm_kb,
    file_actions_kb,
)
from src.services.container import Services
from src.utils.filetypes import parse_category

logger = logging.getLogger(__name__)

router = Router(name="browse")


async def _show_categories(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    counts = await services.files.category_counts()
    await views.set_screen_cb(
        callback, state, views.categories_text(counts), categories_kb(counts)
    )


@router.message(Command("library"))
@router.message(F.text == BTN_LIBRARY)
async def cmd_library(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    counts = await services.files.category_counts()
    await views.set_screen(
        bot, message, state, views.categories_text(counts), categories_kb(counts)
    )


@router.callback_query(MenuCB.filter(F.action == "categories"))
async def categories_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await _show_categories(callback, services, state, bot)


@router.callback_query(CategoryCB.filter())
async def category_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = CategoryCB.unpack(callback.data)
    category = parse_category(data.name)
    files = await services.files.by_category(category)
    await views.set_screen_cb(
        callback,
        state,
        views.category_text(category, files),
        category_files_kb(files, category),
    )


@router.callback_query(FileCB.filter(F.action == "view"))
async def file_view_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await views.set_screen_cb(
        callback, state, views.file_text(record), file_actions_kb(record.id)
    )


@router.callback_query(FileCB.filter(F.action == "send"))
async def file_send_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return

    try:
        await services.files.deliver(record, callback.message.chat.id)
    except TelegramAPIError as exc:
        logger.warning("Could not deliver %s: %s", record.id, exc)
        await callback.answer(
            "تعذّر إرسال الملف، ربما حُذف من مجموعة التخزين.", show_alert=True
        )
        return

    await views.set_screen_cb(
        callback, state, views.file_sent_text(record), back_to_menu_kb()
    )


@router.callback_query(FileCB.filter(F.action == "delete"))
async def file_delete_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await views.set_screen_cb(
        callback, state, views.deletion_text(record), delete_confirm_kb(record.id)
    )


@router.callback_query(FileCB.filter(F.action == "confirm"))
async def file_delete_confirm_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    deleted = await services.files.delete(data.file_id)
    if not deleted:
        await callback.answer("تعذّر حذف الملف.", show_alert=True)
        return
    await views.set_screen_cb(
        callback, state, "✅ تم حذف الملف من المكتبة.", back_to_menu_kb()
    )

"""Browse router: library, categories and per-file actions."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import (
    BTN_LIBRARY,
    CategoryCB,
    FileCB,
    MenuCB,
    delete_confirm_kb,
)
from src.bot.states import RenameStates
from src.services.container import Services
from src.utils.filetypes import parse_category

logger = logging.getLogger(__name__)

router = Router(name="browse")


@router.message(Command("library"))
@router.message(F.text == BTN_LIBRARY)
async def cmd_library(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message, state, bot, services, {"k": "categories"}
    )


@router.callback_query(MenuCB.filter(F.action == "categories"))
async def categories_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "categories"}
    )


@router.callback_query(CategoryCB.filter())
async def category_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = CategoryCB.unpack(callback.data)
    category = parse_category(data.name)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "category", "name": category.value},
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
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "file", "id": record.id}
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
        delivered_id = await services.files.deliver(
            record, callback.message.chat.id
        )
    except TelegramAPIError as exc:
        logger.warning("Could not deliver %s: %s", record.id, exc)
        await callback.answer(
            "تعذّر إرسال الملف، ربما حُذف من مجموعة التخزين.", show_alert=True
        )
        return

    # The delivered copy is a real message in the chat: track it so the main menu
    # removes it too instead of leaving the file behind.
    await views.remember(state, delivered_id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "file", "id": record.id},
        notice=views.file_sent_text(record),
        push=False,
    )


@router.callback_query(FileCB.filter(F.action == "rename"))
async def file_rename_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await state.set_state(RenameStates.waiting_name)
    await state.update_data(rename_file_id=record.id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "file", "id": record.id},
        notice=views.rename_prompt_text(record),
        push=False,
    )


@router.message(RenameStates.waiting_name)
async def rename_message(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    name = " ".join((message.text or "").split())
    await views.remember(state, message.message_id)
    if not name or len(name) > 120:
        await message.answer("⚠️ أرسل اسماً بين حرف واحد و١٢٠ حرفاً.")
        return

    stored = await state.get_data()
    file_id = str(stored.get("rename_file_id") or "")
    await state.set_state(None)
    updated = await services.files.rename(file_id, name) if file_id else None
    if updated is None:
        await message.answer("⚠️ تعذّر تحديث الاسم، حاول مرة أخرى.")
        return

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "file", "id": updated.id},
        notice=f"✅ تم تغيير الاسم إلى «{html.escape(updated.file_name, quote=False)}».",
    )


@router.callback_query(FileCB.filter(F.action == "folder"))
async def file_folder_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await state.update_data(folder_file_id=record.id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {
            "k": "pick_folder",
            "u": int(callback.from_user.id),
            "file": record.id,
        },
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
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "file", "id": record.id},
        notice=views.deletion_text(record),
        markup=delete_confirm_kb(record.id),
        push=False,
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
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "categories"},
        notice="✅ تم حذف الملف من المكتبة.",
        push=False,
    )

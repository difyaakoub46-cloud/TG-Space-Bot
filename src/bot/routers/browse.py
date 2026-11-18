"""Browse router: library, categories and per-file actions."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import file_list, navigation, views
from src.bot.file_list import FileListContext
from src.bot.keyboards import (
    LIBRARY_TEXTS,
    CategoryCB,
    FileCB,
    MenuCB,
    SavedCB,
    delete_confirm_kb,
)
from src.bot.states import RenameStates
from src.core.i18n import pick
from src.services.container import Services
from src.utils.filetypes import parse_category
from src.utils.formatters import keep_extension

logger = logging.getLogger(__name__)

router = Router(name="browse")


@router.message(Command("library"))
@router.message(F.text.in_(LIBRARY_TEXTS))
async def cmd_library(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "categories", "u": int(message.from_user.id)},
    )


@router.callback_query(MenuCB.filter(F.action == "categories"))
async def categories_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "categories", "u": int(callback.from_user.id)},
    )


@router.callback_query(SavedCB.filter())
async def saved_step_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Walk through an upload batch: «Next ▶» / «◀ Previous»."""
    data = SavedCB.unpack(callback.data)
    stored = await state.get_data()
    screen = dict(stored.get("screen_key") or {})
    if screen.get("k") != "saved":
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            {"k": "categories", "u": int(callback.from_user.id)},
            push=False,
        )
        return
    try:
        screen["i"] = max(0, int(data.value))
    except (TypeError, ValueError):
        screen["i"] = 0
    await navigation.navigate_callback(
        callback, state, bot, services, screen, push=False
    )


@router.callback_query(CategoryCB.filter())
async def category_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open a category in the shared file list, with no query to type.

    Opening a category remembers the library behind it, so «↩️ Back» walks
    file → category list → library. Coming back to the same category keeps the
    filter, the ordering and the page it was left on.
    """
    data = CategoryCB.unpack(callback.data)
    category = parse_category(data.name)
    user_id = int(callback.from_user.id)
    stored = FileListContext.from_screen(
        (await state.get_data()).get("screen_key") or {}
    )
    paging = stored.view == "category" and stored.value == category.value
    screen = (
        stored.to_screen({"u": user_id})
        if paging
        else file_list.new_screen("category", category.value, user_id)
    )
    await navigation.navigate_callback(
        callback, state, bot, services, screen, push=not paging
    )


@router.callback_query(FileCB.filter(F.action == "view"))
async def file_view_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer(pick("The file no longer exists.", "الملف غير موجود."), show_alert=True)
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
        await callback.answer(pick("The file no longer exists.", "الملف غير موجود."), show_alert=True)
        return

    try:
        delivered_id = await services.files.deliver(
            record, callback.message.chat.id
        )
    except TelegramAPIError as exc:
        logger.warning("Could not deliver %s: %s", record.id, exc)
        await callback.answer(
            pick(
                "Could not send the file; it may have been deleted from its storage group.",
                "تعذّر إرسال الملف، ربما حُذف من مجموعة التخزين.",
            ),
            show_alert=True,
        )
        return

    # The delivered copy is a real message in the chat: track it so the main menu
    # removes it too, and so «↩️ Back» can remove it when its setting says so.
    await views.remember(state, delivered_id)
    await navigation.track_delivered(state, delivered_id)
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
        await callback.answer(pick("The file no longer exists.", "الملف غير موجود."), show_alert=True)
        return
    await state.set_state(RenameStates.waiting_name)
    await state.update_data(
        rename_file_id=record.id, rename_old_name=record.file_name
    )
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
    stored = await state.get_data()
    # Renaming must not change the type of the file: «Noma» becomes «Noma.pdf».
    if name:
        name = keep_extension(name, str(stored.get("rename_old_name") or ""))
    if not name or len(name) > 120:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Send a name between 1 and 120 characters.",
                "⚠️ أرسل اسماً بين حرف واحد و١٢٠ حرفاً.",
            )
        )
        return

    file_id = str(stored.get("rename_file_id") or "")
    await state.set_state(None)
    updated = await services.files.rename(file_id, name) if file_id else None
    if updated is None:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Could not update the name, please try again.",
                "⚠️ تعذّر تحديث الاسم، حاول مرة أخرى.",
            )
        )
        return

    # The new name has been consumed: remove the user's text message so the chat
    # keeps only the file card.
    await views.delete_screen_message(bot, message.chat.id, message.message_id)
    await navigation.forget(state, message.message_id)

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "file", "id": updated.id},
        notice=pick(
            f"✅ The name was changed to «{html.escape(updated.file_name, quote=False)}».",
            f"✅ تم تغيير الاسم إلى «{html.escape(updated.file_name, quote=False)}».",
        ),
    )


@router.callback_query(FileCB.filter(F.action == "folder"))
async def file_folder_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileCB.unpack(callback.data)
    record = await services.files.get(data.file_id)
    if record is None:
        await callback.answer(pick("The file no longer exists.", "الملف غير موجود."), show_alert=True)
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
        await callback.answer(pick("The file no longer exists.", "الملف غير موجود."), show_alert=True)
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
        await callback.answer(
            pick("Could not delete the file.", "تعذّر حذف الملف."), show_alert=True
        )
        return
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "categories", "u": int(callback.from_user.id)},
        notice=pick(
            "✅ The file was removed from the library.",
            "✅ تم حذف الملف من المكتبة.",
        ),
        push=False,
    )

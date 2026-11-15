"""Virtual folders: group files without moving a single byte.

A folder is only a label over rows of the ``files`` table; the file itself stays in
its Telegram storage group, so deleting a folder never deletes a file.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import FolderCB, MenuCB
from src.bot.states import FolderStates
from src.db.repository import SupabaseError
from src.services.container import Services

logger = logging.getLogger(__name__)

router = Router(name="folders")

_MAX_NAME = 80


def _user_id(callback: CallbackQuery) -> int:
    return int(callback.from_user.id)


def _duplicate(exc: SupabaseError) -> bool:
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


def _folders_key(user_id: int) -> dict[str, object]:
    return {"k": "folders", "u": user_id}


def _folder_key(user_id: int, folder_id: str) -> dict[str, object]:
    return {"k": "folder", "u": user_id, "id": folder_id}


@router.callback_query(MenuCB.filter(F.action == "folders"))
async def menu_folders_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, _folders_key(_user_id(callback))
    )


@router.callback_query(FolderCB.filter(F.action == "open"))
async def folder_open_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FolderCB.unpack(callback.data)
    folder = await services.files.folder(data.value, _user_id(callback))
    if folder is None:
        await callback.answer("المجلد غير موجود.", show_alert=True)
        return
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, _folder_key(_user_id(callback), folder.id)
    )


@router.callback_query(FolderCB.filter(F.action == "put"))
async def folder_put_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FolderCB.unpack(callback.data)
    stored = await state.get_data()
    file_id = stored.get("folder_file_id")
    folder = await services.files.folder(data.value, _user_id(callback))
    if folder is None:
        await callback.answer("المجلد غير موجود.", show_alert=True)
        return
    if not file_id:
        await callback.answer("اختر ملفاً أولاً.", show_alert=True)
        return

    await services.files.add_to_folder(folder.id, str(file_id))
    await state.update_data(folder_file_id=None)
    files = await services.files.folder_files(folder.id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _folder_key(_user_id(callback), folder.id),
        notice=f"✅ أُضيف الملف إلى مجلد «{folder.name}».\n\n"
        + views.folder_text(folder, files),
    )


@router.callback_query(FolderCB.filter(F.action == "new"))
async def folder_new_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    stored = await state.get_data()
    await state.set_state(FolderStates.waiting_name)
    await state.update_data(folder_file_id=stored.get("folder_file_id"))
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "new_folder", "u": _user_id(callback)},
        push=False,
    )


@router.message(FolderStates.waiting_name)
async def folder_name_message(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    name = " ".join((message.text or "").split())[:_MAX_NAME]
    await views.remember(state, message.message_id)
    if not name:
        await message.answer("⚠️ أرسل اسماً صالحاً للمجلد.")
        return

    user_id = int(message.from_user.id) if message.from_user else 0
    stored = await state.get_data()
    pending = stored.get("folder_file_id")

    try:
        folder = await services.files.create_folder(user_id, name)
    except SupabaseError as exc:
        if _duplicate(exc):
            await message.answer(f"⚠️ يوجد مجلد باسم «{name}» بالفعل.")
        else:
            logger.exception("Could not create folder %s", name)
            await message.answer("⚠️ تعذّر إنشاء المجلد، حاول مرة أخرى.")
        return

    await state.set_state(None)
    notice = f"✅ أُنشئ مجلد «{folder.name}»."
    if pending:
        await services.files.add_to_folder(folder.id, str(pending))
        await state.update_data(folder_file_id=None)
        notice += " وأُضيف إليه الملف."

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        _folder_key(user_id, folder.id),
        notice=notice,
        push=False,
    )

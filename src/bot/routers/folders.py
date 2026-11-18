"""Virtual folders: group files without moving a single byte.

A folder is only a label over rows of the ``files`` table; the file itself stays in
its Telegram storage group, so deleting a folder never deletes a file.

Folders are created once and never renamed: the name is the folder's identity, and
the list is always sorted alphabetically and paged.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import FolderCB, MenuCB
from src.bot.states import FolderStates
from src.core.i18n import pick
from src.db.repository import SupabaseError
from src.services.container import Services
from src.utils.sorting import SortSpec

logger = logging.getLogger(__name__)

router = Router(name="folders")

_MAX_NAME = 80


def _user_id(callback: CallbackQuery) -> int:
    return int(callback.from_user.id)


def _duplicate(exc: SupabaseError) -> bool:
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


def _folders_key(user_id: int, page: int = 1) -> dict[str, object]:
    return {"k": "folders", "u": user_id, "p": max(1, page)}


def _folder_key(user_id: int, folder_id: str) -> dict[str, object]:
    return {"k": "folder", "u": user_id, "id": folder_id}


async def _open_folder(
    state: FSMContext, user_id: int, folder_id: str
) -> tuple[dict[str, object], SortSpec]:
    """The folder screen, keeping the sorting the user chose for this folder."""
    screen = _folder_key(user_id, folder_id)
    stored = (await state.get_data()).get("screen_key") or {}
    if stored.get("k") == "folder" and stored.get("id") == folder_id:
        spec = SortSpec.from_screen(stored)
        screen.update(spec.to_screen())
    else:
        spec = SortSpec()
    return screen, spec


@router.callback_query(MenuCB.filter(F.action == "folders"))
async def menu_folders_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, _folders_key(_user_id(callback))
    )


@router.callback_query(FolderCB.filter(F.action == "page"))
async def folders_page_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Move to another page of the alphabetically sorted folder list."""
    data = FolderCB.unpack(callback.data)
    try:
        page = max(1, int(data.value))
    except (TypeError, ValueError):
        page = 1
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _folders_key(_user_id(callback), page),
        push=False,
    )


@router.callback_query(FolderCB.filter(F.action == "fpage"))
async def folder_files_page_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Turn the pages of the files inside the folder that is already open."""
    data = FolderCB.unpack(callback.data)
    try:
        page = max(1, int(data.value))
    except (TypeError, ValueError):
        page = 1

    stored = dict((await state.get_data()).get("screen_key") or {})
    if stored.get("k") != "folder":
        # No folder is open (an older button, or the trail was cleared): show the
        # folder list instead of failing the tap.
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            _folders_key(_user_id(callback)),
            push=False,
        )
        return

    stored["p"] = page
    await navigation.navigate_callback(
        callback, state, bot, services, stored, push=False
    )


@router.callback_query(FolderCB.filter(F.action == "noop"))
async def folders_page_indicator_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """The page number button only acknowledges the tap."""
    await callback.answer()


@router.callback_query(FolderCB.filter(F.action == "open"))
async def folder_open_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FolderCB.unpack(callback.data)
    folder = await services.files.folder(data.value, _user_id(callback))
    if folder is None:
        await callback.answer(
            pick("The folder no longer exists.", "المجلد غير موجود."), show_alert=True
        )
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
        await callback.answer(
            pick("The folder no longer exists.", "المجلد غير موجود."), show_alert=True
        )
        return
    if not file_id:
        await callback.answer(
            pick("Pick a file first.", "اختر ملفاً أولاً."), show_alert=True
        )
        return

    await services.files.add_to_folder(folder.id, str(file_id))
    await state.update_data(folder_file_id=None)
    screen, sort = await _open_folder(state, _user_id(callback), folder.id)
    files = await services.files.folder_files(folder.id, sort)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        screen,
        notice=pick(
            f"✅ The file was added to the folder «{folder.name}».\n\n",
            f"✅ أُضيف الملف إلى مجلد «{folder.name}».\n\n",
        )
        + views.folder_text(folder, files, sort),
    )


@router.callback_query(FolderCB.filter(F.action == "move_up"))
@router.callback_query(FolderCB.filter(F.action == "move_down"))
async def folder_move_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Reorder the favourites: one place up or down on the library screen."""
    data = FolderCB.unpack(callback.data)
    user_id = _user_id(callback)
    step = -1 if data.action == "move_up" else 1

    folder = await services.files.move_favorite(data.value, user_id, step)
    if folder is None:
        await callback.answer(
            pick(
                "This folder is not in the favourites.",
                "هذا المجلد ليس في المفضلة.",
            ),
            show_alert=True,
        )
        return

    favorites = await services.files.favorite_folders(user_id)
    position = next(
        (
            index
            for index, item in enumerate(favorites, start=1)
            if item.id == folder.id
        ),
        1,
    )
    screen, sort = await _open_folder(state, user_id, folder.id)
    files = await services.files.folder_files(folder.id, sort)
    notice = pick(
        f"✅ «{folder.name}» is now position {position} of {len(favorites)} in "
        "the favourite folders.",
        f"✅ صار «{folder.name}» في الموضع {position} من {len(favorites)} "
        "في المجلدات المفضلة.",
    )
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        screen,
        notice=notice + "\n\n" + views.folder_text(folder, files, sort),
        push=False,
    )


@router.callback_query(FolderCB.filter(F.action == "favorite"))
@router.callback_query(FolderCB.filter(F.action == "unfavorite"))
async def folder_favorite_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Star or unstar a folder so it becomes a shortcut on the library screen."""
    data = FolderCB.unpack(callback.data)
    user_id = _user_id(callback)
    favorite = data.action == "favorite"

    folder = await services.files.set_favorite(data.value, user_id, favorite)
    if folder is None:
        await callback.answer(
            pick("The folder no longer exists.", "المجلد غير موجود."), show_alert=True
        )
        return

    screen, sort = await _open_folder(state, user_id, folder.id)
    files = await services.files.folder_files(folder.id, sort)
    notice = (
        pick(
            "⭐ The folder was added to the favourites and now appears on the library screen.",
            "⭐ أُضيف المجلد إلى المفضلة، وصار يظهر في شاشة المكتبة.",
        )
        if favorite
        else pick("🌟 The folder was removed from the favourites.", "🌟 أُزيل المجلد من المفضلة.")
    )
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        screen,
        notice=notice + "\n\n" + views.folder_text(folder, files, sort),
        push=False,
    )


@router.callback_query(FolderCB.filter(F.action == "delete"))
async def folder_delete_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Ask before deleting the label; files are never part of this action."""
    data = FolderCB.unpack(callback.data)
    folder = await services.files.folder(data.value, _user_id(callback))
    if folder is None:
        await callback.answer(
            pick("The folder no longer exists.", "المجلد غير موجود."), show_alert=True
        )
        return
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "folder_delete", "u": _user_id(callback), "id": folder.id},
        push=False,
    )


@router.callback_query(FolderCB.filter(F.action == "confirm_delete"))
async def folder_delete_confirm_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Delete the folder row only: every file stays in the library."""
    data = FolderCB.unpack(callback.data)
    user_id = _user_id(callback)
    folder = await services.files.folder(data.value, user_id)
    name = folder.name if folder is not None else ""

    if not await services.files.delete_folder(data.value, user_id):
        await callback.answer(
            pick(
                "Could not delete the folder, please try again.",
                "تعذّر حذف المجلد، حاول مرة أخرى.",
            ),
            show_alert=True,
        )
        return

    await state.update_data(folder_file_id=None)
    logger.info("Deleted the folder %s of user %s (files kept)", data.value, user_id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _folders_key(user_id),
        notice=(
            pick(
                f"✅ The folder «{html.escape(name, quote=False)}» was deleted.\n\n"
                "Every file it held stays in the library and in its storage group.",
                f"✅ حُذف المجلد «{html.escape(name, quote=False)}».\n\n"
                "جميع الملفات التي كانت فيه باقية في المكتبة وفي مجموعات التخزين.",
            )
        ),
        push=False,
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
        await message.answer(
            pick("⚠️ Send a valid folder name.", "⚠️ أرسل اسماً صالحاً للمجلد.")
        )
        return

    user_id = int(message.from_user.id) if message.from_user else 0
    stored = await state.get_data()
    pending = stored.get("folder_file_id")

    try:
        folder = await services.files.create_folder(user_id, name)
    except SupabaseError as exc:
        if _duplicate(exc):
            await message.answer(
                pick(
                    f"⚠️ A folder named «{name}» already exists.",
                    f"⚠️ يوجد مجلد باسم «{name}» بالفعل.",
                )
            )
        else:
            logger.exception("Could not create folder %s", name)
            await message.answer(
                pick(
                    "⚠️ Could not create the folder, please try again.",
                    "⚠️ تعذّر إنشاء المجلد، حاول مرة أخرى.",
                )
            )
        return

    await state.set_state(None)
    notice = pick(
        f"✅ The folder «{folder.name}» was created.",
        f"✅ أُنشئ مجلد «{folder.name}».",
    )
    if pending:
        await services.files.add_to_folder(folder.id, str(pending))
        await state.update_data(folder_file_id=None)
        notice += pick(" The file was added to it.", " وأُضيف إليه الملف.")

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        _folder_key(user_id, folder.id),
        notice=notice,
        push=False,
    )

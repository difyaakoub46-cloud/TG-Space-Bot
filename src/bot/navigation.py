"""Screen navigation: one persistent screen, a trail and a back button.

Every screen is described by a small, JSON-friendly ``key`` such as
``{"k": "category", "name": "video"}``. That lets the *back* button rebuild the
previous screen from the database instead of keeping rendered text around, and it
survives the FSM state being cleared between flows.

The menu tree lives here as screens, not as buttons: «Settings» and «General
status» are ordinary screens, so the back button returns to the main menu and the
statistics / permissions / reserve screens return to the status screen they were
opened from.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot import file_list, library_views, views
from src.bot.keyboards import (
    FolderCB,
    back_to_menu_kb,
    categories_kb,
    file_actions_kb,
    folder_delete_confirm_kb,
    folder_files_kb,
    folders_kb,
    groups_kb,
    help_kb,
    language_kb,
    main_menu_kb,
    pending_kb,
    permissions_kb,
    pick_folder_kb,
    saved_nav_kb,
    settings_kb,
    status_kb,
)
from src.bot.states import SearchStates
from src.bot.status import group_permissions_text, statistics_text, status_text
from src.core.i18n import get_language, pick
from src.services.container import Services
from src.services.files import FILES_PER_PAGE
from src.utils.sorting import SortSpec

logger = logging.getLogger(__name__)

MAX_TRAIL = 20

# The JoinCB value of the fallback group (its category value is meaningless).
JOIN_FALLBACK = "fallback"

MAIN: dict[str, object] = {"k": "main"}


def _folder_page_row(page: int, pages: int) -> list[InlineKeyboardButton]:
    """First / Previous / Next / Last for the files of one folder.

    The page number lives in the ``FolderCB`` value; the folder itself is taken
    from the screen key, so turning a page never reopens another folder.
    """
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton(
                text=pick("⏮ First", "⏮ الأولى"),
                callback_data=FolderCB(action="fpage", value="1").pack(),
            )
        )
        buttons.append(
            InlineKeyboardButton(
                text=pick("◀ Previous", "◀ السابق"),
                callback_data=FolderCB(action="fpage", value=str(page - 1)).pack(),
            )
        )
    if page < pages:
        buttons.append(
            InlineKeyboardButton(
                text=pick("Next ▶", "التالي ▶"),
                callback_data=FolderCB(action="fpage", value=str(page + 1)).pack(),
            )
        )
        buttons.append(
            InlineKeyboardButton(
                text=pick("Last ⏭", "⏭ الأخيرة"),
                callback_data=FolderCB(action="fpage", value=str(pages)).pack(),
            )
        )
    return buttons


def _with_folder_pages(
    markup: InlineKeyboardMarkup, page: int, pages: int, after: int
) -> InlineKeyboardMarkup:
    """Put the page controls right under the folder's file buttons."""
    row = _folder_page_row(page, pages)
    if not row:
        return markup
    rows = [list(item) for item in markup.inline_keyboard]
    rows.insert(min(after, len(rows)), row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _page(screen: dict[str, object]) -> int:
    """The page kept in a screen key, defaulting to the first one."""
    try:
        return max(1, int(screen.get("p") or 1))
    except (TypeError, ValueError):
        return 1


async def build(
    services: Services, screen: dict[str, object] | None
) -> tuple[str, InlineKeyboardMarkup]:
    """Rebuild the text and the keyboard of a screen from its key."""
    screen = screen or MAIN
    kind = screen.get("k")

    if kind == "categories":
        # The totals and the folder list are independent: ask for both at once.
        totals, folders = await asyncio.gather(
            services.files.category_totals(),
            services.files.folders(int(screen.get("u") or 0)),
        )
        favorites = sorted(
            (folder for folder in folders if folder.favorite),
            key=lambda folder: folder.favorite_sort_key,
        )
        return (
            views.categories_text(totals, favorites, len(folders)),
            categories_kb(totals, favorites, len(folders)),
        )

    if kind == "list":
        # One component, two entry points: «🔍 Search» passes the typed query and a
        # library category its name; the list itself is written once.
        return await file_list.screen(services, screen)

    if kind == "file":
        record = await services.files.get(str(screen.get("id") or ""))
        if record is not None:
            folders = await services.files.file_folders(record.id)
            return views.file_text(record, folders), file_actions_kb(record.id)

    if kind == "folders":
        folders = await services.files.folders(int(screen.get("u") or 0))
        per_page = services.settings.results_per_page()
        total_pages = max(1, -(-len(folders) // per_page))
        page = min(_page(screen), total_pages)
        start = (page - 1) * per_page
        items = folders[start : start + per_page]
        return (
            views.folders_text(items, page, total_pages, total=len(folders)),
            folders_kb(items, page, total_pages),
        )

    if kind == "folder":
        folder = await services.files.folder(
            str(screen.get("id") or ""), int(screen.get("u") or 0)
        )
        if folder is not None:
            sort = SortSpec.from_screen(screen)
            files, total, page = await services.files.folder_page(
                folder.id, sort, page=_page(screen)
            )
            pages = max(1, -(-total // FILES_PER_PAGE))
            return (
                views.folder_text(folder, files, sort),
                _with_folder_pages(
                    folder_files_kb(files, folder.id, folder.favorite, sort),
                    page,
                    pages,
                    len(files),
                ),
            )

    if kind == "folder_delete":
        folder = await services.files.folder(
            str(screen.get("id") or ""), int(screen.get("u") or 0)
        )
        if folder is not None:
            return (
                views.folder_delete_text(folder),
                folder_delete_confirm_kb(folder.id),
            )

    if kind == "pick_folder":
        record = await services.files.get(str(screen.get("file") or ""))
        if record is not None:
            folders = await services.files.folders(int(screen.get("u") or 0))
            return views.pick_folder_text(record), pick_folder_kb(folders)

    if kind == "new_folder":
        return views.new_folder_text(), back_to_menu_kb()

    if kind == "settings":
        user_id = int(screen.get("u") or 0) or None
        show_saved = services.preferences.show_saved_screen(user_id)
        keep_sent = services.preferences.keep_sent_files(user_id)
        return (
            views.settings_text()
            + library_views.saved_screen_settings_text(show_saved)
            + library_views.keep_sent_settings_text(keep_sent),
            settings_kb(show_saved, keep_sent),
        )

    if kind == "saved":
        # A whole upload batch: one file per screen, walked with «Next ▶».
        ids = [part for part in str(screen.get("ids") or "").split(",") if part]
        fetched = await asyncio.gather(*(services.files.get(part) for part in ids))
        records = [record for record in fetched if record is not None]
        if records:
            index = min(max(0, int(screen.get("i") or 0)), len(records) - 1)
            duplicates = [
                part for part in str(screen.get("dup") or "").split(",") if part
            ]
            pending = int(screen.get("p") or 0)
            failures = int(screen.get("x") or 0)
            return (
                library_views.saved_batch_text(
                    records, index, duplicates, pending, failures
                ),
                saved_nav_kb(
                    records,
                    index,
                    duplicates,
                    show_pending=bool(pending),
                ),
            )

    if kind == "language":
        language = get_language()
        return views.language_text(language), language_kb(language)

    if kind == "stats":
        return await statistics_text(services), back_to_menu_kb()

    if kind == "status":
        return await status_text(services), status_kb()

    if kind == "permissions":
        return await group_permissions_text(services), permissions_kb()

    if kind == "pending":
        store = services.pending
        latest = store.latest()
        return (
            views.pending_text(
                store.total,
                store.stats(),
                latest.file_name if latest else None,
                latest.reason if latest else None,
            ),
            pending_kb(store.total),
        )

    if kind == "help":
        return views.help_text(), help_kb()

    if kind == "join_groups":
        settings = services.settings
        groups = [
            (category.value, views.category_label(category))
            for category in sorted(settings.storage_chats(), key=lambda item: item.value)
        ]
        if settings.storage_chat_fallback is not None:
            groups.append((JOIN_FALLBACK, views.fallback_label()))
        return views.join_groups_text(), groups_kb(groups)

    # The main menu (and any screen that no longer exists) shows the library total.
    stats = await services.files.stats()
    return (
        views.welcome_text(
            int(stats.get("total", 0)), int(stats.get("total_bytes", 0) or 0)
        ),
        main_menu_kb(),
    )


async def _push_current(state: FSMContext) -> None:
    """Remember the screen being left so the back button can return to it."""
    data = await state.get_data()
    current = data.get("screen_key")
    if not current:
        return
    trail = [dict(entry) for entry in data.get("trail") or []]
    if trail and trail[-1] == current:
        return
    trail.append(dict(current))
    await state.update_data(trail=trail[-MAX_TRAIL:])


async def _render(
    services: Services,
    screen: dict[str, object],
    notice: str | None,
    markup: InlineKeyboardMarkup | None,
) -> tuple[str, InlineKeyboardMarkup]:
    text, keyboard = await build(services, screen)
    return notice or text, markup or keyboard


async def _sync_typing(state: FSMContext, screen: dict[str, object]) -> None:
    """Keep accepting a typed message exactly while the search list is on screen.

    Search has no separate prompt: the shared file list itself listens for text.
    The state is dropped as soon as another screen is rendered, so typing anywhere
    else keeps doing whatever that screen expects.
    """
    if screen.get("k") == "list" and screen.get("v") == "search":
        await state.set_state(SearchStates.waiting_query)
        return
    try:
        current = await state.get_state()
    except AttributeError:  # a plain dict in the tests
        return
    if current == SearchStates.waiting_query.state:
        await state.set_state(None)


async def navigate_message(
    message: Message,
    state: FSMContext,
    bot: Bot,
    services: Services,
    screen: dict[str, object],
    *,
    notice: str | None = None,
    markup: InlineKeyboardMarkup | None = None,
    push: bool = True,
) -> None:
    if push:
        await _push_current(state)
    text, keyboard = await _render(services, screen, notice, markup)
    await state.update_data(screen_key=dict(screen))
    await _sync_typing(state, screen)
    await views.set_screen(bot, message, state, text, keyboard)


async def navigate_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    services: Services,
    screen: dict[str, object],
    *,
    notice: str | None = None,
    markup: InlineKeyboardMarkup | None = None,
    push: bool = True,
) -> None:
    if push:
        await _push_current(state)
    text, keyboard = await _render(services, screen, notice, markup)
    await state.update_data(screen_key=dict(screen))
    await _sync_typing(state, screen)
    await views.set_screen_cb(callback, state, text, keyboard)


async def forget(state: FSMContext, *message_ids: int) -> None:
    """Stop tracking messages that have just been deleted."""
    ids = {int(mid) for mid in message_ids if mid}
    if not ids:
        return
    data = await state.get_data()
    tracked = [int(mid) for mid in (data.get("cleanup_ids") or []) if mid]
    await state.update_data(cleanup_ids=[mid for mid in tracked if mid not in ids])


async def track_delivered(state: FSMContext, message_id: int) -> None:
    """Remember a copy sent from a file screen, so «↩️ Back» can remove it."""
    if not message_id:
        return
    data = await state.get_data()
    ids = [int(mid) for mid in (data.get("delivered_ids") or []) if mid]
    ids.append(int(message_id))
    await state.update_data(delivered_ids=ids[-MAX_TRAIL * 5 :])


async def clear_delivered(
    bot: Bot, chat_id: int, state: FSMContext, keep: bool = False
) -> None:
    """Remove the copies opened from the file screens.

    A file opened with «📤 Send» is an ordinary message in the chat, so «↩️ Back»
    used to leave it behind even after returning to the library. The copies are
    forgotten in every case; they are deleted only when the setting says so, and
    they stay in the tracked list of the main menu either way.
    """
    data = await state.get_data()
    ids = [int(mid) for mid in (data.get("delivered_ids") or []) if mid]
    update: dict[str, object] = {"delivered_ids": []}
    if not keep and ids and chat_id:
        # The copy is already tracked for the main menu; forget it there too so
        # the wipe does not try to delete the same message a second time.
        removed = set(ids)
        tracked = [int(mid) for mid in (data.get("cleanup_ids") or []) if mid]
        update["cleanup_ids"] = [mid for mid in tracked if mid not in removed]
        for message_id in ids:
            await views.delete_screen_message(bot, chat_id, message_id)
    await state.update_data(**update)


async def go_back(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    """Return to the previous screen, removing the opened copies when asked to."""
    # A file opened with «📤 Send» is a normal chat message, so it must be removed
    # here: «↩️ Back» used to leave it behind even after returning to the library.
    user_id = callback.from_user.id if callback.from_user is not None else None
    if callback.message is not None:
        await clear_delivered(
            callback.bot,
            callback.message.chat.id,
            state,
            keep=services.preferences.keep_sent_files(user_id),
        )

    data = await state.get_data()
    trail = [dict(entry) for entry in data.get("trail") or []]
    target = trail.pop() if trail else dict(MAIN)
    await state.update_data(trail=trail)

    text, keyboard = await build(services, target)
    await state.update_data(screen_key=dict(target))
    await _sync_typing(state, target)
    await views.set_screen_cb(callback, state, text, keyboard)

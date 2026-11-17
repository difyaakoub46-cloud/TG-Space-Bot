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
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.bot import views
from src.bot.keyboards import (
    back_to_menu_kb,
    categories_kb,
    category_files_kb,
    file_actions_kb,
    folder_delete_confirm_kb,
    folder_files_kb,
    folders_kb,
    groups_kb,
    help_kb,
    history_kb,
    language_kb,
    main_menu_kb,
    pending_kb,
    permissions_kb,
    pick_folder_kb,
    saved_nav_kb,
    search_list_kb,
    search_prompt_kb,
    settings_kb,
    status_kb,
)
from src.bot.status import group_permissions_text, statistics_text, status_text
from src.core.i18n import get_language
from src.services.container import Services
from src.utils.filetypes import parse_category
from src.utils.sorting import SortSpec

logger = logging.getLogger(__name__)

MAX_TRAIL = 20

# The JoinCB value of the fallback group (its category value is meaningless).
JOIN_FALLBACK = "fallback"

MAIN: dict[str, object] = {"k": "main"}


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
        totals = await services.files.category_totals()
        folders = await services.files.folders(int(screen.get("u") or 0))
        favorites = sorted(
            (folder for folder in folders if folder.favorite),
            key=lambda folder: folder.favorite_sort_key,
        )
        return (
            views.categories_text(totals, favorites, len(folders)),
            categories_kb(totals, favorites, len(folders)),
        )

    if kind == "category":
        category = parse_category(str(screen.get("name") or ""))
        sort = SortSpec.from_screen(screen)
        per_page = services.settings.results_per_page()
        files, total, page = await services.files.category_page(
            category, sort, page=_page(screen), per_page=per_page
        )
        pages = max(1, -(-total // per_page))
        totals = await services.files.category_totals()
        return (
            views.category_text(
                category, files, totals.get(category), sort, page, pages
            ),
            category_files_kb(files, category, sort, page, pages),
        )

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
            files = await services.files.folder_files(folder.id, sort)
            return (
                views.folder_text(folder, files, sort),
                folder_files_kb(files, folder.id, folder.favorite, sort),
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

    if kind == "search_prompt":
        return views.search_prompt_text(), search_prompt_kb()

    if kind == "search":
        query = str(screen.get("q") or "")
        results = await services.files.search(query)
        per_page = services.settings.results_per_page()
        pages = max(1, -(-len(results) // per_page))
        page = min(_page(screen), pages)
        start = (page - 1) * per_page
        items = results[start : start + per_page]
        return (
            views.search_results_text(items, query, page, pages, total=len(results)),
            search_list_kb(items, page, pages),
        )

    if kind == "settings":
        user_id = int(screen.get("u") or 0) or None
        show_saved = services.preferences.show_saved_screen(user_id)
        return views.settings_text(show_saved), settings_kb(show_saved)

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
                views.saved_batch_text(
                    records, index, duplicates, pending, failures
                ),
                saved_nav_kb(
                    records,
                    index,
                    duplicates,
                    show_pending=bool(pending),
                ),
            )

    if kind == "history":
        category_value = str(screen.get("c") or "")
        category = parse_category(category_value) if category_value else None
        span = str(screen.get("r") or "all")
        per_page = services.settings.results_per_page()
        files, total, page = await services.files.history_page(
            category, span, page=_page(screen), per_page=per_page
        )
        pages = max(1, -(-total // per_page))
        return (
            views.history_text(files, total, category, span, page, pages),
            history_kb(files, category, span, page, pages),
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
    await views.set_screen_cb(callback, state, text, keyboard)


async def go_back(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    """Return to the previous screen, leaving every message untouched."""
    data = await state.get_data()
    trail = [dict(entry) for entry in data.get("trail") or []]
    target = trail.pop() if trail else dict(MAIN)
    await state.update_data(trail=trail)

    text, keyboard = await build(services, target)
    await state.update_data(screen_key=dict(target))
    await views.set_screen_cb(callback, state, text, keyboard)

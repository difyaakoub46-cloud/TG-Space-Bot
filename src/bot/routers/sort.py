"""Sorting router: the «🔃 فرز / ترتيب» panel of a folder.

The library categories and the search list carry their ordering in the shared
file-list component; a folder has its own in-memory sort, and this panel keeps it
in the screen key (``s`` and ``d``) so a folder keeps its order while the user
opens files and comes back.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.bot import navigation, views
from src.bot.keyboards import SortCB, sort_menu_kb
from src.core.i18n import pick
from src.services.container import Services
from src.utils.sorting import SortSpec

logger = logging.getLogger(__name__)

router = Router(name="sort")

SORTABLE = ("folder",)


async def _current_screen(state: FSMContext) -> dict[str, object] | None:
    screen = (await state.get_data()).get("screen_key") or {}
    if screen.get("k") not in SORTABLE:
        return None
    return dict(screen)


@router.callback_query(SortCB.filter(F.action == "menu"))
async def sort_menu_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the panel on top of the current list, without losing its place."""
    screen = await _current_screen(state)
    if screen is None:
        await callback.answer(
            pick(
                "Sorting is not available on this screen.",
                "لا يمكن الفرز في هذه الشاشة.",
            ),
            show_alert=True,
        )
        return

    sort = SortSpec.from_screen(screen)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        screen,
        notice=views.sort_menu_text(sort),
        markup=sort_menu_kb(sort),
        push=False,
    )


@router.callback_query(SortCB.filter(F.action == "set"))
async def sort_set_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Apply one ordering and render the list again in place."""
    data = SortCB.unpack(callback.data)
    screen = await _current_screen(state)
    if screen is None:
        await callback.answer(
            pick(
                "Sorting is not available on this screen.",
                "لا يمكن الفرز في هذه الشاشة.",
            ),
            show_alert=True,
        )
        return

    screen.update(SortSpec.parse(data.field, data.direction).to_screen())
    # A new ordering renumbers every page, so the list starts again from the first.
    screen["p"] = 1
    await navigation.navigate_callback(
        callback, state, bot, services, screen, push=False
    )


@router.callback_query(SortCB.filter(F.action == "close"))
async def sort_close_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Leave the panel and show the list with the ordering already chosen."""
    screen = await _current_screen(state)
    if screen is None:
        await callback.answer()
        return
    await navigation.navigate_callback(
        callback, state, bot, services, screen, push=False
    )

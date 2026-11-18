"""The shared file-list router: filters, page turns, days jumps and sorting.

Every button of the component carries its whole context, so this handler never
guesses: it applies what the tap changes (a filter, a page, a day or an ordering),
keeps the rest, and renders the list again in place.
"""

from __future__ import annotations

from dataclasses import replace

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.bot import file_list, navigation
from src.bot.file_list import FileListCB, FileListContext
from src.services.container import Services
from src.services.files import history_date

router = Router(name="file_list")


async def _context(
    callback: CallbackQuery, state: FSMContext, data: FileListCB
) -> FileListContext:
    """The list the tap belongs to, with its full (untruncated) context value."""
    stored_screen = dict((await state.get_data()).get("screen_key") or {})
    stored = FileListContext.from_screen(stored_screen)
    value = data.value
    # The callback carries only the beginning of a long query; the screen it was
    # typed on still knows the whole of it.
    if stored.view == data.view and stored.value.startswith(value):
        value = stored.value
    anchor = history_date(data.anchor) if data.anchor else stored.anchor
    return FileListContext(
        view=data.view,
        value=value,
        period=data.period,
        sort=data.sort,
        page=max(1, data.page),
        anchor=anchor,
    )


@router.callback_query(FileListCB.filter())
async def file_list_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileListCB.unpack(callback.data)
    ctx = await _context(callback, state, data)
    base = dict((await state.get_data()).get("screen_key") or {})
    base.setdefault("u", int(callback.from_user.id))

    if data.action == "sort":
        # The ordering panel sits on top of the same list, without losing its place.
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            ctx.to_screen(base),
            notice=file_list.sort_menu_text(ctx),
            markup=file_list.sort_menu_kb(ctx),
            push=False,
        )
        return

    if data.action == "sorted":
        # A new ordering renumbers every page, so the list starts again from one.
        ctx = replace(ctx, page=1)
    elif data.action == "clear":
        ctx = replace(ctx, value="", page=1)

    await navigation.navigate_callback(
        callback, state, bot, services, ctx.to_screen(base), push=False
    )

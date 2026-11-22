"""The shared file-list router: filters, page turns, period jumps and sorting.

Every button of the component carries its whole context, so this handler never
guesses: it applies what the tap changes (a filter, a page, a period or an
ordering), keeps the rest, and renders the list again in place.
"""

from __future__ import annotations

from dataclasses import replace

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.bot import file_list, navigation, views
from src.bot.file_list import FileListCB, FileListContext
from src.services.container import Services
from src.services.files import history_date
from src.utils.sorting import sort_from_key

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
        edit=stored.edit,
    )


@router.callback_query(FileListCB.filter())
async def file_list_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = FileListCB.unpack(callback.data)
    ctx = await _context(callback, state, data)
    base = dict((await state.get_data()).get("screen_key") or {})
    base.setdefault("u", int(callback.from_user.id))
    shown = FileListContext.from_screen(base)

    if data.action == "noop":
        # An inert button (Previous on page one, Next on the last page): it is in
        # the row for the layout, not to do anything.
        await views.ack(callback)
        return

    if (
        data.action == "filter"
        and data.period == shown.period
        and data.period != "all"
    ):
        # The same filter tapped twice asks for a date, not for the same page.
        periods = await services.files.available_periods(
            ctx.view, ctx.value, span=ctx.period
        )
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            ctx.to_screen(base),
            notice=file_list.period_picker_text(ctx, periods),
            markup=file_list.period_picker_kb(ctx, periods),
            push=False,
        )
        return

    if data.action == "edit":
        # The switch only flips the flag in the screen key: the list keeps its
        # section, its period, its ordering and its page.
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            replace(ctx, edit=not ctx.edit).to_screen(base),
            push=False,
        )
        return

    if data.action == "sort":
        # The ordering panel sits on top of the same list, without losing its place.
        await navigation.navigate_callback(
            callback,
            state,
            bot,
            services,
            ctx.to_screen(base),
            notice=views.sort_menu_text(sort_from_key(ctx.sort)),
            markup=file_list.sort_menu_kb(ctx),
            push=False,
        )
        return

    if data.action == "section":
        # The two sections are the same list under another filter: the period and
        # the ordering stay, the page starts again.
        ctx = replace(ctx, value=data.value, page=1)
    elif data.action == "sorted":
        # A new ordering renumbers every page, so the list starts again from one.
        ctx = replace(ctx, page=1)
    elif data.action == "clear":
        ctx = replace(ctx, value="", page=1)
        # The typed query is no longer needed: remove that user message from the
        # chat instead of leaving it above the cleared list.
        typed_id = (await state.get_data()).get("search_message_id")
        if typed_id and callback.message is not None:
            await views.delete_screen_message(
                bot, callback.message.chat.id, int(typed_id)
            )
            await navigation.forget(state, int(typed_id))
            await state.update_data(search_message_id=None)

    await navigation.navigate_callback(
        callback, state, bot, services, ctx.to_screen(base), push=False
    )

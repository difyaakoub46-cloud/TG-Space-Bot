"""Search router: the shared file list, narrowed by whatever the user types.

«🔍 Search» is an entry point into :mod:`src.bot.file_list`, not a screen of its
own. Opening it shows every upload; a typed message becomes the list's context
value, and the list filters the files by name (and offers the matching folders)
with the same filters, sorting, pages and date jumps as every other list.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import file_list, navigation, views
from src.bot.file_list import FileListContext
from src.bot.keyboards import SEARCH_TEXTS, MenuCB
from src.bot.states import SearchStates
from src.core.i18n import pick
from src.services.container import Services

router = Router(name="search")

_MAX_QUERY = 80


def _text_screen(message: Message, state_data: dict[str, object], query: str) -> dict[str, object]:
    """The search list for a typed query, keeping the period and ordering shown."""
    stored = FileListContext.from_screen(state_data.get("screen_key"))
    screen = file_list.new_screen(
        "search",
        query,
        int(message.from_user.id) if message.from_user else 0,
    )
    if stored.view == "search":
        screen["f"] = stored.period
        screen["srt"] = stored.sort
    return screen


@router.message(Command("search"))
@router.message(F.text.in_(SEARCH_TEXTS))
async def cmd_search(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    user_id = int(message.from_user.id) if message.from_user else 0
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message, state, bot, services, file_list.new_screen("search", "", user_id)
    )


@router.callback_query(MenuCB.filter(F.action == "search"))
async def menu_search_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        file_list.new_screen("search", "", int(callback.from_user.id)),
    )


@router.message(SearchStates.waiting_query)
async def search_query_message(
    message: Message, state: FSMContext, services: Services, bot
) -> None:
    """Whatever is typed on the search list becomes its context value."""
    query = " ".join((message.text or "").split())[:_MAX_QUERY]
    if not query:
        await message.answer(
            pick(
                "⚠️ Type the name of a file or a folder.",
                "⚠️ أدخل اسم ملف أو مجلد للبحث عنه.",
            )
        )
        return

    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        _text_screen(message, await state.get_data(), query),
    )

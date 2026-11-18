"""Search router: the upload history, narrowed by whatever the user types.

«🔍 Search» and «📂 History uploads» are one screen now. Opening Search shows the
whole upload history (its period filters, eight files per page and the same date
jumps); a typed message is the only addition, and it searches the files by name
and the folders by name at the same time. The old results screen is gone, so a
page turn, a filter and a jump all keep working through ``HistoryCB``.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import SEARCH_TEXTS, MenuCB, SearchCB
from src.bot.states import SearchStates
from src.core.i18n import pick
from src.services.container import Services
from src.services.files import history_date, history_span

router = Router(name="search")

_MAX_QUERY = 80


def _search_screen(
    user_id: int,
    *,
    span: str = "all",
    anchor: object = None,
    query: str = "",
    page: int = 1,
) -> dict[str, object]:
    """A position on the search screen: what is shown and what is typed.

    The period defaults to «All time», because opening Search must show every
    upload; the filters can narrow it afterwards without losing the query.
    """
    return {
        "k": "search",
        "f": history_span(span),
        "d": history_date(anchor).isoformat(),
        "u": int(user_id or 0),
        "q": query,
        "p": max(1, page),
    }


async def _open(callback: CallbackQuery, state: FSMContext, bot, services: Services) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _search_screen(int(callback.from_user.id)),
    )


@router.message(Command("search"))
@router.message(F.text.in_(SEARCH_TEXTS))
async def cmd_search(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    user_id = int(message.from_user.id) if message.from_user else 0
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message, state, bot, services, _search_screen(user_id)
    )


@router.callback_query(MenuCB.filter(F.action == "search"))
async def menu_search_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await _open(callback, state, bot, services)


@router.message(SearchStates.waiting_query)
async def search_query_message(
    message: Message, state: FSMContext, services: Services, bot
) -> None:
    """Whatever is typed on the search screen becomes its query."""
    query = " ".join((message.text or "").split())[:_MAX_QUERY]
    if not query:
        await message.answer(
            pick(
                "⚠️ Type the name of a file or a folder.",
                "⚠️ أدخل اسم ملف أو مجلد للبحث عنه.",
            )
        )
        return

    stored = dict((await state.get_data()).get("screen_key") or {})
    on_search = stored.get("k") == "search"
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        _search_screen(
            int(message.from_user.id) if message.from_user else 0,
            # A newer query keeps the period on screen; the first one starts from
            # the whole history.
            span=str(stored.get("f") or "all") if on_search else "all",
            anchor=stored.get("d") if on_search else None,
            query=query,
        ),
    )


@router.callback_query(SearchCB.filter(F.action == "page"))
async def search_page_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Turn a page of search results (buttons from the older results screen)."""
    data = SearchCB.unpack(callback.data)
    try:
        page = max(1, int(data.value))
    except (TypeError, ValueError):
        page = 1
    stored = dict((await state.get_data()).get("screen_key") or {})
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _search_screen(
            int(callback.from_user.id),
            span=str(stored.get("f") or "all"),
            anchor=stored.get("d"),
            query=str(stored.get("q") or ""),
            page=page,
        ),
        push=False,
    )


@router.callback_query(SearchCB.filter(F.action == "clear"))
async def search_clear_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Drop the query and leave the plain upload history on screen."""
    stored = dict((await state.get_data()).get("screen_key") or {})
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        _search_screen(
            int(callback.from_user.id),
            span=str(stored.get("f") or "all"),
            anchor=stored.get("d"),
        ),
        push=False,
    )


@router.callback_query(SearchCB.filter(F.action == "cancel"))
async def search_cancel_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state, keep_nav=False)
    await state.update_data(trail=[])
    await navigation.navigate_callback(
        callback, state, bot, services, navigation.MAIN, push=False
    )

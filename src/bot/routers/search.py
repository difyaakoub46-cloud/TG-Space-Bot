"""Search router: prompt, query execution and results pagination."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import (
    BTN_SEARCH,
    MenuCB,
    SearchCB,
    main_menu_kb,
    search_list_kb,
    search_prompt_kb,
)
from src.bot.states import SearchStates
from src.services.container import Services

router = Router(name="search")


def _per_page(services: Services) -> int:
    return services.settings.results_per_page()


def _total_pages(total: int, per_page: int) -> int:
    return max(1, -(-total // per_page))


async def _show_results(
    callback: CallbackQuery,
    services: Services,
    state: FSMContext,
    page: int,
) -> None:
    data = await state.get_data()
    query = str(data.get("search_query") or "")

    results = await services.files.search(query)
    per_page = _per_page(services)
    pages = _total_pages(len(results), per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    page_items = results[start : start + per_page]

    await views.set_screen_cb(
        callback,
        state,
        views.search_results_text(
            page_items, query, page, pages, total=len(results)
        ),
        search_list_kb(page_items, page, pages),
    )


@router.message(Command("search"))
@router.message(F.text == BTN_SEARCH)
async def cmd_search(message: Message, state: FSMContext, bot) -> None:
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_query="")
    await views.set_screen(
        bot, message, state, views.search_prompt_text(), search_prompt_kb()
    )


@router.callback_query(MenuCB.filter(F.action == "search"))
async def menu_search_callback(
    callback: CallbackQuery, state: FSMContext, bot
) -> None:
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_query="")
    await views.set_screen_cb(
        callback, state, views.search_prompt_text(), search_prompt_kb()
    )


@router.message(SearchStates.waiting_query)
async def search_query_message(
    message: Message, state: FSMContext, services: Services, bot
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ أدخل اسم الملف الذي تبحث عنه.")
        return

    await state.set_state(None)
    await state.update_data(search_query=query)

    results = await services.files.search(query)
    per_page = _per_page(services)
    pages = _total_pages(len(results), per_page)
    page_items = results[:per_page]

    await views.set_screen(
        bot,
        message,
        state,
        views.search_results_text(
            page_items, query, 1, pages, total=len(results)
        ),
        search_list_kb(page_items, 1, pages),
    )


@router.callback_query(SearchCB.filter(F.action == "page"))
async def search_page_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    data = SearchCB.unpack(callback.data)
    try:
        page = int(data.value)
    except ValueError:
        page = 1
    await _show_results(callback, services, state, page)


@router.callback_query(SearchCB.filter(F.action == "cancel"))
async def search_cancel_callback(
    callback: CallbackQuery, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.set_screen_cb(
        callback, state, views.welcome_text(), main_menu_kb()
    )

"""Search router: prompt, query execution and results pagination."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import navigation, views
from src.bot.keyboards import BTN_SEARCH, MenuCB, SearchCB
from src.bot.states import SearchStates
from src.services.container import Services

router = Router(name="search")


@router.message(Command("search"))
@router.message(F.text == BTN_SEARCH)
async def cmd_search(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.remember(state, message.message_id)
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_query="")
    await navigation.navigate_message(
        message, state, bot, services, {"k": "search_prompt"}
    )


@router.callback_query(MenuCB.filter(F.action == "search"))
async def menu_search_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_query="")
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "search_prompt"}
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
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "search", "q": query, "p": 1},
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
    stored = await state.get_data()
    query = str(stored.get("search_query") or "")
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "search", "q": query, "p": page},
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

"""Search router: query, results listing and filters."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from src.bot import views
from src.bot.keyboards import (
    BTN_CANCEL,
    BTN_SEARCH,
    MenuCB,
    SearchCancelCB,
    SearchFilterCB,
    SearchPageCB,
    back_to_menu_kb,
    cancel_reply_kb,
    main_menu_kb,
    search_list_kb,
    search_prompt_kb,
)
from src.bot.states import SearchStates
from src.services.container import Services

router = Router(name="search")

MIN_QUERY_LENGTH = 2


async def _prompt_for_query(message: Message, state: FSMContext, bot) -> None:
    await state.set_state(SearchStates.waiting_query)
    await views.set_screen(bot, message, state, views.search_prompt_text(), search_prompt_kb())


@router.message(Command("search"))
@router.message(F.text == BTN_SEARCH)
async def search_command(message: Message, state: FSMContext, bot) -> None:
    await _prompt_for_query(message, state, bot)


@router.message(SearchStates.waiting_query)
async def search_query_message(message: Message, state: FSMContext, services: Services, bot) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ أدخل اسم الملف للبحث.")
        return

    results = await services.files.search(query)
    total_pages = (len(results) + 9) // 10
    page = 1
    page_items = results[:10]
    await views.set_screen(
        bot,
        message,
        state,
        views.search_results_text(page_items, query, page, total_pages),
        search_list_kb(page_items, page, total_pages),
    )


@router.callback_query(SearchPageCB.filter())
async def search_page_callback(callback: CallbackQuery, state: FSMContext, services: Services, bot) -> None:
    page = int(callback.data.split("search_page_", 1)[1])
    query = (await state.get_data()).get("query", "")
    results = await services.files.search(query)
    total_pages = (len(results) + 9) // 10
    page = max(1, min(page, total_pages))
    page_items = results[(page-1)*10:page*10]
    await views.set_screen_cb(
        callback,
        state,
        views.search_results_text(page_items, query, page, total_pages),
        search_list_kb(page_items, page, total_pages),
    )


@router.callback_query(SearchFilterCB.filter())
async def search_filter_callback(callback: CallbackQuery, state: FSMContext, services: Services, bot) -> None:
    category = callback.data.split("search_filter_", 1)[1]
    query = (await state.get_data()).get("query", "")
    results = await services.files.search(query, category=category)
    total_pages = (len(results) + 9) // 10
    page_items = results[:10]
    await views.set_screen_cb(
        callback,
        state,
        views.search_results_text(page_items, query, 1, total_pages),
        search_list_kb(page_items, 1, total_pages),
    )


@router.callback_query(SearchCancelCB.filter())
async def search_cancel_callback(callback: CallbackQuery, state: FSMContext, bot) -> None:
    await state.clear()
    await views.set_screen_cb(callback, state, "✅ تم الإلغاء.", main_menu_kb())


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext, bot) -> None:
    await state.clear()
    await views.set_screen(bot, message, state, "✅ تم الإلغاء.", main_menu_kb())


def search_results_text(results: list, query: str, page: int, total_pages: int) -> str:
    lines = [f"🔍 <b>نتائج البحث عن "{query}"</b>"]
    for file in results:
        lines.append(f"- {file.file_name}")
    lines.append(f"\nالصفحة {page} من {total_pages}")
    return "\n".join(lines)

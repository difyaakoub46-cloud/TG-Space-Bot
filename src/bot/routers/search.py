"""Quick search by file name, with category filters and pagination."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import (
    BTN_CANCEL,
    BTN_SEARCH,
    MenuCB,
    SearchFilterCB,
    SearchPageCB,
    cancel_reply_kb,
    main_menu_kb,
    main_reply_kb,
    search_list_kb,
)
from src.bot.states import SearchStates
from src.services.container import Services
from src.utils.filetypes import parse_category

router = Router(name="search")

MIN_QUERY_LENGTH = 2


async def _prompt_for_query(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(query=None, category=None, page=1)
    await message.answer(
        "🔍 أرسل اسم الملف الذي تبحث عنه.\n"
        f"اكتب {MIN_QUERY_LENGTH} أحرف على الأقل، ويمكنك الإلغاء بالزر أدناه.",
        reply_markup=cancel_reply_kb(),
    )


@router.message(Command("search"))
@router.message(F.text == BTN_SEARCH)
async def search_command(message: Message, state: FSMContext) -> None:
    await _prompt_for_query(message, state)


@router.callback_query(MenuCB.filter(F.action == "search"))
async def search_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is not None:
        await _prompt_for_query(callback.message, state)


@router.message(SearchStates.waiting_query, F.text == BTN_CANCEL)
async def cancel_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("تم إلغاء البحث.", reply_markup=main_reply_kb())


@router.message(SearchStates.waiting_query, F.text)
async def run_search(message: Message, state: FSMContext, services: Services) -> None:
    query = (message.text or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        await message.answer(f"⚠️ اكتب {MIN_QUERY_LENGTH} أحرف على الأقل للبحث.")
        return

    await state.update_data(query=query, category=None, page=1)
    # Leave the state but keep the search context for pagination/filtering.
    await state.set_state(None)

    page = await services.files.search(query=query, page=1)
    await message.answer(
        views.search_text(page, query, None),
        reply_markup=search_list_kb(page.items, page.page, page.pages, None),
        disable_web_page_preview=True,
    )


async def _rerun(
    callback: CallbackQuery,
    state: FSMContext,
    services: Services,
    *,
    page_number: int,
    category_value: str | None,
) -> None:
    data = await state.get_data()
    query = data.get("query")
    if not query:
        await views.show(
            callback,
            "⚠️ انتهت صلاحية نتائج البحث. ابدأ بحثاً جديداً عبر /search.",
            main_menu_kb(),
        )
        return

    category = parse_category(category_value) if category_value else None
    result = await services.files.search(query=query, category=category, page=page_number)
    await state.update_data(category=category_value, page=result.page)
    await views.show(
        callback,
        views.search_text(result, query, category_value),
        search_list_kb(result.items, result.page, result.pages, category_value),
    )


@router.callback_query(SearchPageCB.filter())
async def change_page(
    callback: CallbackQuery,
    callback_data: SearchPageCB,
    state: FSMContext,
    services: Services,
) -> None:
    await callback.answer()
    data = await state.get_data()
    await _rerun(
        callback,
        state,
        services,
        page_number=callback_data.page,
        category_value=data.get("category"),
    )


@router.callback_query(SearchFilterCB.filter())
async def filter_category(
    callback: CallbackQuery,
    callback_data: SearchFilterCB,
    state: FSMContext,
    services: Services,
) -> None:
    await callback.answer()
    category_value = None if callback_data.category == "all" else callback_data.category
    await _rerun(callback, state, services, page_number=1, category_value=category_value)

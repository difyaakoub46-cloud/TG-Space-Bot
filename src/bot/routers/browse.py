"""Library browsing per category, file details, delivery and deletion."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot import views
from src.bot.keyboards import (
    BTN_LIBRARY,
    BTN_STATS,
    CategoryCB,
    FileCB,
    MenuCB,
    back_to_menu_kb,
    browse_list_kb,
    categories_kb,
    delete_confirm_kb,
    file_actions_kb,
    main_menu_kb,
)
from src.db.repository import SupabaseError
from src.services.container import Services
from src.utils.filetypes import parse_category

logger = logging.getLogger(__name__)

router = Router(name="library")


# ── entry points ────────────────────────────────
@router.message(Command("library"))
@router.message(F.text == BTN_LIBRARY)
async def library_message(message: Message, services: Services) -> None:
    counts = await services.files.category_counts()
    await message.answer(views.categories_text(counts), reply_markup=categories_kb(counts))


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def stats_message(message: Message, services: Services) -> None:
    stats = await services.files.stats()
    await message.answer(views.stats_text(stats), reply_markup=back_to_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "categories"))
async def categories_callback(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    counts = await services.files.category_counts()
    await views.show(callback, views.categories_text(counts), categories_kb(counts))


@router.callback_query(MenuCB.filter(F.action == "stats"))
async def stats_callback(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    stats = await services.files.stats()
    await views.show(callback, views.stats_text(stats), back_to_menu_kb())


# ── category pages ──────────────────────────────
@router.callback_query(CategoryCB.filter())
async def category_page(
    callback: CallbackQuery, callback_data: CategoryCB, services: Services
) -> None:
    await callback.answer()
    category = parse_category(callback_data.category)
    page = await services.files.search(category=category, page=callback_data.page)
    await views.show(
        callback,
        views.search_text(page, None, category.value),
        browse_list_kb(page.items, category.value, page.page, page.pages),
    )


# ── single file ─────────────────────────────────
@router.callback_query(FileCB.filter(F.action == "view"))
async def view_file(
    callback: CallbackQuery, callback_data: FileCB, services: Services
) -> None:
    await callback.answer()
    record = await services.files.get(callback_data.file_id)
    if record is None:
        await views.show(callback, "⚠️ هذا الملف لم يعد موجوداً في المكتبة.", main_menu_kb())
        return
    await views.show(callback, views.file_card(record), file_actions_kb(record.id))


@router.callback_query(FileCB.filter(F.action == "send"))
async def send_file(
    callback: CallbackQuery, callback_data: FileCB, services: Services
) -> None:
    record = await services.files.get(callback_data.file_id)
    if record is None:
        await callback.answer("⚠️ الملف غير موجود في المكتبة.", show_alert=True)
        return

    target_chat = callback.message.chat.id if callback.message else callback.from_user.id
    try:
        await services.files.send_to_user(record, target_chat)
    except TelegramAPIError as exc:
        logger.warning("Delivery of %s failed: %s", record.id, exc)
        await callback.answer(
            "⚠️ تعذّر إرسال الملف: النسخة الأصلية لم تعد متاحة في مجموعة التخزين.",
            show_alert=True,
        )
        return

    await callback.answer("✅ تم إرسال الملف.")


@router.callback_query(FileCB.filter(F.action == "delete"))
async def ask_delete(
    callback: CallbackQuery, callback_data: FileCB, services: Services
) -> None:
    await callback.answer()
    record = await services.files.get(callback_data.file_id)
    if record is None:
        await views.show(callback, "⚠️ هذا الملف لم يعد موجوداً في المكتبة.", main_menu_kb())
        return
    await views.show(callback, views.delete_prompt_text(record), delete_confirm_kb(record.id))


@router.callback_query(FileCB.filter(F.action == "confirm_delete"))
async def confirm_delete(
    callback: CallbackQuery, callback_data: FileCB, services: Services
) -> None:
    await callback.answer("جارٍ الحذف…")
    try:
        record = await services.files.delete(callback_data.file_id)
    except LookupError:
        await views.show(callback, "⚠️ الملف غير موجود مسبقاً.", main_menu_kb())
        return
    except SupabaseError:
        logger.exception("Deleting %s failed", callback_data.file_id)
        await views.show(
            callback,
            "❌ فشل حذف السجل من قاعدة البيانات. حاول مرة أخرى بعد قليل.",
            main_menu_kb(),
        )
        return

    await views.show(callback, views.deletion_text(record), main_menu_kb())


# Private chats only: the library UI is not meant to run inside groups.
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

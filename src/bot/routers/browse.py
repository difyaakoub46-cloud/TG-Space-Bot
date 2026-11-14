"""Browse router: library, categories and file actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from src.bot import views
from src.bot.keyboards import (
    CategoryCB,
    FileCB,
    MenuCB,
    back_to_menu_kb,
    categories_kb,
    category_files_kb,
    delete_confirm_kb,
    file_actions_kb,
)
from src.services.container import Services

router = Router(name="browse")


@router.message(Command("library"))
@router.message(F.text == "📚 المكتبة")
async def library_message(message: Message, services: Services, state: FSMContext, bot) -> None:
    counts = await services.files.category_counts()
    await views.set_screen(bot, message, state, views.categories_text(counts), categories_kb(counts))


@router.callback_query(MenuCB.filter(F.action == "categories"))
async def categories_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    counts = await services.files.category_counts()
    await views.set_screen_cb(callback, state, views.categories_text(counts), categories_kb(counts))


@router.callback_query(CategoryCB.filter())
async def category_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    category = callback.data.split("category_", 1)[1]
    files = await services.files.by_category(category)
    await views.set_screen_cb(callback, state, views.category_text(category, files), category_files_kb(files, category))


@router.callback_query(FileCB.filter())
async def file_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    file_id = callback.data.split("file_", 1)[1]
    record = await services.files.get(file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await views.set_screen_cb(callback, state, views.file_text(record), file_actions_kb(record.id))


@router.callback_query(F.data.startswith("file_send_"))
async def file_send_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    file_id = callback.data.split("file_send_", 1)[1]
    record = await services.files.get(file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await callback.message.answer_document(
        document=record.file_id,
        caption=views.file_caption(record),
    )
    await views.set_screen_cb(callback, state, views.file_sent_text(record), back_to_menu_kb())


@router.callback_query(F.data.startswith("file_delete_"))
async def file_delete_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    file_id = callback.data.split("file_delete_", 1)[1]
    record = await services.files.get(file_id)
    if record is None:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return
    await views.set_screen_cb(callback, state, views.deletion_text(record), delete_confirm_kb(file_id))


@router.callback_query(F.data.startswith("file_delete_confirm_"))
async def file_delete_confirm_callback(callback: CallbackQuery, services: Services, state: FSMContext, bot) -> None:
    file_id = callback.data.split("file_delete_confirm_", 1)[1]
    deleted = await services.files.delete(file_id)
    if not deleted:
        await callback.answer("فشل الحذف.", show_alert=True)
        return
    await views.set_screen_cb(callback, state, "✅ تم حذف الملف.", back_to_menu_kb())


def category_text(category: str, files: list) -> str:
    lines = [f"📁 <b>{category.capitalize()}</b>"]
    for file in files:
        lines.append(f"- {file.file_name}")
    return "\n".join(lines)


def file_text(record) -> str:
    return (
        f"📄 <b>{record.file_name}</b>\n\n"
        f"الحجم: {record.size_bytes} بايت\n"
        f"الفئة: {record.category}\n"
    )


def file_caption(record) -> str:
    return f"{record.file_name}\n\n{rc_token}"


def file_sent_text(record) -> str:
    return f"✅ تم إرسال {record.file_name}"

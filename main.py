"""TG Space Bot — Telegram file storage bot backed by Supabase."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from src.bot.dispatcher import build_dispatcher
from src.core.config import get_settings
from src.core.logging import configure_logging
from src.db.repository import FileRepository
from src.services.container import Services
from src.services.files import FileService
from src.services.storage import StorageService

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="القائمة الرئيسية"),
    BotCommand(command="search", description="البحث عن ملف بالاسم"),
    BotCommand(command="library", description="تصفح المكتبة حسب الفئة"),
    BotCommand(command="stats", description="إحصائيات التخزين"),
    BotCommand(command="help", description="دليل الاستخدام"),
    BotCommand(command="cancel", description="إلغاء العملية الحالية"),
]


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    repository = FileRepository(settings)
    storage = StorageService(bot, settings)
    services = Services(
        settings=settings,
        repository=repository,
        storage=storage,
        files=FileService(repository, storage, settings),
    )
    dispatcher = build_dispatcher(services)

    try:
        me = await bot.get_me()
        logger.info("Bot started as @%s (id=%s)", me.username, me.id)
        await bot.set_my_commands(COMMANDS)
        await storage.verify_chats()
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await repository.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")

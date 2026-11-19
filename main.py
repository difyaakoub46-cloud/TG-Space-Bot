#!/usr/bin/env python3
"""TG-Space-Bot entry point: polling bot + FastAPI keep-alive server."""

from __future__ import annotations

import asyncio
import logging
import threading

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from src.bot import reports
from src.bot.dispatcher import build_dispatcher
from src.core.config import ConfigurationError, Settings
from src.core.logging import setup_logging
from src.services.container import build_services
from src.web.app import create_app

logger = logging.getLogger(__name__)


def start_keepalive_server(settings: Settings) -> threading.Thread:
    """Serve the health endpoints in a daemon thread (Render / UptimeRobot)."""
    app = create_app()

    def _serve() -> None:
        try:
            uvicorn.run(
                app,
                host=settings.server_host,
                port=settings.server_port,
                log_level="warning",
                access_log=False,
            )
        except Exception:  # noqa: BLE001 - keep polling alive even if HTTP fails
            logger.exception("Keep-alive HTTP server stopped unexpectedly")

    thread = threading.Thread(target=_serve, name="keepalive", daemon=True)
    thread.start()
    return thread


async def _send_owner_report(bot, owner_id: int | None, text: str) -> None:
    """Deliver a startup report to the owner; a missing owner means log only."""
    if owner_id is None:
        logger.warning("OWNER_ID is not set: the storage report was only logged")
        return
    try:
        await bot.send_message(owner_id, text)
    except TelegramAPIError:
        logger.warning("Could not deliver the storage report", exc_info=True)


async def report_storage_health(settings: Settings, services) -> None:
    """Reach every storage group at boot and tell the owner what is broken.

    Without this the first broken group is discovered at the first upload; here
    the mismatch is reported before any file is involved.
    """
    configured = settings.storage_chats()
    # The fallback group is an emergency store, not a library group: a category
    # without its own group is still reported as missing.
    missing = settings.uncovered_categories()

    if not configured:
        logger.warning(
            "No storage groups configured: set TG_STORAGE_CHAT_<CATEGORY> "
            "variables before uploading files."
        )
    else:
        logger.info(
            "Storage groups configured for: %s",
            ", ".join(category.value for category in configured),
        )
        if missing:
            logger.warning(
                "No storage group configured for: %s - uploads of these types "
                "will be refused (check the TG_STORAGE_CHAT_<CATEGORY> names)",
                ", ".join(category.value for category in missing),
            )

    try:
        checks = await services.storage.check_chats()
    except Exception:  # noqa: BLE001 - startup must not fail on a bad chat id
        logger.exception("Could not verify storage groups")
        return

    if not missing and all(check.reachable for check in checks):
        logger.info("All %s storage group(s) are reachable", len(checks))
        return

    await _send_owner_report(
        services.bot, settings.owner_id, reports.storage_check_text(checks, missing)
    )


async def run_bot(settings: Settings, services, dispatcher) -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    services.bind_bot(bot)

    await report_storage_health(settings, services)

    try:
        logger.info("Starting long polling")
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await bot.session.close()
        await services.repository.close()


def main() -> None:
    settings = Settings()
    setup_logging(settings)
    # The bot is private: with no allow-list it stops here, before the keep-alive
    # server or polling start, so it can never be left open to everybody.
    try:
        settings.require_allowed_users()
    except ConfigurationError as exc:
        raise SystemExit(1) from exc

    services = build_services(settings)
    dispatcher = build_dispatcher(services)

    logger.info("Starting TG-Space-Bot")
    start_keepalive_server(settings)
    logger.info(
        "Keep-alive HTTP server: http://%s:%s/health",
        settings.server_host,
        settings.server_port,
    )

    try:
        asyncio.run(run_bot(settings, services, dispatcher))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()

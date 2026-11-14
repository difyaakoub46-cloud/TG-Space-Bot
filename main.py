#!/usr/bin/env python3
"""TG-Space-Bot entry point."""

from __future__ import annotations

import asyncio
import logging

from src.bot.dispatcher import build_dispatcher
from src.core.config import Settings
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings)

    logger.info("Starting TG-Space-Bot")
    dispatcher = build_dispatcher(settings)
    asyncio.run(dispatcher.start_polling(settings.bot_token))


if __name__ == "__main__":
    main()

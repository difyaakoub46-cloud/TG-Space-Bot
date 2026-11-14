"""Logging setup for the bot."""

from __future__ import annotations

import logging
import sys

from src.core.config import Settings


def setup_logging(settings: Settings) -> None:
    level = logging.INFO
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)

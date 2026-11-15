"""Initialize the Supabase schema for TG-Space-Bot.

Usage: ``SUPABASE_DATABASE_URL=postgresql://... python -m scripts.init_db``
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import psycopg

logger = logging.getLogger("init_db")

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"


def _dsn() -> str:
    dsn = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "SUPABASE_DATABASE_URL (or DATABASE_URL) must be set to run migrations."
        )
    return dsn


async def run() -> None:
    conn = await psycopg.AsyncConnection.connect(_dsn(), autocommit=True)
    try:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            logger.info("Running migration: %s", path.name)
            async with conn.cursor() as cur:
                await cur.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    logger.info("All migrations applied")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s"
    )
    try:
        asyncio.run(run())
    except psycopg.Error as exc:
        logger.error("Migration failed: %s", exc)
        sys.exit(1)

"""Initialize the Supabase schema for TG-Space-Bot."""

from __future__ import annotations

import asyncio
import os
import sys

import psycopg


async def run() -> None:
    conn = await psycopg.aincpy.connect(
        os.environ["SUPABASE_DATABASE_URL"], autocommit=True
    )
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        migrations_dir = os.path.join(root, "migrations")

        for name in sorted(os.listdir(migrations_dir)):
            if not name.endswith(".sql"):
                continue
            path = os.path.join(migrations_dir, name)
            with open(path, encoding="utf-8") as f:
                sql = f.read()
            logger = __import__("logging").getLogger(__name__)
            logger.info("Running migration: %s", name)
            async with conn.cursor() as cur:
                await cur.execute(sql)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())

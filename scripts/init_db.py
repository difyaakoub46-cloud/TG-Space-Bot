"""Apply all SQL migrations to the Supabase Postgres database.

Usage:
    export SUPABASE_DB_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
    python -m scripts.init_db
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> int:
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print(
            "SUPABASE_DB_URL is not set.\n"
            "Copy the connection string from Supabase → Project Settings → Database "
            "and export it as SUPABASE_DB_URL.",
            file=sys.stderr,
        )
        return 1

    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        print("psycopg is required: pip install 'psycopg[binary]'", file=sys.stderr)
        return 1

    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        print(f"No migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    with psycopg.connect(dsn, autocommit=True) as connection:
        for path in migrations:
            sql = path.read_text(encoding="utf-8")
            with connection.cursor() as cursor:
                cursor.execute(sql)
            print(f"✓ applied {path.name}")

    print("Database schema is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

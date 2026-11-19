"""Rebuild the schema from ``migrations/`` on a throwaway database and check it.

The migrations are the only thing that creates the schema, so a type or order
mistake stays invisible until someone rebuilds a database from scratch — that is
exactly how ``files.id`` (uuid) and ``folder_files.file_id`` (integer) drifted
apart. This script catches that class of bug before it reaches a deployment:

1. It creates a brand-new, empty database (never the configured one).
2. It applies every ``migrations/*.sql`` file in name order, exactly as
   :mod:`scripts.init_db` does.
3. It asserts the objects that must exist, and — the important part — that every
   foreign key column has the same type as the column it references.
4. It runs a small insert/update/delete flow through the real triggers, views
   and constraints, then drops the temporary database.

Usage::

    python -m scripts.verify_schema

Two ways to provide a server:

* ``VERIFY_DATABASE_URL`` — a scratch PostgreSQL/Supabase connection string
  (``postgresql://...``). A temporary database is created on it and dropped at
  the end, so the database in the URL itself is never touched.
* leave it unset and install ``pgserver`` (``pip install pgserver``), which
  downloads a private PostgreSQL just for this check.

``pg_trgm`` is a Supabase extension. When the test server does not ship it, the
script replaces that one extension line and its trigram index with a plain btree
index, prints a warning, and still validates every table, key, trigger and view.
Exit code is 0 when everything passed and 1 on the first structural failure.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

logging.basicConfig(level=logging.INFO, format="%(levelname)s [verify_schema] %(message)s")
logger = logging.getLogger("verify_schema")

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"

EXPECTED_TABLES = ("files", "files_stats", "folders", "folder_files")
EXPECTED_VIEWS = ("files_by_category", "folder_totals")
EXPECTED_TRIGGERS = ("files_sync_stats", "files_touch_updated_at")
# Supabase creates these roles; a bare PostgreSQL does not have them, and 005
# grants SELECT to them.
SUPABASE_ROLES = ("anon", "authenticated", "service_role")
# 011 closes the totals views and the date-picker function to the public roles.
PUBLIC_ROLES = ("anon", "authenticated")
PERIODS_FUNCTION = "public.files_periods(text, text, text, integer, integer)"


class VerificationError(RuntimeError):
    """A structural expectation was not met."""


def migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise VerificationError(f"no migrations found in {MIGRATIONS_DIR}")
    return files


def _prepare_sql(sql: str, *, has_trgm: bool) -> str:
    """Return the migration text, neutralising pg_trgm when it is unavailable."""
    if has_trgm:
        return sql
    without_extension = re.sub(
        r"^create extension if not exists pg_trgm;\s*$",
        "-- pg_trgm is not available on the verification server; skipped",
        sql,
        flags=re.MULTILINE,
    )
    return without_extension.replace(
        "using gin (file_name gin_trgm_ops)", "using btree (file_name)"
    )


def apply_migrations(conn: psycopg.Connection, *, has_trgm: bool) -> None:
    for path in migration_files():
        logger.info("applying %s", path.name)
        sql = _prepare_sql(path.read_text(encoding="utf-8"), has_trgm=has_trgm)
        try:
            conn.execute(sql)
        except psycopg.Error as exc:
            raise VerificationError(f"{path.name} failed: {exc}") from exc


def _rows(cur: psycopg.Cursor, sql: str, params: Any = None) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def check_objects(conn: psycopg.Connection) -> list[str]:
    """Every object the bot uses must exist after the migrations ran."""
    problems: list[str] = []
    cur = conn.cursor()

    tables = {row[0] for row in _rows(cur, "select tablename from pg_tables where schemaname='public'")}
    for table in EXPECTED_TABLES:
        if table not in tables:
            problems.append(f"missing table public.{table}")

    views = {row[0] for row in _rows(cur, "select viewname from pg_views where schemaname='public'")}
    for view in EXPECTED_VIEWS:
        if view not in views:
            problems.append(f"missing view public.{view}")

    triggers = {
        row[0]
        for row in _rows(
            cur, "select tgname from pg_trigger where not tgisinternal"
        )
    }
    for trigger in EXPECTED_TRIGGERS:
        if trigger not in triggers:
            problems.append(f"missing trigger {trigger}")

    indexes = {row[0] for row in _rows(cur, "select indexname from pg_indexes where schemaname='public'")}
    for index in (
        "files_unique_file_id_idx",
        "files_storage_message_key",
        "folders_user_name_key",
        "folder_files_file_idx",
    ):
        if index not in indexes:
            problems.append(f"missing index {index}")

    return problems


def check_foreign_key_types(conn: psycopg.Connection) -> list[str]:
    """A foreign key column must have exactly the type of its target column.

    This is the regression guard for the uuid/integer drift: PostgreSQL cannot
    create such a constraint, so a rebuild would fail — and a mismatch that some
    day *can* be created would still be a bug worth failing on.
    """
    problems: list[str] = []
    cur = conn.cursor()
    rows = _rows(
        cur,
        """
        select
            con.conname,
            src.relname                as source_table,
            src_col.attname            as source_column,
            format_type(src_col.atttypid, src_col.atttypmod) as source_type,
            tgt.relname                as target_table,
            tgt_col.attname            as target_column,
            format_type(tgt_col.atttypid, tgt_col.atttypmod) as target_type
        from pg_constraint con
        join pg_class src      on src.oid = con.conrelid
        join pg_namespace n    on n.oid = src.relnamespace
        join pg_class tgt      on tgt.oid = con.confrelid
        join pg_attribute src_col on src_col.attrelid = con.conrelid
                                 and src_col.attnum = con.conkey[1]
        join pg_attribute tgt_col on tgt_col.attrelid = con.confrelid
                                 and tgt_col.attnum = con.confkey[1]
        where con.contype = 'f'
          and n.nspname = 'public'
        order by con.conname
        """,
    )
    if not rows:
        problems.append("no foreign keys found: folder_files lost its references")
    for name, source_table, source_column, source_type, target_table, target_column, target_type in rows:
        if source_type != target_type:
            problems.append(
                f"{name}: {source_table}.{source_column} is {source_type} but "
                f"{target_table}.{target_column} is {target_type}"
            )
    return problems


def check_identity(conn: psycopg.Connection) -> list[str]:
    """``files.id`` and ``folders.id`` must be integer identities, as the bot expects."""
    problems: list[str] = []
    cur = conn.cursor()
    for table in ("files", "folders"):
        rows = _rows(
            cur,
            """
            select data_type, is_identity
            from information_schema.columns
            where table_schema = 'public' and table_name = %s and column_name = 'id'
            """,
            (table,),
        )
        if not rows:
            problems.append(f"missing column {table}.id")
            continue
        data_type, is_identity = rows[0]
        if data_type != "bigint" or is_identity != "YES":
            problems.append(
                f"{table}.id is {data_type} (identity={is_identity}); expected bigint identity"
            )
    return problems


def check_view_security(conn: psycopg.Connection) -> list[str]:
    """The totals views must not leak their rows to the public roles.

    A view runs with its owner's rights unless ``security_invoker`` is on, so a
    plain revoke would not be enough; 011 turns both on and takes the privileges
    away. The bot reaches PostgREST with the service key, which keeps SELECT.
    """
    problems: list[str] = []
    cur = conn.cursor()
    for view in EXPECTED_VIEWS:
        rows = _rows(
            cur,
            """
            select coalesce(array_to_string(c.reloptions, ','), '')
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relname = %s
            """,
            (view,),
        )
        if not rows:
            problems.append(f"missing view public.{view}")
            continue
        options = {item.strip().lower() for item in rows[0][0].split(",") if item.strip()}
        if "security_invoker=true" not in options:
            problems.append(
                f"{view}: security_invoker is not enabled (reloptions={rows[0][0] or 'none'})"
            )
        for role in PUBLIC_ROLES:
            allowed = _rows(
                cur, "select has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{view}")
            )[0][0]
            if allowed:
                problems.append(f"{view}: {role} still has SELECT")
        kept = _rows(
            cur, "select has_table_privilege('service_role', %s, 'SELECT')", (f"public.{view}",)
        )[0][0]
        if not kept:
            problems.append(f"{view}: service_role lost SELECT, so the bot cannot read it")
    return problems


def check_function_security(conn: psycopg.Connection) -> list[str]:
    """``files_periods`` must stay callable for the bot only.

    A function is executable by ``PUBLIC`` as soon as it is created, and ``anon``
    inherits ``PUBLIC``, so revoking only from ``anon`` would leave it callable.
    """
    problems: list[str] = []
    cur = conn.cursor()
    if not _rows(
        cur,
        """
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = 'files_periods'
        """,
    ):
        return ["missing function public.files_periods"]

    for role, expected in (("anon", False), ("authenticated", True), ("service_role", True)):
        allowed = _rows(
            cur, "select has_function_privilege(%s, %s, 'EXECUTE')", (role, PERIODS_FUNCTION)
        )[0][0]
        if allowed is not expected:
            problems.append(
                f"files_periods: {role} has EXECUTE={allowed}; expected {expected}"
            )

    public_grants = _rows(
        cur,
        """
        select count(*)
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
        where n.nspname = 'public' and p.proname = 'files_periods'
          and acl.grantee = 0 and acl.privilege_type = 'EXECUTE'
        """,
    )[0][0]
    if public_grants:
        problems.append("files_periods is still executable by PUBLIC")
    return problems


def smoke_test(conn: psycopg.Connection) -> list[str]:
    """Exercise the triggers, the views and the constraints on the new schema."""
    problems: list[str] = []
    cur = conn.cursor()
    try:
        cur.execute(
            """
            insert into public.files
                (file_id, file_unique_id, file_name, file_type, category,
                 file_size, user_id, chat_id, message_id)
            values ('verify-file', 'verify-unique', 'verify.pdf', 'application/pdf',
                    'document', 1000, 1, -100111, 1)
            returning id
            """
        )
        file_id = cur.fetchone()[0]
        cur.execute("insert into public.folders (user_id, name) values (1, 'verify') returning id")
        folder_id = cur.fetchone()[0]
        cur.execute(
            "insert into public.folder_files (folder_id, file_id) values (%s, %s)",
            (folder_id, file_id),
        )
        total = _rows(cur, "select value from public.files_stats where setting='total_bytes'")[0][0]
        if total != 1000:
            problems.append(f"files_stats.total_bytes is {total} after an insert; expected 1000")

        cur.execute("update public.files set file_size = 2500 where id = %s", (file_id,))
        total = _rows(cur, "select value from public.files_stats where setting='total_bytes'")[0][0]
        if total != 2500:
            problems.append(f"files_stats.total_bytes is {total} after an update; expected 2500")

        category_totals = _rows(
            cur, "select files, bytes from public.files_by_category where category='document'"
        )
        if category_totals != [(1, 2500)]:
            problems.append(f"files_by_category returned {category_totals}; expected [(1, 2500)]")

        folder_totals = _rows(
            cur, "select files, bytes from public.folder_totals where folder_id = %s", (folder_id,)
        )
        if folder_totals != [(1, 2500)]:
            problems.append(f"folder_totals returned {folder_totals}; expected [(1, 2500)]")

        cur.execute("delete from public.files where id = %s", (file_id,))
        links = _rows(cur, "select count(*) from public.folder_files")[0][0]
        total = _rows(cur, "select value from public.files_stats where setting='total_bytes'")[0][0]
        if links != 0:
            problems.append(f"{links} folder link(s) survived the file delete; expected 0")
        if total != 0:
            problems.append(f"files_stats.total_bytes is {total} after a delete; expected 0")
        if _rows(cur, "select count(*) from public.folders")[0][0] != 1:
            problems.append("deleting a file also removed its folder")
    finally:
        conn.rollback()
    return problems


CHECKS = (
    ("objects", check_objects),
    ("foreign key types", check_foreign_key_types),
    ("identity columns", check_identity),
    ("view security", check_view_security),
    ("function security", check_function_security),
    ("runtime smoke test", smoke_test),
)


def _database_url(server_url: str, name: str) -> str:
    """The same connection string, pointing at the database ``name``.

    Only the path's last segment changes: query parameters such as
    ``?host=/tmp/pgdata`` (a unix socket) or ``?sslmode=require`` must survive,
    otherwise the copy connects somewhere else entirely.
    """
    parts = urlsplit(server_url)
    parent = parts.path.rsplit("/", 1)[0]
    return urlunsplit(parts._replace(path=f"{parent}/{name}"))


def _temporary_database(server_url: str) -> tuple[str, str]:
    """Create an empty database on ``server_url``; returns (name, url)."""
    name = f"verify_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(server_url, autocommit=True)
    try:
        admin.execute(f'create database "{name}"')
    finally:
        admin.close()
    return name, _database_url(server_url, name)


def _drop_database(server_url: str, name: str) -> None:
    try:
        admin = psycopg.connect(server_url, autocommit=True)
    except psycopg.Error:  # pragma: no cover - the temp server may be gone already
        return
    try:
        admin.execute(f'drop database if exists "{name}" with (force)')
    finally:
        admin.close()


def _server_url(explicit: str | None) -> tuple[str, Any]:
    """Resolve a server URL, or start a private pgserver; returns (url, owner)."""
    if explicit:
        return explicit, None
    try:
        import pgserver  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise VerificationError(
            "no PostgreSQL server: set VERIFY_DATABASE_URL, or install pgserver "
            "with `pip install pgserver`"
        ) from exc
    server = pgserver.get_server(str(Path(os.environ.get("TMPDIR", "/tmp")) / "verify_schema_pgdata"))
    logger.info("started a private PostgreSQL with pgserver")
    return server.get_uri(), server


def run(server_url: str | None = None) -> list[str]:
    """Rebuild the schema and return a list of problems (empty means healthy)."""
    url, owner = _server_url(server_url)
    name, db_url = _temporary_database(url)
    problems: list[str] = []
    try:
        conn = psycopg.connect(db_url, autocommit=True)
        try:
            cur = conn.cursor()
            # Supabase ships these roles; grants in 005 need them to exist.
            for role in SUPABASE_ROLES:
                cur.execute(
                    "select 1 from pg_roles where rolname = %s", (role,)
                )
                if cur.fetchone() is None:
                    cur.execute(f'create role "{role}" nologin')
                    problems.append(
                        f'note: role "{role}" did not exist and was created for the check'
                    )
            available = {
                row[0]
                for row in _rows(cur, "select name from pg_available_extensions")
            }
            has_trgm = "pg_trgm" in available
            if not has_trgm:
                logger.warning(
                    "pg_trgm is not available on this server: the extension and its "
                    "trigram index are replaced by a btree index for the structural check"
                )
            apply_migrations(conn, has_trgm=has_trgm)
            logger.info("all %s migrations applied to a fresh database", len(migration_files()))

            for label, check in CHECKS:
                found = check(conn)
                if found:
                    problems.extend(f"{label}: {item}" for item in found)
                else:
                    logger.info("%s: ok", label)
        finally:
            conn.close()
    finally:
        _drop_database(url, name)
        if owner is not None:
            try:
                owner.cleanup()
            except Exception:  # pragma: no cover - best effort shutdown
                logger.debug("could not stop the private server", exc_info=True)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("VERIFY_DATABASE_URL"),
        help="scratch PostgreSQL connection string (defaults to a private pgserver)",
    )
    args = parser.parse_args(argv)

    try:
        problems = run(args.database_url)
    except VerificationError as exc:
        logger.error("schema verification could not run: %s", exc)
        return 1

    hard = [problem for problem in problems if not problem.startswith("note:")]
    for note in problems:
        if note.startswith("note:"):
            logger.info(note)
    if hard:
        for problem in hard:
            logger.error(problem)
        logger.error("schema verification FAILED (%s problem(s))", len(hard))
        return 1
    logger.info("schema verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""File operations service: storage, metadata and library queries."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.db.repository import FileRepository, SupabaseError
from src.services.cache import TTLCache
from src.services.pending import PendingFile, PendingStore
from src.services.storage import StorageService
from src.utils.filetypes import FileCandidate, FileCategory, parse_category
from src.utils.sorting import (
    SortSpec,
    database_order,
    normalise_sort,
    sort_files,
    sort_from_key,
)

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 100

# One screen of a file list, everywhere: a category, a folder and the shared
# list all show eight files at most.
FILES_PER_PAGE = 8

# How long a screen may reuse the library totals and the folder list. Every write
# path clears the cache, so a new upload or a deleted file is visible immediately.
CACHE_TTL_SECONDS = 30.0
HISTORY_SPANS: tuple[str, ...] = ("day", "week", "month", "year", "all")

# The two ways into the shared file list: a typed query or a library category.
LIST_VIEWS: tuple[str, ...] = ("search", "category")

# The fallback of the period picker (when the ``files_periods`` function has not
# been installed yet) reads the newest dates in pages of this size, and never
# scans more than ``_DATES_SCAN`` rows looking for enough distinct periods.
_DATES_BATCH = 200
_DATES_SCAN = 1000


def history_span(value: object) -> str:
    """A requested history period, defaulting to today."""
    span = str(value or "").strip().lower()
    return span if span in HISTORY_SPANS else "day"


def local_zone() -> tzinfo:
    """The timezone the day, week, month and year boundaries follow.

    The bot serves one owner, so «today» is the owner's day: a file sent at 00:30
    belongs to the day that started half an hour ago locally, not to the UTC day
    that still runs. The zone is the deployment's own (``TZ``, or the system's),
    so a server outside UTC groups the uploads the way the user sees them.
    """
    name = os.environ.get("TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown TZ=%r, using the system timezone", name)
    return datetime.now().astimezone().tzinfo or timezone.utc


def history_date(value: object = None, zone: tzinfo | None = None) -> date:
    """The day a period is anchored on, defaulting to today in the local zone.

    The anchor is what the screen shows and what the filter buttons keep: a week
    is the week around it, a month its month, and a day is the day itself.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(zone or local_zone()).date()


def _period_key(day: date, span: str) -> tuple[int, ...]:
    """The bucket one file belongs to, for a period filter."""
    if span == "week":
        year, week, _weekday = day.isocalendar()
        return (year, week)
    if span == "month":
        return (day.year, day.month)
    if span == "year":
        return (day.year,)
    return (day.year, day.month, day.day)


def history_window(
    span: str, anchor: date, zone: tzinfo | None = None
) -> tuple[datetime | None, datetime | None]:
    """The half-open range ``[start, end)`` of a period, as UTC instants.

    A day, a week, a month and a year are the periods *of the local zone*: their
    midnights are converted to UTC before they reach PostgREST, so a file sent at
    00:30 local time counts towards the day it was sent on. The range is computed
    here and sent to the query, so the slice and the count always describe the same
    window without fetching the library first.
    """
    span = history_span(span)
    zone = zone or local_zone()
    if span == "day":
        first, after = anchor, anchor + timedelta(days=1)
    elif span == "week":
        first = anchor - timedelta(days=anchor.weekday())
        after = first + timedelta(days=7)
    elif span == "month":
        first = anchor.replace(day=1)
        after = (first + timedelta(days=32)).replace(day=1)
    elif span == "year":
        first, after = date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1)
    else:
        return None, None
    return (
        datetime.combine(first, time.min, tzinfo=zone).astimezone(timezone.utc),
        datetime.combine(after, time.min, tzinfo=zone).astimezone(timezone.utc),
    )


@dataclass(frozen=True)
class FileListPage:
    """One screen of the shared file list: at most eight files of one period.

    The page is a window on the *whole* result of the view's filter, never a
    window on a fetched prefix: the ordering and the slice are applied by the
    database. ``next_anchor`` is the anchor of the closest *older* period that
    still has files for the same view, found by the database, so the quick jump
    never walks empty periods one by one.
    """

    files: list[StoredFile]
    total: int
    page: int
    pages: int
    view: str
    value: str
    span: str
    sort: str
    anchor: date
    start: datetime | None = None
    end: datetime | None = None
    next_anchor: date | None = None

    @property
    def older_page(self) -> int | None:
        """The next page inside this period, when one is left."""
        return self.page + 1 if self.page < self.pages else None

    @property
    def newer_page(self) -> int | None:
        """The previous page inside this period, when one is left."""
        return self.page - 1 if self.page > 1 else None


def _is_duplicate(exc: SupabaseError) -> bool:
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


class DuplicateFileError(Exception):
    """Raised when the exact same Telegram file is already in the library."""

    def __init__(self, record: StoredFile) -> None:
        super().__init__(f"الملف موجود أصلاً: {record.file_name}")
        self.record = record


class QuarantinedFileError(Exception):
    """The file could not be registered, so it is held in the fallback group."""

    def __init__(self, pending: PendingFile) -> None:
        super().__init__(f"الملف محفوظ في الاحتياط: {pending.file_name}")
        self.pending = pending


@dataclass
class RepairReport:
    """What «🔧 إصلاح» managed to do with the files waiting in the fallback."""

    repaired: int = 0
    duplicates: int = 0
    failed: int = 0
    kept: int = 0
    database_error: str | None = None
    errors: list[str] = field(default_factory=list)


class FileStorageServices:
    """High level operations shared by the upload, browse and search routers."""

    def __init__(
        self,
        settings: Settings,
        repository: FileRepository,
        storage: StorageService | None = None,
        bot: Bot | None = None,
        pending: PendingStore | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._storage = storage
        self._bot = bot
        self._pending = pending
        # Library totals, category totals and the folders of each user only change
        # on a write, so a screen may reuse them for a few seconds.
        self._cache = TTLCache(CACHE_TTL_SECONDS)
        # Running background fingerprints; referenced so the loop keeps them alive.
        self._hash_tasks: set[asyncio.Task] = set()

    def bind_bot(self, bot: Bot) -> None:
        """The bot is needed to read a file's bytes when fingerprinting it."""
        self._bot = bot

    def bind_storage(self, storage: StorageService) -> None:
        self._storage = storage

    @property
    def pending(self) -> PendingStore:
        """The emergency registry, created (and loaded) when it is first needed."""
        if self._pending is None:
            self._pending = PendingStore(self._settings.pending_store_path)
            self._pending.load()
        return self._pending

    @property
    def storage(self) -> StorageService:
        if self._storage is None:
            raise RuntimeError("StorageService is not bound to a bot yet")
        return self._storage

    async def content_hash(self, candidate: FileCandidate) -> str | None:
        """SHA-256 of the file bytes, or ``None`` when they cannot be read.

        Telegram refuses downloads above 20 MB, so the fingerprint is best effort:
        a missing hash never blocks an upload, it only leaves the file without it.
        """
        if self._bot is None or not candidate.file_id:
            return None
        if candidate.file_size and candidate.file_size > self._settings.hash_max_bytes:
            logger.info(
                "%s is larger than the hashing limit; stored without a fingerprint",
                candidate.file_name,
            )
            return None

        buffer = BytesIO()
        try:
            await self._bot.download(candidate.file_id, destination=buffer)
        except (TelegramAPIError, OSError):
            logger.warning(
                "Could not read %s to fingerprint it",
                candidate.file_name,
                exc_info=True,
            )
            return None
        data = buffer.getvalue()
        return hashlib.sha256(data).hexdigest() if data else None

    def _invalidate_catalog(self) -> None:
        """Drop the cached library totals and folder lists after a write."""
        self._cache.clear()

    def _schedule_hash(self, record: StoredFile, candidate: FileCandidate) -> None:
        """Fingerprint a stored file afterwards, never before it is copied.

        Reading the bytes to hash them downloaded the whole file a second time, so
        every upload waited for the size of the file (and a batch waited for all of
        them). The download now runs as its own task: it never delays the reply,
        and a failure only leaves the row without a fingerprint.
        """
        if self._bot is None or not candidate.file_id:
            return
        if candidate.file_size and candidate.file_size > self._settings.hash_max_bytes:
            return
        task = asyncio.create_task(self._store_hash(record, candidate))
        self._hash_tasks.add(task)
        task.add_done_callback(self._hash_tasks.discard)

    async def _store_hash(self, record: StoredFile, candidate: FileCandidate) -> None:
        try:
            fingerprint = await self.content_hash(candidate)
            if fingerprint:
                await self._repository.set_content_hash(record.id, fingerprint)
        except Exception:  # noqa: BLE001 - a fingerprint is best effort
            logger.debug(
                "Could not fingerprint %s in the background",
                candidate.file_name,
                exc_info=True,
            )

    async def save_from_message(
        self, message: Message, candidate: FileCandidate
    ) -> StoredFile:
        """Copy the file into its storage group and persist its metadata.

        The same Telegram file is never stored twice, thanks to the unique
        ``file_unique_id`` column; a different file with the same name is stored
        and told apart by its content fingerprint. When the registration fails the
        file is held in the emergency group instead of the library.
        """
        try:
            existing = await self._repository.find_by_unique_id(
                candidate.file_unique_id or ""
            )
        except SupabaseError as exc:
            # The database is not answering at all: hold the file right away.
            raise await self._quarantine(message, candidate, exc) from exc
        if existing is not None:
            raise DuplicateFileError(existing)

        # The fingerprint is computed *after* the copy (see ``_schedule_hash``):
        # reading the bytes here downloaded the file a second time and made every
        # upload wait for its size. ``file_unique_id`` is what keeps one Telegram
        # file from being stored twice.
        storage = self.storage
        chat_id, message_id = await storage.upload(message, candidate)

        uploader_id = message.from_user.id if message.from_user else None
        try:
            record = await self._repository.create(
                file_id=candidate.file_id,
                file_unique_id=candidate.file_unique_id,
                file_name=candidate.file_name,
                file_type=candidate.mime_type,
                category=candidate.category.value,
                file_size=candidate.file_size,
                content_hash=None,
                user_id=uploader_id,
                chat_id=chat_id,
                message_id=message_id,
            )
        except SupabaseError as exc:
            if not _is_duplicate(exc):
                # Registration failed: the copy is pulled back out of the library
                # and held in the fallback group instead of being lost.
                raise await self._quarantine(
                    message, candidate, exc, library_copy=(chat_id, message_id)
                ) from exc
            # The unique index caught a concurrent copy of the same file: undo the
            # extra copy we just made and report the file already on record.
            await storage.remove(chat_id, message_id)
            duplicate = await self._repository.find_by_unique_id(
                candidate.file_unique_id or ""
            )
            if duplicate is None:
                raise
            raise DuplicateFileError(duplicate) from exc

        logger.info(
            "Stored %s (%s) as %s/%s%s",
            record.file_name,
            record.category.value,
            record.storage_chat_id,
            record.storage_message_id,
            f" hash={record.content_hash[:12]}" if record.content_hash else "",
        )
        self._invalidate_catalog()
        self._schedule_hash(record, candidate)
        return record

    async def _quarantine(
        self,
        message: Message,
        candidate: FileCandidate,
        exc: SupabaseError,
        library_copy: tuple[int, int] | None = None,
    ) -> Exception:
        """Hold the file in the fallback group and remember it for the repair.

        Returns the exception the caller raises: a ``QuarantinedFileError`` once the
        copy is safely held, or the original database error when even the fallback
        copy fails (nothing was saved, so the upload reports a plain failure).
        """
        storage = self.storage
        if library_copy is not None:
            await storage.remove(*library_copy)

        reason = " ".join(str(exc).split())[:200] or type(exc).__name__
        try:
            chat_id, message_id = await storage.hold(message)
        except Exception:  # noqa: BLE001 - report the database failure as it is
            logger.exception(
                "Could not hold %s in the fallback group", candidate.file_name
            )
            return exc

        entry = PendingFile(
            chat_id=chat_id,
            message_id=message_id,
            file_name=candidate.file_name,
            mime_type=candidate.mime_type,
            size_bytes=candidate.file_size or 0,
            category=candidate.category,
            file_id=candidate.file_id or "",
            file_unique_id=candidate.file_unique_id,
            uploader_id=message.from_user.id if message.from_user else None,
            reason=reason,
        )
        await self.pending.add(entry)
        logger.warning(
            "Held %s in the fallback group as %s/%s: %s",
            candidate.file_name,
            chat_id,
            message_id,
            reason,
        )
        return QuarantinedFileError(entry)

    async def repair_pending(self) -> RepairReport:
        """Register every held file and move it into its real storage group.

        The database is probed once before any copy is made, and the walk stops as
        soon as it fails again: no file is ever moved without a row behind it.
        """
        entries = await self.pending.all()
        report = RepairReport()
        if not entries:
            return report

        try:
            await self._repository.count()
        except SupabaseError as exc:
            report.database_error = " ".join(str(exc).split())[:200]
            return report

        groups = self._settings.storage_chats()
        for entry in entries:
            target = groups.get(entry.category)
            if target is None:
                report.kept += 1
                report.errors.append(
                    f"{entry.file_name}: لا توجد مجموعة تخزين لهذه الفئة"
                )
                continue

            try:
                chat_id, message_id = await self.storage.move(
                    entry.chat_id, entry.message_id, int(target)
                )
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
                logger.warning("Could not move %s: %s", entry.file_name, exc)
                report.failed += 1
                report.errors.append(f"{entry.file_name}: {type(exc).__name__}")
                continue

            try:
                await self._repository.create(
                    file_id=entry.file_id,
                    file_unique_id=entry.file_unique_id,
                    file_name=entry.file_name,
                    file_type=entry.mime_type,
                    category=entry.category.value,
                    file_size=entry.size_bytes,
                    content_hash=None,
                    user_id=entry.uploader_id,
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except SupabaseError as exc:
                # Never leave an unregistered copy in the library.
                await self.storage.remove(chat_id, message_id)
                if _is_duplicate(exc):
                    await self.storage.remove(entry.chat_id, entry.message_id)
                    await self.pending.remove(entry.chat_id, entry.message_id)
                    report.duplicates += 1
                    continue
                report.database_error = " ".join(str(exc).split())[:200]
                report.errors.append(f"{entry.file_name}: {report.database_error}")
                break

            await self.storage.remove(entry.chat_id, entry.message_id)
            await self.pending.remove(entry.chat_id, entry.message_id)
            report.repaired += 1

        logger.info(
            "Repair finished: %s repaired, %s duplicates, %s failed, %s kept",
            report.repaired,
            report.duplicates,
            report.failed,
            report.kept,
        )
        if report.repaired:
            self._invalidate_catalog()
        return report

    async def get(self, file_id: str) -> StoredFile | None:
        return await self._repository.get(file_id)

    async def search(
        self,
        query: str | None = None,
        *,
        category: FileCategory | str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[StoredFile]:
        parsed_category = (
            parse_category(category) if category is not None else None
        )
        results, _total = await self._repository.search(
            query=query, category=parsed_category, limit=limit
        )
        return results

    async def category_page(
        self,
        category: FileCategory | str,
        sort: SortSpec | None = None,
        *,
        page: int = 1,
        per_page: int = FILES_PER_PAGE,
    ) -> tuple[list[StoredFile], int, int]:
        """One page of a category: its files, the total, and the page shown.

        Both the ordering and the slice are done by PostgREST, so every page is a
        window on the *whole* category instead of a window on a fetched prefix: the
        last file is reachable and the sort covers all of them.
        """
        spec = sort or SortSpec()
        parsed = parse_category(category)
        order = database_order(spec)
        per_page = max(1, per_page)
        page = max(1, page)

        async def fetch(target: int) -> tuple[list[StoredFile], int]:
            return await self._repository.search(
                category=parsed,
                limit=per_page,
                offset=(target - 1) * per_page,
                order=order,
            )

        files, total = await fetch(page)
        last_page = max(1, -(-total // per_page))
        if not files and page > last_page:
            # The library shrank (or the page came from an older screen): show the
            # last page that still has files instead of an empty list.
            page = last_page
            files, total = await fetch(page)
        return files, total, page

    async def folder_page(
        self,
        folder_id: str,
        sort: SortSpec | None = None,
        *,
        page: int = 1,
        per_page: int = FILES_PER_PAGE,
    ) -> tuple[list[StoredFile], int, int]:
        """One page of a folder: its eight files, the total and the page shown.

        A folder is sorted in memory (names are compared with the Arabic
        diacritics folded), so the slice is taken from the whole sorted list and
        the screen never shows more than ``per_page`` files.
        """
        files = await self.folder_files(folder_id, sort)
        per_page = max(1, per_page)
        pages = max(1, -(-len(files) // per_page))
        page = min(max(1, page), pages)
        start = (page - 1) * per_page
        return files[start : start + per_page], len(files), page

    async def file_list_page(
        self,
        view: str = "search",
        value: str = "",
        *,
        span: str = "all",
        sort: str = "newest",
        anchor: object = None,
        page: int = 1,
        per_page: int = FILES_PER_PAGE,
    ) -> FileListPage:
        """One page of the shared file list, for either entry point.

        ``view`` says where the list came from: ``category`` filters the rows by
        their category, ``search`` by a name fragment. Both then go through the
        same query — the period becomes the ``WHERE created_at`` window, the sort
        the ``ORDER BY``, and the page the ``LIMIT``/``OFFSET`` slice — so the
        ordering and the filter always cover the whole list before pagination.

        The closest older period holding a file of the same view is looked up for
        the quick jump («Next Day/Week/Month/Year ⏭️»), so the jump works from any
        page of the period and never walks the empty ones.
        """
        view = str(view or "search")
        view = view if view in LIST_VIEWS else "search"
        value = " ".join(str(value or "").split())
        span = span if str(span or "") in HISTORY_SPANS else "all"
        sort = normalise_sort(sort)
        anchor = history_date(anchor)
        start, end = history_window(span, anchor)
        per_page = max(1, per_page)
        page = max(1, page)

        # The view's own WHERE clause, shared by the page and the period jump.
        query = value if view == "search" and value else None
        category = parse_category(value) if view == "category" and value else None
        order = database_order(sort_from_key(sort))

        async def fetch(target: int) -> tuple[list[StoredFile], int]:
            return await self._repository.search(
                query=query,
                category=category,
                created_after=start,
                created_before=end,
                limit=per_page,
                offset=(target - 1) * per_page,
                order=order,
            )

        async def fetch_page() -> tuple[list[StoredFile], int]:
            """The page, falling back to the last one when the library shrank."""
            nonlocal page
            found, count = await fetch(page)
            pages_total = max(1, -(-count // per_page))
            if not found and page > 1:
                # The page came from an older screen (or a file was deleted): show
                # the last page of the period instead of an empty one.
                page = pages_total
                found, count = await fetch(page)
            return found, count

        # The quick jump is offered on every page of a period, not only on its
        # last one, and it does not depend on the page itself: both queries run
        # together instead of one after the other.
        if start is None:
            files, total = await fetch_page()
            next_anchor: date | None = None
        else:
            (files, total), next_anchor = await asyncio.gather(
                fetch_page(),
                self._nearest_older_day(start, query=query, category=category),
            )
        pages = max(1, -(-total // per_page))

        return FileListPage(
            files=files,
            total=total,
            page=page,
            pages=pages,
            view=view,
            value=value,
            span=span,
            sort=sort,
            anchor=anchor,
            start=start,
            end=end,
            next_anchor=next_anchor,
        )

    async def _nearest_older_day(
        self,
        moment: datetime,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
    ) -> date | None:
        """The day of the newest file stored before ``moment``.

        One row is enough: the period holding that file is the closest older period
        with a match, so a jump never walks empty periods one by one. The view's own
        filter is kept, so a jump never lands on a period without a match.
        """
        files, _total = await self._repository.search(
            query=query,
            category=category,
            created_before=moment,
            limit=1,
            order="created_at.desc",
        )
        return files[0].created_at.astimezone(local_zone()).date() if files else None

    async def available_periods(
        self,
        view: str = "search",
        value: str = "",
        *,
        span: str = "day",
        limit: int = 24,
    ) -> list[tuple[date, int]]:
        """The periods that hold a file, with how many, newest first.

        This feeds the picker a filter opens on its second tap. The grouping and
        the counting are done by Postgres in a single request (``files_periods``:
        ``date(created_at)`` and ``count(*)`` grouped by it, or by its
        week/month/year truncation), so the picker neither reads the matching rows
        nor asks the count of each period separately. The view's own filter is
        applied, so the picker never offers an empty period.
        """
        view = str(view or "search")
        view = view if view in LIST_VIEWS else "search"
        value = " ".join(str(value or "").split())
        span = history_span(span)
        if span == "all":
            return []

        query = value if view == "search" and value else None
        category = parse_category(value) if view == "category" and value else None

        try:
            return await self._repository.period_counts(
                span=span, query=query, category=category, limit=limit
            )
        except SupabaseError:
            # The SQL function is not installed yet: fold the dates here instead, so
            # the picker keeps working (its counts then cover the scanned window).
            logger.warning(
                "files_periods is unavailable; folding the dates in the bot",
                exc_info=True,
            )
        return await self._folded_period_counts(query, category, span, limit)

    async def _folded_period_counts(
        self,
        query: str | None,
        category: FileCategory | None,
        span: str,
        limit: int,
    ) -> list[tuple[date, int]]:
        """Periods and their counts, folded from the newest ``created_at`` values."""
        seen: dict[tuple[int, ...], list] = {}
        offset = 0
        while len(seen) < limit and offset < _DATES_SCAN:
            dates = await self._repository.created_dates(
                query=query, category=category, limit=_DATES_BATCH, offset=offset
            )
            if not dates:
                break
            for moment in dates:
                day = moment.astimezone(local_zone()).date()
                start, _end = history_window(span, day)
                anchor = start.astimezone(local_zone()).date() if start else day
                bucket = seen.setdefault(_period_key(day, span), [anchor, 0])
                bucket[1] += 1
            offset += _DATES_BATCH
        return [(anchor, count) for anchor, count in list(seen.values())[:limit]]

    async def category_counts(self) -> dict[FileCategory, int]:
        return await self._repository.category_counts()

    async def category_totals(self) -> dict[FileCategory, tuple[int, int]]:
        """Files and bytes per category (one round trip, thanks to the view)."""
        return await self._cache.get(
            "category_totals", self._repository.category_totals
        )

    async def stats(self) -> dict[str, Any]:
        """The library totals, reused for a moment by the next screen."""
        return await self._cache.get("stats", self._load_stats)

    async def _load_stats(self) -> dict[str, Any]:
        """The whole library summary from one request.

        It used to be six per-category counts plus a separate byte counter, which
        is what made the main menu wait for two network round trips per tap.
        """
        totals = await self._repository.category_totals()
        counts = {category: count for category, (count, _size) in totals.items()}
        total_files = sum(counts.values())
        total_bytes = sum(size for _count, size in totals.values())
        if not total_bytes and total_files:
            # The bytes view is not deployed yet: fall back to the live counter.
            try:
                total_bytes = await self._repository.total_size() or 0
            except SupabaseError:
                total_bytes = 0
        return {
            "total": total_files,
            "total_bytes": total_bytes,
            "categories": counts,
        }

    async def deliver(self, record: StoredFile, target_chat_id: int) -> int:
        """Send the stored copy back to a user and return the new message id."""
        _chat_id, message_id = await self.storage.deliver(
            record.storage_chat_id, record.storage_message_id, target_chat_id
        )
        return message_id

    async def rename(self, file_id: str, name: str) -> StoredFile | None:
        """Rename a file in the library (the Telegram copy keeps its own name)."""
        return await self._repository.rename(file_id, name)

    async def folders(self, user_id: int) -> list[Folder]:
        """Folders of one user, always in case-insensitive alphabetical order."""
        return await self._cache.get(
            f"folders:{int(user_id)}",
            lambda: self._repository.list_folders(user_id),
        )

    async def search_folders(
        self, query: str, user_id: int, limit: int = FILES_PER_PAGE
    ) -> tuple[list[Folder], int]:
        """The folders whose name contains ``query``, and how many there are.

        A folder is a label, not a row of the file table, so its search runs here on
        the user's (already alphabetical) list. The screen shows at most ``limit``
        of them, keeping the eight-item rule of every list in the bot.
        """
        needle = " ".join(str(query or "").split()).casefold()
        if not needle:
            return [], 0
        matched = [
            folder
            for folder in await self.folders(user_id)
            if needle in folder.name.casefold()
        ]
        return matched[: max(1, limit)], len(matched)

    async def folder(self, folder_id: str, user_id: int) -> Folder | None:
        return await self._repository.get_folder(folder_id, user_id)

    async def favorite_folders(self, user_id: int) -> list[Folder]:
        """The shortcuts of the library screen, in their manual order.

        A favourite without an explicit position keeps the alphabetical fallback,
        so folders starred before the ordering existed still show up.
        """
        folders = [
            folder for folder in await self.folders(user_id) if folder.favorite
        ]
        return sorted(folders, key=lambda folder: folder.favorite_sort_key)

    async def set_favorite(
        self, folder_id: str, user_id: int, favorite: bool
    ) -> Folder | None:
        """Star a folder at the end of the favourites, or unstar it."""
        order: int | None = None
        if favorite:
            favorites = await self.favorite_folders(user_id)
            order = max(
                (folder.favorite_order or 0 for folder in favorites), default=-1
            ) + 1

        await self._repository.set_folder_favorite(
            folder_id, user_id, favorite, order
        )
        self._cache.invalidate_prefix("folders:")
        return await self._repository.get_folder(folder_id, user_id)

    async def move_favorite(
        self, folder_id: str, user_id: int, step: int
    ) -> Folder | None:
        """Move a favourite one place up (``step=-1``) or down (``step=1``)."""
        favorites = await self.favorite_folders(user_id)
        ids = [folder.id for folder in favorites]
        if folder_id not in ids:
            return None

        index = ids.index(folder_id)
        target = max(0, min(index + step, len(favorites) - 1))
        if target == index:
            return await self._repository.get_folder(folder_id, user_id)

        favorites[index], favorites[target] = favorites[target], favorites[index]
        for position, folder in enumerate(favorites):
            await self._repository.set_folder_order(folder.id, user_id, position)
        self._cache.invalidate_prefix("folders:")
        return await self._repository.get_folder(folder_id, user_id)

    async def create_folder(self, user_id: int, name: str) -> Folder:
        folder = await self._repository.create_folder(user_id, name)
        self._cache.invalidate_prefix("folders:")
        return folder

    async def folder_files(
        self, folder_id: str, sort: SortSpec | None = None
    ) -> list[StoredFile]:
        """Files of one folder, ordered by the requested sort."""
        files = await self._repository.files_in_folder(folder_id)
        return sort_files(files, sort or SortSpec())

    async def delete_folder(self, folder_id: str, user_id: int) -> bool:
        """Remove the folder label only: its files are kept in the library."""
        deleted = await self._repository.delete_folder(folder_id, user_id)
        self._cache.invalidate_prefix("folders:")
        return deleted

    async def add_to_folder(self, folder_id: str, file_id: str) -> None:
        await self._repository.add_to_folder(folder_id, file_id)
        self._cache.invalidate_prefix("folders:")

    async def file_folders(self, file_id: str) -> list[str]:
        return await self._repository.folders_of_file(file_id)

    async def delete(self, file_id: str) -> bool:
        """Delete the stored Telegram copy and its metadata row."""
        record = await self._repository.delete(file_id)
        if record is None:
            return False
        await self.storage.remove(record.storage_chat_id, record.storage_message_id)
        self._invalidate_catalog()
        return True


__all__ = [
    "DuplicateFileError",
    "FileStorageServices",
    "QuarantinedFileError",
    "RepairReport",
]

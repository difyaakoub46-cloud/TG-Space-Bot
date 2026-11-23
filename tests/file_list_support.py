"""The doubles and the builders the two file-list test modules share.

Both modules drive the real :func:`src.bot.file_list.screen` and the real
:class:`FileStorageServices`; only PostgREST is replaced by an in-memory
repository that applies the same WHERE / ORDER BY / LIMIT / OFFSET the database
does. That repository, the service built on it, the screen builders and the
router doubles live here once instead of in both test modules.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.services.files import FileStorageServices, history_window
from src.utils.filetypes import FileCategory

UTC = timezone.utc
TODAY = date(2026, 11, 18)
OLDER_DAY = date(2026, 11, 15)
WEEK_DAY = date(2026, 11, 16)


def moment(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def record(
    index: int,
    created_at: datetime,
    name: str | None = None,
    size: int = 1024,
    category: FileCategory = FileCategory.document,
) -> StoredFile:
    return StoredFile(
        id=f"id-{index}",
        file_id=f"file-{index}",
        file_unique_id=f"unique-{index}",
        file_name=name or f"file-{index}.pdf",
        category=category,
        mime_type="application/pdf",
        size_bytes=size,
        uploader_id=1,
        storage_chat_id=-100,
        storage_message_id=index,
        created_at=created_at,
    )


def _has_type(file: StoredFile, link_type: str | None) -> bool:
    """The fake's ``WHERE link_type``: files only unless a section is asked for."""
    if link_type is None:
        return True
    if link_type == "none":
        return file.link_type is None
    return file.link_type == link_type


class ListRepository:
    """In-memory PostgREST: the view filter, the window, the order and the slice."""

    def __init__(
        self, files: list[StoredFile], folders: list[Folder] | None = None
    ) -> None:
        self.files = list(files)
        self._folders = list(folders or [])
        self.calls: list[dict[str, object]] = []

    async def list_folders(self, user_id: int) -> list[Folder]:
        return list(self._folders)

    async def files_in_folder(self, folder_id: str) -> list[StoredFile]:
        return list(self.files)

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        link_type: str | None = "none",
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str = "created_at.desc",
    ) -> tuple[list[StoredFile], int]:
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
            and _has_type(file, link_type)
            and (created_after is None or file.created_at >= created_after)
            and (created_before is None or file.created_at < created_before)
        ]
        column, _, direction = order.partition(".")
        key = {
            "created_at": lambda file: file.created_at,
            "file_size": lambda file: file.size_bytes,
            "file_name": lambda file: file.file_name,
        }[column]
        rows.sort(key=key, reverse=direction == "desc")
        self.calls.append(
            {
                "query": query,
                "category": category,
                "link_type": link_type,
                "limit": limit,
                "offset": offset,
                "order": order,
            }
        )
        return rows[offset : offset + limit], len(rows)

    async def created_dates(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        link_type: str | None = "none",
        limit: int = 200,
        offset: int = 0,
    ) -> list[datetime]:
        """The dates only, newest first: what the period picker folds."""
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
            and _has_type(file, link_type)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=True)
        return [file.created_at for file in rows[offset : offset + limit]]

    async def period_counts(
        self,
        *,
        span: str = "day",
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 24,
        zone_offset: int = 0,
    ) -> list[tuple[date, int]]:
        """Periods and their counts, as the grouped SQL function returns them."""
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=True)
        # ``files_periods`` groups in the caller's offset, so the fake does too.
        zone = timezone(timedelta(minutes=zone_offset))
        buckets: dict[date, int] = {}
        for file in rows:
            day = file.created_at.astimezone(zone).date()
            start, _end = history_window(span, day, zone)
            anchor = start.astimezone(zone).date() if start is not None else day
            buckets[anchor] = buckets.get(anchor, 0) + 1
        return sorted(buckets.items(), key=lambda item: item[0], reverse=True)[:limit]


def service(
    files: list[StoredFile], folders: list[Folder] | None = None
) -> FileStorageServices:
    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    return FileStorageServices(settings, ListRepository(files, folders))


def screen(view: str = "category", value: str = "document", **extra) -> dict:
    base = {
        "k": "list",
        "v": view,
        "val": value,
        "f": "all",
        "srt": "newest",
        "p": 1,
        "d": TODAY.isoformat(),
        "u": 1,
    }
    base.update(extra)
    return base


def documents(count: int, day: date = TODAY) -> list[StoredFile]:
    """``count`` documents uploaded ``count`` minutes apart (newest first)."""
    return [
        record(index, moment(day, 20 - index // 60, 59 - index % 60))
        for index in range(count)
    ]


class Callback:
    """The tapped callback a router is handed."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=7)
        self.message = None


class State:
    """A state whose only data is the screen key it shows."""

    def __init__(self, screen_key: dict[str, object]) -> None:
        self.data = {"screen_key": dict(screen_key)}

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **values: object) -> None:
        self.data.update(values)


async def navigate(
    monkeypatch, module, target_data, stored_screen, services=None
) -> dict[str, object]:
    """Drive a router with a packed callback, capturing the screen it asks for.

    ``services`` is handed to the router as it is: a tap that resolves a period
    anchor (a filter press) needs the real service behind the fake repository.
    """
    captured: dict[str, object] = {}

    async def fake_navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)
        captured["notice"] = kwargs.get("notice")
        captured["markup"] = kwargs.get("markup")

    monkeypatch.setattr(module.navigation, "navigate_callback", fake_navigate)
    await module.file_list_callback(
        Callback(target_data),
        services=services,
        state=State(stored_screen),
        bot=None,
    )
    return captured

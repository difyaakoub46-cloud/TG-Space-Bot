"""Shared fixtures for the emergency-hold tests.

The tests exercise the real :class:`FileStorageServices` and the real
:class:`StorageService`; only Telegram and PostgREST are replaced by small fakes
that record what the bot would have done. That keeps the tests honest about the
order of the calls (copy, delete, hold, move) without a network.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow `pytest` from any working directory
    sys.path.insert(0, str(ROOT))

from src.bot import library_views  # noqa: E402
from src.core.config import Settings  # noqa: E402
from src.services.files import FileStorageServices  # noqa: E402
from src.services.pending import PendingStore  # noqa: E402
from src.services.storage import StorageService  # noqa: E402
from src.utils.filetypes import FileCategory  # noqa: E402

DOCUMENTS_GROUP = -100222
IMAGES_GROUP = -100111
FALLBACK_GROUP = -100999

BASE_SETTINGS: dict[str, Any] = {
    "bot_token": "123:test-token",
    "supabase_url": "https://example.supabase.co",
    "supabase_key": "sb_secret_test",
    "storage_chat_document": DOCUMENTS_GROUP,
    "storage_chat_image": IMAGES_GROUP,
    "storage_chat_fallback": FALLBACK_GROUP,
}


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings with a temporary registry path and test-only chat ids."""
    values = dict(BASE_SETTINGS)
    values["pending_store_path"] = str(tmp_path / "pending_files.json")
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeBot:
    """The two Telegram methods the storage backend uses, with a call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._next_id = 900

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int):
        self.calls.append(("copy", chat_id, from_chat_id, message_id))
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def delete_message(self, chat_id: int, message_id: int):
        self.calls.append(("delete", chat_id, message_id))
        return True


class FakeRepository:
    """Scriptable replacement for :class:`FileRepository`.

    Each hook is either ``None`` or an exception instance to raise, which is how
    the tests simulate a Supabase outage at one specific step.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.existing: Any = None
        self.fail_find: Exception | None = None
        self.fail_create: Exception | None = None
        self.fail_count: Exception | None = None
        self._message_ids = iter(range(1, 1000))

    async def find_by_unique_id(self, file_unique_id: str):
        if self.fail_find is not None:
            raise self.fail_find
        return self.existing

    async def create(self, **fields: Any):
        if self.fail_create is not None:
            raise self.fail_create
        self.rows.append(dict(fields))
        return SimpleNamespace(
            id=str(len(self.rows)),
            file_name=fields.get("file_name", "file"),
            category=SimpleNamespace(value=str(fields.get("category", "other"))),
            storage_chat_id=int(fields.get("chat_id") or 0),
            storage_message_id=int(fields.get("message_id") or 0),
            content_hash=fields.get("content_hash"),
        )

    async def count(self, **_: Any) -> int:
        if self.fail_count is not None:
            raise self.fail_count
        return len(self.rows)


def make_message(chat_id: int = 1, message_id: int = 2, user_id: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=message_id,
        from_user=SimpleNamespace(id=user_id),
    )


def make_candidate(
    name: str = "report.pdf",
    category: FileCategory = FileCategory.document,
    mime: str = "application/pdf",
    size: int = 10,
    unique_id: str = "unique-1",
) -> SimpleNamespace:
    """A :class:`FileCandidate`-shaped object; only the fields used are set."""
    return SimpleNamespace(
        file_name=name,
        mime_type=mime,
        category=category,
        file_id="file-id",
        file_unique_id=unique_id,
        file_size=size,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def store(settings: Settings) -> PendingStore:
    registry = PendingStore(settings.pending_store_path)
    registry.load()
    return registry


@pytest.fixture
def service(
    settings: Settings,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> FileStorageServices:
    """The real service stack, with Telegram and the database replaced."""
    storage = StorageService(bot, settings)
    return FileStorageServices(
        settings, repository, storage=storage, bot=None, pending=store
    )


# The list header writes «📅 Today: …» by comparing the day on screen with the real
# clock, so the file-list tests used to pass only while the machine's date happened
# to be the day their dates describe. That scenario is one fixed week (18 November
# 2026), and moving it with the real day instead would break the day, week and month
# assertions of the period picker every Monday and around the end of a month, so the
# clock is frozen to that week here.
FROZEN_NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make «today» a fixed day for every test that reads the clock."""

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_NOW if tz is not None else FROZEN_NOW.replace(tzinfo=None)

    monkeypatch.setattr(library_views, "datetime", _FixedDatetime)

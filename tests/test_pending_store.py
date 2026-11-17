"""The emergency registry: the JSON file that makes «إصلاح» possible."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.pending import PendingFile, PendingStore
from src.utils.filetypes import FileCategory


def entry(
    message_id: int = 501,
    category: FileCategory = FileCategory.document,
    reason: str = "Supabase request failed: timed out",
) -> PendingFile:
    return PendingFile(
        chat_id=-100999,
        message_id=message_id,
        file_name=f"file-{message_id}.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        category=category,
        file_id="file-id",
        file_unique_id=f"unique-{message_id}",
        uploader_id=7,
        reason=reason,
    )


def test_registry_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    first = PendingStore(path)
    first.load()
    first._entries.append(entry())  # noqa: SLF001 - simulate a write through add()
    first._write()  # noqa: SLF001

    second = PendingStore(path)
    second.load()

    assert second.total == 1
    latest = second.latest()
    assert latest is not None
    assert (latest.chat_id, latest.message_id) == (-100999, 501)
    assert latest.category is FileCategory.document
    assert latest.file_unique_id == "unique-501"


async def test_add_keeps_one_entry_per_stored_message(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    store.load()

    await store.add(entry(reason="first reason"))
    await store.add(entry(reason="second reason"))

    assert store.total == 1
    assert store.latest() is not None
    assert store.latest().reason == "second reason"


async def test_remove_reports_whether_it_removed_anything(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    store.load()
    await store.add(entry())

    assert await store.remove(-100999, 501) is True
    assert await store.remove(-100999, 501) is False
    assert store.total == 0


async def test_stats_and_latest_group_by_category(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    store.load()
    await store.add(entry(1, FileCategory.document))
    await store.add(entry(2, FileCategory.document))
    await store.add(entry(3, FileCategory.image))

    assert store.stats() == {FileCategory.document: 2, FileCategory.image: 1}
    assert store.latest() is not None
    assert store.latest().category is FileCategory.image


async def test_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "pending.json"
    store = PendingStore(path)
    store.load()
    await store.add(entry())

    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["files"][0]["category"] == "document"


def test_a_corrupt_registry_starts_empty_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    path.write_text("{not json", encoding="utf-8")
    store = PendingStore(path)

    store.load()  # must not raise

    assert store.total == 0


def test_invalid_entries_are_skipped_and_valid_ones_kept(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    path.write_text(
        json.dumps(
            {
                "files": [
                    {"chat_id": -100999, "message_id": 1},  # valid enough: defaults fill in
                    {"chat_id": "not-a-number", "message_id": 2},  # invalid
                    "not-even-an-object",
                ]
            }
        ),
        encoding="utf-8",
    )
    store = PendingStore(path)

    store.load()

    assert store.total == 1
    assert store.latest() is not None
    assert store.latest().chat_id == -100999


def test_a_missing_registry_is_not_an_error(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "never-written.json")

    store.load()

    assert store.total == 0
    assert store.latest() is None


@pytest.mark.parametrize("category", list(FileCategory))
def test_every_category_round_trips_through_the_registry(
    tmp_path: Path, category: FileCategory
) -> None:
    path = tmp_path / "pending.json"
    store = PendingStore(path)
    store.load()
    store._entries.append(entry(10, category))  # noqa: SLF001
    store._write()  # noqa: SLF001

    reloaded = PendingStore(path)
    reloaded.load()

    assert reloaded.latest() is not None
    assert reloaded.latest().category is category

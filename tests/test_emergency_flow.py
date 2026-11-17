"""The emergency path: a file whose registration fails is held, then repaired.

Every test states the call order the bot must produce, because the whole point of
this path is that a file is never left in the library without its row, and a held
copy is never deleted before its row exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import (
    DOCUMENTS_GROUP,
    FALLBACK_GROUP,
    FakeBot,
    FakeRepository,
    make_candidate,
    make_message,
    make_settings,
)
from src.db.repository import SupabaseError
from src.services.files import (
    DuplicateFileError,
    FileStorageServices,
    QuarantinedFileError,
)
from src.services.pending import PendingFile, PendingStore
from src.services.storage import StorageService
from src.utils.filetypes import FileCategory


def build_service(
    settings, repository: FakeRepository, bot: FakeBot, store: PendingStore
) -> FileStorageServices:
    return FileStorageServices(
        settings, repository, storage=StorageService(bot, settings), bot=None, pending=store
    )


def held_entry(message_id: int = 501, category: FileCategory = FileCategory.document) -> PendingFile:
    return PendingFile(
        chat_id=FALLBACK_GROUP,
        message_id=message_id,
        file_name=f"held-{message_id}.pdf",
        mime_type="application/pdf",
        size_bytes=99,
        category=category,
        file_id="held-file-id",
        file_unique_id=f"held-unique-{message_id}",
        uploader_id=7,
        reason="GET https://example.supabase.co/rest/v1/files failed with 503",
    )


# --------------------------------------------------------------------------- #
# Holding a file
# --------------------------------------------------------------------------- #
async def test_a_dead_database_holds_the_file_before_any_library_copy(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    """The duplicate check fails first, so nothing is ever copied into the library."""
    repository.fail_find = SupabaseError("GET files failed with 503: upstream unavailable")

    with pytest.raises(QuarantinedFileError) as caught:
        await service.save_from_message(make_message(), make_candidate())

    assert bot.calls == [("copy", FALLBACK_GROUP, 1, 2)], "exactly one copy, into the fallback group"
    assert caught.value.pending.file_name == "report.pdf"
    assert caught.value.pending.category is FileCategory.document
    assert "503" in caught.value.pending.reason
    assert store.total == 1
    assert repository.rows == [], "no row was written, so nothing entered the library"


async def test_a_failed_row_pulls_the_library_copy_back_then_holds_it(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    repository.fail_create = SupabaseError("INSERT files failed with 500: boom")

    with pytest.raises(QuarantinedFileError):
        await service.save_from_message(make_message(), make_candidate())

    assert bot.calls == [
        ("copy", DOCUMENTS_GROUP, 1, 2),  # the copy the library would have kept
        ("delete", DOCUMENTS_GROUP, 901),  # ... pulled back out
        ("copy", FALLBACK_GROUP, 1, 2),  # ... and held instead
    ]
    assert store.total == 1
    assert repository.rows == []


async def test_a_missing_fallback_group_reports_the_database_error_and_keeps_nothing(
    tmp_path: Path,
    repository: FakeRepository,
    bot: FakeBot,
) -> None:
    """Without TG_STORAGE_CHAT_FALLBACK the upload fails as before, but cleanly."""
    settings = make_settings(tmp_path, storage_chat_fallback=None)
    store = PendingStore(settings.pending_store_path)
    store.load()
    service = build_service(settings, repository, bot, store)
    repository.fail_create = SupabaseError("INSERT files failed with 500: boom")

    with pytest.raises(SupabaseError) as caught:
        await service.save_from_message(make_message(), make_candidate())

    assert "boom" in str(caught.value)
    assert store.total == 0
    assert bot.calls == [
        ("copy", DOCUMENTS_GROUP, 1, 2),
        ("delete", DOCUMENTS_GROUP, 901),
    ], "the library copy is still removed, so no unregistered file stays in the library"


async def test_a_known_file_is_never_copied_twice(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    repository.existing = SimpleNamespace(id="1", file_name="report.pdf")

    with pytest.raises(DuplicateFileError):
        await service.save_from_message(make_message(), make_candidate())

    assert bot.calls == []
    assert store.total == 0


async def test_the_registry_file_records_what_the_repair_needs(
    service: FileStorageServices, repository: FakeRepository, tmp_path: Path
) -> None:
    repository.fail_find = SupabaseError("timeout")

    with pytest.raises(QuarantinedFileError):
        await service.save_from_message(make_message(), make_candidate())

    payload = json.loads(Path(service.pending.path).read_text(encoding="utf-8"))
    saved = payload["files"][0]
    assert (saved["chat_id"], saved["message_id"]) == (FALLBACK_GROUP, 901)
    assert saved["category"] == "document"
    assert saved["uploader_id"] == 3
    assert saved["reason"]


# --------------------------------------------------------------------------- #
# Repairing the held files
# --------------------------------------------------------------------------- #
async def test_repair_does_nothing_while_the_database_is_down(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    await store.add(held_entry())
    repository.fail_count = SupabaseError("GET files failed with 503")

    report = await service.repair_pending()

    assert report.repaired == 0
    assert report.database_error is not None and "503" in report.database_error
    assert bot.calls == [], "no file may move while the database cannot record it"
    assert store.total == 1, "the file stays held and repairable"


async def test_repair_registers_the_row_then_removes_the_held_copy(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    await store.add(held_entry())

    report = await service.repair_pending()

    assert report.repaired == 1
    assert report.failed == report.kept == report.duplicates == 0
    assert bot.calls == [
        ("copy", DOCUMENTS_GROUP, FALLBACK_GROUP, 501),  # into the real category group
        ("delete", FALLBACK_GROUP, 501),  # the held copy, only after the row
    ]
    row = repository.rows[0]
    assert row["chat_id"] == DOCUMENTS_GROUP
    assert row["message_id"] == 901
    assert row["category"] == "document"
    assert row["file_name"] == "held-501.pdf"
    assert store.total == 0
    assert json.loads(Path(service.pending.path).read_text(encoding="utf-8"))["files"] == []


async def test_repair_keeps_a_file_whose_category_has_no_group(
    tmp_path: Path, repository: FakeRepository, bot: FakeBot
) -> None:
    settings = make_settings(tmp_path, storage_chat_document=None)
    store = PendingStore(settings.pending_store_path)
    store.load()
    service = build_service(settings, repository, bot, store)
    await store.add(held_entry())

    report = await service.repair_pending()

    assert report.repaired == 0
    assert report.kept == 1
    assert report.errors and "held-501.pdf" in report.errors[0]
    assert bot.calls == []
    assert store.total == 1


async def test_repair_drops_a_file_that_was_registered_meanwhile(
    service: FileStorageServices,
    repository: FakeRepository,
    bot: FakeBot,
    store: PendingStore,
) -> None:
    await store.add(held_entry())
    repository.fail_create = SupabaseError(
        "POST files failed with 409: duplicate key value violates unique constraint"
    )

    report = await service.repair_pending()

    assert report.duplicates == 1
    assert report.repaired == 0
    assert bot.calls == [
        ("copy", DOCUMENTS_GROUP, FALLBACK_GROUP, 501),
        ("delete", DOCUMENTS_GROUP, 901),  # never leave an unregistered library copy
        ("delete", FALLBACK_GROUP, 501),  # the duplicate's held copy is not needed
    ]
    assert store.total == 0


async def test_repair_stops_at_the_first_real_database_error_and_keeps_the_rest(
    tmp_path: Path, bot: FakeBot
) -> None:
    class FailsOnSecondRow(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls = 0

        async def create(self, **fields):
            self.create_calls += 1
            if self.create_calls == 2:
                raise SupabaseError("INSERT files failed with 500: down again")
            return await super().create(**fields)

    settings = make_settings(tmp_path)
    store = PendingStore(settings.pending_store_path)
    store.load()
    repository = FailsOnSecondRow()
    await store.add(held_entry(501))
    await store.add(held_entry(502))
    service = build_service(settings, repository, bot, store)

    report = await service.repair_pending()

    assert report.repaired == 1
    assert report.database_error is not None and "down again" in report.database_error
    assert store.total == 1, "the failed entry and the untouched one stay held"
    assert store.latest() is not None and store.latest().message_id == 502
    assert bot.calls == [
        ("copy", DOCUMENTS_GROUP, FALLBACK_GROUP, 501),
        ("delete", FALLBACK_GROUP, 501),
        ("copy", DOCUMENTS_GROUP, FALLBACK_GROUP, 502),
        ("delete", DOCUMENTS_GROUP, 902),
    ]


async def test_repair_is_a_no_op_with_an_empty_registry(
    service: FileStorageServices, bot: FakeBot
) -> None:
    report = await service.repair_pending()

    assert report.repaired == 0
    assert report.database_error is None
    assert bot.calls == []

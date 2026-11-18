"""The rename and search-cleanup fixes: extension kept, typed messages removed."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from src.bot import navigation
from src.bot.file_list import FileListCB
from src.bot.keyboards import folder_files_kb
from src.bot.routers import browse
from src.bot.routers import file_list as file_list_router
from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory
from src.utils.formatters import keep_extension

UTC = timezone.utc


def stored_file(name: str = "Name.pdf") -> StoredFile:
    return StoredFile(
        id="id-1",
        file_id="file-1",
        file_unique_id="unique-1",
        file_name=name,
        category=FileCategory.document,
        mime_type="application/pdf",
        size_bytes=1024,
        uploader_id=7,
        storage_chat_id=-100,
        storage_message_id=1,
        created_at=datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
    )


class ChatState:
    """The part of the FSM context these handlers use."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = dict(data or {})
        self.state = None

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **values: object) -> None:
        self.data.update(values)

    async def set_state(self, value) -> None:
        self.state = value


def test_a_new_name_keeps_the_extension_of_the_old_one() -> None:
    assert keep_extension("Noma", "Name.pdf") == "Noma.pdf"
    # A name that already carries the extension is left alone.
    assert keep_extension("Noma.pdf", "Name.pdf") == "Noma.pdf"
    assert keep_extension("Noma.PDF", "Name.pdf") == "Noma.PDF"
    # Another extension is kept as typed, and the old one is appended.
    assert keep_extension("Noma.zip", "Name.pdf") == "Noma.zip.pdf"
    # Without an old extension there is nothing to add.
    assert keep_extension("Noma", "README") == "Noma"


async def test_renaming_keeps_the_extension_and_deletes_the_typed_message(
    monkeypatch,
) -> None:
    stored = stored_file()
    captured: dict[str, object] = {}
    deleted: list[tuple[int, int]] = []

    class Files:
        async def get(self, file_id: str):
            return stored

        async def rename(self, file_id: str, name: str):
            captured["file_id"] = file_id
            captured["name"] = name
            return replace(stored, file_name=name)

    async def navigate(message, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["notice"] = kwargs.get("notice")

    async def delete(bot, chat_id, message_id):
        deleted.append((chat_id, message_id))

    monkeypatch.setattr(browse.navigation, "navigate_message", navigate)
    monkeypatch.setattr(browse.views, "delete_screen_message", delete)
    message = SimpleNamespace(
        text="Noma",
        message_id=9,
        chat=SimpleNamespace(id=55),
        from_user=SimpleNamespace(id=7),
        answer=None,
    )
    state = ChatState({"rename_file_id": stored.id, "rename_old_name": "Name.pdf"})

    await browse.rename_message(
        message,
        services=SimpleNamespace(files=Files()),
        state=state,
        bot=None,
    )

    assert captured["name"] == "Noma.pdf"
    assert captured["file_id"] == stored.id
    assert captured["screen"] == {"k": "file", "id": stored.id}
    assert "Noma.pdf" in captured["notice"]
    # The user's text is gone; only the file card stays in the chat.
    assert deleted == [(55, 9)]


async def test_clearing_the_search_deletes_the_typed_message(monkeypatch) -> None:
    deleted: list[tuple[int, int]] = []
    captured: dict[str, object] = {}

    async def delete(bot, chat_id, message_id):
        deleted.append((chat_id, message_id))

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target

    monkeypatch.setattr(file_list_router.views, "delete_screen_message", delete)
    monkeypatch.setattr(file_list_router.navigation, "navigate_callback", navigate)
    state = ChatState(
        {
            "screen_key": {
                "k": "list",
                "v": "search",
                "val": "holiday",
                "f": "all",
                "srt": "newest",
                "p": 1,
                "u": 7,
            },
            "search_message_id": 9,
            "cleanup_ids": [9, 12],
        }
    )
    callback = SimpleNamespace(
        data=FileListCB(
            action="clear", view="search", value="holiday", page=1
        ).pack(),
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(chat=SimpleNamespace(id=55)),
    )

    await file_list_router.file_list_callback(
        callback, services=None, state=state, bot=None
    )

    assert deleted == [(55, 9)]
    assert state.data["search_message_id"] is None
    # The screen message (12) stays tracked; the deleted query is forgotten.
    assert state.data["cleanup_ids"] == [12]
    assert captured["screen"]["val"] == ""


def test_the_folder_actions_sit_in_one_row() -> None:
    folder = Folder(id="f-1", name="Invoices")
    markup = folder_files_kb([], folder.id, favorite=False)

    rows = [[button.text for button in row] for row in markup.inline_keyboard]
    action_row = next(row for row in rows if any("favourites" in text for text in row))

    assert len(action_row) == 2
    assert any("Add to favourites" in text for text in action_row)
    assert any("Delete the folder" in text for text in action_row)


async def test_forget_drops_a_deleted_message_from_the_cleanup_list() -> None:
    state = ChatState({"cleanup_ids": [4, 9, 12]})
    await navigation.forget(state, 9)
    assert state.data["cleanup_ids"] == [4, 12]

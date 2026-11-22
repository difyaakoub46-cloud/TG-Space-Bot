"""«📄 Single file view»: a new file replaces the one the bot sent before it.

The setting only ever removes the copy the bot itself delivered last: the tracked
lists of the existing cleanup (``cleanup_ids`` / ``delivered_ids``), «↩️ Back» and
the main menu are left exactly as they were, so these tests check that the new
delete never replaces or damages them.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError

from src.bot import navigation, views
from src.bot.keyboards import FileCB
from src.bot.routers import browse
from src.services.preferences import UserPreferences
from tests.file_list_support import TODAY, moment, record


class _Files:
    """``get`` and ``deliver``, with the ids the handler hands out."""

    def __init__(self, rows, *, fail: Exception | None = None) -> None:
        self.rows = {row.id: row for row in rows}
        self.fail = fail
        self.delivered: list[tuple[str, int]] = []
        self.next_id = 500

    async def get(self, file_id: str):
        return self.rows.get(file_id)

    async def deliver(self, record, chat_id: int):
        if self.fail is not None:
            raise self.fail
        self.next_id += 1
        self.delivered.append((record.id, chat_id))
        return self.next_id

    async def stats(self) -> dict[str, object]:
        return {}


class _Bot:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.edits: list[tuple[str, object]] = []

    async def delete_message(self, chat_id: int, message_id: int):
        self.deleted.append(message_id)
        return True

    async def edit_message_text(
        self, chat_id, message_id, text, reply_markup=None, **kwargs
    ):
        self.edits.append((text, reply_markup))
        return SimpleNamespace(message_id=message_id)


class _Callback:
    def __init__(self, data: str, user_id: int = 7) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=5), message_id=99)
        self.bot = _Bot()


class _State:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = dict(data or {})

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **values: object) -> None:
        self.data.update(values)

    async def clear(self) -> None:
        self.data.clear()

    async def get_state(self):
        return None

    async def set_state(self, value) -> None:
        return None


def _preferences(tmp_path: pathlib.Path, *, single: bool = True) -> UserPreferences:
    store = UserPreferences(tmp_path / "preferences.json")
    store.load()
    if single:
        store._single_view[7] = True  # noqa: SLF001 - the setting, without a write
    return store


def _services(tmp_path: pathlib.Path, rows, *, single: bool = True, fail=None):
    return SimpleNamespace(
        files=_Files(rows, fail=fail),
        preferences=_preferences(tmp_path, single=single),
    )


def _capture(monkeypatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = dict(target)
        captured["notice"] = kwargs.get("notice")

    monkeypatch.setattr(browse.navigation, "navigate_callback", fake_navigate)
    return captured


def _quick(file_id: str) -> _Callback:
    return _Callback(FileCB(action="quick", file_id=file_id).pack())


def _send(file_id: str) -> _Callback:
    return _Callback(FileCB(action="send", file_id=file_id).pack())


async def _tap(handler, callback, state, services) -> None:
    await handler(callback, services=services, state=state, bot=callback.bot)


# --------------------------------------------------------------------------- #
# The setting on
# --------------------------------------------------------------------------- #
async def test_a_new_file_removes_the_previous_one(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    rows = [record(1, moment(TODAY, 12)), record(2, moment(TODAY, 13))]
    services = _services(tmp_path, rows)
    state = _State(
        {
            "screen_key": {"k": "list", "v": "category", "val": "document"},
            "last_sent_message_id": 111,
            "cleanup_ids": [111, 90],
            "delivered_ids": [111],
        }
    )
    callback = _quick("id-1")

    await _tap(browse.file_quick_send_callback, callback, state, services)

    # Only the copy the bot sent before is removed.
    assert callback.bot.deleted == [111]
    # The new copy is now the one to replace, and it is tracked as before.
    assert state.data["last_sent_message_id"] == 501
    assert state.data["delivered_ids"] == [111, 501]
    assert state.data["cleanup_ids"] == [111, 90, 501]
    # The tracked lists themselves are never rewritten by this feature: the old
    # id stays in them, and its later deletion is left to fail silently.
    assert 90 in state.data["cleanup_ids"]


async def test_the_card_send_replaces_the_previous_copy_too(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    rows = [record(1, moment(TODAY, 12))]
    services = _services(tmp_path, rows)
    state = _State({"screen_key": {"k": "file", "id": "id-1"}, "last_sent_message_id": 111})
    callback = _send("id-1")

    await _tap(browse.file_send_callback, callback, state, services)

    assert callback.bot.deleted == [111]
    assert state.data["last_sent_message_id"] == 501


async def test_the_second_tap_replaces_the_first_file(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    rows = [record(1, moment(TODAY, 12)), record(2, moment(TODAY, 13))]
    services = _services(tmp_path, rows)
    state = _State({"screen_key": {"k": "list", "v": "search", "val": ""}})

    await _tap(browse.file_quick_send_callback, _quick("id-1"), state, services)
    first = state.data["last_sent_message_id"]
    second = _quick("id-2")
    await _tap(browse.file_quick_send_callback, second, state, services)

    assert second.bot.deleted == [first]
    assert state.data["last_sent_message_id"] == 502


async def test_the_first_file_of_a_session_removes_nothing(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    services = _services(tmp_path, [record(1, moment(TODAY, 12))])
    state = _State({"screen_key": {"k": "list", "v": "search", "val": ""}})
    callback = _quick("id-1")

    await _tap(browse.file_quick_send_callback, callback, state, services)

    assert callback.bot.deleted == []


# --------------------------------------------------------------------------- #
# The setting off, and the failure
# --------------------------------------------------------------------------- #
async def test_with_the_setting_off_the_previous_copy_stays(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    services = _services(tmp_path, [record(1, moment(TODAY, 12))], single=False)
    state = _State(
        {"screen_key": {"k": "list", "v": "search", "val": ""}, "last_sent_message_id": 111}
    )
    callback = _quick("id-1")

    await _tap(browse.file_quick_send_callback, callback, state, services)

    # Nothing accumulates differently from before, and the pointer is still kept.
    assert callback.bot.deleted == []
    assert state.data["last_sent_message_id"] == 501


async def test_a_failed_send_keeps_the_previous_file(monkeypatch, tmp_path) -> None:
    captured = _capture(monkeypatch)
    rows = [record(1, moment(TODAY, 12))]
    services = _services(
        tmp_path,
        rows,
        fail=TelegramAPIError(method=SimpleNamespace(), message="gone"),
    )
    state = _State(
        {"screen_key": {"k": "list", "v": "search", "val": ""}, "last_sent_message_id": 111}
    )
    alerts: list[object] = []

    async def ack(callback, text=None, *, show_alert: bool = False) -> None:
        alerts.append(text)

    monkeypatch.setattr(browse.views, "ack", ack)
    callback = _quick("id-1")

    await _tap(browse.file_quick_send_callback, callback, state, services)

    # The screen still shows its file, and the old copy is still the one to replace.
    assert "screen" not in captured
    assert callback.bot.deleted == []
    assert state.data["last_sent_message_id"] == 111
    assert alerts


async def test_a_copy_that_is_already_gone_fails_silently(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch)
    services = _services(tmp_path, [record(1, moment(TODAY, 12))])
    state = _State(
        {"screen_key": {"k": "list", "v": "search", "val": ""}, "last_sent_message_id": 111}
    )
    callback = _quick("id-1")

    async def refuse(chat_id: int, message_id: int):
        raise TelegramAPIError(method=SimpleNamespace(), message="message to delete not found")

    callback.bot.delete_message = refuse

    await _tap(browse.file_quick_send_callback, callback, state, services)

    # The new copy is still delivered and remembered.
    assert state.data["last_sent_message_id"] == 501


# --------------------------------------------------------------------------- #
# Back and the main menu
# --------------------------------------------------------------------------- #
async def test_opening_a_screen_between_two_files_keeps_the_pointer(tmp_path) -> None:
    state = _State({"last_sent_message_id": 111, "cleanup_ids": [111]})

    await views.reset_flow(state)

    assert state.data["last_sent_message_id"] == 111


async def test_the_main_menu_forgets_the_file_it_cannot_replace(monkeypatch, tmp_path) -> None:
    state = _State({"last_sent_message_id": 111, "screen_key": {"k": "preferences", "u": 7}})
    callback = _Callback("menu")
    services = SimpleNamespace(
        files=_Files([]), preferences=_preferences(tmp_path, single=False)
    )

    await navigation.navigate_callback(callback, state, callback.bot, services, navigation.MAIN)

    # The menu wipes the chat, so the id it would delete next is meaningless.
    assert state.data["last_sent_message_id"] is None
    assert state.data["screen_key"] == {"k": "main"}

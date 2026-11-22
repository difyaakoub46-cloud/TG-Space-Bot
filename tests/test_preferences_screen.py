"""«⚙️ Preferences»: the sub-screen of Settings and the setting it now holds.

The display settings used to sit inside «⚙️ Settings» next to the language and
the group gate. They moved one level down, so these tests check the shape of both
screens, the third setting («📄 Single file view»), and that the buttons of the
older messages keep working by redrawing the screen they now belong to.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.bot import navigation
from src.bot.keyboards import MenuCB
from src.bot.routers import commands
from src.services.preferences import UserPreferences


def _preferences(tmp_path: Path) -> UserPreferences:
    store = UserPreferences(tmp_path / "preferences.json")
    store.load()
    return store


def _services(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(preferences=_preferences(tmp_path))


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _rows(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


class _Bot:
    """The one rendering call the shared screen helper makes."""

    def __init__(self) -> None:
        self.edits: list[tuple[str, object]] = []

    async def edit_message_text(
        self, chat_id, message_id, text, reply_markup=None, **kwargs
    ):
        self.edits.append((text, reply_markup))
        return SimpleNamespace(message_id=message_id)


class _Callback:
    def __init__(self, action: str, user_id: int = 7) -> None:
        self.data = MenuCB(action=action).pack()
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=5), message_id=99)
        self.bot = _Bot()
        self.alerts: list[tuple[object, bool]] = []

    async def answer(self, text=None, show_alert: bool = False):
        self.alerts.append((text, show_alert))


class _State:
    """The part of the FSM context the screens read and write."""

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


# --------------------------------------------------------------------------- #
# The stored setting
# --------------------------------------------------------------------------- #
async def test_the_single_view_setting_is_off_until_it_is_turned_on(
    tmp_path: Path,
) -> None:
    store = _preferences(tmp_path)

    assert store.single_file_view(7) is False


async def test_every_setting_of_the_file_is_kept_together(tmp_path: Path) -> None:
    store = _preferences(tmp_path)
    await store.set_single_file_view(7, True)
    await store.set_keep_sent_files(7, True)
    await store.set_saved_screen(7, False)

    reloaded = _preferences(tmp_path)

    assert reloaded.single_file_view(7) is True
    assert reloaded.keep_sent_files(7) is True
    assert reloaded.show_saved_screen(7) is False
    # The new key did not push the older ones out of the file.
    raw = json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8"))
    assert set(raw) == {"languages", "saved_screen", "keep_sent", "single_view"}


async def test_a_broken_entry_never_turns_the_setting_on(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"single_view": {"7": "yes"}}), encoding="utf-8")

    store = _preferences(tmp_path)

    # Only a real JSON boolean counts, so a stray string leaves the default.
    assert store.single_file_view(7) is False


# --------------------------------------------------------------------------- #
# The two screens
# --------------------------------------------------------------------------- #
async def test_settings_keeps_its_own_actions_and_points_at_the_preferences(
    tmp_path: Path,
) -> None:
    text, markup = await navigation.build(_services(tmp_path), {"k": "settings", "u": 7})
    labels = _labels(markup)

    assert any(label.startswith("🌐 Change language") for label in labels)
    assert any(label.startswith("⚙️ Preferences") for label in labels)
    assert any(label.startswith("🔗 Join the groups") for label in labels)
    assert any(label.startswith("❓ Help") for label in labels)
    # The three display toggles are gone from this screen, text and buttons.
    assert not any("Saved-file screen" in label for label in labels)
    assert not any("Keep the opened files" in label for label in labels)
    assert not any("Single file view" in label for label in labels)
    assert "Single file view" not in text
    # Back and the main menu still close the screen.
    assert any(label.startswith("↩️ Back") for label in labels)
    assert any(label.startswith("🔙 Main menu") for label in labels)


async def test_the_preferences_screen_holds_the_three_display_settings(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    await services.preferences.set_single_file_view(7, True)

    text, markup = await navigation.build(services, {"k": "preferences", "u": 7})
    labels = _labels(markup)

    assert any(label.startswith("📨 Saved-file screen") for label in labels)
    assert any(label.startswith("🗂 Keep the opened files") for label in labels)
    assert any(label.startswith("📄 Single file view: ✅ On") for label in labels)
    assert "Single file view" in text
    assert "Preferences" in text


async def test_the_single_view_switch_sits_on_its_own_row(tmp_path: Path) -> None:
    _text, markup = await navigation.build(
        _services(tmp_path), {"k": "preferences", "u": 7}
    )

    rows = _rows(markup)
    single = next(row for row in rows if any("📄" in label for label in row))

    assert len(single) == 1


# --------------------------------------------------------------------------- #
# The buttons
# --------------------------------------------------------------------------- #
async def test_the_preferences_button_opens_the_screen_one_level_down(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    state = _State({"screen_key": {"k": "settings", "u": 7}})
    callback = _Callback("preferences")

    await commands.menu_preferences_callback(
        callback, services=services, state=state, bot=callback.bot
    )

    assert state.data["screen_key"] == {"k": "preferences", "u": 7}
    # «↩️ Back» returns to the screen it was opened from.
    assert state.data["trail"] == [{"k": "settings", "u": 7}]
    assert "Preferences" in callback.bot.edits[-1][0]


async def test_the_single_view_switch_flips_and_redraws_the_preferences(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    state = _State({"screen_key": {"k": "preferences", "u": 7}})
    callback = _Callback("toggle_single_view")

    await commands.menu_toggle_single_view_callback(
        callback, services=services, state=state, bot=callback.bot
    )

    assert services.preferences.single_file_view(7) is True
    assert state.data["screen_key"] == {"k": "preferences", "u": 7}
    assert "Single file view is on" in callback.bot.edits[-1][0]


async def test_the_older_toggle_buttons_redraw_the_preferences_screen(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    state = _State({"screen_key": {"k": "settings", "u": 7}})
    callback = _Callback("toggle_saved")

    await commands.menu_toggle_saved_callback(
        callback, services=services, state=state, bot=callback.bot
    )

    assert services.preferences.show_saved_screen(7) is False
    assert state.data["screen_key"] == {"k": "preferences", "u": 7}
    assert "Saved-file screen" in callback.bot.edits[-1][0]


async def test_back_returns_from_the_preferences_to_settings(tmp_path: Path) -> None:
    services = _services(tmp_path)
    state = _State(
        {
            "screen_key": {"k": "preferences", "u": 7},
            "trail": [{"k": "settings", "u": 7}],
        }
    )
    callback = _Callback("back")

    await navigation.go_back(callback, state, services)

    assert state.data["screen_key"] == {"k": "settings", "u": 7}
    assert state.data["trail"] == []
    assert "Settings" in callback.bot.edits[-1][0]

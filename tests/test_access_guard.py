"""B5: the bot is private.

An account outside ALLOWED_USER_IDS is refused with a clear message and never
reaches a handler, and an empty ALLOWED_USER_IDS is a fatal misconfiguration: the
guard raises instead of letting everybody in.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import CallbackQuery, Message

from src.bot.middlewares import AccessMiddleware
from src.core.config import ConfigurationError
from src.core.i18n import Language
from tests.conftest import make_settings

ALLOWED = 42
STRANGER = 99
# The refusal follows the user's language; the fake preferences below say Arabic.
REFUSAL = "⛔ هذا البوت خاص. لا يُسمح لك باستخدامه."


class _Preferences:
    """The one lookup the guard makes: the user's stored language."""

    def language_for(self, user_id: int | None) -> Language:
        return Language.ar


def _services(tmp_path: Path, allowed: str):
    settings = make_settings(tmp_path, allowed_user_ids_raw=allowed)
    return SimpleNamespace(settings=settings, preferences=_Preferences())


class _Message(Message):
    """A real ``Message`` (the guard checks the type) with a recorded answer."""

    answers: list[str]

    @classmethod
    def fake(cls, user_id: int) -> "_Message":
        message = cls.model_construct(
            message_id=1,
            chat=SimpleNamespace(id=1),
            from_user=SimpleNamespace(id=user_id),
        )
        # aiogram models are frozen, so the recording list bypasses __setattr__.
        object.__setattr__(message, "answers", [])
        return message

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(message_id=1)


class _Callback(CallbackQuery):
    """A real ``CallbackQuery`` whose tap is recorded instead of sent."""

    alert: list[tuple[str | None, bool]]

    @classmethod
    def fake(cls, user_id: int) -> "_Callback":
        callback = cls.model_construct(
            id="1",
            chat_instance="1",
            from_user=SimpleNamespace(id=user_id),
            data="menu:home",
        )
        object.__setattr__(callback, "alert", [])
        return callback

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.alert.append((text, show_alert))


class _Recorder:
    """Stands in for the handler: records whether it was reached."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, event, data):
        self.calls += 1
        return "handled"


async def test_a_stranger_is_refused_and_never_reaches_the_handler(
    tmp_path: Path,
) -> None:
    guard = AccessMiddleware(_services(tmp_path, f"{ALLOWED}"))
    handler = _Recorder()
    message = _Message.fake(STRANGER)

    result = await guard(handler, message, {})

    assert result is None
    assert handler.calls == 0
    assert message.answers == [REFUSAL]


async def test_a_stranger_tapping_a_button_gets_an_alert(tmp_path: Path) -> None:
    guard = AccessMiddleware(_services(tmp_path, f"{ALLOWED}"))
    handler = _Recorder()
    callback = _Callback.fake(STRANGER)

    await guard(handler, callback, {})

    assert handler.calls == 0
    assert callback.alert == [(REFUSAL, True)]


async def test_a_listed_user_reaches_the_handler(tmp_path: Path) -> None:
    guard = AccessMiddleware(_services(tmp_path, f"{ALLOWED}, 7"))
    handler = _Recorder()
    message = _Message.fake(ALLOWED)

    result = await guard(handler, message, {})

    assert result == "handled"
    assert handler.calls == 1
    assert message.answers == []


def test_an_empty_allow_list_stops_the_bot(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        AccessMiddleware(_services(tmp_path, ""))


def test_an_unparsable_allow_list_also_stops_the_bot(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        AccessMiddleware(_services(tmp_path, "not-an-id"))

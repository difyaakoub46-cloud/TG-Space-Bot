"""B2: ``views.ack`` never acknowledges the same query a second time.

``AckMiddleware`` answers every callback query before the handler runs, so the
second answer Telegram would reject is skipped entirely. A notice is still shown
when there is one, and it falls back to a normal message when the query is too
old to carry a popup.
"""

from __future__ import annotations

from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError

from src.bot import views


class _Message:
    """The one method the fallback notice uses."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(message_id=1)


class _Callback:
    """A callback query, optionally one Telegram already refuses to answer."""

    def __init__(self, *, reject: bool = False) -> None:
        self.answer_calls: list[tuple[str | None, bool]] = []
        self.reject = reject
        self.message = _Message()
        self.from_user = SimpleNamespace(id=7)

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answer_calls.append((text, show_alert))
        if self.reject:
            raise TelegramAPIError(SimpleNamespace(), "query is too old")


async def test_a_bare_ack_does_nothing() -> None:
    callback = _Callback()

    await views.ack(callback)

    # The middleware already answered the tap: no second answer, no message.
    assert callback.answer_calls == []
    assert callback.message.answers == []


async def test_a_notice_is_shown_as_an_alert() -> None:
    callback = _Callback()

    await views.ack(callback, "الملف غير موجود.", show_alert=True)

    assert callback.answer_calls == [("الملف غير موجود.", True)]
    assert callback.message.answers == []


async def test_a_rejected_notice_still_reaches_the_user() -> None:
    callback = _Callback(reject=True)

    await views.ack(callback, "تعذّر حذف الملف.", show_alert=True)

    assert callback.answer_calls == [("تعذّر حذف الملف.", True)]
    assert callback.message.answers == ["تعذّر حذف الملف."]

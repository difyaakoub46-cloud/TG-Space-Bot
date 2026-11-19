"""The two «Re-check» buttons must ask Telegram, not the stored report.

``StorageService.check_chats`` reuses its answer for ``CHECKS_TTL_SECONDS`` so a
screen opened twice does not re-probe every group. That made both refresh buttons
redraw the same stale answer; they now pass ``refresh=True`` and the screen opened
right after reads the fresh report from the cache (one real check, not two).

These tests call the real handlers and replace only navigation and the storage
service, so they pin the one thing that matters: the flag that reaches
``check_chats``, and the screen the button opens.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bot.keyboards import MenuCB
from src.bot.routers import status as status_router


class FakeStorage:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[bool] = []
        self.error = error

    async def check_chats(self, *, refresh: bool = False):
        self.calls.append(refresh)
        if self.error is not None:
            raise self.error
        return []


async def _run(monkeypatch, handler, storage) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push")

    monkeypatch.setattr(status_router.navigation, "navigate_callback", navigate)
    callback = SimpleNamespace(data="menu:anything", from_user=SimpleNamespace(id=1), message=None)
    await handler(
        callback, services=SimpleNamespace(storage=storage), state=None, bot=None
    )
    return captured


@pytest.mark.parametrize(("handler", "screen"), [
    (status_router.status_refresh_callback, {"k": "status"}),
    (status_router.permissions_refresh_callback, {"k": "permissions"}),
])
async def test_both_recheck_buttons_ask_telegram_again(monkeypatch, handler, screen) -> None:
    storage = FakeStorage()

    captured = await _run(monkeypatch, handler, storage)

    assert storage.calls == [True]
    assert captured["screen"] == screen
    # The screen is redrawn in place, not pushed as a new page of the trail.
    assert captured["push"] is False


async def test_a_failed_recheck_still_opens_the_screen(monkeypatch) -> None:
    storage = FakeStorage(error=RuntimeError("Telegram is down"))

    captured = await _run(monkeypatch, status_router.status_refresh_callback, storage)

    # The screen reports the failure itself; the tap never ends with an error.
    assert storage.calls == [True]
    assert captured["screen"] == {"k": "status"}


def test_the_buttons_are_the_ones_the_router_filters() -> None:
    assert MenuCB(action="status_check").pack().startswith("menu:status_check")
    assert MenuCB(action="permissions_check").pack().startswith("menu:permissions_check")

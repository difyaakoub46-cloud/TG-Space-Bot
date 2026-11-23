"""Every period filter opens on a period that actually holds a file.

A «📅 Day/Week/Month/Year» filter used to keep the anchor of the period it was
tapped from — today's — so it showed an empty window whenever nothing had been
uploaded today, and the same happened to a typed search made while a filter was
active. These tests fix that behaviour in place: the newest period with a match
is the one a filter opens, «⏮️ Previous …» walks the other way, and both agree
with the period picker.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tests.file_list_support import (
    OLDER_DAY,
    TODAY,
    WEEK_DAY,
    State,
    documents,
    moment,
    navigate,
    record,
    screen,
    service,
)

from src.bot import file_list, navigation
from src.bot.file_list import FileListCB
from src.bot.routers import file_list as file_list_router
from src.bot.routers import search as search_router
from src.services.files import history_date
from src.utils.filetypes import FileCategory


def _filter_tap(span: str, anchor: date = TODAY) -> str:
    return FileListCB(
        action="filter",
        view="category",
        value="document",
        period=span,
        sort="newest",
        page=1,
        anchor=anchor.isoformat(),
    ).pack()


async def _press(monkeypatch, library, target: str, stored) -> dict[str, object]:
    return await navigate(
        monkeypatch,
        file_list_router,
        target,
        stored,
        services=SimpleNamespace(files=library),
    )


def _jump(markup, prefix: str) -> FileListCB | None:
    for row in markup.inline_keyboard:
        for button in row:
            if button.text.startswith(prefix):
                return FileListCB.unpack(button.callback_data)
    return None


# --------------------------------------------------------------------------- #
# The filter press: the newest period with a file, never an empty today
# --------------------------------------------------------------------------- #
async def test_a_filter_tap_opens_the_newest_day_with_files(monkeypatch) -> None:
    library = service([record(1, moment(OLDER_DAY)), record(2, moment(WEEK_DAY))])

    captured = await _press(monkeypatch, library, _filter_tap("day"), screen(f="all"))

    assert captured["screen"]["f"] == "day"
    # 18 November holds nothing; the newest day that does is 16 November.
    assert captured["screen"]["d"] == WEEK_DAY.isoformat()
    assert captured["screen"]["p"] == 1


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        # A week is anchored on its Monday, a month on its first day, a year on
        # its 1 January: the shape the period picker already uses.
        ("week", WEEK_DAY),
        ("month", date(2026, 11, 1)),
        ("year", date(2026, 1, 1)),
    ],
)
async def test_a_filter_tap_snaps_the_anchor_like_the_picker(
    monkeypatch, span: str, expected: date
) -> None:
    library = service([record(1, moment(WEEK_DAY))])

    captured = await _press(
        monkeypatch, library, _filter_tap(span), screen(f="all")
    )

    assert captured["screen"]["f"] == span
    assert captured["screen"]["d"] == expected.isoformat()


async def test_the_anchor_follows_the_views_own_filter(monkeypatch) -> None:
    """A search is anchored on the newest day the *query* has a match in."""
    library = service(
        [record(1, moment(TODAY)), record(2, moment(OLDER_DAY), "holiday.jpg")]
    )
    target = FileListCB(
        action="filter",
        view="search",
        value="holiday",
        period="day",
        sort="newest",
        page=1,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _press(
        monkeypatch, library, target, screen("search", "holiday", f="all")
    )

    # Today's document does not match «holiday»: the list opens on 15 November.
    assert captured["screen"]["val"] == "holiday"
    assert captured["screen"]["d"] == OLDER_DAY.isoformat()


async def test_an_empty_view_keeps_the_anchor_it_had(monkeypatch) -> None:
    library = service([])

    captured = await _press(monkeypatch, library, _filter_tap("day"), screen(f="all"))

    assert captured["screen"]["f"] == "day"
    assert captured["screen"]["d"] == TODAY.isoformat()


async def test_latest_anchor_agrees_with_the_picker() -> None:
    library = service(documents(2) + [record(100, moment(OLDER_DAY))])

    for span in ("day", "week", "month", "year"):
        anchor = await library.latest_anchor(span, "category", "document")
        periods = await library.available_periods(
            "category", "document", span=span
        )
        # The anchor a filter opens on is the newest period the picker offers,
        # so the ✅ of the picker lands on the period on screen.
        assert anchor == periods[0][0]

    # «All time» is one single period and needs no anchor at all.
    assert await library.latest_anchor("all", "category", "document") is None


async def test_a_filter_tap_never_lists_the_empty_period_it_came_from(
    monkeypatch,
) -> None:
    library = service([record(100, moment(OLDER_DAY))])
    services = SimpleNamespace(files=library)

    captured = await _press(monkeypatch, library, _filter_tap("day"), screen(f="all"))
    text, markup = await navigation.build(services, captured["screen"])

    files = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("file:quick:")
    ]
    assert files == ["file:quick:id-100"]
    assert "📅 2026-11-15" in text


async def test_opening_a_list_at_all_time_reads_no_anchor() -> None:
    library = service(documents(1))
    repository = library._repository  # noqa: SLF001 - the fake, to read its calls

    key = await file_list.opened_screen(
        SimpleNamespace(files=library), "search", "", 7
    )

    assert key["f"] == "all"
    assert key["u"] == 7
    # «All time» lists everything: no extra round trip to find an anchor.
    assert repository.calls == []


# --------------------------------------------------------------------------- #
# «⏮️ Previous Day/Week/Month/Year»: the other direction of the quick jump
# --------------------------------------------------------------------------- #
async def test_the_previous_jump_reaches_the_closest_newer_day() -> None:
    library = service([record(1, moment(TODAY)), record(2, moment(OLDER_DAY))])

    _text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", d=OLDER_DAY.isoformat())
    )

    jump = _jump(markup, "⏮️")
    assert jump is not None
    assert (jump.action, jump.page, jump.anchor) == (
        "open",
        1,
        TODAY.isoformat(),
    )
    assert jump.period == "day"


async def test_no_previous_jump_when_nothing_is_newer() -> None:
    library = service(documents(2) + [record(100, moment(OLDER_DAY))])

    _text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )

    assert _jump(markup, "⏮️") is None


async def test_the_previous_jump_keeps_the_views_own_filter() -> None:
    library = service(
        [
            record(1, moment(TODAY), "photo.jpg", category=FileCategory.image),
            record(2, moment(OLDER_DAY)),
        ]
    )

    _text, markup = await navigation.build(
        SimpleNamespace(files=library),
        screen("category", "document", f="day", d=OLDER_DAY.isoformat()),
    )

    # The newer upload is an image, so this category has no newer day at all.
    assert _jump(markup, "⏮️") is None


async def test_tapping_the_previous_jump_opens_the_newer_period(monkeypatch) -> None:
    library = service([record(1, moment(TODAY)), record(2, moment(OLDER_DAY))])
    target = FileListCB(
        action="open",
        view="category",
        value="document",
        period="day",
        sort="newest",
        page=1,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _press(
        monkeypatch, library, target, screen(f="day", d=OLDER_DAY.isoformat())
    )

    assert captured["screen"]["d"] == TODAY.isoformat()
    assert captured["screen"]["p"] == 1


async def test_both_jumps_walk_the_days_that_hold_files() -> None:
    library = service(
        [
            record(1, moment(TODAY)),
            record(2, moment(WEEK_DAY)),
            record(3, moment(OLDER_DAY)),
        ]
    )
    services = SimpleNamespace(files=library)

    _text, middle = await navigation.build(
        services, screen(f="day", d=WEEK_DAY.isoformat())
    )

    # Neither direction walks the empty days one by one.
    assert _jump(middle, "⏮️").anchor == TODAY.isoformat()
    assert _jump(middle, "Next Day").anchor == OLDER_DAY.isoformat()


# --------------------------------------------------------------------------- #
# The same rule for a typed search
# --------------------------------------------------------------------------- #
async def _type(monkeypatch, library, text: str, stored) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def remember(state, *message_ids):
        return None

    async def fake_navigate(message, state, bot, services, target, **kwargs):
        captured["screen"] = target

    monkeypatch.setattr(search_router.views, "remember", remember)
    monkeypatch.setattr(search_router.navigation, "navigate_message", fake_navigate)
    message = SimpleNamespace(
        text=text, message_id=5, from_user=SimpleNamespace(id=7)
    )
    await search_router.search_query_message(
        message,
        services=SimpleNamespace(files=library),
        state=State(stored),
        bot=None,
    )
    return captured


async def test_typing_a_search_under_a_day_filter_opens_the_newest_match(
    monkeypatch,
) -> None:
    library = service(
        [record(1, moment(TODAY)), record(2, moment(WEEK_DAY), "holiday.jpg")]
    )

    captured = await _type(
        monkeypatch, library, "holiday", screen("search", "", f="day")
    )

    assert captured["screen"]["val"] == "holiday"
    assert captured["screen"]["f"] == "day"
    # Today's document does not match; 16 November does.
    assert captured["screen"]["d"] == WEEK_DAY.isoformat()


async def test_a_search_with_no_match_keeps_the_day_anchor(monkeypatch) -> None:
    library = service([record(1, moment(TODAY))])

    captured = await _type(
        monkeypatch, library, "nothing-matches", screen("search", "", f="day")
    )

    assert captured["screen"]["f"] == "day"
    assert captured["screen"]["d"] == history_date().isoformat()

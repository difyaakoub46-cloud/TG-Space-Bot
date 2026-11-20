"""The shared file list: one screen, two entry points (search and category).

The tests drive the real :func:`src.bot.file_list.screen` and the real
:class:`FileStorageServices`; only PostgREST is replaced by a small in-memory
repository that applies the same WHERE / ORDER BY / LIMIT / OFFSET the database
does, so the ordering and the eight-file slice are exercised for real.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.file_list_support import (
    Callback,
    OLDER_DAY,
    State,
    TODAY,
    WEEK_DAY,
    documents,
    moment,
    navigate,
    record,
    screen,
    service,
)

from src.bot import file_list, navigation
from src.bot.file_list import FileListCB, FileListContext
from src.bot.keyboards import CategoryCB
from src.bot.routers import browse
from src.bot.routers import search as search_router
from src.bot.states import SearchStates
from src.db.models import Folder
from src.services.files import FILES_PER_PAGE
from src.utils.filetypes import FileCategory


def buttons(markup, prefix: str) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith(prefix)
    ]


# --------------------------------------------------------------------------- #
# The list itself
# --------------------------------------------------------------------------- #
async def test_the_list_shows_eight_files_and_the_date_on_top() -> None:
    library = service(documents(20))

    text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )

    assert "file(s)" in text
    assert "📅 Today: 2026-11-18" in text  # the date is the first line under it
    assert "20 file(s)" in text
    assert "Page 1 of 3" in text
    file_buttons = buttons(markup, "file:view:")
    assert len(file_buttons) == FILES_PER_PAGE == 8
    # The filter row, eight files, the page row (only «Next»: there is nothing
    # newer to go back to and no older day to jump to), the sort row, Back / Main.
    assert [len(row) for row in markup.inline_keyboard] == [5] + [1] * 8 + [1, 1, 2]

    _text, second = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=2)
    )
    assert set(buttons(markup, "file:view:")) & set(
        buttons(second, "file:view:")
    ) == set()


async def test_the_ordering_covers_the_whole_list_before_pagination() -> None:
    files = [
        record(index, moment(TODAY, 10, index), f"file-{index}.pdf", size=index * 10)
        for index in range(10)
    ]
    library = service(files)
    repository = library._repository  # noqa: SLF001 - the fake, to read its calls

    _text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(srt="size_asc")
    )

    # The smallest file of the whole list is on page one, not the smallest of the
    # first eight rows the database happened to return.
    assert buttons(markup, "file:view:")[0] == "file:view:id-0"
    assert repository.calls[-1]["order"] == "file_size.asc"
    assert (repository.calls[-1]["limit"], repository.calls[-1]["offset"]) == (8, 0)

    _text, last = await navigation.build(
        SimpleNamespace(files=library), screen(srt="size_asc", p=2)
    )
    assert buttons(last, "file:view:") == ["file:view:id-8", "file:view:id-9"]
    assert repository.calls[-1]["offset"] == 8


async def test_the_period_filter_narrows_the_list() -> None:
    library = service(
        documents(2) + [record(100, moment(OLDER_DAY)), record(101, moment(WEEK_DAY))]
    )

    text, today_markup = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )
    assert "Today" in text
    assert len(buttons(today_markup, "file:view:")) == 2

    _text, week = await navigation.build(
        SimpleNamespace(files=library), screen(f="week")
    )
    # Today's two files and Monday's file are in this week; 15 November is not.
    assert len(buttons(week, "file:view:")) == 3

    _text, all_time = await navigation.build(
        SimpleNamespace(files=library), screen(f="all")
    )
    assert len(buttons(all_time, "file:view:")) == 4


async def test_the_quick_jump_reaches_the_closest_older_day() -> None:
    library = service(documents(20) + [record(100, moment(OLDER_DAY))])

    _text, last = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=3)
    )

    next_buttons = [
        button
        for row in last.inline_keyboard
        for button in row
        if button.text.startswith("Next Day")
    ]
    assert len(next_buttons) == 1
    jump = FileListCB.unpack(next_buttons[0].callback_data)
    assert jump.action == "open"
    assert jump.page == 1
    assert jump.anchor == OLDER_DAY.isoformat()
    assert jump.period == "day"
    assert jump.view == "category"


async def test_an_empty_period_still_offers_the_jump_to_older_files() -> None:
    library = service([record(1, moment(TODAY)), record(2, moment(OLDER_DAY))])
    empty_day = (OLDER_DAY + timedelta(days=1)).isoformat()

    _text, markup = await navigation.build(
        SimpleNamespace(files=library),
        screen(f="day", d=empty_day, val="document"),
    )

    # Nothing to show here and nothing newer, so no «Previous» is drawn at all,
    # while the quick jump still reaches the closest older day that has files.
    assert not any(
        button.text.startswith("◀️ Previous")
        for row in markup.inline_keyboard
        for button in row
    )

    following = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith("Next Day")
    )
    assert FileListCB.unpack(following.callback_data).anchor == OLDER_DAY.isoformat()


async def test_every_filter_button_carries_its_own_period() -> None:
    library = service(documents(1))

    _text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )

    filters = markup.inline_keyboard[0]
    periods = [FileListCB.unpack(button.callback_data).period for button in filters]
    # «All time» first, then the growing periods: the five fit in one row.
    assert periods == ["all", "day", "week", "month", "year"]
    assert [button.text.replace("✅ ", "") for button in filters] == [
        "📁 All time",
        "📅 Day",
        "📅 Week",
        "📅 Month",
        "📅 Year",
    ]
    # The active period is the only one marked with ✅.
    assert filters[1].text.startswith("✅ ")
    assert not any(button.text.startswith("✅ ") for button in filters[:1] + filters[2:])
    # A filter tap is its own action, so a second tap can open the picker.
    assert all(data.action == "filter" for data in (
        FileListCB.unpack(button.callback_data) for button in filters
    ))


# --------------------------------------------------------------------------- #
# The two entry points
# --------------------------------------------------------------------------- #
async def test_search_passes_its_query_and_the_category_passes_its_name() -> None:
    files = [
        record(1, moment(TODAY, 20), "holiday-photo.jpg", category=FileCategory.image),
        record(2, moment(TODAY, 19), "report.pdf"),
    ]
    folders = [Folder(id="55", name="Photos 2026", count=4)]
    library = service(files, folders)
    repository = library._repository  # noqa: SLF001 - the fake, to read its calls

    search_text, search_kb = await navigation.build(
        SimpleNamespace(files=library), screen("search", "photo")
    )
    assert "Search: «photo»" in search_text
    assert "1 file(s)" in search_text and "1 folder(s)" in search_text
    assert len(buttons(search_kb, "file:view:")) == 1
    assert repository.calls[-1]["query"] == "photo"
    assert repository.calls[-1]["category"] is None
    assert len(buttons(search_kb, "fld:open:")) == 1
    assert len(buttons(search_kb, "fl:clear")) == 1

    category_text, category_kb = await navigation.build(
        SimpleNamespace(files=library), screen("category", "document")
    )
    assert "report.pdf" not in category_text  # files are buttons, not text
    assert len(buttons(category_kb, "file:view:")) == 1
    assert repository.calls[-1]["query"] is None
    assert repository.calls[-1]["category"] is FileCategory.document
    # Only search can be cleared: a category has no query to drop.
    assert buttons(category_kb, "fl:clear") == []
    # Both entry points render the very same component layout.
    assert len(search_kb.inline_keyboard[0]) == 5
    assert len(category_kb.inline_keyboard[0]) == 5
    assert search_kb.inline_keyboard[0][0].callback_data.startswith("fl:filter:")


async def test_the_sort_panel_offers_the_six_orderings() -> None:
    ctx = FileListContext(view="category", value="document", anchor=TODAY)

    markup = file_list.sort_menu_kb(ctx)
    choices = [
        FileListCB.unpack(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("fl:sorted")
    ]

    assert [choice.sort for choice in choices] == [
        "newest",
        "oldest",
        "size_asc",
        "size_desc",
        "name_asc",
        "name_desc",
    ]
    assert [choice.page for choice in choices] == [1] * 6
    assert markup.inline_keyboard[0][0].text.startswith("✅ ")
    assert all(choice.view == "category" for choice in choices)
    assert all(choice.value == "document" for choice in choices)


async def test_the_typing_state_is_only_on_the_search_list() -> None:
    class FakeState:
        def __init__(self) -> None:
            self.current = None

        async def get_state(self):
            return self.current

        async def set_state(self, value) -> None:
            self.current = value.state if value is not None else None

    state = FakeState()
    await navigation._sync_typing(state, screen("search", ""))  # noqa: SLF001
    assert state.current == SearchStates.waiting_query.state

    await navigation._sync_typing(state, screen("category", "image"))  # noqa: SLF001
    assert state.current is None


async def test_typing_a_query_keeps_the_period_and_the_ordering(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def remember(state, *message_ids):
        return None

    async def fake_navigate(message, state, bot, services, target, **kwargs):
        captured["screen"] = target

    monkeypatch.setattr(search_router.views, "remember", remember)
    monkeypatch.setattr(search_router.navigation, "navigate_message", fake_navigate)
    message = SimpleNamespace(
        text="holiday photo",
        message_id=5,
        from_user=SimpleNamespace(id=7),
    )
    await search_router.search_query_message(
        message,
        services=None,
        state=State(screen("search", "old", f="week", srt="size_desc")),
        bot=None,
    )

    assert captured["screen"]["val"] == "holiday photo"
    assert captured["screen"]["f"] == "week"
    assert captured["screen"]["srt"] == "size_desc"
    assert captured["screen"]["p"] == 1


# --------------------------------------------------------------------------- #
# The Back trail from a category
# --------------------------------------------------------------------------- #
async def test_opening_a_category_remembers_the_library_behind_it(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", fake_navigate)
    await browse.category_callback(
        Callback(CategoryCB(name="image", page=1).pack()),
        services=None,
        state=State({"k": "categories", "u": 7}),
        bot=None,
    )

    assert captured["screen"]["k"] == "list"
    assert captured["screen"]["v"] == "category"
    assert captured["screen"]["val"] == "image"
    assert captured["screen"]["f"] == "all"
    assert captured["screen"]["p"] == 1
    assert captured["push"] is True


async def test_reopening_the_same_category_keeps_its_place(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", fake_navigate)
    await browse.category_callback(
        Callback(CategoryCB(name="image", page=1).pack()),
        services=None,
        state=State(screen("category", "image", f="week", srt="size_desc", p=2)),
        bot=None,
    )

    assert captured["screen"]["f"] == "week"
    assert captured["screen"]["srt"] == "size_desc"
    assert captured["screen"]["p"] == 2
    assert captured["push"] is False

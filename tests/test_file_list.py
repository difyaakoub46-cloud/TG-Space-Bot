"""The shared file list: one screen, two entry points (search and category).

The tests drive the real :func:`src.bot.file_list.screen` and the real
:class:`FileStorageServices`; only PostgREST is replaced by a small in-memory
repository that applies the same WHERE / ORDER BY / LIMIT / OFFSET the database
does, so the ordering and the eight-file slice are exercised for real.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.bot import file_list, navigation
from src.bot.file_list import FileListCB, FileListContext
from src.bot.keyboards import CategoryCB
from src.bot.routers import browse
from src.bot.routers import file_list as file_list_router
from src.bot.routers import search as search_router
from src.bot.states import SearchStates
from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.db.repository import SupabaseError
from src.services.files import (
    FILES_PER_PAGE,
    FileStorageServices,
    history_window,
)
from src.utils.filetypes import FileCategory
from src.utils.formatters import truncate_name

UTC = timezone.utc
TODAY = date(2026, 11, 18)
OLDER_DAY = date(2026, 11, 15)
WEEK_DAY = date(2026, 11, 16)


def moment(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def record(
    index: int,
    created_at: datetime,
    name: str | None = None,
    size: int = 1024,
    category: FileCategory = FileCategory.document,
) -> StoredFile:
    return StoredFile(
        id=f"id-{index}",
        file_id=f"file-{index}",
        file_unique_id=f"unique-{index}",
        file_name=name or f"file-{index}.pdf",
        category=category,
        mime_type="application/pdf",
        size_bytes=size,
        uploader_id=1,
        storage_chat_id=-100,
        storage_message_id=index,
        created_at=created_at,
    )


class ListRepository:
    """In-memory PostgREST: the view filter, the window, the order and the slice."""

    def __init__(
        self, files: list[StoredFile], folders: list[Folder] | None = None
    ) -> None:
        self.files = list(files)
        self._folders = list(folders or [])
        self.calls: list[dict[str, object]] = []

    async def list_folders(self, user_id: int) -> list[Folder]:
        return list(self._folders)

    async def files_in_folder(self, folder_id: str) -> list[StoredFile]:
        return list(self.files)

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str = "created_at.desc",
    ) -> tuple[list[StoredFile], int]:
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
            and (created_after is None or file.created_at >= created_after)
            and (created_before is None or file.created_at < created_before)
        ]
        column, _, direction = order.partition(".")
        key = {
            "created_at": lambda file: file.created_at,
            "file_size": lambda file: file.size_bytes,
            "file_name": lambda file: file.file_name,
        }[column]
        rows.sort(key=key, reverse=direction == "desc")
        self.calls.append(
            {
                "query": query,
                "category": category,
                "limit": limit,
                "offset": offset,
                "order": order,
            }
        )
        return rows[offset : offset + limit], len(rows)

    async def created_dates(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[datetime]:
        """The dates only, newest first: what the period picker folds."""
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=True)
        return [file.created_at for file in rows[offset : offset + limit]]

    async def period_counts(
        self,
        *,
        span: str = "day",
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 24,
    ) -> list[tuple[date, int]]:
        """Periods and their counts, as the grouped SQL function returns them."""
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (category is None or file.category is category)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=True)
        buckets: dict[date, int] = {}
        for file in rows:
            start, _end = history_window(span, file.created_at.date())
            anchor = start.date() if start is not None else file.created_at.date()
            buckets[anchor] = buckets.get(anchor, 0) + 1
        return sorted(buckets.items(), key=lambda item: item[0], reverse=True)[:limit]


def service(
    files: list[StoredFile], folders: list[Folder] | None = None
) -> FileStorageServices:
    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    return FileStorageServices(settings, ListRepository(files, folders))


def screen(view: str = "category", value: str = "document", **extra) -> dict:
    base = {
        "k": "list",
        "v": view,
        "val": value,
        "f": "all",
        "srt": "newest",
        "p": 1,
        "d": TODAY.isoformat(),
        "u": 1,
    }
    base.update(extra)
    return base


def buttons(markup, prefix: str) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith(prefix)
    ]


def documents(count: int, day: date = TODAY) -> list[StoredFile]:
    """``count`` documents uploaded ``count`` minutes apart (newest first)."""
    return [
        record(index, moment(day, 20 - index // 60, 59 - index % 60))
        for index in range(count)
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
    # The filter row, eight files, the page row (Previous, Next, jump), the sort
    # row, Back / Main menu.
    assert [len(row) for row in markup.inline_keyboard] == [5] + [1] * 8 + [3, 1, 2]

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

    # Nothing to show here and nothing newer, so «Previous» is inert while the
    # quick jump still reaches the closest older day that has files.
    previous = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith("◀️ Previous")
    )
    assert FileListCB.unpack(previous.callback_data).action == "noop"

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
    class State:
        def __init__(self) -> None:
            self.current = None

        async def get_state(self):
            return self.current

        async def set_state(self, value) -> None:
            self.current = value.state if value is not None else None

    state = State()
    await navigation._sync_typing(state, screen("search", ""))  # noqa: SLF001
    assert state.current == SearchStates.waiting_query.state

    await navigation._sync_typing(state, screen("category", "image"))  # noqa: SLF001
    assert state.current is None


async def test_typing_a_query_keeps_the_period_and_the_ordering(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def remember(state, *message_ids):
        return None

    async def navigate(message, state, bot, services, target, **kwargs):
        captured["screen"] = target

    monkeypatch.setattr(search_router.views, "remember", remember)
    monkeypatch.setattr(search_router.navigation, "navigate_message", navigate)
    message = SimpleNamespace(
        text="holiday photo",
        message_id=5,
        from_user=SimpleNamespace(id=7),
    )
    await search_router.search_query_message(
        message,
        services=None,
        state=_State(screen("search", "old", f="week", srt="size_desc")),
        bot=None,
    )

    assert captured["screen"]["val"] == "holiday photo"
    assert captured["screen"]["f"] == "week"
    assert captured["screen"]["srt"] == "size_desc"
    assert captured["screen"]["p"] == 1


# --------------------------------------------------------------------------- #
# The Back trail from a category
# --------------------------------------------------------------------------- #
class _Callback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=7)
        self.message = None


class _State:
    def __init__(self, screen_key: dict[str, object]) -> None:
        self.data = {"screen_key": dict(screen_key)}

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **values: object) -> None:
        self.data.update(values)


async def _navigate(monkeypatch, module, target_data, stored_screen) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)
        captured["notice"] = kwargs.get("notice")
        captured["markup"] = kwargs.get("markup")

    monkeypatch.setattr(module.navigation, "navigate_callback", navigate)
    await module.file_list_callback(
        _Callback(target_data),
        services=None,
        state=_State(stored_screen),
        bot=None,
    )
    return captured


async def test_opening_a_category_remembers_the_library_behind_it(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", navigate)
    await browse.category_callback(
        _Callback(CategoryCB(name="image", page=1).pack()),
        services=None,
        state=_State({"k": "categories", "u": 7}),
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

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", navigate)
    await browse.category_callback(
        _Callback(CategoryCB(name="image", page=1).pack()),
        services=None,
        state=_State(screen("category", "image", f="week", srt="size_desc", p=2)),
        bot=None,
    )

    assert captured["screen"]["f"] == "week"
    assert captured["screen"]["srt"] == "size_desc"
    assert captured["screen"]["p"] == 2
    assert captured["push"] is False


# --------------------------------------------------------------------------- #
# The component's router
# --------------------------------------------------------------------------- #
async def test_a_filter_tap_keeps_the_query_order_and_view(monkeypatch) -> None:
    target = FileListCB(
        action="open",
        view="search",
        value="photo",
        period="week",
        sort="size_desc",
        page=2,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("search", "photo")
    )

    assert captured["screen"] == screen(
        "search", "photo", f="week", srt="size_desc", p=2
    )
    assert captured["push"] is False


async def test_choosing_an_ordering_returns_to_the_first_page(monkeypatch) -> None:
    target = FileListCB(
        action="sorted",
        view="category",
        value="document",
        period="month",
        sort="name_asc",
        page=4,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("category", p=4)
    )

    assert captured["screen"]["srt"] == "name_asc"
    assert captured["screen"]["f"] == "month"
    assert captured["screen"]["p"] == 1


async def test_the_sort_button_opens_the_panel_without_losing_the_list(
    monkeypatch,
) -> None:
    target = FileListCB(
        action="sort",
        view="category",
        value="document",
        sort="oldest",
        page=2,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("category", p=2)
    )

    assert captured["screen"]["p"] == 2
    assert "Current ordering" in captured["notice"]
    assert captured["markup"] is not None


async def test_clearing_the_search_empties_the_context(monkeypatch) -> None:
    target = FileListCB(
        action="clear",
        view="search",
        value="photo",
        period="all",
        page=2,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("search", "photo", p=2)
    )

    assert captured["screen"]["val"] == ""
    assert captured["screen"]["p"] == 1


async def test_a_long_query_is_recovered_from_the_screen_it_was_typed_on(
    monkeypatch,
) -> None:
    long_query = "very long file name that overflows a callback"
    target = FileListCB(
        action="open",
        view="search",
        value=long_query[:16],
        period="all",
        page=1,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("search", long_query)
    )

    assert captured["screen"]["val"] == long_query


@pytest.mark.parametrize("view", ["search", "category"])
async def test_an_empty_list_says_so_without_losing_its_controls(view: str) -> None:
    library = service([])

    text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(view, "photo")
    )

    assert "No matching files" in text or "No files in this period" in text
    # Filters, the page row, sort, (clear for search), back / main menu.
    assert len(markup.inline_keyboard) == (5 if view == "search" else 4)
    assert markup.inline_keyboard[-1][0].callback_data.startswith("menu:back")


# --------------------------------------------------------------------------- #
# Page turners and the quick period jump
# --------------------------------------------------------------------------- #
async def test_previous_and_next_are_inert_at_the_edges_of_the_period() -> None:
    library = service(documents(20))

    def find(markup, prefix: str):
        return next(
            button
            for row in markup.inline_keyboard
            for button in row
            if button.text.startswith(prefix)
        )

    _text, first = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )
    assert FileListCB.unpack(find(first, "◀️ Previous").callback_data).action == "noop"

    _text, second = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=2)
    )
    pager = FileListCB.unpack(find(second, "◀️ Previous").callback_data)
    assert (pager.action, pager.page) == ("open", 1)
    assert FileListCB.unpack(find(second, "Next ▶️").callback_data).page == 3

    _text, last = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=3)
    )
    assert FileListCB.unpack(find(last, "Next ▶️").callback_data).action == "noop"


@pytest.mark.parametrize(
    ("period", "anchor_day", "label"),
    [
        ("week", TODAY - timedelta(days=20), "Next Week ⏭️"),
        ("month", date(2026, 4, 3), "Next Month ⏭️"),
        ("year", date(2024, 5, 4), "Next Year ⏭️"),
    ],
)
async def test_the_quick_jump_names_the_active_period(
    period: str, anchor_day: date, label: str
) -> None:
    library = service([record(1, moment(TODAY)), record(2, moment(anchor_day))])

    _text, markup = await navigation.build(
        SimpleNamespace(files=library), screen(f=period)
    )

    jump = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == label
    )
    data = FileListCB.unpack(jump.callback_data)
    # Straight to the closest older period with files, not to the next page.
    assert (data.action, data.page, data.anchor) == (
        "open",
        1,
        anchor_day.isoformat(),
    )
    assert data.period == period


# --------------------------------------------------------------------------- #
# The period picker: the second tap on a filter
# --------------------------------------------------------------------------- #
async def test_the_picker_offers_only_periods_that_hold_a_matching_file() -> None:
    library = service(documents(1) + [record(100, moment(OLDER_DAY))])

    # Every period comes with its own file count, from the same query.
    assert await library.available_periods(
        "category", "document", span="day"
    ) == [(TODAY, 1), (OLDER_DAY, 1)]
    # 15 November is the Sunday of the previous week, so the week counts split.
    assert await library.available_periods(
        "category", "document", span="week"
    ) == [(WEEK_DAY, 1), (date(2026, 11, 9), 1)]
    # A month is one period, anchored on its first day, with both files counted.
    assert await library.available_periods(
        "category", "document", span="month"
    ) == [(date(2026, 11, 1), 2)]
    # «All time» is a single period: it has no picker.
    assert await library.available_periods("category", "document", span="all") == []

    search_library = service(
        [record(1, moment(WEEK_DAY), "holiday.jpg"), record(2, moment(TODAY))]
    )
    # The picker keeps the view's filter, so a search offers only its own days.
    assert await search_library.available_periods(
        "search", "holiday", span="day"
    ) == [(WEEK_DAY, 1)]


async def test_the_picker_falls_back_to_folded_dates(monkeypatch) -> None:
    library = service(documents(2) + [record(100, moment(OLDER_DAY))])

    async def unavailable(**_kwargs):
        raise SupabaseError("POST rpc/files_periods failed with 404")

    # Before migration 009 is applied the picker still works: the dates are read
    # in batches and folded here, with the same counts.
    monkeypatch.setattr(library._repository, "period_counts", unavailable)

    assert await library.available_periods(
        "category", "document", span="day"
    ) == [(TODAY, 2), (OLDER_DAY, 1)]


async def test_a_long_file_name_is_cut_in_the_middle_of_the_button() -> None:
    long_name = "tg-space-bot-deploy-7e9a560-2026-11-15-1432.log"
    library = service([record(1, moment(TODAY, 19, 25), long_name, size=21 * 1024)])

    _text, markup = await navigation.build(SimpleNamespace(files=library), screen())

    labels = [
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith("📄 ")
    ]
    # First 12 characters, «...», last 8: the button stays on one line.
    assert labels == ["📄 tg-space-bot...1432.log - 21.0 KB - 19:25"]
    # A name that already fits is shown as it is.
    assert truncate_name("photo_AQA.jpg") == "photo_AQA.jpg"


async def test_a_second_tap_on_the_active_filter_opens_the_picker(monkeypatch) -> None:
    library = service(documents(2) + [record(100, moment(OLDER_DAY))])
    target = FileListCB(
        action="filter",
        view="category",
        value="document",
        period="day",
        sort="newest",
        page=1,
        anchor=TODAY.isoformat(),
    ).pack()

    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target_screen, **kwargs):
        captured["screen"] = target_screen
        captured["notice"] = kwargs.get("notice")
        captured["markup"] = kwargs.get("markup")

    monkeypatch.setattr(file_list_router.navigation, "navigate_callback", navigate)
    await file_list_router.file_list_callback(
        _Callback(target),
        services=SimpleNamespace(files=library),
        state=_State(screen("category", f="day")),
        bot=None,
    )

    assert "Pick a period" in captured["notice"]
    labels = [
        button.text for row in captured["markup"].inline_keyboard for button in row
    ]
    assert labels[:2] == ["✅ 2026-11-18 · 2", "2026-11-15 · 1"]
    # The picker sits on the same list, on the same page, with a way back to it.
    assert captured["screen"]["f"] == "day"
    assert captured["markup"].inline_keyboard[-1][0].text.startswith("↩️")


async def test_a_first_tap_on_another_filter_just_applies_it(monkeypatch) -> None:
    target = FileListCB(
        action="filter",
        view="category",
        value="document",
        period="week",
        sort="newest",
        page=1,
        anchor=TODAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("category", f="day")
    )

    assert captured["screen"]["f"] == "week"
    assert captured["notice"] is None


async def test_picking_a_period_opens_it(monkeypatch) -> None:
    target = FileListCB(
        action="anchor",
        view="category",
        value="document",
        period="day",
        sort="newest",
        page=1,
        anchor=OLDER_DAY.isoformat(),
    ).pack()

    captured = await _navigate(
        monkeypatch, file_list_router, target, screen("category", f="day")
    )

    assert captured["screen"]["f"] == "day"
    assert captured["screen"]["d"] == OLDER_DAY.isoformat()
    assert captured["screen"]["p"] == 1

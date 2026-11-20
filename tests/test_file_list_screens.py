"""The file list's controls: its router, its sort panel and the period picker.

These tests start from the screen the list leaves behind and drive the real
:func:`src.bot.routers.file_list.file_list_callback`, so a tap's effect on the
query, the ordering, the page and the open panel is checked end to end.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.bot import navigation
from src.bot.file_list import FileListCB
from src.bot.routers import file_list as file_list_router
from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.db.repository import SupabaseError
from src.services import files as files_module
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
        zone_offset: int = 0,
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
        # ``files_periods`` groups in the caller's offset, so the fake does too.
        zone = timezone(timedelta(minutes=zone_offset))
        buckets: dict[date, int] = {}
        for file in rows:
            day = file.created_at.astimezone(zone).date()
            start, _end = history_window(span, day, zone)
            anchor = start.astimezone(zone).date() if start is not None else day
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



def documents(count: int, day: date = TODAY) -> list[StoredFile]:
    """``count`` documents uploaded ``count`` minutes apart (newest first)."""
    return [
        record(index, moment(day, 20 - index // 60, 59 - index % 60))
        for index in range(count)
    ]


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
    # Filters, sort, (clear for search), back / main menu. There is no page row:
    # an empty period has nowhere to page to, so it gets no pagers.
    assert len(markup.inline_keyboard) == (4 if view == "search" else 3)
    assert markup.inline_keyboard[-1][0].callback_data.startswith("menu:back")


# --------------------------------------------------------------------------- #
# Page turners and the quick period jump
# --------------------------------------------------------------------------- #
async def test_previous_and_next_only_exist_when_they_can_be_used() -> None:
    library = service(documents(20))

    def find(markup, prefix: str):
        return next(
            button
            for row in markup.inline_keyboard
            for button in row
            if button.text.startswith(prefix)
        )

    def labels(markup) -> list[str]:
        return [button.text for row in markup.inline_keyboard for button in row]

    _text, first = await navigation.build(
        SimpleNamespace(files=library), screen(f="day")
    )
    # Nothing newer than the first page, so no inert «Previous» is drawn.
    assert not any(text.startswith("◀️ Previous") for text in labels(first))

    _text, second = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=2)
    )
    pager = FileListCB.unpack(find(second, "◀️ Previous").callback_data)
    assert (pager.action, pager.page) == ("open", 1)
    assert FileListCB.unpack(find(second, "Next ▶️").callback_data).page == 3

    _text, last = await navigation.build(
        SimpleNamespace(files=library), screen(f="day", p=3)
    )
    # Nothing older than the last page, so no inert «Next» is drawn either.
    assert not any(text.startswith("Next ▶️") for text in labels(last))


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


async def test_the_picker_and_the_list_agree_in_a_non_utc_zone(monkeypatch) -> None:
    """B4: the picker groups in the user's zone, like the list windows do."""
    east = timezone(timedelta(hours=3))
    monkeypatch.setattr(files_module, "local_zone", lambda: east)
    # 22:30 UTC is already 01:30 on the next day in a zone three hours ahead.
    late = datetime(2026, 11, 18, 22, 30, tzinfo=UTC)
    library = service([record(7, late)])

    periods = await library.available_periods("category", "document", span="day")
    # The local day of the upload, not the UTC one that was still running.
    assert periods == [(date(2026, 11, 19), 1)]

    page = await library.file_list_page(
        "category", "document", span="day", anchor=periods[0][0]
    )
    # The day the picker offers opens the page that holds the file.
    assert [file.id for file in page.files] == ["id-7"]
    assert (page.start, page.end) == (
        datetime(2026, 11, 18, 21, 0, tzinfo=UTC),
        datetime(2026, 11, 19, 21, 0, tzinfo=UTC),
    )

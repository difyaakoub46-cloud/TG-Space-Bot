"""«History uploads»: eight files per screen, page turns and the date jumps.

The tests drive the real :meth:`FileStorageServices.history_page` and the real
:func:`history_kb`; only PostgREST is replaced by a small in-memory repository
that applies the same window / ordering / slice the database does.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.bot import navigation
from src.bot.keyboards import FolderCB, HistoryCB, folder_files_kb, history_kb
from src.core.config import Settings
from src.db.models import StoredFile
from src.services.files import FILES_PER_PAGE, FileStorageServices, history_window
from src.utils.filetypes import FileCategory

UTC = timezone.utc
TODAY = date(2026, 11, 18)
OLDER_DAY = date(2026, 11, 15)


def moment(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def record(index: int, created_at: datetime) -> StoredFile:
    return StoredFile(
        id=f"id-{index}",
        file_id=f"file-{index}",
        file_unique_id=f"unique-{index}",
        file_name=f"file-{index}.pdf",
        category=FileCategory.document,
        mime_type="application/pdf",
        size_bytes=1024,
        uploader_id=1,
        storage_chat_id=-100,
        storage_message_id=index,
        created_at=created_at,
    )


class HistoryRepository:
    """In-memory PostgREST: the half-open window, the order and the slice."""

    def __init__(self, files: list[StoredFile]) -> None:
        self.files = list(files)
        self.calls: list[dict[str, object]] = []

    async def files_in_folder(self, folder_id: str) -> list[StoredFile]:
        return list(self.files)

    async def search(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str = "created_at.desc",
        **_: object,
    ) -> tuple[list[StoredFile], int]:
        rows = [
            file
            for file in self.files
            if (created_after is None or file.created_at >= created_after)
            and (created_before is None or file.created_at < created_before)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=order.endswith("desc"))
        self.calls.append(
            {
                "after": created_after,
                "before": created_before,
                "limit": limit,
                "offset": offset,
            }
        )
        return rows[offset : offset + limit], len(rows)


def service(files: list[StoredFile]) -> FileStorageServices:
    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    return FileStorageServices(settings, HistoryRepository(files))


def callbacks(markup) -> list[list[str]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def history_buttons(markup) -> list[HistoryCB]:
    found = []
    for row in markup.inline_keyboard:
        for button in row:
            if (button.callback_data or "").startswith("hist:"):
                found.append(HistoryCB.unpack(button.callback_data))
    return found


def today_files(count: int) -> list[StoredFile]:
    """``count`` files uploaded today, minutes apart, newest first."""
    return [
        record(index, moment(TODAY, 20 - index // 60, 59 - index % 60))
        for index in range(count)
    ]


async def test_a_day_is_eight_files_then_next_page_then_next_day() -> None:
    files = today_files(20) + [
        record(100, moment(OLDER_DAY)),
        record(101, moment(date(2026, 11, 10))),
    ]
    library = service(files)

    first = await library.history_page("day", TODAY)
    assert len(first.files) == FILES_PER_PAGE == 8
    assert (first.total, first.page, first.pages) == (20, 1, 3)
    assert first.older_page == 2
    assert first.newer_page is None
    # The day is not finished, so there is nothing to jump to yet.
    assert first.next_anchor is None

    first_kb = callbacks(history_kb(first))
    assert history_buttons(history_kb(first))[-1] == HistoryCB(
        action="page", filter_type="day", target_date="2026-11-18", page_number=2
    )
    assert all(
        button.action != "jump" for button in history_buttons(history_kb(first))
    )
    # Filters, eight file rows, one navigation row, then Back / Main menu.
    assert [len(row) for row in first_kb] == [3, 2] + [1] * 8 + [1, 2]

    middle = await library.history_page("day", TODAY, page=2)
    assert len(middle.files) == 8
    assert (middle.newer_page, middle.older_page) == (1, 3)

    last = await library.history_page("day", TODAY, page=3)
    assert len(last.files) == 4
    assert last.older_page is None
    # 16 and 17 November are empty: the jump lands on the closest day with files.
    assert last.next_anchor == OLDER_DAY

    jumps = [
        button
        for button in history_buttons(history_kb(last))
        if button.action == "jump"
    ]
    assert jumps == [
        HistoryCB(
            action="jump", filter_type="day", target_date="2026-11-15", page_number=1
        )
    ]


async def test_an_empty_day_jumps_to_the_closest_day_on_each_side() -> None:
    library = service(
        [
            record(1, moment(TODAY)),
            record(2, moment(OLDER_DAY)),
            record(3, moment(date(2026, 11, 10))),
        ]
    )

    empty = await library.history_page("day", date(2026, 11, 16))

    assert empty.files == []
    assert empty.total == 0
    assert empty.next_anchor == OLDER_DAY  # closest older day with files
    assert empty.prev_anchor == TODAY  # closest newer day with files

    jumps = {
        button.target_date
        for button in history_buttons(history_kb(empty))
        if button.action == "jump"
    }
    assert jumps == {"2026-11-18", "2026-11-15"}


async def test_a_page_turn_keeps_the_period_and_the_anchor() -> None:
    library = service(today_files(9))

    page = await library.history_page("day", TODAY)
    button = next(
        button
        for button in history_buttons(history_kb(page))
        if button.action == "page"
    )

    assert button == HistoryCB(
        action="page", filter_type="day", target_date="2026-11-18", page_number=2
    )


@pytest.mark.parametrize("span", ["week", "month", "year"])
async def test_period_bounds_are_half_open(span: str) -> None:
    start, end = history_window(span, TODAY)
    assert start is not None and end is not None

    library = service(
        [
            record(1, start),
            record(2, start - timedelta(minutes=1)),
            record(3, end - timedelta(minutes=1)),
            record(4, end),
        ]
    )

    page = await library.history_page(span, TODAY)

    assert (page.start, page.end) == (start, end)
    assert page.total == 2
    assert {file.id for file in page.files} == {"id-1", "id-3"}


async def test_week_month_and_year_start_on_the_right_day() -> None:
    week_start, week_end = history_window("week", TODAY)
    assert week_start == moment(TODAY - timedelta(days=TODAY.weekday()), 0)
    assert week_end - week_start == timedelta(days=7)

    month_start, month_end = history_window("month", TODAY)
    assert month_start == moment(date(2026, 11, 1), 0)
    assert month_end == moment(date(2026, 12, 1), 0)

    year_start, year_end = history_window("year", TODAY)
    assert year_start == moment(date(2026, 1, 1), 0)
    assert year_end == moment(date(2027, 1, 1), 0)

    assert history_window("all", TODAY) == (None, None)


async def test_all_time_has_no_period_jumps() -> None:
    library = service([record(1, moment(TODAY)), record(2, moment(OLDER_DAY))])

    page = await library.history_page("all")

    assert page.total == 2
    assert page.next_anchor is None and page.prev_anchor is None
    assert all(button.action != "jump" for button in history_buttons(history_kb(page)))


async def test_a_category_page_holds_eight_files_too() -> None:
    library = service(today_files(20))

    first, total, page = await library.category_page(FileCategory.document)
    assert len(first) == FILES_PER_PAGE == 8
    assert (total, page) == (20, 1)

    last, _total, page = await library.category_page(FileCategory.document, page=3)
    assert len(last) == 4
    assert page == 3


async def test_a_folder_page_holds_eight_files_and_clamps_the_page() -> None:
    library = service(today_files(20))

    first, total, page = await library.folder_page("folder-1")
    assert (len(first), total, page) == (8, 20, 1)

    middle, _total, page = await library.folder_page("folder-1", page=2)
    assert (len(middle), page) == (8, 2)

    last, _total, page = await library.folder_page("folder-1", page=9)
    assert (len(last), page) == (4, 3)

    assert {file.id for file in first} & {file.id for file in middle} == set()


def test_the_folder_page_controls_sit_under_its_files() -> None:
    markup = navigation._with_folder_pages(  # noqa: SLF001 - the row it inserts
        folder_files_kb(today_files(5), folder_id="folder-1"), 2, 3, 5
    )

    steps = [
        FolderCB.unpack(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("fld:fpage:")
    ]
    assert [step.value for step in steps] == ["1", "1", "3", "3"]
    # The pages come right after the five file buttons, before the sort button.
    assert len(markup.inline_keyboard[5]) == 4


async def test_history_kb_orders_filters_files_navigation_then_menu() -> None:
    library = service(today_files(3))

    markup = history_kb(await library.history_page("day", TODAY))
    rows = callbacks(markup)

    assert [len(row) for row in rows] == [3, 2, 1, 1, 1, 2]
    filters = history_buttons(markup)[:5]
    assert [button.action for button in filters] == ["filter"] * 5
    assert [button.filter_type for button in filters] == [
        "day",
        "week",
        "month",
        "year",
        "all",
    ]
    assert {button.target_date for button in filters} == {"2026-11-18"}
    assert {button.page_number for button in filters} == {1}
    # The active period is the only one marked, and it is the first button.
    texts = [button.text for button in markup.inline_keyboard[0]]
    assert texts[0].startswith("✅ ")
    assert not any(text.startswith("✅ ") for text in texts[1:])

    for row in rows[2:5]:
        assert row[0].startswith("file:")
    assert rows[-1][0].startswith("menu:back")
    assert rows[-1][1].startswith("menu:main")

    file_buttons = [
        button
        for row in markup.inline_keyboard[2:5]
        for button in row
    ]
    assert all(button.text.startswith("📄 ") for button in file_buttons)
    assert all(" - " in button.text for button in file_buttons)

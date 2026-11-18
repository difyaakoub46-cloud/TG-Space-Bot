"""The merged «🔍 Search» screen and the «↩️ Back» trail of a category.

Search is «History uploads» plus a query: the tests drive the real
:func:`navigation.build` with both kinds and check that the filters, the eight
files, the page turns and the folder matches all live on one screen.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.bot import navigation
from src.bot.keyboards import CategoryCB, FolderCB, HistoryCB, SearchCB
from src.bot.routers import browse
from src.bot.states import SearchStates
from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.services.files import FILES_PER_PAGE, FileStorageServices
from src.utils.filetypes import FileCategory

UTC = timezone.utc
TODAY = date(2026, 11, 18)
OLDER_DAY = date(2026, 11, 10)


def moment(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def record(index: int, created_at: datetime, name: str | None = None) -> StoredFile:
    return StoredFile(
        id=f"id-{index}",
        file_id=f"file-{index}",
        file_unique_id=f"unique-{index}",
        file_name=name or f"file-{index}.pdf",
        category=FileCategory.document,
        mime_type="application/pdf",
        size_bytes=1024,
        uploader_id=1,
        storage_chat_id=-100,
        storage_message_id=index,
        created_at=created_at,
    )


class SearchRepository:
    """In-memory PostgREST: window, name search, ordering and slice."""

    def __init__(
        self, files: list[StoredFile], folders: list[Folder] | None = None
    ) -> None:
        self.files = list(files)
        self._folders = list(folders or [])
        self.calls: list[dict[str, object]] = []

    async def files_in_folder(self, folder_id: str) -> list[StoredFile]:
        return list(self.files)

    async def list_folders(self, user_id: int) -> list[Folder]:
        return list(self._folders)

    async def search(
        self,
        *,
        query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str = "created_at.desc",
        **_: object,
    ) -> tuple[list[StoredFile], int]:
        needle = (query or "").casefold()
        rows = [
            file
            for file in self.files
            if (not needle or needle in file.file_name.casefold())
            and (created_after is None or file.created_at >= created_after)
            and (created_before is None or file.created_at < created_before)
        ]
        rows.sort(key=lambda file: file.created_at, reverse=order.endswith("desc"))
        self.calls.append({"query": query, "limit": limit, "offset": offset})
        return rows[offset : offset + limit], len(rows)


def service(
    files: list[StoredFile], folders: list[Folder] | None = None
) -> FileStorageServices:
    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    return FileStorageServices(settings, SearchRepository(files, folders))


def buttons(markup, prefix: str) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith(prefix)
    ]


def photos(count: int, day: date = TODAY) -> list[StoredFile]:
    return [
        record(index, moment(day, 20 - index // 60, 59 - index % 60), "photo-%d.jpg" % index)
        for index in range(count)
    ]


async def test_search_opens_the_whole_history_eight_files_to_a_page() -> None:
    files = photos(20) + [record(100, moment(OLDER_DAY), "old-report.pdf")]
    library = service(files)

    text, markup = await navigation.build(
        SimpleNamespace(files=library),
        {"k": "search", "f": "all", "p": 1, "u": 1},
    )

    assert "Search" in text
    assert "All time" in text
    assert "Type a message" in text
    file_buttons = buttons(markup, "file:view:")
    assert len(file_buttons) == FILES_PER_PAGE == 8
    assert len(buttons(markup, "hist:filter")) == 5
    # Filters, eight files, the page controls, Back / Main menu.
    assert [len(row) for row in markup.inline_keyboard] == [3, 2] + [1] * 8 + [1, 2]

    second_text, second = await navigation.build(
        SimpleNamespace(files=library),
        {"k": "search", "f": "all", "p": 2, "u": 1},
    )
    assert "Search" in second_text
    assert set(buttons(markup, "file:view:")) & set(buttons(second, "file:view:")) == set()


async def test_a_typed_query_narrows_the_files_and_finds_the_folders() -> None:
    files = [
        record(1, moment(TODAY, 20), "holiday-photo.jpg"),
        record(2, moment(TODAY, 19), "report.pdf"),
        record(3, moment(TODAY, 18), "old-photo-2.jpg"),
    ]
    folders = [
        Folder(id="55", name="Photos 2026", count=4),
        Folder(id="56", name="Documents", count=7),
    ]
    library = service(files, folders)

    text, markup = await navigation.build(
        SimpleNamespace(files=library),
        {"k": "search", "f": "all", "p": 1, "u": 1, "q": "photo"},
    )

    assert "holiday-photo.jpg" not in text  # the files are buttons, not text
    assert "2 file(s)" in text and "1 folder(s)" in text
    assert len(buttons(markup, "file:view:")) == 2
    folder_buttons = buttons(markup, "fld:open:")
    assert len(folder_buttons) == 1
    assert FolderCB.unpack(folder_buttons[0]) == FolderCB(action="open", value="55")
    assert buttons(markup, "search:clear") == [SearchCB(action="clear").pack()]
    # The filters stay on top and Back / Main menu stay at the bottom.
    assert len(markup.inline_keyboard[0]) == 3
    assert markup.inline_keyboard[-1][0].callback_data.startswith("menu:back")
    # Two files, one folder, then «Clear search», then Back / Main menu.
    assert [len(row) for row in markup.inline_keyboard[-4:]] == [1, 1, 1, 2]


async def test_search_without_a_match_says_so_and_keeps_the_clear_button() -> None:
    library = service(photos(2), [Folder(id="7", name="Documents", count=1)])

    text, markup = await navigation.build(
        SimpleNamespace(files=library),
        {"k": "search", "f": "all", "p": 1, "u": 1, "q": "zzz"},
    )

    assert "No matching files or folders." in text
    assert buttons(markup, "file:view:") == []
    assert buttons(markup, "fld:open:") == []
    assert buttons(markup, "search:clear")


@pytest.mark.parametrize(
    ("span", "expected"),
    [("day", 1), ("all", 2)],
)
async def test_the_period_filter_still_applies_to_the_search(
    span: str, expected: int
) -> None:
    library = service(
        [
            record(1, moment(TODAY, 20), "photo-today.jpg"),
            record(2, moment(OLDER_DAY), "photo-old.jpg"),
        ]
    )

    _text, markup = await navigation.build(
        SimpleNamespace(files=library),
        {"k": "search", "f": span, "d": TODAY.isoformat(), "p": 1, "u": 1, "q": "photo"},
    )

    assert len(buttons(markup, "file:view:")) == expected


async def test_the_search_screen_listens_for_text_and_other_screens_do_not() -> None:
    class State:
        def __init__(self) -> None:
            self.current = None

        async def get_state(self):
            return self.current

        async def set_state(self, value) -> None:
            self.current = value.state if value is not None else None

    state = State()
    await navigation._sync_typing(state, {"k": "search"})  # noqa: SLF001
    assert state.current == SearchStates.waiting_query.state

    await navigation._sync_typing(state, {"k": "category"})  # noqa: SLF001
    assert state.current is None


class _Callback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=7)
        self.message = None


class _State:
    def __init__(self, screen: dict[str, object]) -> None:
        self.data = {"screen_key": dict(screen)}

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)


async def _calls(monkeypatch, screen, category) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", navigate)
    await browse.category_callback(
        _Callback(CategoryCB(name=category, page=1).pack()),
        services=None,
        state=_State(screen),
        bot=None,
    )
    return captured


async def test_opening_a_category_remembers_the_library_behind_it(monkeypatch) -> None:
    captured = await _calls(monkeypatch, {"k": "categories", "u": 7}, "image")

    assert captured["screen"] == {"k": "category", "name": "image", "p": 1}
    assert captured["push"] is True


async def test_turning_a_page_does_not_push_the_library_again(monkeypatch) -> None:
    captured = await _calls(monkeypatch, {"k": "category", "name": "image"}, "image")

    assert captured["push"] is False
    assert captured["screen"] == {"k": "category", "name": "image", "p": 1}


async def test_history_buttons_on_the_search_screen_keep_the_query(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(browse.navigation, "navigate_callback", navigate)
    screen = {"k": "search", "f": "all", "d": TODAY.isoformat(), "u": 7, "q": "photo", "p": 1}
    await browse.history_callback(
        _Callback(
            HistoryCB(
                action="filter", filter_type="week", target_date=TODAY.isoformat()
            ).pack()
        ),
        services=None,
        state=_State(screen),
        bot=None,
    )

    assert captured["screen"] == {
        "k": "search",
        "f": "week",
        "d": TODAY.isoformat(),
        "p": 1,
        "u": 7,
        "q": "photo",
    }
    assert captured["push"] is False


async def test_the_history_screen_itself_is_still_a_history_screen(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = target

    monkeypatch.setattr(browse.navigation, "navigate_callback", navigate)
    await browse.history_callback(
        _Callback(
            HistoryCB(
                action="page",
                filter_type="day",
                target_date="2026-11-18",
                page_number=2,
            ).pack()
        ),
        services=None,
        state=_State({"k": "history", "f": "day", "d": TODAY.isoformat(), "p": 1}),
        bot=None,
    )

    assert captured["screen"] == {
        "k": "history",
        "f": "day",
        "d": TODAY.isoformat(),
        "p": 2,
    }

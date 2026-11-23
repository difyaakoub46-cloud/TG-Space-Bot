"""The links feature: what counts as a link, how it is stored, how it is listed.

The extraction is tested on the awkward inputs people actually paste (several
links in one message, Arabic punctuation glued to the address, a Telegram link
without a scheme), the storage on the calls it makes (one copy per link, no copy
for a repeat, no orphan copy on a failure), and the screen on what a tap carries.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from conftest import (
    LINKS_GROUP,
    FakeBot,
    FakeRepository,
    make_message,
    make_settings,
)
from file_list_support import TODAY, moment, navigate, record, screen, service
from src.bot import file_list, views
from src.bot.file_list import FileListCB, FileListContext
from src.bot.keyboards import LinkCB, LinkNameCB, main_menu_kb
from src.bot.routers import file_list as file_list_router
from src.bot.routers import links as links_router
from src.bot.states import LinkStates
from src.core.config import ConfigurationError
from src.db.models import StoredFile
from src.db.repository import SupabaseError
from src.services.files import FileStorageServices, LinkSaveReport, NotABotError
from src.services.links import LinkInfo
from src.services.storage import StorageService
from src.utils.links import GLOBAL, TELEGRAM, extract_links


# --------------------------------------------------------------------------- #
# What counts as a link, and which section it lands in
# --------------------------------------------------------------------------- #
def test_a_telegram_link_is_filed_as_telegram() -> None:
    assert [(link.url, link.link_type) for link in extract_links(
        "شاهد https://t.me/space_channel"
    )] == [("https://t.me/space_channel", TELEGRAM)]


def test_any_other_address_is_filed_as_global() -> None:
    links = extract_links("docs: https://example.com/a/b?page=2")
    assert [(link.url, link.link_type) for link in links] == [
        ("https://example.com/a/b?page=2", GLOBAL)
    ]


def test_several_links_keep_the_order_they_were_sent_in() -> None:
    links = extract_links("https://t.me/one ثم https://example.com/two")
    assert [link.link_type for link in links] == [TELEGRAM, GLOBAL]


def test_arabic_punctuation_around_a_link_is_not_part_of_it() -> None:
    links = extract_links("انظر https://t.me/abc، ثم أكمل")
    assert [link.url for link in links] == ["https://t.me/abc"]


def test_the_same_link_twice_in_one_message_is_one_link() -> None:
    links = extract_links("https://t.me/abc https://T.ME/abc")
    assert [link.url for link in links] == ["https://t.me/abc"]


def test_a_bare_telegram_host_needs_no_scheme() -> None:
    assert [link.url for link in extract_links("t.me/abc")] == ["https://t.me/abc"]


def test_text_without_a_link_yields_nothing() -> None:
    assert extract_links("report.pdf و https://ما ليس رابطاً") == []
    assert extract_links("mail me at a@t.me/xyz") == []


# --------------------------------------------------------------------------- #
# The storage group
# --------------------------------------------------------------------------- #
async def test_upload_link_copies_the_message_into_the_links_group(tmp_path) -> None:
    settings = make_settings(tmp_path, storage_chat_links=LINKS_GROUP)
    bot = FakeBot()
    storage = StorageService(bot, settings)

    chat_id, message_id = await storage.upload_link(make_message(chat_id=42, message_id=7))

    assert chat_id == LINKS_GROUP
    assert message_id > 0
    assert bot.calls == [("copy", LINKS_GROUP, 42, 7)]


async def test_upload_link_refuses_without_a_configured_group(tmp_path) -> None:
    storage = StorageService(FakeBot(), make_settings(tmp_path))
    with pytest.raises(ConfigurationError):
        await storage.upload_link(make_message())


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #
def _link_service(
    tmp_path, bot: FakeBot, repository: FakeRepository
) -> FileStorageServices:
    settings = make_settings(tmp_path, storage_chat_links=LINKS_GROUP)
    return FileStorageServices(
        settings, repository, storage=StorageService(bot, settings)
    )


async def test_a_link_is_stored_as_a_link_row(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)

    report = await services.save_links(make_message(), extract_links("https://t.me/abc"))

    assert len(report.saved) == 1
    row = repository.rows[0]
    assert row["link_url"] == "https://t.me/abc"
    assert row["link_type"] == TELEGRAM
    # The table's own constraints: a link is «other», weighs nothing and has no
    # Telegram file identity.
    assert row["category"] == "other"
    assert row["file_size"] == 0
    assert row["file_unique_id"] is None
    assert row["chat_id"] == LINKS_GROUP


async def test_every_link_of_a_message_gets_its_own_copy(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)

    report = await services.save_links(
        make_message(message_id=9),
        extract_links("https://t.me/abc and https://example.com/x"),
    )

    assert len(report.saved) == 2
    assert len(repository.rows) == 2
    assert [call[0] for call in bot.calls] == ["copy", "copy"]
    assert len({row["message_id"] for row in repository.rows}) == 2


async def test_a_repeated_link_is_reported_as_already_there(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    repository.links["https://t.me/abc"] = SimpleNamespace(
        link_url="https://t.me/abc", link_type=TELEGRAM
    )
    services = _link_service(tmp_path, bot, repository)

    report = await services.save_links(make_message(), extract_links("https://t.me/abc"))

    assert report.saved == []
    assert [row.link_url for row in report.duplicates] == ["https://t.me/abc"]
    assert bot.calls == []  # nothing is copied for a link already on record


async def test_a_failed_registration_removes_the_copy(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    repository.fail_create = SupabaseError("POST files failed with 500")
    services = _link_service(tmp_path, bot, repository)

    report = await services.save_links(make_message(), extract_links("https://t.me/abc"))

    assert report.saved == []
    assert [url for url, _reason in report.failures] == ["https://t.me/abc"]
    assert [call[0] for call in bot.calls] == ["copy", "delete"]
    assert repository.rows == []


# --------------------------------------------------------------------------- #
# The screens
# --------------------------------------------------------------------------- #
def _link_row(index: int, url: str, link_type: str, day=TODAY):
    return dataclasses.replace(
        record(index, moment(day, 12, index)),
        file_name=url,
        link_type=link_type,
        link_url=url,
        size_bytes=0,
    )


async def test_the_file_list_leaves_the_links_out() -> None:
    services = service(
        [
            _link_row(1, "https://t.me/abc", TELEGRAM),
            record(2, moment(TODAY, 13), name="report.pdf"),
        ]
    )

    page = await services.file_list_page("category", "document")

    assert [file.file_name for file in page.files] == ["report.pdf"]
    assert services._repository.calls[0]["link_type"] == "none"


async def test_the_links_screen_shows_only_its_own_section() -> None:
    services = service(
        [
            _link_row(1, "https://t.me/abc", TELEGRAM),
            _link_row(2, "https://example.com/x", GLOBAL),
        ]
    )

    telegram = await services.file_list_page("links", "telegram")
    global_links = await services.file_list_page("links", "global")

    assert [file.link_url for file in telegram.files] == ["https://t.me/abc"]
    assert [file.link_url for file in global_links.files] == ["https://example.com/x"]


async def test_an_unknown_section_opens_the_telegram_one() -> None:
    services = service([_link_row(1, "https://t.me/abc", TELEGRAM)])

    page = await services.file_list_page("links", "everything")

    assert page.value == TELEGRAM
    assert [file.link_url for file in page.files] == ["https://t.me/abc"]


async def test_a_link_button_opens_the_link_and_names_its_section() -> None:
    services = SimpleNamespace(
        files=service([_link_row(1, "https://t.me/abc", TELEGRAM)])
    )

    text, markup = await file_list.screen(services, screen("links", "telegram"))

    assert views.links_section_label(TELEGRAM) in text
    assert views.count_links(1) in text
    buttons = [button for row in markup.inline_keyboard for button in row]
    links = [button for button in buttons if button.url]
    assert [button.url for button in links] == ["https://t.me/abc"]
    # The type takes the place of the size on a file button, and the time stays.
    assert links[0].text.startswith("📱 ")
    assert "t.me/abc" in links[0].text
    assert "12:01" in links[0].text


def test_the_main_menu_has_the_links_button() -> None:
    texts = [button.text for row in main_menu_kb().inline_keyboard for button in row]
    assert views.links_label() in texts


# --------------------------------------------------------------------------- #
# The handler: a stored link leaves no message behind, exactly like an upload
# --------------------------------------------------------------------------- #
class _DeleteBot:
    """Records every delete the handler asks for."""

    def __init__(self) -> None:
        self.deleted: list[int] = []

    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> bool:
        self.deleted.extend(message_ids)
        return True

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True


class _LinkMessage:
    """One text message carrying a link, with the answers the handler sends."""

    def __init__(self, message_id: int = 21, text: str = "https://t.me/abc") -> None:
        self.chat = SimpleNamespace(id=5)
        self.message_id = message_id
        self.from_user = SimpleNamespace(id=7)
        self.text = text
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(
            chat=SimpleNamespace(id=self.chat.id), message_id=999, text=text
        )


class _SaveFiles:
    """The one service call the handler makes."""

    def __init__(self, report: LinkSaveReport) -> None:
        self.report = report
        self.calls = 0

    async def save_links(self, message, links):
        self.calls += 1
        return self.report


async def _run_handler(
    monkeypatch, tmp_path, report: LinkSaveReport, *, show_saved: bool = True
) -> tuple[_DeleteBot, _LinkMessage, dict[str, object]]:
    bot, message = _DeleteBot(), _LinkMessage()
    services = SimpleNamespace(
        settings=make_settings(tmp_path, storage_chat_links=LINKS_GROUP),
        files=_SaveFiles(report),
        preferences=SimpleNamespace(show_saved_screen=lambda _user_id: show_saved),
    )

    async def reset_flow(state, **kwargs) -> None:
        return None

    async def remember(state, *message_ids: int) -> None:
        return None

    captured: dict[str, object] = {}

    async def navigate(message, state, bot, services, target, **kwargs) -> None:
        captured["screen"] = dict(target)
        captured["notice"] = kwargs.get("notice")

    monkeypatch.setattr(links_router.views, "reset_flow", reset_flow)
    monkeypatch.setattr(links_router.views, "remember", remember)
    monkeypatch.setattr(links_router.navigation, "navigate_message", navigate)

    await links_router.save_links_message(message, services, SimpleNamespace(), bot)
    return bot, message, captured


async def test_a_stored_link_message_is_removed_and_leaves_one_screen(
    monkeypatch, tmp_path
) -> None:
    report = LinkSaveReport()
    report.saved.append(
        SimpleNamespace(link_url="https://t.me/abc", link_type=TELEGRAM)
    )

    bot, message, captured = await _run_handler(monkeypatch, tmp_path, report)

    # The message goes, like the message of a stored file...
    assert bot.deleted == [message.message_id]
    # ...and no new message with its own main-menu keyboard is sent.
    assert message.answers == []
    # The result is drawn on the single persistent screen, on the links list.
    assert captured["screen"]["k"] == "list"
    assert captured["screen"]["v"] == "links"
    assert "https://t.me/abc" in str(captured["notice"])


async def test_a_failed_link_keeps_its_message(monkeypatch, tmp_path) -> None:
    report = LinkSaveReport()
    report.failures.append(("https://t.me/abc", "the links group is not writable"))

    bot, message, captured = await _run_handler(monkeypatch, tmp_path, report)

    assert bot.deleted == []
    assert message.message_id not in bot.deleted
    assert "not writable" in str(captured["notice"])


# --------------------------------------------------------------------------- #
# Names: the bot badge, and the handle that is refused
# --------------------------------------------------------------------------- #
class _Resolver:
    """A resolver with scripted answers, so no page is ever fetched."""

    def __init__(self, **infos: LinkInfo) -> None:
        self.infos = infos
        self.calls: list[str] = []

    async def resolve(self, url: str) -> LinkInfo:
        self.calls.append(url)
        return self.infos.get(url, LinkInfo())


async def test_a_confirmed_bot_keeps_its_name_and_its_badge(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(
        _Resolver(
            **{
                "https://t.me/helperbot": LinkInfo(
                    name="Helper", is_bot=True, source="preview"
                )
            }
        )
    )

    report = await services.save_links(make_message(), extract_links("@HelperBot"))

    assert len(report.saved) == 1
    row = repository.rows[0]
    assert row["link_url"] == "https://t.me/helperbot"
    assert row["link_name"] == "Helper"
    assert row["link_is_bot"] is True
    # The same badge the list shows, now that the row carries the flag.
    assert report.saved[0].link_display_name == "Helper | Bot"


async def test_the_bot_father_handle_is_saved_with_its_name_and_badge(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(
        _Resolver(
            **{
                "https://t.me/botfather": LinkInfo(
                    name="BotFather", is_bot=True, source="preview"
                )
            }
        )
    )

    report = await services.save_links(make_message(), extract_links("@BotFather"))

    assert len(report.saved) == 1
    row = repository.rows[0]
    assert (row["link_url"], row["link_name"], row["link_is_bot"]) == (
        "https://t.me/botfather",
        "BotFather",
        True,
    )
    assert report.saved[0].link_display_name == "BotFather | Bot"


async def test_a_short_bot_handle_is_saved_too(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(
        _Resolver(
            **{"https://t.me/gif": LinkInfo(name="GIF", is_bot=True, source="preview")}
        )
    )

    report = await services.save_links(make_message(), extract_links("@gif"))

    assert len(report.saved) == 1
    assert repository.rows[0]["link_url"] == "https://t.me/gif"
    assert repository.rows[0]["link_is_bot"] is True


async def test_a_handle_that_is_not_a_bot_is_refused_before_the_copy(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(
        _Resolver(
            **{
                "https://t.me/someone": LinkInfo(name="Someone", source="preview")
            }
        )
    )

    report = await services.save_links(make_message(), extract_links("@Someone"))

    assert report.saved == []
    assert len(report.rejected) == 1
    assert report.rejected[0].unconfirmed is False
    assert bot.calls == []  # nothing was copied into the links group
    assert repository.rows == []  # and no row was written


async def test_a_handle_that_cannot_be_checked_is_refused_as_unconfirmed(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(_Resolver())  # the default answer is an unread fallback

    report = await services.save_links(make_message(), extract_links("@someone"))

    assert report.saved == []
    assert report.rejected[0].unconfirmed is True
    assert bot.calls == []


async def test_an_address_keeps_its_name_without_a_badge(tmp_path) -> None:
    bot, repository = FakeBot(), FakeRepository()
    services = _link_service(tmp_path, bot, repository)
    services.bind_links(
        _Resolver(
            **{
                "https://t.me/space_channel": LinkInfo(
                    name="Space Channel", source="telegram"
                )
            }
        )
    )

    report = await services.save_links(
        make_message(), extract_links("https://t.me/space_channel")
    )

    row = repository.rows[0]
    assert row["link_name"] == "Space Channel"
    assert row["link_is_bot"] is False
    assert report.saved[0].link_display_name == "Space Channel"


def test_a_bot_button_shows_its_name_and_the_badge() -> None:
    row = dataclasses.replace(
        _link_row(1, "https://t.me/helper", TELEGRAM),
        link_name="Helper",
        link_is_bot=True,
    )

    label = file_list._link_label(row)

    assert "Helper | Bot" in label
    assert "https://t.me/helper" not in label


def test_the_result_screen_shows_the_name_instead_of_the_address() -> None:
    report = LinkSaveReport()
    report.saved.append(
        SimpleNamespace(
            link_url="https://t.me/abc",
            link_type=TELEGRAM,
            link_display_name="Helper | Bot",
        )
    )

    notice = links_router.saved_text(report)

    assert "Helper | Bot" in notice
    assert "https://t.me/abc" not in notice


def test_the_result_screen_explains_a_refused_handle() -> None:
    report = LinkSaveReport()
    report.rejected.append(NotABotError(extract_links("@Someone")[0]))

    notice = links_router.saved_text(report)

    assert "@Someone" in notice
    assert "not a bot" in notice


async def test_a_refused_handle_keeps_its_message_and_is_explained(
    monkeypatch, tmp_path
) -> None:
    report = LinkSaveReport()
    report.rejected.append(NotABotError(extract_links("@Someone")[0]))

    bot, message, captured = await _run_handler(monkeypatch, tmp_path, report)

    assert bot.deleted == []
    assert message.message_id not in bot.deleted
    assert "not a bot" in str(captured["notice"])


async def test_a_link_name_can_be_set_later(tmp_path) -> None:
    repository = FakeRepository()
    services = _link_service(tmp_path, FakeBot(), repository)

    await services.set_link_name("7", "My Name", True)

    assert repository.renamed == [("7", "My Name", True)]


# --------------------------------------------------------------------------- #
# The naming question: asked once, for one fresh global link
# --------------------------------------------------------------------------- #
class _State:
    """The parts of ``FSMContext`` the flow uses, kept in a dict."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.state: str | None = None

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **fields: object) -> None:
        self.data.update(fields)

    async def set_state(self, value) -> None:
        self.state = getattr(value, "state", value)

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


class _NameFiles:
    """``save_links`` and ``set_link_name``, recording what was asked."""

    def __init__(self, report: LinkSaveReport, updated=None) -> None:
        self.report = report
        self.updated = updated
        self.names: list[tuple[str, str]] = []

    async def save_links(self, message, links):
        return self.report

    async def set_link_name(self, link_id, name, is_bot=False):
        self.names.append((link_id, name))
        return self.updated


async def _run_links_message(
    monkeypatch,
    tmp_path,
    report: LinkSaveReport,
    *,
    text: str = "https://example.com/x",
    show_saved: bool = True,
):
    """Drive the links handler and hand back the screen it drew."""
    bot, message = _DeleteBot(), _LinkMessage(text=text)
    files = _NameFiles(report)
    services = SimpleNamespace(
        settings=make_settings(tmp_path, storage_chat_links=LINKS_GROUP),
        files=files,
        preferences=SimpleNamespace(show_saved_screen=lambda _user_id: show_saved),
    )
    state = _State()
    captured: dict[str, object] = {}

    async def navigate(*args, **kwargs) -> None:
        captured["screen"] = dict(args[4])
        captured["notice"] = kwargs.get("notice")
        captured["push"] = kwargs.get("push", True)

    monkeypatch.setattr(links_router.navigation, "navigate_message", navigate)
    monkeypatch.setattr(links_router.navigation, "navigate_callback", navigate)

    await links_router.save_links_message(message, services, state, bot)
    return bot, message, state, files, services, captured


def _saved_link(link_type: str = GLOBAL, index: int = 1) -> StoredFile:
    url = "https://example.com/x" if link_type == GLOBAL else "https://t.me/abc"
    return _link_row(index, url, link_type)


async def test_one_new_global_link_asks_for_its_name(monkeypatch, tmp_path) -> None:
    report = LinkSaveReport()
    report.saved.append(_saved_link())

    _bot, _message, state, _files, _services, captured = await _run_links_message(
        monkeypatch, tmp_path, report
    )

    assert state.state == LinkStates.waiting_name.state
    assert state.data["link_id"] == "id-1"
    assert captured["screen"]["ask"] == "id-1"
    assert "name?" in str(captured["notice"])


async def test_a_telegram_link_is_never_asked_about(monkeypatch, tmp_path) -> None:
    report = LinkSaveReport()
    report.saved.append(_saved_link(TELEGRAM))

    _bot, _message, state, _files, _services, captured = await _run_links_message(
        monkeypatch, tmp_path, report, text="https://t.me/abc"
    )

    assert state.state is None
    assert "ask" not in captured["screen"]


async def test_a_message_with_two_links_is_not_asked_about(monkeypatch, tmp_path) -> None:
    report = LinkSaveReport()
    report.saved.extend([_saved_link(GLOBAL, 1), _saved_link(GLOBAL, 2)])

    _bot, _message, state, _files, _services, captured = await _run_links_message(
        monkeypatch,
        tmp_path,
        report,
        text="https://example.com/x and https://example.com/y",
    )

    assert state.state is None
    assert "ask" not in captured["screen"]


async def test_a_duplicate_link_is_not_asked_about(monkeypatch, tmp_path) -> None:
    report = LinkSaveReport()
    report.duplicates.append(_saved_link())

    _bot, _message, state, _files, _services, captured = await _run_links_message(
        monkeypatch, tmp_path, report
    )

    assert state.state is None
    assert "ask" not in captured["screen"]


async def test_the_question_is_skipped_while_the_saved_screen_is_off(
    monkeypatch, tmp_path
) -> None:
    report = LinkSaveReport()
    report.saved.append(_saved_link())

    _bot, _message, state, _files, _services, captured = await _run_links_message(
        monkeypatch, tmp_path, report, show_saved=False
    )

    assert state.state is None
    assert captured == {}


async def _ready_to_name(monkeypatch, tmp_path, **kwargs):
    """The state of a screen that is already asking for a name."""
    report = LinkSaveReport()
    report.saved.append(_saved_link())
    bot, _message, state, files, services, captured = await _run_links_message(
        monkeypatch, tmp_path, report, **kwargs
    )
    files.updated = dataclasses.replace(_saved_link(), link_name="موقعي")
    return bot, state, files, services, captured


async def test_the_typed_name_is_stored_and_its_message_removed(
    monkeypatch, tmp_path
) -> None:
    bot, state, files, services, captured = await _ready_to_name(monkeypatch, tmp_path)
    answer = _LinkMessage(message_id=44, text="موقعي")
    deleted: list[int] = []

    async def delete_screen_message(bot, chat_id: int, message_id: int) -> None:
        deleted.append(message_id)

    monkeypatch.setattr(
        links_router.views, "delete_screen_message", delete_screen_message
    )
    await links_router.link_name_message(answer, services, state, bot)

    assert files.names == [("id-1", "موقعي")]
    assert deleted == [44]
    assert state.state is None
    assert "موقعي" in str(captured["notice"])
    assert "ask" not in captured["screen"]


async def test_a_link_typed_as_the_name_stays_a_name(monkeypatch, tmp_path) -> None:
    bot, state, files, services, _captured = await _ready_to_name(monkeypatch, tmp_path)
    answer = _LinkMessage(message_id=45, text="https://example.com/pretty")

    async def ignore(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(links_router.views, "delete_screen_message", ignore)
    await links_router.link_name_message(answer, services, state, bot)

    # The name is stored as typed; no second link was extracted from it.
    assert files.names == [("id-1", "https://example.com/pretty")]
    assert files.report.saved == [_saved_link()]


async def test_a_too_long_name_is_refused_and_the_question_stays(
    monkeypatch, tmp_path
) -> None:
    bot, state, files, services, _captured = await _ready_to_name(monkeypatch, tmp_path)
    answer = _LinkMessage(message_id=46, text="x" * 121)

    await links_router.link_name_message(answer, services, state, bot)

    assert files.names == []
    assert state.state == LinkStates.waiting_name.state
    assert answer.answers  # the user was told the length is wrong


async def test_skipping_clears_the_state_and_the_question(monkeypatch, tmp_path) -> None:
    _bot, state, _files, services, captured = await _ready_to_name(
        monkeypatch, tmp_path
    )
    callback = SimpleNamespace(
        data="ln:skip:id-1",
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(chat=SimpleNamespace(id=5)),
    )

    await links_router.link_name_skip(callback, services, state, SimpleNamespace())

    assert state.state is None
    assert "ask" not in captured["screen"]
    assert captured["push"] is False


async def test_the_question_buttons_are_on_the_links_screen() -> None:
    services = SimpleNamespace(
        files=service([_link_row(1, "https://example.com/x", GLOBAL)])
    )
    key = screen("links", "telegram")
    key["ask"] = "id-1"

    _text, markup = await file_list.screen(services, key)

    answers = [
        LinkNameCB.unpack(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("ln:")
    ]
    assert {(answer.action, answer.link_id) for answer in answers} == {
        ("skip", "id-1"),
        ("cancel", "id-1"),
    }


# --------------------------------------------------------------------------- #
# «✏️ Edit mode»: the switch and the callbacks it turns the buttons into
# --------------------------------------------------------------------------- #
def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _packed(markup):
    return [button.callback_data for button in _buttons(markup) if button.callback_data]


def _actions(markup, prefix: str):
    return [
        data
        for data in _packed(markup)
        if data.startswith(prefix)
    ]


def test_a_new_links_screen_starts_with_the_edit_mode_off() -> None:
    key = file_list.new_screen("links", TELEGRAM, 7)

    assert "e" not in key
    assert FileListContext.from_screen(key).edit is False


def test_the_edit_flag_survives_a_round_trip_through_the_screen_key() -> None:
    key = file_list.new_screen("links", TELEGRAM, 7)
    key["e"] = True

    ctx = FileListContext.from_screen(key)

    assert ctx.edit is True
    assert ctx.to_screen(key)["e"] is True


async def test_the_links_screen_has_the_edit_switch() -> None:
    services = SimpleNamespace(
        files=service([_link_row(1, "https://t.me/abc", TELEGRAM)])
    )

    _text, markup = await file_list.screen(services, screen("links", "telegram"))

    actions = [FileListCB.unpack(data) for data in _actions(markup, "fl:")]
    assert any(action.action == "edit" for action in actions)


async def test_edit_mode_turns_the_link_buttons_into_callbacks() -> None:
    services = SimpleNamespace(
        files=service([_link_row(1, "https://t.me/abc", TELEGRAM)])
    )

    _text, markup = await file_list.screen(
        services, screen("links", "telegram", e=True)
    )

    # No button opens the address any more: every tap stays inside the bot.
    assert [button.url for button in _buttons(markup) if button.url] == []
    opens = [LinkCB.unpack(data) for data in _actions(markup, "lk:")]
    assert {(open_.action, open_.link_id) for open_ in opens} == {("open", "id-1")}


async def test_the_search_list_carries_the_file_switch_not_the_links_one() -> None:
    services = SimpleNamespace(
        files=service([record(1, moment(TODAY, 12), name="a.pdf")])
    )

    _text, markup = await file_list.screen(services, screen("search", "a.pdf"))

    texts = [button.text for button in _buttons(markup)]
    # The links screen's own toggle stays there; the shared file lists add theirs.
    assert "🔴 ✏️ Edit: off" in texts
    assert "✏️ Edit mode: off" not in texts


async def test_the_switch_flips_the_mode_without_moving_the_list(monkeypatch) -> None:
    key = screen("links", "telegram", p=2)
    data = file_list.callback_data(FileListContext.from_screen(key), "edit")

    captured = await navigate(monkeypatch, file_list_router, data, key)

    assert captured["screen"]["e"] is True
    assert captured["screen"]["p"] == 2
    assert captured["push"] is False


# --------------------------------------------------------------------------- #
# The link's actions: rename it, or delete it after a confirmation
# --------------------------------------------------------------------------- #
class _LinkFiles:
    """The three service calls the action screens make, with what they received."""

    def __init__(self, rows: list[StoredFile]) -> None:
        self.rows = {row.id: row for row in rows}
        self.names: list[tuple[str, str, bool]] = []
        self.deleted: list[str] = []
        self.fail_delete = False

    async def get(self, file_id: str):
        return self.rows.get(file_id)

    async def set_link_name(self, link_id: str, name: str, is_bot: bool = False):
        self.names.append((link_id, name, is_bot))
        return dataclasses.replace(self.rows[link_id], link_name=name, link_is_bot=is_bot)

    async def delete(self, file_id: str):
        if self.fail_delete:
            return False
        self.deleted.append(file_id)
        return self.rows.pop(file_id, None) is not None


def _link_services(rows: list[StoredFile]) -> SimpleNamespace:
    return SimpleNamespace(files=_LinkFiles(rows))


def _tap(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(chat=SimpleNamespace(id=5)),
    )


async def _drive(monkeypatch, handler, data: str, state, services) -> dict:
    """Call one link handler with a real callback and capture what it renders."""
    captured: dict[str, object] = {}

    async def fake_navigate(callback, state, bot, services, target, **kwargs):
        captured["screen"] = dict(target)
        captured["push"] = kwargs.get("push", True)
        captured["notice"] = kwargs.get("notice")
        captured["markup"] = kwargs.get("markup")

    monkeypatch.setattr(links_router.navigation, "navigate_callback", fake_navigate)
    await handler(_tap(data), services=services, state=state, bot=SimpleNamespace())
    return captured


async def test_the_link_actions_screen_offers_rename_and_delete() -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])

    text, markup = await links_router.navigation.build(
        services, {"k": "link", "id": "id-1"}
    )

    assert "t.me/abc" in text
    actions = {LinkCB.unpack(data).action for data in _actions(markup, "lk:")}
    assert actions == {"rename", "delete"}


async def test_the_edit_button_opens_the_actions_and_remembers_the_list(
    monkeypatch,
) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    state = _State()
    state.data["screen_key"] = screen("links", "telegram")

    captured = await _drive(
        monkeypatch,
        links_router.link_open_callback,
        LinkCB(action="open", link_id="id-1").pack(),
        state,
        services,
    )

    assert captured["screen"] == {"k": "link", "id": "id-1"}
    assert state.data["link_list_screen"]["k"] == "list"
    assert captured["push"] is True


async def test_rename_asks_for_a_name_and_waits(monkeypatch) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    state = _State()

    captured = await _drive(
        monkeypatch,
        links_router.link_rename_callback,
        LinkCB(action="rename", link_id="id-1").pack(),
        state,
        services,
    )

    assert state.state == LinkStates.renaming.state
    assert state.data["link_rename_id"] == "id-1"
    assert captured["screen"] == {"k": "link", "id": "id-1"}
    assert "name" in str(captured["notice"]).lower()


async def test_a_typed_link_name_is_stored_with_the_bot_badge_kept(
    monkeypatch,
) -> None:
    row = dataclasses.replace(
        _link_row(1, "https://t.me/helper", TELEGRAM),
        link_name="Helper",
        link_is_bot=True,
    )
    services = _link_services([row])
    state = _State()
    state.data["link_rename_id"] = "id-1"
    state.state = LinkStates.renaming.state
    answer = _LinkMessage(message_id=44, text="اسمي")
    deleted: list[int] = []

    async def delete_screen_message(bot, chat_id: int, message_id: int) -> None:
        deleted.append(message_id)

    async def navigate(message, state, bot, services, target, **kwargs) -> None:
        captured["screen"] = dict(target)
        captured["notice"] = kwargs.get("notice")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        links_router.views, "delete_screen_message", delete_screen_message
    )
    monkeypatch.setattr(links_router.navigation, "navigate_message", navigate)

    await links_router.link_rename_message(answer, services, state, SimpleNamespace())

    assert services.files.names == [("id-1", "اسمي", True)]
    assert deleted == [44]
    assert state.state is None
    assert captured["screen"] == {"k": "link", "id": "id-1"}
    assert "اسمي" in str(captured["notice"])


async def test_a_too_long_link_name_is_refused_and_the_question_stays(
    monkeypatch,
) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    state = _State()
    state.data["link_rename_id"] = "id-1"
    state.state = LinkStates.renaming.state
    answer = _LinkMessage(message_id=45, text="x" * 121)

    await links_router.link_rename_message(answer, services, state, SimpleNamespace())

    assert services.files.names == []
    assert state.state == LinkStates.renaming.state
    assert answer.answers


async def test_a_link_name_typed_as_an_address_is_still_only_a_name(
    monkeypatch,
) -> None:
    services = _link_services([_link_row(1, "https://example.com/x", GLOBAL)])
    state = _State()
    state.data["link_rename_id"] = "id-1"
    state.state = LinkStates.renaming.state
    answer = _LinkMessage(message_id=46, text="https://example.com/pretty")

    async def ignore(*args, **kwargs) -> None:
        return None

    async def navigate(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(links_router.views, "delete_screen_message", ignore)
    monkeypatch.setattr(links_router.navigation, "navigate_message", navigate)

    await links_router.link_rename_message(answer, services, state, SimpleNamespace())

    assert services.files.names == [
        ("id-1", "https://example.com/pretty", False)
    ]


async def test_the_delete_button_asks_before_deleting(monkeypatch) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    state = _State()

    captured = await _drive(
        monkeypatch,
        links_router.link_delete_callback,
        LinkCB(action="delete", link_id="id-1").pack(),
        state,
        services,
    )

    assert captured["screen"] == {"k": "link_delete", "id": "id-1"}
    assert captured["push"] is False
    assert services.files.deleted == []


async def test_the_delete_confirmation_names_the_link() -> None:
    services = _link_services([_link_row(1, "https://example.com/x", GLOBAL)])

    text, markup = await links_router.navigation.build(
        services, {"k": "link_delete", "id": "id-1"}
    )

    assert "example.com/x" in text
    actions = {LinkCB.unpack(data).action for data in _actions(markup, "lk:")}
    assert actions == {"confirm", "open"}


async def test_confirming_deletes_the_link_and_returns_to_the_saved_list(
    monkeypatch,
) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    state = _State()
    state.data["screen_key"] = {"k": "link_delete", "id": "id-1"}
    state.data["link_list_screen"] = screen("links", "telegram", p=2)

    captured = await _drive(
        monkeypatch,
        links_router.link_delete_confirm_callback,
        LinkCB(action="confirm", link_id="id-1").pack(),
        state,
        services,
    )

    assert services.files.deleted == ["id-1"]
    assert captured["screen"]["v"] == "links"
    assert captured["screen"]["p"] == 2
    assert captured["push"] is False
    assert "✅" in str(captured["notice"])


async def test_a_failed_link_deletion_is_reported_and_stays_on_screen(
    monkeypatch,
) -> None:
    services = _link_services([_link_row(1, "https://t.me/abc", TELEGRAM)])
    services.files.fail_delete = True
    alerts: list[tuple[object, dict]] = []

    async def ack(callback, text=None, **kwargs) -> None:
        alerts.append((text, kwargs))

    async def navigate(*args, **kwargs) -> None:
        raise AssertionError("a failed deletion must not redraw another screen")

    monkeypatch.setattr(links_router.views, "ack", ack)
    monkeypatch.setattr(links_router.navigation, "navigate_callback", navigate)

    await links_router.link_delete_confirm_callback(
        _tap(LinkCB(action="confirm", link_id="id-1").pack()),
        services=services,
        state=_State(),
        bot=SimpleNamespace(),
    )

    assert alerts and "delete" in str(alerts[0][0]).lower()
    assert alerts[0][1].get("show_alert") is True

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
from file_list_support import TODAY, moment, record, screen, service
from src.bot import file_list, views
from src.bot.keyboards import main_menu_kb
from src.bot.routers import links as links_router
from src.core.config import ConfigurationError
from src.db.repository import SupabaseError
from src.services.files import FileStorageServices, LinkSaveReport
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

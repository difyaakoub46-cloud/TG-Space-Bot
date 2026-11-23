"""The names of saved links: a bare username, a channel title, a bot, a domain.

The extraction is tested on the inputs that decide whether an «@» is a link at all,
and the resolver on the two sources it reads (the chat itself, then the public
preview page) plus the memory that keeps it from asking twice.
"""

from __future__ import annotations

from aiogram.exceptions import TelegramAPIError
from aiogram.methods import GetChat

from src.services.links import (
    LinkResolver,
    domain_name,
    parse_preview,
    telegram_handle,
)
from src.utils.links import TELEGRAM, extract_links


# --------------------------------------------------------------------------- #
# What counts as a username
# --------------------------------------------------------------------------- #
def test_a_username_alone_in_the_message_is_a_telegram_link() -> None:
    links = extract_links("@MyBot")

    assert [(link.url, link.link_type, link.handle) for link in links] == [
        ("https://t.me/mybot", TELEGRAM, "MyBot")
    ]


def test_surrounding_spaces_do_not_stop_a_username() -> None:
    assert [link.url for link in extract_links("  @space_channel\n")] == [
        "https://t.me/space_channel"
    ]


def test_a_mention_inside_a_sentence_is_not_a_link() -> None:
    assert extract_links("راسلني على @MyBot من فضلك") == []


def test_an_address_is_never_read_as_a_username() -> None:
    assert extract_links("a@b.com") == []
    # An «@» inside an address belongs to that address: the link stays global and
    # no username is read from it.
    links = extract_links("https://example.com/@name")
    assert [link.handle for link in links] == [None]


def test_a_username_that_is_too_short_is_not_a_link() -> None:
    assert extract_links("@ab") == []


def test_a_short_bot_username_is_a_link() -> None:
    # Bots made before the five-letter rule keep their short names.
    links = extract_links("@gif")

    assert [(link.url, link.link_type, link.handle) for link in links] == [
        ("https://t.me/gif", TELEGRAM, "gif")
    ]


def test_a_username_and_a_link_in_one_message_keep_the_link_only() -> None:
    assert [link.url for link in extract_links("@MyBot https://example.com/x")] == [
        "https://example.com/x"
    ]


def test_the_username_inside_a_telegram_address_is_read() -> None:
    assert telegram_handle("https://t.me/space_channel") == "space_channel"
    # A post belongs to its channel, and the web preview is the same channel.
    assert telegram_handle("https://t.me/space_channel/42") == "space_channel"
    assert telegram_handle("https://t.me/s/space_channel") == "space_channel"
    assert telegram_handle("tg://resolve?domain=space_channel") == "space_channel"
    # A short bot name is a username too, both in an address and bare.
    assert telegram_handle("https://t.me/gif") == "gif"
    assert telegram_handle("https://t.me/Gif") == "Gif"


def test_an_invite_link_names_no_username() -> None:
    assert telegram_handle("https://t.me/+AbCdEfGh") is None
    assert telegram_handle("https://t.me/joinchat/AbCdEfGh") is None
    assert telegram_handle("https://example.com/space_channel") is None


def test_a_global_link_is_named_by_its_domain() -> None:
    assert domain_name("https://www.google.com/search?q=1") == "google.com"
    assert domain_name("https://youtu.be/Q_hyCryvyuk") == "youtu.be"


# --------------------------------------------------------------------------- #
# The preview page
# --------------------------------------------------------------------------- #
# The page of a bot carries two «tgme_page_extra» lines: the username, then the
# count of its users. Reading only the first one made every bot look like a person.
_BOT_PAGE = """
<meta property="og:title" content="BotFather">
<div class="tgme_page_extra">
  @BotFather
</div>
<div class="tgme_page_extra">8 163 753 monthly users</div>
<div class="tgme_page_action">
  <a class="tgme_action_button_new shine" href="tg://resolve?domain=BotFather">Start Bot</a>
</div>
"""

# The same page served in another language, with no button to fall back on.
_RUSSIAN_BOT_PAGE = """
<meta property="og:title" content="Помощник">
<div class="tgme_page_extra">@pomoshchnik_bot</div>
<div class="tgme_page_extra">12 400 пользователей в месяц</div>
"""

_CHANNEL_PAGE = """
<meta property="og:title" content="Telegram News">
<div class="tgme_page_extra">@telegram</div>
<div class="tgme_page_extra">9 071 863 subscribers</div>
<a class="tgme_action_button_new shine" href="tg://resolve?domain=telegram">View in Telegram</a>
"""

_RUSSIAN_CHANNEL_PAGE = """
<meta property="og:title" content="Новости">
<div class="tgme_page_extra">9 071 863 подписчиков</div>
<a class="tgme_action_button_new shine" href="tg://resolve?domain=news">Открыть в Telegram</a>
"""

# No users line at all: only the button says what the account is.
_BUTTON_ONLY_BOT_PAGE = """
<meta property="og:title" content="Helper">
<div class="tgme_page_extra">@helper_bot</div>
<a class="tgme_action_button_new shine" href="tg://resolve?domain=helper_bot">Start Bot</a>
"""

_PERSON_PAGE = """
<meta property="og:title" content="Pavel Durov">
<div class="tgme_page_extra"></div>
<a class="tgme_action_button_new shine" href="tg://resolve?domain=durov">Send Message</a>
"""


def test_a_bot_page_names_a_bot() -> None:
    preview = parse_preview(_BOT_PAGE)
    assert preview is not None
    assert (preview.name, preview.is_bot) == ("BotFather", True)


def test_a_bot_page_in_another_language_is_still_a_bot() -> None:
    preview = parse_preview(_RUSSIAN_BOT_PAGE)
    assert preview is not None
    assert (preview.name, preview.is_bot) == ("Помощник", True)


def test_the_start_button_alone_names_a_bot() -> None:
    preview = parse_preview(_BUTTON_ONLY_BOT_PAGE)
    assert preview is not None
    assert (preview.name, preview.is_bot) == ("Helper", True)


def test_a_channel_page_is_not_a_bot() -> None:
    for page, name in (
        (_CHANNEL_PAGE, "Telegram News"),
        (_RUSSIAN_CHANNEL_PAGE, "Новости"),
    ):
        preview = parse_preview(page)
        assert preview is not None
        assert (preview.name, preview.is_bot) == (name, False)


def test_a_page_without_users_line_is_not_a_bot() -> None:
    preview = parse_preview(_PERSON_PAGE)
    assert preview is not None
    assert preview.is_bot is False


def test_a_page_without_a_title_is_unreadable() -> None:
    assert parse_preview("<html></html>") is None


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #
class _Bot:
    """The one Telegram call the resolver makes, with its answers scripted."""

    def __init__(self, chat=None, error: Exception | None = None) -> None:
        self.chat = chat
        self.error = error
        self.asked: list[str] = []

    async def get_chat(self, chat_id: str):
        self.asked.append(chat_id)
        if self.error is not None:
            raise self.error
        return self.chat


class _Resolver(LinkResolver):
    """A resolver whose preview page is scripted instead of fetched."""

    def __init__(self, pages: dict | None = None, bot=None, clock=None) -> None:
        super().__init__(bot, **({"clock": clock} if clock is not None else {}))
        self.pages = pages or {}
        self.fetched: list[str] = []

    async def _preview(self, handle: str):
        self.fetched.append(handle)
        return self.pages.get(handle.casefold())


def _chat_not_found() -> TelegramAPIError:
    return TelegramAPIError(GetChat(chat_id="@x"), "Bad Request: chat not found")


async def test_a_public_channel_is_named_by_its_title() -> None:
    bot = _Bot(chat=type("Chat", (), {"type": "channel", "title": "قناة البرمجة"})())
    resolver = _Resolver(bot=bot)

    info = await resolver.resolve("https://t.me/coding_channel")

    assert (info.name, info.is_bot, info.source) == ("قناة البرمجة", False, "telegram")
    assert bot.asked == ["@coding_channel"]
    assert resolver.fetched == []  # the chat answered, the page was not needed


async def test_a_bot_is_read_from_its_public_page() -> None:
    resolver = _Resolver(
        pages={"helper_bot": parse_preview(_BOT_PAGE)},
        bot=_Bot(error=_chat_not_found()),
    )

    info = await resolver.resolve("https://t.me/helper_bot")

    assert (info.name, info.is_bot, info.source) == ("BotFather", True, "preview")
    assert info.confirmed is True


async def test_a_short_username_is_read_from_its_page() -> None:
    resolver = _Resolver(
        pages={"gif": parse_preview(_BOT_PAGE)},
        bot=_Bot(error=_chat_not_found()),
    )

    info = await resolver.resolve("https://t.me/gif")

    assert (info.name, info.is_bot, info.confirmed) == ("BotFather", True, True)
    assert resolver.fetched == ["gif"]


async def test_an_unreadable_username_falls_back_to_the_username_itself() -> None:
    resolver = _Resolver(bot=_Bot(error=_chat_not_found()))

    info = await resolver.resolve("https://t.me/private_thing")

    assert (info.name, info.is_bot, info.source) == ("private_thing", False, "fallback")
    assert info.confirmed is False


async def test_an_invite_link_keeps_its_address() -> None:
    bot = _Bot(chat=None)
    resolver = _Resolver(bot=bot)

    info = await resolver.resolve("https://t.me/+AbCdEfGh")

    assert info.name is None
    assert bot.asked == []


async def test_a_global_link_is_named_by_its_domain() -> None:
    resolver = _Resolver()

    info = await resolver.resolve("https://www.google.com/search?q=space")

    assert (info.name, info.is_bot, info.source) == ("google.com", False, "domain")


async def test_a_name_is_read_once_and_reused() -> None:
    now = [1000.0]
    bot = _Bot(chat=type("Chat", (), {"type": "channel", "title": "Space"})())
    resolver = _Resolver(bot=bot, clock=lambda: now[0])

    await resolver.resolve("https://t.me/space_channel")
    now[0] += 60.0
    again = await resolver.resolve("https://t.me/space_channel")

    assert again.name == "Space"
    assert bot.asked == ["@space_channel"]

    # After the hour the name is read again, so a renamed channel is noticed.
    now[0] += 3600.0
    await resolver.resolve("https://t.me/space_channel")
    assert bot.asked == ["@space_channel", "@space_channel"]


async def test_a_failed_read_is_retried_sooner_than_a_name() -> None:
    now = [1000.0]
    resolver = _Resolver(bot=_Bot(error=_chat_not_found()), clock=lambda: now[0])

    await resolver.resolve("https://t.me/private_thing")
    now[0] += 60.0
    await resolver.resolve("https://t.me/private_thing")

    assert resolver.fetched == ["private_thing"]

    now[0] += 601.0
    await resolver.resolve("https://t.me/private_thing")
    assert resolver.fetched == ["private_thing", "private_thing"]

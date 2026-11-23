"""Readable names for the saved links.

A link is listed by its name, not by its address: «موقع جوجل» instead of the whole
URL, and «Helper | Bot» for a bot account. Two sources answer that name:

* ``getChat`` for a public channel or group, which gives its title directly.
* the public preview page (``t.me/<name>``) for everything else. It is needed
  because ``getChat`` reads a username only for a public channel or supergroup: a
  bot — or a person — is a private chat, and the Bot API answers «chat not found»
  for it. The page names the account and says «N monthly users» for a bot,
  «N subscribers» for a channel and «N members» for a group. The page is served
  in the visitor's language, so the line is not the only clue: a bot's page also
  offers a «Start Bot» button, which no channel or person has.

Reading a name is best effort, never a condition for saving a link: an unreachable
name leaves the link with the username (or the domain) as its label, exactly like a
fingerprint that could not be computed.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.utils.links import TELEGRAM, TELEGRAM_HOSTS, classify, is_username

logger = logging.getLogger(__name__)

# A name does not change every minute, so it is read once and reused. A fallback
# is retried sooner than a confirmed name, so a channel that becomes public later
# is picked up without asking Telegram about every message.
NAME_TTL_SECONDS = 60.0 * 60.0
MISS_TTL_SECONDS = 60.0 * 10.0
PREVIEW_TIMEOUT_SECONDS = 5.0
MAX_NAME_LENGTH = 120

_PREVIEW_URL = "https://t.me/{name}"
# Telegram's page answers a plain client; the header only says who is asking.
_USER_AGENT = "TG-Space-Bot/1.0 (+https://t.me)"

# The pieces of the preview page that matter: the name, the lines under it and
# the button at the bottom. A bot's page carries two lines, its username and then
# its users, so every one of them is read and not just the first.
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"', re.IGNORECASE)
_EXTRA_RE = re.compile(
    r'class="tgme_page_extra"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL
)
_ACTION_RE = re.compile(
    r'class="tgme_action_button[^"]*"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")

# A bot's users line, in the languages the page is commonly served in.
_BOT_EXTRA_MARKERS = (
    "monthly users",
    "пользовател",
    "مستخدم",
    "utilisateur",
    "benutzer",
    "usuarios",
)

# The word «bot» as the button spells it: «Start Bot», «Запустить бота», «بدء
# البوت». A channel's button says «View in Telegram» and a person's «Send
# Message», so the word is enough, and it survives any other translation.
_BOT_BUTTON_WORDS = ("bot", "бот", "بوت")


def _clean(name: object) -> str:
    """One line of at most ``MAX_NAME_LENGTH`` characters."""
    return " ".join(str(name or "").split())[:MAX_NAME_LENGTH]


@dataclass(frozen=True, slots=True)
class LinkInfo:
    """What a link should be called, and whether it is a bot.

    ``source`` says who answered: ``telegram`` (the chat itself), ``preview`` (the
    public page), ``domain`` (a global link's host) or ``fallback`` (nothing could
    be read, the label is the username itself). ``confirmed`` is what a caller
    checks before claiming a username really is a bot.
    """

    name: str | None = None
    is_bot: bool = False
    source: str = "fallback"

    @property
    def confirmed(self) -> bool:
        """True when Telegram itself named this link."""
        return self.source in {"telegram", "preview"}


@dataclass(frozen=True, slots=True)
class Preview:
    """The public preview page of a username."""

    name: str
    is_bot: bool = False


def _is_bot_page(page: str) -> bool:
    """Whether a preview page describes a bot, whatever language it came in."""
    extra = " ".join(
        _TAG_RE.sub(" ", match.group(1)) for match in _EXTRA_RE.finditer(page)
    ).casefold()
    if any(marker in extra for marker in _BOT_EXTRA_MARKERS):
        return True
    button = " ".join(
        _TAG_RE.sub(" ", match.group(1)) for match in _ACTION_RE.finditer(page)
    ).casefold()
    return any(word in button for word in _BOT_BUTTON_WORDS)


def parse_preview(page: str) -> Preview | None:
    """The name (and the bot flag) of a preview page, or None without a name."""
    title = _OG_TITLE_RE.search(page or "")
    name = _clean(html.unescape(title.group(1))) if title else ""
    if not name:
        return None
    # «N monthly users» is a bot; a channel says «subscribers» and a group
    # «members», and a person's page carries none of the three.
    return Preview(name=name, is_bot=_is_bot_page(page))


def telegram_handle(url: str) -> str | None:
    """The public username inside a Telegram link, when it has one.

    An invite link (``t.me/+hash``, ``t.me/joinchat/...``) names no chat, and a
    post link (``t.me/channel/12``) is read by its channel.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() == "tg":
        found = parse_qs(parts.query).get("domain") or []
        name = found[0] if found else ""
    else:
        if (parts.hostname or "").lower() not in TELEGRAM_HOSTS:
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            return None
        # «t.me/s/name» is the web preview of the same channel.
        name = segments[1] if segments[0].casefold() == "s" and len(segments) > 1 else segments[0]
        if name.startswith("+") or name.casefold() in {"joinchat", "s", "c"}:
            return None
    return name if is_username(name) else None


def domain_name(url: str) -> str | None:
    """The host of a global link: «google.com» for ``https://www.google.com/…``."""
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


class LinkResolver:
    """Names for links, read once and remembered for a while."""

    def __init__(
        self,
        bot: Bot | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._clock = clock
        # key -> (expires_at, info); the clock is injectable so a test can age an
        # entry instead of sleeping.
        self._entries: dict[str, tuple[float, LinkInfo]] = {}

    def bind_bot(self, bot: Bot) -> None:
        """Attach the bot once it exists (the resolver asks it for a chat title)."""
        self._bot = bot

    async def resolve(self, url: str) -> LinkInfo:
        """The name of one link; never raises, a failure is a fallback name."""
        if classify(url) != TELEGRAM:
            name = domain_name(url)
            return LinkInfo(name=name, source="domain" if name else "fallback")

        handle = telegram_handle(url)
        if handle is None:
            # An invite link: only the address itself describes it.
            return LinkInfo()
        return await self._telegram(handle)

    # ------------------------------------------------------------------ #
    # One username
    # ------------------------------------------------------------------ #
    async def _telegram(self, handle: str) -> LinkInfo:
        key = handle.casefold()
        cached = self._fresh(key)
        if cached is not None:
            return cached

        info = await self._read_telegram(handle)
        self._remember(
            key, info, NAME_TTL_SECONDS if info.confirmed else MISS_TTL_SECONDS
        )
        return info

    async def _read_telegram(self, handle: str) -> LinkInfo:
        """Ask the chat first, then the public page, then fall back to the handle."""
        chat = None
        if self._bot is not None:
            try:
                chat = await self._bot.get_chat("@" + handle)
            except TelegramAPIError:
                # A bot or a person is a private chat: the Bot API refuses to read
                # it by username, and the preview page is the only public source.
                logger.debug("getChat could not read @%s", handle, exc_info=True)

        if chat is not None and getattr(chat, "type", "") != "private":
            name = _clean(getattr(chat, "title", None) or getattr(chat, "first_name", ""))
            if name:
                return LinkInfo(name=name, source="telegram")

        preview = await self._preview(handle)
        if preview is not None:
            return LinkInfo(name=preview.name, is_bot=preview.is_bot, source="preview")
        return LinkInfo(name=handle, source="fallback")

    async def _preview(self, handle: str) -> Preview | None:
        """The public page of a username, or None when it cannot be read."""
        try:
            async with httpx.AsyncClient(
                timeout=PREVIEW_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = await client.get(_PREVIEW_URL.format(name=handle))
        except (httpx.HTTPError, ValueError):
            logger.warning("Could not read the page of @%s", handle, exc_info=True)
            return None
        if response.status_code != httpx.codes.OK:
            logger.info(
                "The page of @%s answered %s", handle, response.status_code
            )
            return None
        return parse_preview(response.text)

    # ------------------------------------------------------------------ #
    # Memory
    # ------------------------------------------------------------------ #
    def _fresh(self, key: str) -> LinkInfo | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[0] > self._clock():
            return entry[1]
        self._entries.pop(key, None)
        return None

    def _remember(self, key: str, info: LinkInfo, ttl: float) -> None:
        self._entries[key] = (self._clock() + ttl, info)


__all__ = [
    "LinkInfo",
    "LinkResolver",
    "Preview",
    "domain_name",
    "parse_preview",
    "telegram_handle",
]

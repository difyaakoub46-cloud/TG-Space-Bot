"""Recognise the links inside a message and sort them into the two lists.

A message may carry one link, several, or a link next to a sentence. The bot
saves each of them on its own, and the screen has two sections, so every link is
classified here instead of at the screen:

* ``telegram`` — a Telegram link (``t.me/...``, ``telegram.me/...``, ``tg://...``)
* ``global``   — any other ``http``/``https`` link

The URL is normalised (scheme and host in lower case, no trailing punctuation)
before it is used as the identity of the link: the database keeps one row per
URL, so a link typed twice must produce the same string both times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

TELEGRAM = "telegram"
GLOBAL = "global"

# The two sections of the links screen, in the order they are shown.
LINK_TYPES: tuple[str, ...] = (TELEGRAM, GLOBAL)

# Telegram's public hosts. Anything else is a global link.
TELEGRAM_HOSTS = frozenset(
    {"t.me", "telegram.me", "telegram.dog", "www.t.me", "www.telegram.me"}
)

# A link starts with a scheme, or with one of Telegram's hosts (people often
# paste «t.me/name» without «https://»). The lookbehind keeps the bare form from
# matching inside a longer address or an e-mail.
_LINK_RE = re.compile(
    r"(?i)(?:https?://|tg://)[^\s<>\"']+"
    r"|(?<![\w./@])(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>\"']+"
)

# Sentence punctuation that ends up glued to a pasted link, Latin and Arabic.
_TRAILING = " \t\r\n.,;:!?…'\"”’»«>)]}،؛؟"


@dataclass(frozen=True, slots=True)
class Link:
    """One link, with the section of the screen it belongs to."""

    url: str
    link_type: str

    @property
    def is_telegram(self) -> bool:
        return self.link_type == TELEGRAM


def _clean(raw: str) -> str:
    return raw.strip().rstrip(_TRAILING)


def _normalise(raw: str) -> str:
    """The one spelling of a link, so the same link is never stored twice."""
    url = _clean(raw)
    if not url:
        return ""
    lowered = url.lower()
    if lowered.startswith("tg://"):
        return "tg://" + url[len("tg://") :]
    if not lowered.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc:
        return url
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query,
         parts.fragment)
    )


def classify(url: str) -> str:
    """``telegram`` for a Telegram link, ``global`` for everything else."""
    lowered = url.lower()
    if lowered.startswith("tg://"):
        return TELEGRAM
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return GLOBAL
    return TELEGRAM if host.lower() in TELEGRAM_HOSTS else GLOBAL


def _has_host(url: str) -> bool:
    """Whether a web address really names a host.

    «https://» followed by a word is not a link: the text after the scheme has to
    look like a domain (a dot inside it), so a sentence that happens to start with
    a scheme is not saved as if it were an address.
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return "." in host and not host.startswith(".") and not host.endswith(".")


def extract_links(text: str | None) -> list[Link]:
    """Every link of a message, de-duplicated, in the order they appear."""
    if not text:
        return []
    found: dict[str, Link] = {}
    for match in _LINK_RE.finditer(text):
        url = _normalise(match.group(0))
        if not url or url in found:
            continue
        link_type = classify(url)
        if link_type == GLOBAL and not _has_host(url):
            continue
        found[url] = Link(url=url, link_type=link_type)
    return list(found.values())


__all__ = [
    "GLOBAL",
    "LINK_TYPES",
    "Link",
    "TELEGRAM",
    "TELEGRAM_HOSTS",
    "classify",
    "extract_links",
]

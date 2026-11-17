"""The bot's two languages, and the per-update language context.

English is the default. The chosen language is stored per user (see
:mod:`src.services.preferences`) and installed by the middleware for the duration
of one update, so every text builder can call :func:`pick` without the language
being threaded through a dozen signatures.
"""

from __future__ import annotations

from contextvars import ContextVar
from enum import Enum


class Language(str, Enum):
    en = "en"
    ar = "ar"


DEFAULT_LANGUAGE = Language.en

_current: ContextVar[Language] = ContextVar("bot_language", default=DEFAULT_LANGUAGE)


def parse_language(value: object) -> Language:
    """A stored or callback value as a Language; anything unknown means English.

    The enum itself is returned as-is: ``str()`` on a ``str``-enum member gives
    ``"Language.ar"`` on Python 3.11+, which would not parse back.
    """
    if isinstance(value, Language):
        return value
    try:
        return Language(str(value or "").strip().lower())
    except ValueError:
        return DEFAULT_LANGUAGE


def get_language() -> Language:
    return _current.get()


def set_language(language: Language | str) -> None:
    _current.set(parse_language(language))


def reset_language() -> None:
    _current.set(DEFAULT_LANGUAGE)


def pick(english: str, arabic: str) -> str:
    """The text for the active language, written at the call site.

    Keeping both versions side by side (instead of a key catalogue) means a
    screen never shows half a translation: whoever edits a line sees both.
    """
    return arabic if _current.get() is Language.ar else english


def language_name(language: Language) -> str:
    """The name of a language, always written in that language."""
    return "العربية" if language is Language.ar else "English"


__all__ = [
    "DEFAULT_LANGUAGE",
    "Language",
    "get_language",
    "language_name",
    "parse_language",
    "pick",
    "reset_language",
    "set_language",
]

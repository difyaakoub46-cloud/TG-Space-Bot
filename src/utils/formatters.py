"""Text formatting helpers."""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone

# Size units stay in Latin letters in both languages: only the surrounding text
# is translated, so a size reads the same wherever it appears.
_UNITS = ("B", "KB", "MB", "GB", "TB")


# A file button keeps one line: a long name is cut in the middle instead of being
# wrapped by Telegram, so every row of the panel has the same height.
NAME_HEAD = 12
NAME_TAIL = 8


def escape_text(value: object) -> str:
    """Escape a value for Telegram's HTML parse mode.

    Quotes are left alone on purpose: the text always goes inside a message body,
    never inside an attribute, and a file name reads better with its quotes.
    """
    return html.escape(str(value), quote=False)


def truncate_name(
    name: object, head: int = NAME_HEAD, tail: int = NAME_TAIL
) -> str:
    """The first ``head`` and the last ``tail`` characters of a file name.

    ``tg-space-bot-deploy-7e9a560-2026-11-15-1432.log`` becomes
    ``tg-space-bot...1432.log``: the beginning still names the file and the end
    still gives its extension, while the label never grows past a single line.
    A name short enough to fit is returned unchanged.
    """
    text = " ".join(str(name or "").split())
    if len(text) <= head + tail + 3:
        return text
    return f"{text[:head]}...{text[-tail:]}"


def keep_extension(new_name: str, old_name: str) -> str:
    """Give a renamed file the extension it already had.

    Renaming ``Name.pdf`` to ``Noma`` must not lose the type of the file, so the
    old extension is appended unless the new name already ends with it. A new
    name that carries another extension keeps both, and an old name without an
    extension leaves the new name untouched.
    """
    extension = os.path.splitext(str(old_name or ""))[1]
    if not extension:
        return new_name
    if os.path.splitext(new_name)[1].casefold() == extension.casefold():
        return new_name
    return f"{new_name}{extension}"


def format_number(n: int) -> str:
    return f"{n:,}"


def human_size(size: int | float | None) -> str:
    """Render a byte count as a compact, human readable string."""
    value = float(size or 0)
    unit_index = 0
    while value >= 1024 and unit_index < len(_UNITS) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {_UNITS[unit_index]}"
    return f"{value:.1f} {_UNITS[unit_index]}"


def human_total(size: int | float | None) -> str:
    """Aggregate size: megabytes by default, gigabytes once it is large."""
    value = float(size or 0)
    if value <= 0:
        return "0 MB"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{int(value)} B"


def format_datetime(value: datetime | None) -> str:
    """Render a timestamp in UTC, stable across platforms."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

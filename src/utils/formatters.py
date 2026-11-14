"""Small formatting helpers shared by services and views."""

from __future__ import annotations

import html
from datetime import datetime, timezone

_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(size_bytes: int | None) -> str:
    """Return a compact human readable size, e.g. ``1.4 MB``."""
    size = float(size_bytes or 0)
    if size < 1024:
        return f"{int(size)} B"
    for unit in _UNITS[1:]:
        size /= 1024
        if size < 1024 or unit == _UNITS[-1]:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"


def format_datetime(value: datetime | None) -> str:
    """Format a timestamp in UTC as ``YYYY-MM-DD HH:MM UTC``."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def truncate(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def escape(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def clip_button(text: str, limit: int = 45) -> str:
    """Buttons are single-line; normalize whitespace and truncate."""
    return truncate(text, limit)

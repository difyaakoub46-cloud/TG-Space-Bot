"""Text formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone

# Size units stay in Latin letters in both languages: only the surrounding text
# is translated, so a size reads the same wherever it appears.
_UNITS = ("B", "KB", "MB", "GB", "TB")


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

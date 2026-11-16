"""Sorting rules for file lists (library categories and folders).

The rules live here, away from the bot layer, so the same order is used by the
keyboard labels, the rendered text and the database-facing service.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.db.models import StoredFile

# Arabic diacritics and the tatweel are ignored when comparing names, and the
# alef/ya/ta-marbuta variants are folded so «أحمد» and «احمد» sort together.
_ARABIC_NOISE = re.compile(r"[\u0640\u064b-\u0652\u0670]")
_LETTER_FOLDS = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"}
)


class SortField(str, Enum):
    size = "size"
    date = "date"
    name = "name"


class SortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


# Most useful first impression: the freshest files on top.
DEFAULT_FIELD = SortField.date
DEFAULT_DIRECTION = SortDirection.desc


def name_key(value: str) -> str:
    """Case- and diacritic-insensitive key that works for Arabic and Latin."""
    cleaned = _ARABIC_NOISE.sub("", value or "").translate(_LETTER_FOLDS)
    return cleaned.casefold()


@dataclass(frozen=True, slots=True)
class SortSpec:
    """One complete ordering: a field plus a direction."""

    field: SortField = DEFAULT_FIELD
    direction: SortDirection = DEFAULT_DIRECTION

    @classmethod
    def parse(cls, field: Any, direction: Any) -> "SortSpec":
        try:
            parsed_field = SortField(str(field))
        except ValueError:
            parsed_field = DEFAULT_FIELD
        try:
            parsed_direction = SortDirection(str(direction))
        except ValueError:
            parsed_direction = DEFAULT_DIRECTION
        return cls(parsed_field, parsed_direction)

    @classmethod
    def from_screen(cls, screen: dict[str, Any] | None) -> "SortSpec":
        """Read the sort of a screen key (``{"k": "category", "s": ..., "d": ...}``)."""
        screen = screen or {}
        return cls.parse(screen.get("s"), screen.get("d"))

    def to_screen(self) -> dict[str, str]:
        """The part of a screen key that remembers this ordering."""
        return {"s": self.field.value, "d": self.direction.value}


def sort_files(files: Iterable[StoredFile], spec: SortSpec) -> list[StoredFile]:
    """Order files by name, size or date, ascending or descending."""
    if spec.field is SortField.size:
        key = lambda item: (item.size_bytes, name_key(item.file_name), item.id)
    elif spec.field is SortField.name:
        key = lambda item: (name_key(item.file_name), item.file_name.casefold(), item.id)
    else:
        key = lambda item: (item.created_at, name_key(item.file_name), item.id)
    return sorted(files, key=key, reverse=spec.direction is SortDirection.desc)


def database_order(spec: SortSpec) -> str:
    """PostgREST ``order`` matching the requested sort (used before the cap)."""
    column = {
        SortField.size: "file_size",
        SortField.date: "created_at",
        SortField.name: "file_name",
    }[spec.field]
    return f"{column}.{spec.direction.value}"


__all__ = [
    "SortDirection",
    "SortField",
    "SortSpec",
    "database_order",
    "name_key",
    "sort_files",
]

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


@dataclass(frozen=True, slots=True)
class SortChoice:
    """One of the six orderings: its key, its rule and its button label.

    The label is bilingual here and rendered with ``pick`` in the bot layer, the
    same way a category or a period is named, so the six orderings are written
    once and every panel shows the same six names.
    """

    key: str
    spec: SortSpec
    labels: tuple[str, str]


# The freshest files on top is the first entry, and the default of a new list.
SORT_CHOICES: tuple[SortChoice, ...] = (
    SortChoice(
        "newest",
        SortSpec(SortField.date, SortDirection.desc),
        ("🕐 Newest first", "🕐 الأحدث أولاً"),
    ),
    SortChoice(
        "oldest",
        SortSpec(SortField.date, SortDirection.asc),
        ("🕐 Oldest first", "🕐 الأقدم أولاً"),
    ),
    SortChoice(
        "size_asc",
        SortSpec(SortField.size, SortDirection.asc),
        ("📦 Smallest first", "📦 الأصغر أولاً"),
    ),
    SortChoice(
        "size_desc",
        SortSpec(SortField.size, SortDirection.desc),
        ("📦 Largest first", "📦 الأكبر أولاً"),
    ),
    SortChoice(
        "name_asc",
        SortSpec(SortField.name, SortDirection.asc),
        ("🔤 A→Z / أ→ي", "🔤 أ→ي / A→Z"),
    ),
    SortChoice(
        "name_desc",
        SortSpec(SortField.name, SortDirection.desc),
        ("🔤 Z→A / ي→أ", "🔤 ي→أ / Z→A"),
    ),
)

# The keys as they travel in callback data, in the order the panels list them.
SORT_KEYS: tuple[str, ...] = tuple(choice.key for choice in SORT_CHOICES)

_SORT_SPECS: dict[str, SortSpec] = {
    choice.key: choice.spec for choice in SORT_CHOICES
}


def normalise_sort(value: Any) -> str:
    """A sort key that is safe to put in a screen or a callback."""
    key = str(value or "").strip().lower()
    return key if key in _SORT_SPECS else SORT_KEYS[0]


def sort_from_key(key: Any) -> SortSpec:
    """The ordering a short sort key stands for (newest first by default)."""
    return _SORT_SPECS[normalise_sort(key)]


def sort_choice(spec: SortSpec) -> SortChoice:
    """The choice an ordering stands for (newest first when it is unknown)."""
    for choice in SORT_CHOICES:
        if choice.spec == spec:
            return choice
    return SORT_CHOICES[0]


def sort_key(spec: SortSpec) -> str:
    """The short key of an ordering, for a screen or a callback."""
    for key, candidate in _SORT_SPECS.items():
        if candidate == spec:
            return key
    return SORT_KEYS[0]


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
    "SORT_CHOICES",
    "SORT_KEYS",
    "SortChoice",
    "SortDirection",
    "SortField",
    "SortSpec",
    "database_order",
    "name_key",
    "normalise_sort",
    "sort_choice",
    "sort_files",
    "sort_from_key",
    "sort_key",
]

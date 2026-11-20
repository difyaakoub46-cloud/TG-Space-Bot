"""Database row models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from src.utils.filetypes import FileCategory, parse_category
from src.utils.formatters import format_datetime, human_size, human_total


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class Folder:
    """A virtual folder: a label over files that stay in their storage groups."""

    id: str
    name: str
    count: int = 0
    bytes: int = 0
    favorite: bool = False
    favorite_order: int | None = None

    @property
    def size_human(self) -> str:
        return human_total(self.bytes)

    @property
    def favorite_sort_key(self) -> tuple[bool, int, str]:
        """Order favourites by their manual position, then alphabetically."""
        return (self.favorite_order is None, self.favorite_order or 0, self.name.casefold())


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A file tracked in Supabase and physically stored in a Telegram group."""

    id: str
    file_id: str
    file_unique_id: str | None
    file_name: str
    category: FileCategory
    mime_type: str | None
    size_bytes: int
    uploader_id: int | None
    storage_chat_id: int
    storage_message_id: int
    created_at: datetime
    # SHA-256 of the file bytes; empty when the file was too large to read.
    content_hash: str | None = None
    # A row is either a file (no link columns) or a saved link, whose section is
    # ``telegram`` or ``global``.
    link_type: str | None = None
    link_url: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredFile":
        """Reconstruct a StoredFile from a PostgREST row.

        The remote table keeps the physical columns ``chat_id``,
        ``message_id``, ``user_id``, ``file_size`` and ``file_type`` (the MIME
        string). The app-facing property names (``storage_chat_id``,
        ``storage_message_id``, ``uploader_id``, ``size_bytes``, ``mime_type``)
        are preserved for callers.
        """
        return cls(
            id=str(row["id"]),
            file_id=str(row.get("file_id") or ""),
            file_unique_id=row.get("file_unique_id"),
            file_name=str(row.get("file_name") or "بدون اسم"),
            category=parse_category(row.get("category")),
            mime_type=row.get("file_type"),
            size_bytes=int(row.get("file_size") or 0),
            uploader_id=int(row["user_id"]) if row.get("user_id") is not None else None,
            storage_chat_id=int(row["chat_id"]),
            storage_message_id=int(row["message_id"]),
            created_at=_parse_datetime(row.get("created_at")),
            content_hash=row.get("content_hash"),
            link_type=row.get("link_type"),
            link_url=row.get("link_url"),
        )

    @property
    def is_link(self) -> bool:
        """True for a saved link, false for a file."""
        return self.link_type is not None

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)

    @property
    def created_human(self) -> str:
        return format_datetime(self.created_at)

    @property
    def hash_short(self) -> str | None:
        """Short form of the content fingerprint, for display."""
        return f"{self.content_hash[:16]}…" if self.content_hash else None

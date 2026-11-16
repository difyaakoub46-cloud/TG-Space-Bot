"""The emergency registry of the files held in the fallback group.

When Supabase refuses a registration the file is copied into
``TG_STORAGE_CHAT_FALLBACK`` instead of the library, and it is remembered here.

The registry is a small JSON file because Telegram's Bot API cannot list a chat's
history: without it the held copies would be unreachable and nothing could ever be
repaired. Point ``PENDING_STORE_PATH`` at a persistent disk (a Render disk) so the
registry also survives a restart or a redeploy; on an ephemeral filesystem it is
lost with the container while the copies stay in the group.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.filetypes import FileCategory, parse_category

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PendingFile:
    """One file waiting in the fallback group for its registration."""

    chat_id: int
    message_id: int
    file_name: str
    mime_type: str
    size_bytes: int
    category: FileCategory
    file_id: str = ""
    file_unique_id: str | None = None
    uploader_id: int | None = None
    reason: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["category"] = self.category.value
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PendingFile":
        return cls(
            chat_id=int(row.get("chat_id") or 0),
            message_id=int(row.get("message_id") or 0),
            file_name=str(row.get("file_name") or "ملف"),
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            size_bytes=int(row.get("size_bytes") or 0),
            category=parse_category(row.get("category")),
            file_id=str(row.get("file_id") or ""),
            file_unique_id=row.get("file_unique_id") or None,
            uploader_id=row.get("uploader_id"),
            reason=str(row.get("reason") or ""),
            created_at=str(row.get("created_at") or ""),
        )


class PendingStore:
    """File-backed list of :class:`PendingFile`, safe against a corrupt file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: list[PendingFile] = []

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        """Read the registry once; a missing or broken file means an empty one."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "The pending registry at %s is unreadable; starting empty",
                self._path,
                exc_info=True,
            )
            return
        rows = raw.get("files") if isinstance(raw, dict) else raw
        entries: list[PendingFile] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                entries.append(PendingFile.from_row(row))
            except (TypeError, ValueError):
                logger.warning("Skipped an invalid pending entry: %s", row)
        self._entries = entries
        if entries:
            logger.warning(
                "%s file(s) are waiting in the fallback group for a repair",
                len(entries),
            )

    def _write(self) -> None:
        """Write the registry atomically so a crash cannot truncate it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"files": [entry.to_row() for entry in self._entries]},
            ensure_ascii=False,
            indent=2,
        )
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self._path)

    async def add(self, entry: PendingFile) -> None:
        self._entries = [
            kept
            for kept in self._entries
            if (kept.chat_id, kept.message_id) != (entry.chat_id, entry.message_id)
        ]
        self._entries.append(entry)
        self._write()

    async def remove(self, chat_id: int, message_id: int) -> bool:
        before = len(self._entries)
        self._entries = [
            kept
            for kept in self._entries
            if (kept.chat_id, kept.message_id) != (int(chat_id), int(message_id))
        ]
        if len(self._entries) == before:
            return False
        self._write()
        return True

    async def all(self) -> list[PendingFile]:
        return list(self._entries)

    @property
    def total(self) -> int:
        return len(self._entries)

    def stats(self) -> dict[FileCategory, int]:
        """How many held files fall into each category."""
        counts: dict[FileCategory, int] = {}
        for entry in self._entries:
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return counts

    def latest(self) -> PendingFile | None:
        return self._entries[-1] if self._entries else None


__all__ = ["PendingFile", "PendingStore"]

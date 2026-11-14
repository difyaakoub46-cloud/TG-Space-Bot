"""Async Supabase (PostgREST) repository for file metadata."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Mapping

import httpx

from src.core.config import Settings
from src.db.models import StoredFile
from src.utils.filetypes import FileCategory

logger = logging.getLogger(__name__)

_UNSAFE_QUERY_CHARS = re.compile(r"[^0-9A-Za-z\u0600-\u06FF .\-]")
_MAX_QUERY_LENGTH = 80


class SupabaseError(RuntimeError):
    """Raised when the Supabase REST API answers with an error."""


def _sanitize_query(value: str) -> str:
    cleaned = _UNSAFE_QUERY_CHARS.sub(" ", value)
    return re.sub(r"\s+", " ", cleaned).strip()[:_MAX_QUERY_LENGTH]


def _parse_total(content_range: str | None) -> int:
    """Extract the total row count from a ``Content-Range`` header."""
    if not content_range or "/" not in content_range:
        return 0
    total = content_range.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else 0


class FileRepository:
    """Thin, fully asynchronous client for the ``files`` table."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._endpoint = f"{settings.supabase_url}/rest/v1/{settings.supabase_table}"

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        key = self._settings.supabase_key.get_secret_value()
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def _request(self, method: str, *, params: Mapping[str, str] | None = None,
                       json: Any = None, prefer: str | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = await self._client.request(
                    method,
                    self._endpoint,
                    params=params,
                    json=json,
                    headers=self._headers(prefer),
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code >= 400:
                raise SupabaseError(
                    f"{method} {self._endpoint} failed with {response.status_code}: "
                    f"{response.text[:300]}"
                )
            return response

        raise SupabaseError(f"Supabase request failed: {last_error}")

    @staticmethod
    def _rows(response: httpx.Response) -> list[dict[str, Any]]:
        if not response.content:
            return []
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        return []

    async def create(self, **fields: Any) -> StoredFile:
        """Insert a file row using the real table column names.

        The application passes app-level names (``storage_chat_id``,
        ``storage_message_id``, ``uploader_id``, ``mime_type``, ``size_bytes``);
        this remaps them to the physical columns so PostgREST never sees an unknown
        column (which would raise 42703).
        """
        mapped: dict[str, Any] = dict(fields)

        # size
        if "size_bytes" in mapped and "file_size" not in mapped:
            mapped["file_size"] = mapped.pop("size_bytes")

        # storage location (real table: chat_id / message_id)
        if "storage_chat_id" in mapped and "chat_id" not in mapped:
            mapped["chat_id"] = mapped.pop("storage_chat_id")
        if "storage_message_id" in mapped and "message_id" not in mapped:
            mapped["message_id"] = mapped.pop("storage_message_id")

        # owner (real table: user_id)
        if "uploader_id" in mapped and "user_id" not in mapped:
            mapped["user_id"] = mapped.pop("uploader_id")

        # detailed type (real table: file_type, not mime_type)
        if "mime_type" in mapped and "file_type" not in mapped:
            mapped["file_type"] = mapped.pop("mime_type")

        response = await self._request(
            "POST", json=mapped, prefer="return=representation"
        )
        rows = self._rows(response)
        if not rows:
            raise SupabaseError("Supabase did not return the inserted file row")
        return StoredFile.from_row(rows[0])

    async def get(self, file_id: str) -> StoredFile | None:
        response = await self._request(
            "GET", params={"select": "*", "id": f"eq.{file_id}", "limit": "1"}
        )
        rows = self._rows(response)
        return StoredFile.from_row(rows[0]) if rows else None

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[StoredFile], int]:
        params: dict[str, str] = {
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit),
            "offset": str(offset),
        }
        if category is not None:
            params["category"] = f"eq.{category.value}"
        if query:
            cleaned = _sanitize_query(query)
            if cleaned:
                params["file_name"] = f"ilike.*{cleaned}*"

        response = await self._request(
            "GET", params=params, prefer="count=exact"
        )
        total = _parse_total(response.headers.get("content-range"))
        return [StoredFile.from_row(row) for row in self._rows(response)], total

    async def count(self, *, category: FileCategory | None = None) -> int:
        params: dict[str, str] = {"select": "id", "limit": "1"}
        if category is not None:
            params["category"] = f"eq.{category.value}"
        response = await self._request(
            "HEAD", params=params, prefer="count=exact"
        )
        return _parse_total(response.headers.get("content-range"))

    async def category_counts(self) -> dict[str, int]:
        categories = list(FileCategory)
        results = await asyncio.gather(*(self.count(category=cat) for cat in categories))
        return {cat.value: total for cat, total in zip(categories, results)}

    async def delete(self, file_id: str) -> StoredFile | None:
        response = await self._request(
            "DELETE",
            params={"id": f"eq.{file_id}"},
            prefer="return=representation",
        )
        rows = self._rows(response)
        return StoredFile.from_row(rows[0]) if rows else None

    async def total_size(self) -> int | None:
        """Total storage bytes maintained live by the ``files_stats`` trigger.

        Queried from the **files_stats** table (not ``files``), because that is the
        single-row counter updated by the ``files_sync_stats`` trigger. This avoids both
        disallowed PostgREST aggregates (PGRST123) and any ambiguity with the
        ``files`` table.
        """
        stats_endpoint = f"{self._settings.supabase_url}/rest/v1/files_stats"
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = await self._client.request(
                    "GET",
                    stats_endpoint,
                    params={"select": "value", "setting": "eq.total_bytes", "limit": "1"},
                    headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code >= 400:
                raise SupabaseError(
                    f"GET {stats_endpoint} (files_stats) failed with "
                    f"{response.status_code}: {response.text[:300]}"
                )
            break
        else:
            raise SupabaseError(f"files_stats request failed: {last_error}")  # pragma: no cover

        rows = self._rows(response)
        if not rows:
            logger.warning("files_stats.total_bytes row is missing; run migration 002")
            return None
        value = rows[0].get("value")
        return int(value) if value is not None else None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

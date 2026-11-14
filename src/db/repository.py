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

# PostgREST treats these characters as filter/pattern syntax; strip them so a
# user supplied search term can never change the shape of the query.
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

    # ── plumbing ────────────────────────────────
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

    # ── public API ──────────────────────────────
    async def create(self, **fields: Any) -> StoredFile:
        response = await self._request(
            "POST", json=fields, prefer="return=representation"
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
        """Sum stored bytes using a PostgREST aggregate; ``None`` if unsupported."""
        try:
            response = await self._request(
                "GET", params={"select": "size_bytes.sum()"}
            )
        except SupabaseError as exc:
            logger.warning("Aggregate query failed: %s", exc)
            return None

        rows = self._rows(response)
        if not rows:
            return 0
        row = rows[0]
        for key in ("sum", "size_bytes_sum", "sum_size_bytes"):
            value = row.get(key)
            if value is not None:
                return int(value)
        return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

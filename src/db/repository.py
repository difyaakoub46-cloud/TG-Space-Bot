"""Async Supabase (PostgREST) repository for file metadata."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import date, datetime
from typing import Any, Mapping

import httpx

from src.core.config import Settings
from src.db.models import Folder, StoredFile
from src.utils.filetypes import FileCategory, parse_category

logger = logging.getLogger(__name__)

_UNSAFE_QUERY_CHARS = re.compile(r"[^0-9A-Za-z\u0600-\u06FF .\-]")
_MAX_QUERY_LENGTH = 80

# One row of ``folders_overview``: the folder itself plus its totals.
_FOLDER_FIELDS = "id,name,is_favorite,favorite_order,files,bytes"

# A 429 or a 5xx is PostgREST being busy or briefly restarting, not a reason to
# fail an upload: the same request is worth two more tries with a growing pause.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5


def _is_worth_retrying(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _retry_delay(attempt: int) -> float:
    return _RETRY_BASE_DELAY * attempt


class SupabaseError(RuntimeError):
    """Raised when the Supabase REST API answers with an error."""


def _sanitize_query(value: str) -> str:
    cleaned = _UNSAFE_QUERY_CHARS.sub(" ", value)
    return re.sub(r"\s+", " ", cleaned).strip()[:_MAX_QUERY_LENGTH]


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


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
        headers = {"apikey": key, "Accept": "application/json"}
        # New Supabase keys (sb_secret_... / sb_publishable_...) are opaque tokens,
        # not JWTs. PostgREST tries to parse anything on Authorization as a JWT, so
        # sending such a key there makes every request fail with 401. Legacy
        # service_role / anon keys are JWTs (they start with "eyJ") and still go on
        # both headers.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _url(self, path: str | None) -> str:
        return self._endpoint if path is None else f"{self._settings.supabase_url}/rest/v1/{path}"

    async def _request(self, method: str, *, path: str | None = None,
                       params: Mapping[str, str] | None = None,
                       json: Any = None, prefer: str | None = None) -> httpx.Response:
        url = self._url(path)
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._headers(prefer),
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == _MAX_ATTEMPTS:
                    break
                await asyncio.sleep(_retry_delay(attempt))
                continue

            if _is_worth_retrying(response.status_code) and attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "PostgREST answered %s; retrying (%s of %s)",
                    response.status_code,
                    attempt,
                    _MAX_ATTEMPTS - 1,
                )
                await asyncio.sleep(_retry_delay(attempt))
                continue

            if response.status_code >= 400:
                raise SupabaseError(
                    f"{method} {url} failed with {response.status_code}: "
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

    async def find_by_unique_id(self, file_unique_id: str) -> StoredFile | None:
        """The row already holding this exact Telegram file, if any."""
        if not file_unique_id:
            return None
        response = await self._request(
            "GET",
            params={
                "select": "*",
                "file_unique_id": f"eq.{file_unique_id}",
                "limit": "1",
            },
        )
        rows = self._rows(response)
        return StoredFile.from_row(rows[0]) if rows else None

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str = "created_at.desc",
    ) -> tuple[list[StoredFile], int]:
        params: dict[str, str] = {
            "select": "*",
            "order": order,
            "limit": str(limit),
            "offset": str(offset),
        }
        if category is not None:
            params["category"] = f"eq.{category.value}"
        if created_after is not None and created_before is not None:
            # A closed window for «History uploads»: PostgREST needs both bounds on
            # the same column as one `and`, since a dict cannot repeat `created_at`.
            params["and"] = (
                f"(created_at.gte.{created_after.isoformat()},"
                f"created_at.lt.{created_before.isoformat()})"
            )
        elif created_after is not None:
            # A date window for «History uploads»: only files saved since then.
            params["created_at"] = f"gte.{created_after.isoformat()}"
        elif created_before is not None:
            # Everything saved strictly before an instant (the end of a period).
            params["created_at"] = f"lt.{created_before.isoformat()}"
        if query:
            cleaned = _sanitize_query(query)
            if cleaned:
                params["file_name"] = f"ilike.*{cleaned}*"

        # `count=exact` makes Postgres read every matching row just to report the
        # total, on every page: that is the heavy query behind a slow «next page».
        # `count=planned` takes the same number from the query plan instead, so a
        # page opens without counting the library; the total is then an estimate
        # (accurate enough for the page count and the result counter).
        response = await self._request(
            "GET", params=params, prefer="count=planned"
        )
        rows = self._rows(response)
        total = _parse_total(response.headers.get("content-range"))
        # A plan that underestimates must not hide the rows it just returned: the
        # page on screen always counts itself, so the pager never treats it as gone.
        total = max(total, offset + len(rows))
        return [StoredFile.from_row(row) for row in rows], total

    async def created_dates(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[datetime]:
        """The ``created_at`` of the newest rows, for the period picker.

        Only the date column travels, newest first: PostgREST has no ``DISTINCT``,
        so the picker folds these days into periods itself.
        """
        params: dict[str, str] = {
            "select": "created_at",
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

        response = await self._request("GET", params=params)
        dates: list[datetime] = []
        for row in self._rows(response):
            try:
                dates.append(datetime.fromisoformat(str(row["created_at"])))
            except (KeyError, ValueError):
                continue
        return dates

    async def period_counts(
        self,
        *,
        span: str = "day",
        query: str | None = None,
        category: FileCategory | None = None,
        limit: int = 24,
        zone_offset: int = 0,
    ) -> list[tuple[date, int]]:
        """The periods that hold a file and how many, newest first, in one query.

        The grouping and the counting happen inside Postgres (``files_periods``):
        ``select date(created_at), count(*) ... group by date(created_at)`` for a
        day, its week/month/year truncation otherwise. One request returns every
        period with its own count, so the picker never reads the matching rows and
        never asks the count of each period separately. ``zone_offset`` is how many
        minutes the user's zone is ahead of UTC, so a period starts at the local
        midnight instead of UTC's.
        """
        payload: dict[str, Any] = {
            "p_span": span,
            "p_query": _sanitize_query(query) if query else None,
            "p_category": category.value if category is not None else None,
            "p_limit": max(1, int(limit)),
            "p_offset": int(zone_offset),
        }
        response = await self._request(
            "POST", path="rpc/files_periods", json=payload
        )
        periods: list[tuple[date, int]] = []
        for row in self._rows(response):
            try:
                anchor = datetime.fromisoformat(str(row.get("anchor"))).date()
            except (TypeError, ValueError):
                continue
            periods.append((anchor, int(row.get("files") or 0)))
        return periods

    async def count(self, *, category: FileCategory | None = None) -> int:
        params: dict[str, str] = {"select": "id", "limit": "1"}
        if category is not None:
            params["category"] = f"eq.{category.value}"
        response = await self._request(
            "HEAD", params=params, prefer="count=exact"
        )
        return _parse_total(response.headers.get("content-range"))

    async def category_counts(self) -> dict[FileCategory, int]:
        categories = list(FileCategory)
        results = await asyncio.gather(*(self.count(category=cat) for cat in categories))
        return {cat: total for cat, total in zip(categories, results)}

    async def category_totals(self) -> dict[FileCategory, tuple[int, int]]:
        """Files and bytes per category, read from the ``files_by_category`` view."""
        try:
            response = await self._request(
                "GET",
                path="files_by_category",
                params={"select": "category,files,bytes"},
            )
        except SupabaseError:
            logger.warning(
                "files_by_category is unavailable; falling back to counts only",
                exc_info=True,
            )
            counts = await self.category_counts()
            return {category: (total, 0) for category, total in counts.items()}

        totals = {category: (0, 0) for category in FileCategory}
        for row in self._rows(response):
            category = parse_category(row.get("category"))
            totals[category] = (
                int(row.get("files") or 0),
                int(row.get("bytes") or 0),
            )
        return totals

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
        response = await self._request(
            "GET",
            path="files_stats",
            params={"select": "value", "setting": "eq.total_bytes", "limit": "1"},
        )

        rows = self._rows(response)
        if not rows:
            logger.warning("files_stats.total_bytes row is missing; run migration 002")
            return None
        value = rows[0].get("value")
        return int(value) if value is not None else None

    async def rename(self, file_id: str, name: str) -> StoredFile | None:
        """Change the display name only: the stored Telegram copy is untouched."""
        response = await self._request(
            "PATCH",
            params={"id": f"eq.{file_id}"},
            json={"file_name": name},
            prefer="return=representation",
        )
        rows = self._rows(response)
        return StoredFile.from_row(rows[0]) if rows else None

    # ------------------------------------------------------------------ #
    # Virtual folders
    # ------------------------------------------------------------------ #
    @staticmethod
    def _overview_folder(row: Mapping[str, Any]) -> Folder:
        """One ``folders_overview`` row as a folder, totals included."""
        return Folder(
            id=str(row["id"]),
            name=str(row["name"]),
            count=int(row.get("files") or 0),
            bytes=int(row.get("bytes") or 0),
            favorite=bool(row.get("is_favorite")),
            favorite_order=_optional_int(row.get("favorite_order")),
        )

    async def _folders_overview(
        self, params: Mapping[str, str]
    ) -> list[Folder] | None:
        """Folders with their totals in one request, or ``None`` without the view.

        ``folders_overview`` joins each folder to its own counts and bytes, so the
        folders screen waits for one round trip instead of two. A database that
        has not run migration 012 yet answers with an error, and the caller falls
        back to the older two requests: the screen still renders, only slower.
        """
        try:
            response = await self._request(
                "GET", path="folders_overview", params=params
            )
        except SupabaseError:
            logger.warning(
                "folders_overview is unavailable; falling back to folders + "
                "folder_totals (run migration 012)",
                exc_info=True,
            )
            return None
        return [self._overview_folder(row) for row in self._rows(response)]

    async def list_folders(self, user_id: int) -> list[Folder]:
        """Folders of one user, each with its file count and total size."""
        folders = await self._folders_overview(
            {
                "select": _FOLDER_FIELDS,
                "user_id": f"eq.{user_id}",
                "order": "name.asc",
            }
        )
        if folders is None:
            folders = await self._list_folders_separately(user_id)
        # Sorted in Python as well: the database collation is not guaranteed to
        # order Arabic and Latin names the way the user expects.
        return sorted(
            folders, key=lambda folder: (folder.name.casefold(), folder.name)
        )

    async def _list_folders_separately(self, user_id: int) -> list[Folder]:
        """The pre-012 path: the folders list, then their totals."""
        response = await self._request(
            "GET",
            path="folders",
            params={
                "select": "id,name,is_favorite,favorite_order",
                "user_id": f"eq.{user_id}",
                "order": "name.asc",
            },
        )
        folders = [
            Folder(
                id=str(row["id"]),
                name=str(row["name"]),
                favorite=bool(row.get("is_favorite")),
                favorite_order=_optional_int(row.get("favorite_order")),
            )
            for row in self._rows(response)
        ]
        if not folders:
            return []

        ids = ",".join(folder.id for folder in folders)
        totals = await self._folder_totals(f"in.({ids})")
        return [
            Folder(
                id=folder.id,
                name=folder.name,
                count=totals.get(folder.id, (0, 0))[0],
                bytes=totals.get(folder.id, (0, 0))[1],
                favorite=folder.favorite,
                favorite_order=folder.favorite_order,
            )
            for folder in folders
        ]

    async def _folder_totals(self, folder_filter: str) -> dict[str, tuple[int, int]]:
        """Folder id -> (files, bytes), from the ``folder_totals`` view.

        Falls back to counting the links when the view is not deployed yet, so an
        older database still renders (without the byte totals).
        """
        try:
            response = await self._request(
                "GET",
                path="folder_totals",
                params={
                    "select": "folder_id,files,bytes",
                    "folder_id": folder_filter,
                },
            )
        except SupabaseError:
            logger.warning(
                "folder_totals is unavailable; falling back to link counts",
                exc_info=True,
            )
            links = await self._request(
                "GET",
                path="folder_files",
                params={"select": "folder_id", "folder_id": folder_filter},
            )
            tally = Counter(str(row["folder_id"]) for row in self._rows(links))
            return {folder_id: (count, 0) for folder_id, count in tally.items()}

        return {
            str(row["folder_id"]): (
                int(row.get("files") or 0),
                int(row.get("bytes") or 0),
            )
            for row in self._rows(response)
        }

    async def create_folder(self, user_id: int, name: str) -> Folder:
        response = await self._request(
            "POST",
            path="folders",
            json={"user_id": user_id, "name": name},
            prefer="return=representation",
        )
        rows = self._rows(response)
        if not rows:
            raise SupabaseError("Supabase did not return the created folder")
        return Folder(id=str(rows[0]["id"]), name=str(rows[0]["name"]))

    async def get_folder(self, folder_id: str, user_id: int) -> Folder | None:
        """One folder with its totals — one request, not two."""
        found = await self._folders_overview(
            {
                "select": _FOLDER_FIELDS,
                "id": f"eq.{folder_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            }
        )
        if found is not None:
            return found[0] if found else None

        response = await self._request(
            "GET",
            path="folders",
            params={
                "select": "id,name,is_favorite,favorite_order",
                "id": f"eq.{folder_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        rows = self._rows(response)
        if not rows:
            return None
        folder = Folder(
            id=str(rows[0]["id"]),
            name=str(rows[0]["name"]),
            favorite=bool(rows[0].get("is_favorite")),
            favorite_order=_optional_int(rows[0].get("favorite_order")),
        )
        totals = await self._folder_totals(f"eq.{folder.id}")
        count, size = totals.get(folder.id, (0, 0))
        return Folder(
            id=folder.id,
            name=folder.name,
            count=count,
            bytes=size,
            favorite=folder.favorite,
            favorite_order=folder.favorite_order,
        )

    async def set_folder_favorite(
        self,
        folder_id: str,
        user_id: int,
        favorite: bool,
        order: int | None = None,
    ) -> None:
        """Star or unstar a folder, and place it at ``order`` in the favourites."""
        await self._request(
            "PATCH",
            path="folders",
            params={"id": f"eq.{folder_id}", "user_id": f"eq.{user_id}"},
            json={"is_favorite": favorite, "favorite_order": order},
            prefer="return=minimal",
        )

    async def set_folder_order(
        self, folder_id: str, user_id: int, order: int
    ) -> None:
        """Store the manual position of a favourite folder."""
        await self._request(
            "PATCH",
            path="folders",
            params={"id": f"eq.{folder_id}", "user_id": f"eq.{user_id}"},
            json={"favorite_order": order},
            prefer="return=minimal",
        )

    async def delete_folder(self, folder_id: str, user_id: int) -> bool:
        """Delete the folder row only; the files themselves are never touched.

        The ``folder_files`` links cascade with the folder, so every row of
        ``files`` (and every Telegram copy behind it) stays exactly as it was.
        """
        response = await self._request(
            "DELETE",
            path="folders",
            params={"id": f"eq.{folder_id}", "user_id": f"eq.{user_id}"},
            prefer="return=representation",
        )
        return bool(self._rows(response))

    async def add_to_folder(self, folder_id: str, file_id: str) -> None:
        """Link a file to a folder (linking twice is a no-op)."""
        await self._request(
            "POST",
            path="folder_files",
            json={"folder_id": int(folder_id), "file_id": int(file_id)},
            prefer="resolution=ignore-duplicates,return=minimal",
        )

    async def files_in_folder(self, folder_id: str) -> list[StoredFile]:
        response = await self._request(
            "GET",
            path="folder_files",
            params={
                "select": "file_id,files(*)",
                "folder_id": f"eq.{folder_id}",
                "order": "created_at.desc",
            },
        )
        files: list[StoredFile] = []
        for row in self._rows(response):
            payload = row.get("files")
            if isinstance(payload, dict):
                files.append(StoredFile.from_row(payload))
        return files

    async def folders_of_file(self, file_id: str) -> list[str]:
        response = await self._request(
            "GET",
            path="folder_files",
            params={"select": "folders(name)", "file_id": f"eq.{file_id}"},
        )
        names = []
        for row in self._rows(response):
            payload = row.get("folders")
            if isinstance(payload, dict) and payload.get("name"):
                names.append(str(payload["name"]))
        return sorted(names, key=lambda name: (name.casefold(), name))

    async def set_content_hash(self, file_id: str, content_hash: str) -> None:
        """Store a fingerprint computed after the upload, without blocking it."""
        await self._request(
            "PATCH",
            params={"id": f"eq.{file_id}"},
            json={"content_hash": content_hash},
            prefer="return=minimal",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

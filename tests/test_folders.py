"""The folders screen and the favourite reorder, against a scripted PostgREST.

These started as one-off manual checks when migrations 012 and 013 landed: the
folder screen must read every folder *and* its totals in a single request, a
moved favourite must write the whole new order in a single call, and a database
that has not run those migrations yet must still render and still reorder.

The tests drive the real :func:`src.bot.navigation.build`, the real
:class:`FileStorageServices` and the real :class:`FileRepository`; only PostgREST
is replaced, by an in-memory backend that answers the same paths, applies the
same filters and records every request.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.bot import navigation
from src.bot.keyboards import FolderCB
from src.core.config import Settings
from src.db.repository import FileRepository
from src.services.files import FileStorageServices

USER = 7


def folder_row(
    folder_id: int,
    name: str,
    *,
    files: int = 0,
    bytes_total: int = 0,
    favorite: bool = False,
    order: int | None = None,
) -> dict[str, Any]:
    """One row shaped like ``folders_overview``: the folder plus its totals."""
    return {
        "id": folder_id,
        "user_id": USER,
        "name": name,
        "is_favorite": favorite,
        "favorite_order": order,
        "files": files,
        "bytes": bytes_total,
    }


def _matches(value: Any, rule: str) -> bool:
    if rule.startswith("eq."):
        return str(value) == rule[3:]
    if rule.startswith("in.("):
        allowed = rule[4:-1].split(",")
        return str(value) in allowed
    return True


class FolderBackend:
    """A tiny PostgREST for ``folders``, ``folder_totals`` and the two new objects.

    ``overview`` and ``orders`` say whether migrations 012 and 013 are applied;
    turning them off makes those paths answer 404, exactly like a database that
    still has the old schema.
    """

    def __init__(
        self,
        folders: list[dict[str, Any]],
        *,
        overview: bool = True,
        orders: bool = True,
    ) -> None:
        self.folders = {str(row["id"]): dict(row) for row in folders}
        self.overview = overview
        self.orders = orders
        self.requests: list[httpx.Request] = []

    # -- the transport ---------------------------------------------------- #
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.split("/rest/v1/", 1)[-1]
        params = dict(request.url.params)

        if path == "folders_overview":
            if not self.overview:
                return httpx.Response(404, json={"message": "relation does not exist"})
            rows = [dict(row) for row in self.folders.values()]
            for key in ("id", "user_id"):
                rule = params.get(key)
                if rule:
                    rows = [row for row in rows if _matches(row[key], rule)]
            return httpx.Response(200, json=rows)

        if path == "folders" and request.method == "PATCH":
            target = params.get("id", "")
            body = json.loads(request.content or b"{}")
            for row in self.folders.values():
                if _matches(row["id"], target) and _matches(row["user_id"], params.get("user_id", "")):
                    row.update(body)
            return httpx.Response(204)

        if path == "folders":
            rows = [
                {
                    key: row[key]
                    for key in ("id", "name", "is_favorite", "favorite_order")
                }
                for row in self.folders.values()
            ]
            rule = params.get("user_id")
            if rule:
                rows = [
                    row
                    for row in rows
                    if _matches(self.folders[str(row["id"])]["user_id"], rule)
                ]
            return httpx.Response(200, json=rows)

        if path == "folder_totals":
            rows = [
                {"folder_id": row["id"], "files": row["files"], "bytes": row["bytes"]}
                for row in self.folders.values()
                if _matches(row["id"], params.get("folder_id", ""))
            ]
            return httpx.Response(200, json=rows)

        if path == "rpc/set_folder_orders":
            if not self.orders:
                return httpx.Response(404, json={"message": "function does not exist"})
            body = json.loads(request.content or b"{}")
            assert body["p_user_id"] == USER
            for folder_id, position in zip(body["p_folder_ids"], body["p_orders"]):
                row = self.folders[str(folder_id)]
                if row["user_id"] != body["p_user_id"] or not row["is_favorite"]:
                    continue
                row["favorite_order"] = position
            return httpx.Response(204)

        return httpx.Response(200, json=[])

    # -- what the calls looked like --------------------------------------- #
    def requests_to(self, path: str) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.url.path.split("/rest/v1/", 1)[-1] == path
        ]


def library(backend: FolderBackend) -> SimpleNamespace:
    """The screen services, backed by the real repository over the fake REST API."""
    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    repository = FileRepository(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(backend.handler))
    )
    return SimpleNamespace(files=FileStorageServices(settings, repository), settings=settings)


def folders_screen() -> dict[str, object]:
    return {"k": "folders", "u": USER, "p": 1}


def folder_buttons(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("fld:open:")
    ]


# --------------------------------------------------------------------------- #
# The folders screen: one read, or the old two
# --------------------------------------------------------------------------- #
async def test_the_folders_screen_reads_every_folder_and_its_totals_in_one_request() -> None:
    backend = FolderBackend(
        [
            folder_row(1, "Books", files=3, bytes_total=2048),
            folder_row(2, "Photos", files=1, bytes_total=1024, favorite=True, order=0),
        ]
    )
    services = library(backend)

    text, markup = await navigation.build(services, folders_screen())

    # ``folders_overview`` joins the totals, so the screen costs one round trip.
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.method == "GET"
    assert request.url.path.endswith("/folders_overview")
    assert dict(request.url.params)["user_id"] == f"eq.{USER}"

    assert "Books" in text and "Photos" in text
    assert "3 files" in text and "1 file" in text
    assert len(folder_buttons(markup)) == 2


async def test_a_database_without_the_view_still_lists_the_folders() -> None:
    backend = FolderBackend(
        [
            folder_row(1, "Books", files=3, bytes_total=2048),
            folder_row(2, "Photos", files=1, bytes_total=1024),
        ],
        overview=False,
    )
    services = library(backend)

    text, markup = await navigation.build(services, folders_screen())

    # The pre-012 path: the folder list, then their totals.
    assert [request.url.path.rsplit("/", 1)[-1] for request in backend.requests] == [
        "folders_overview",
        "folders",
        "folder_totals",
    ]
    assert "Books" in text and "Photos" in text
    assert "3 files" in text and "1 file" in text
    assert len(folder_buttons(markup)) == 2


# --------------------------------------------------------------------------- #
# Moving a favourite: one call, or one per folder
# --------------------------------------------------------------------------- #
def favorites() -> list[dict[str, Any]]:
    return [
        folder_row(10, "Alpha", favorite=True, order=0),
        folder_row(20, "Beta", favorite=True, order=1),
        folder_row(30, "Gamma", favorite=True, order=2),
        folder_row(40, "Loose"),
    ]


async def test_moving_a_favourite_writes_the_whole_order_in_one_call() -> None:
    backend = FolderBackend(favorites())
    services = library(backend)

    moved = await services.files.move_favorite("20", USER, -1)

    assert moved is not None and moved.id == "20"
    # The function renumbered the list: Beta first, Alpha second, Gamma third.
    assert [backend.folders[key]["favorite_order"] for key in ("20", "10", "30")] == [
        0,
        1,
        2,
    ]
    calls = backend.requests_to("rpc/set_folder_orders")
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {
        "p_user_id": USER,
        "p_folder_ids": [20, 10, 30],
        "p_orders": [0, 1, 2],
    }
    # Nothing was written row by row.
    assert not [request for request in backend.requests if request.method == "PATCH"]


async def test_without_the_function_the_positions_are_still_written() -> None:
    backend = FolderBackend(favorites(), orders=False)
    services = library(backend)

    moved = await services.files.move_favorite("10", USER, 1)

    assert moved is not None and moved.id == "10"
    assert [backend.folders[key]["favorite_order"] for key in ("20", "10", "30")] == [
        0,
        1,
        2,
    ]
    patches = [request for request in backend.requests if request.method == "PATCH"]
    assert len(patches) == 3
    assert sorted(
        (dict(request.url.params)["id"], json.loads(request.content)["favorite_order"])
        for request in patches
    ) == [("eq.10", 1), ("eq.20", 0), ("eq.30", 2)]


async def test_a_folder_that_is_not_a_favourite_is_left_where_it_is() -> None:
    backend = FolderBackend(favorites())
    services = library(backend)

    assert await services.files.move_favorite("40", USER, -1) is None

    assert backend.requests_to("rpc/set_folder_orders") == []
    assert not [request for request in backend.requests if request.method == "PATCH"]


@pytest.mark.parametrize(("folder_id", "step"), [("10", -1), ("30", 1)])
async def test_the_edges_of_the_favourites_keep_their_order(folder_id: str, step: int) -> None:
    backend = FolderBackend(favorites())
    services = library(backend)

    moved = await services.files.move_favorite(folder_id, USER, step)

    assert moved is not None and moved.id == folder_id
    # Already at the top (or the bottom): nothing has to move.
    assert backend.requests_to("rpc/set_folder_orders") == []


def test_the_folders_keyboard_opens_the_folder_it_names() -> None:
    """The screen's buttons carry the folder id the handler re-reads."""
    assert FolderCB(action="open", value="20").pack().startswith("fld:open:20")

"""The period counts the date picker reads: one grouped query, not one per period."""

from __future__ import annotations

import json
from datetime import date

import httpx

from src.core.config import Settings
from src.db.repository import FileRepository
from src.utils.filetypes import FileCategory


def make_repository(
    payload: list[dict[str, object]],
) -> tuple[FileRepository, list[httpx.Request]]:
    """A repository whose PostgREST answers ``payload`` and records the requests."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    settings = Settings(
        _env_file=None,
        bot_token="123:test-token",
        supabase_url="https://example.supabase.co",
        supabase_key="sb_secret_test",
    )
    repository = FileRepository(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return repository, requests


async def test_the_periods_and_their_counts_come_from_one_grouped_query() -> None:
    repository, requests = make_repository(
        [
            {"anchor": "2026-11-18", "files": 12},
            {"anchor": "2026-11-17", "files": 3},
        ]
    )

    periods = await repository.period_counts(
        span="day", category=FileCategory.document, limit=24
    )

    assert periods == [(date(2026, 11, 18), 12), (date(2026, 11, 17), 3)]
    # One request, and it asks the database to do the grouping and the counting.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/rest/v1/rpc/files_periods"
    assert json.loads(request.content) == {
        "p_span": "day",
        "p_query": None,
        "p_category": "document",
        "p_limit": 24,
        "p_offset": 0,
    }


async def test_a_search_passes_its_query_to_the_grouped_query() -> None:
    repository, requests = make_repository([{"anchor": "2026-11-16", "files": 1}])

    await repository.period_counts(span="week", query="  holiday*  ", zone_offset=180)

    body = json.loads(requests[0].content)
    assert body["p_span"] == "week"
    assert body["p_query"] == "holiday"
    assert body["p_category"] is None
    # The grouping happens in the user's zone, three hours ahead of UTC.
    assert body["p_offset"] == 180

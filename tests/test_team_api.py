"""`api/team.py::search_team` — the typeahead read follows `next` until it has
`limit` matches or none remain, and still slices a bare-list response."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from kizen_builder.api import team as team_api
from kizen_builder.api.client import KizenClient
from tests.conftest import FAKE_BASE_URL

TYPEAHEAD = f"{FAKE_BASE_URL}/api/team/typeahead"


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


def _members(start: int, n: int) -> list[dict[str, Any]]:
    return [{"id": f"m{i}"} for i in range(start, start + n)]


def _page(
    rows: list[dict[str, Any]], next_url: str | None, count: int
) -> httpx.Response:
    """One typeahead page, in the DRF envelope shape."""
    return httpx.Response(
        200,
        json={
            "count": count,
            "next": next_url,
            "previous": None,
            "errors": [],
            "results": rows,
        },
    )


@respx.mock
def test_search_team_follows_next_past_100(client):
    route = respx.get(TYPEAHEAD).mock(
        side_effect=[
            _page(
                _members(0, 100),
                f"{TYPEAHEAD}?page=2&page_size=100&search=a",
                150,
            ),
            _page(_members(100, 50), None, 150),
        ]
    )
    rows = team_api.search_team(client, "a", limit=150)
    assert len(rows) == 150
    assert route.call_count == 2
    first = route.calls[0].request.url.params
    assert first["page_size"] == "100"
    assert first["search"] == "a"
    second = route.calls[1].request.url.params
    assert second["page"] == "2"
    assert second["search"] == "a"


@respx.mock
def test_search_team_slices_an_overshooting_envelope(client):
    """Two full pages for ``limit=150``: the walk stops on the limit while
    ``next`` is still set, and the overshoot is sliced off."""
    route = respx.get(TYPEAHEAD).mock(
        side_effect=[
            _page(
                _members(0, 100),
                f"{TYPEAHEAD}?page=2&page_size=100&search=a",
                400,
            ),
            _page(
                _members(100, 100),
                f"{TYPEAHEAD}?page=3&page_size=100&search=a",
                400,
            ),
        ]
    )
    rows = team_api.search_team(client, "a", limit=150)
    assert len(rows) == 150
    assert route.call_count == 2
    assert rows[-1]["id"] == "m149"


@respx.mock
def test_search_team_small_limit_makes_one_request(client):
    # side_effect, not return_value: a second request exhausts the mock and
    # fails at once instead of following ``next`` forever.
    route = respx.get(TYPEAHEAD).mock(
        side_effect=[
            _page(_members(0, 5), f"{TYPEAHEAD}?page=2&page_size=5&search=a", 142)
        ]
    )
    rows = team_api.search_team(client, "a", limit=5)
    assert len(rows) == 5
    assert route.call_count == 1
    assert route.calls[0].request.url.params["page_size"] == "5"


@respx.mock
def test_search_team_slices_a_bare_list(client):
    route = respx.get(TYPEAHEAD).mock(
        return_value=httpx.Response(200, json=_members(0, 12))
    )
    rows = team_api.search_team(client, "a", limit=3)
    assert [r["id"] for r in rows] == ["m0", "m1", "m2"]
    assert route.call_count == 1


@respx.mock
def test_search_team_none_walks_to_the_end(client):
    route = respx.get(TYPEAHEAD).mock(
        side_effect=[
            _page(
                _members(0, 100),
                f"{TYPEAHEAD}?page=2&page_size=100&search=a",
                230,
            ),
            _page(
                _members(100, 100),
                f"{TYPEAHEAD}?page=3&page_size=100&search=a",
                230,
            ),
            _page(_members(200, 30), None, 230),
        ]
    )
    rows = team_api.search_team(client, "a", limit=None)
    assert len(rows) == 230
    assert route.call_count == 3
    assert route.calls[0].request.url.params["page_size"] == "100"

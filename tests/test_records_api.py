"""Records API: search pagination/limit behavior and request shapes.

Regression context: `records list <obj> -n 3` used to walk the ENTIRE object
in pages of 3 because search_records paginated to completion regardless of
how many results the caller wanted.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
import respx

from kizen_builder.api import records as records_api
from kizen_builder.api.client import KizenClient
from tests.conftest import FAKE_BASE_URL, fake_get_object, load_fixture

SEARCH_URL = f"{FAKE_BASE_URL}/api/records/tax_lot/search"


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


def _page(results, next_url=None, count=None):
    return {
        "results": results,
        "next": next_url,
        "count": count if count is not None else len(results),
    }


@respx.mock
def test_search_stops_paginating_at_limit(client):
    """With limit=3 and page_size=3, exactly one request should be enough."""
    records = [{"id": f"r{i}", "name": f"rec {i}"} for i in range(3)]
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=_page(records, next_url="https://kizen.test/page2", count=5000)
        )
    )
    out = records_api.search_records(client, "tax_lot", page_size=3, limit=3)
    assert len(out) == 3
    assert route.call_count == 1


@respx.mock
def test_search_paginates_until_limit(client):
    pages = [
        _page([{"id": "a"}, {"id": "b"}], next_url="x", count=5),
        _page([{"id": "c"}, {"id": "d"}], next_url="y", count=5),
        _page([{"id": "e"}], next_url=None, count=5),
    ]
    route = respx.post(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=p) for p in pages]
    )
    out = records_api.search_records(client, "tax_lot", page_size=2, limit=3)
    # The last page may overshoot the limit (callers truncate); the point is
    # that pagination STOPS once enough records are in hand.
    assert [r["id"] for r in out] == ["a", "b", "c", "d"]
    assert route.call_count == 2  # third page never fetched


@respx.mock
def test_search_no_limit_fetches_all_pages(client):
    pages = [
        _page([{"id": "a"}], next_url="x", count=2),
        _page([{"id": "b"}], next_url=None, count=2),
    ]
    route = respx.post(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=p) for p in pages]
    )
    out = records_api.search_records(client, "tax_lot", page_size=1)
    assert [r["id"] for r in out] == ["a", "b"]
    assert route.call_count == 2


@respx.mock
def test_search_body_wraps_filter_groups(client):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    groups = [{"and": True, "filters": [{"type": "fields_v2", "condition": "="}]}]
    records_api.search_records(client, "tax_lot", filters=groups)
    body = json.loads(route.calls.last.request.content)
    assert body == {"query": groups, "and": True}


@respx.mock
def test_search_body_includes_field_names(client):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    records_api.search_records(client, "tax_lot", field_names=["name", "ticker_symbol"])
    body = json.loads(route.calls.last.request.content)
    assert body["field_names"] == ["name", "ticker_symbol"]


@respx.mock
def test_search_body_omits_field_names_key_when_not_passed(client):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    records_api.search_records(client, "tax_lot")
    body = json.loads(route.calls.last.request.content)
    assert "field_names" not in body
    assert body == {"query": [], "and": True}


@respx.mock
def test_search_text_param_forwarded(client):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    records_api.search_records(client, "tax_lot", search="main st")
    url = route.calls.last.request.url
    assert url.params["search"] == "main st"


@respx.mock
def test_get_record_includes_hidden_fields(client):
    fixture = load_fixture("records/get_tax_lot.json")
    route = respx.get(f"{FAKE_BASE_URL}/api/records/tax_lot/abc-123").mock(
        return_value=httpx.Response(200, json=fixture)
    )
    out = records_api.get_record(client, "tax_lot", "abc-123")
    assert route.calls.last.request.url.params["include_hidden_fields"] == "true"
    assert out == fixture


def test_tools_layer_truncates_and_forwards_limit(monkeypatch):
    """tools.search_records must pass its limit down so the API layer stops."""
    from kizen_builder.tools import records as record_tools

    seen: dict = {}

    def fake_api_search(
        client,
        object_identifier,
        filters=None,
        search=None,
        field_names=None,
        page_size=100,
        limit=None,
    ):
        seen["page_size"] = page_size
        seen["limit"] = limit
        return [{"id": str(i)} for i in range(min(limit or 999, 999))]

    monkeypatch.setattr(record_tools.records_api, "search_records", fake_api_search)
    out = record_tools.search_records("tax_lot", limit=3)
    assert len(out) == 3
    assert seen["limit"] == 3


@respx.mock
def test_tools_layer_limit_none_pages_at_500_and_returns_every_row(monkeypatch):
    """limit=None (the CLI's `-n 0`) reads every page and slices nothing."""
    from kizen_builder.tools import records as record_tools

    seen: dict = {}
    real_search = record_tools.records_api.search_records

    def spy(*args, **kwargs):
        seen["limit"] = kwargs.get("limit", "unset")
        return real_search(*args, **kwargs)

    monkeypatch.setattr(record_tools.records_api, "search_records", spy)
    pages = [
        _page([{"id": f"a{i}"} for i in range(500)], next_url="x", count=700),
        _page([{"id": f"b{i}"} for i in range(200)], next_url=None, count=700),
    ]
    route = respx.post(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=p) for p in pages]
    )
    out = record_tools.search_records("tax_lot", limit=None)
    assert len(out) == 700
    assert seen["limit"] is None
    assert route.call_count == 2
    assert route.calls[0].request.url.params["page_size"] == "500"


def test_tools_layer_forwards_field_names_with_name_added(monkeypatch):
    """A caller's ``field_names`` reaches the API layer, `name` auto-added."""
    from kizen_builder.tools import objects as obj_tools
    from kizen_builder.tools import records as record_tools

    monkeypatch.setattr(obj_tools, "get_object", fake_get_object)
    seen: dict = {}

    def fake_api_search(
        client,
        object_identifier,
        filters=None,
        search=None,
        field_names=None,
        page_size=100,
        limit=None,
    ):
        seen["field_names"] = field_names
        return []

    monkeypatch.setattr(record_tools.records_api, "search_records", fake_api_search)
    record_tools.search_records("tax_lot", field_names=["ticker_symbol"])
    assert seen["field_names"] == ["ticker_symbol", "name"]

    # Already including "name" — not duplicated.
    record_tools.search_records("tax_lot", field_names=["name", "ticker_symbol"])
    assert seen["field_names"] == ["name", "ticker_symbol"]


def test_tools_layer_explicit_empty_field_names_still_sends_name(monkeypatch):
    """``field_names=[]`` means "no extra fields," not "omit the key."

    The live server treats an omitted key as "every field" and an explicit
    empty list as "zero fields" (see this item's live probe, F). Since the
    id+name+requested contract holds even when requested is empty, `[]`
    must reach the wire as `["name"]`, not `None`.
    """
    from kizen_builder.tools import objects as obj_tools
    from kizen_builder.tools import records as record_tools

    monkeypatch.setattr(obj_tools, "get_object", fake_get_object)
    seen: dict = {}

    def fake_api_search(client, object_identifier, **kwargs):
        seen["field_names"] = kwargs.get("field_names")
        return []

    monkeypatch.setattr(record_tools.records_api, "search_records", fake_api_search)
    record_tools.search_records("tax_lot", field_names=[])
    assert seen["field_names"] == ["name"]


@pytest.mark.parametrize(
    "bad_name",
    [
        "bogus_field",  # typo
        "Ticker Symbol",  # display label, not the api_name "ticker_symbol"
        "104e186e-7bab-4149-b2bc-b9c912518d5e",  # field UUID
    ],
    ids=["typo", "display-label", "field-uuid"],
)
def test_tools_layer_rejects_unknown_field_before_any_search_call(
    monkeypatch, bad_name
):
    """An unrecognized field_names entry raises before the api layer is called.

    Matching is on api_name only (per the item's live probe): a display
    label or a field UUID misses the index exactly like a typo does. The
    live server accepts an unknown name and silently drops it (200, no
    error) rather than rejecting it, so this client-side check is
    load-bearing, not defensive.
    """
    from kizen_builder.tools import objects as obj_tools
    from kizen_builder.tools import records as record_tools

    monkeypatch.setattr(obj_tools, "get_object", fake_get_object)
    calls: list[Any] = []

    def fake_api_search(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(record_tools.records_api, "search_records", fake_api_search)
    with pytest.raises(LookupError, match=re.escape(bad_name)):
        record_tools.search_records("tax_lot", field_names=[bad_name])
    assert calls == []

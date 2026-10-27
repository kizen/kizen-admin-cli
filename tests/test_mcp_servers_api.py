"""api/mcp_servers.py: each wrapper calls exactly one method and path."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from kizen_builder.api import mcp_servers as mcp_api
from kizen_builder.api.client import KizenClient
from tests.conftest import FAKE_BASE_URL, load_fixture

BASE = f"{FAKE_BASE_URL}/api/mcp-servers"
SID = "00000000-0000-4000-8000-0000000000e1"


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


@respx.mock
def test_list_follows_pages(client):
    page = load_fixture("mcp_servers/list.json")
    route = respx.get(BASE).mock(
        side_effect=[
            httpx.Response(200, json={**page, "next": f"{BASE}?page=2"}),
            httpx.Response(200, json={"results": [], "next": None}),
        ]
    )
    rows = mcp_api.list_mcp_servers(client)
    assert route.call_count == 2
    assert [r["api_name"] for r in rows] == ["readonly", "support_tools"]


@respx.mock
def test_get(client):
    route = respx.get(f"{BASE}/{SID}").respond(json={"id": SID})
    assert mcp_api.get_mcp_server(client, SID) == {"id": SID}
    assert route.call_count == 1


@respx.mock
def test_create_posts_without_trailing_slash(client):
    route = respx.post(BASE).respond(201, json={"id": SID})
    mcp_api.create_mcp_server(client, {"name": "X", "api_name": "x"})
    assert json.loads(route.calls.last.request.content) == {
        "name": "X",
        "api_name": "x",
    }


@respx.mock
def test_update_patches(client):
    route = respx.patch(f"{BASE}/{SID}").respond(json={"id": SID})
    mcp_api.update_mcp_server(client, SID, {"is_enabled": False})
    assert json.loads(route.calls.last.request.content) == {"is_enabled": False}


@respx.mock
def test_delete(client):
    route = respx.delete(f"{BASE}/{SID}").respond(204)
    assert mcp_api.delete_mcp_server(client, SID) is None
    assert route.call_count == 1


@respx.mock
def test_tool_catalog_returns_the_dict(client):
    tools = load_fixture("mcp_servers/tools.json")
    respx.get(f"{BASE}/tools").respond(json=tools)
    assert mcp_api.get_tool_catalog(client) == tools


@respx.mock
def test_events_history_is_paginated(client):
    history = load_fixture("mcp_servers/events_history.json")
    route = respx.get(f"{BASE}/{SID}/events-history").respond(json=history)
    assert mcp_api.list_events_history(client, SID) == history["results"]
    assert route.call_count == 1


def test_step_options_rows_carry_server_keys():
    """step-options (wrapped with the automations API) returns a subset of the
    server read shape plus `deleted`, so a step's `mcp_server` can be matched
    to a server by id or api_name."""
    server_keys = set(load_fixture("mcp_servers/list.json")["results"][0])
    for row in load_fixture("mcp_servers/step_options.json"):
        assert set(row) - {"deleted"} <= server_keys

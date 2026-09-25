"""API-layer tests for smart-connectors (respx-mocked httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api import smart_connectors as sc
from kizen_builder.api.client import KizenAPIError, KizenClient
from tests.conftest import FAKE_BASE_URL

BASE = f"{FAKE_BASE_URL}/api/smart-connectors"


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


@respx.mock
def test_list_follows_pagination(client):
    # Register the more specific (page=2) route first — respx matches in
    # registration order, so the general route must not shadow it.
    respx.get(BASE, params={"page": "2"}).mock(
        return_value=httpx.Response(
            200, json={"count": 2, "next": None, "results": [{"id": "2", "name": "B"}]}
        )
    )
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                "next": f"{BASE}?page=2",
                "results": [{"id": "1", "name": "A"}],
            },
        )
    )
    rows = sc.list_smart_connectors(client)
    assert [r["id"] for r in rows] == ["1", "2"]


@respx.mock
def test_list_passes_filters(client):
    route = respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    sc.list_smart_connectors(
        client, search="foo", status="operational", connector_type="spreadsheet"
    )
    q = route.calls.last.request.url.params
    assert q["search"] == "foo"
    assert q["status"] == "operational"
    assert q["connector_type"] == "spreadsheet"
    # None-valued filters are dropped, not sent as "None".
    assert "active" not in q


@respx.mock
def test_get_connector(client):
    respx.get(f"{BASE}/upload_counties").mock(
        return_value=httpx.Response(
            200, json={"id": "abc", "api_name": "upload_counties"}
        )
    )
    detail = sc.get_smart_connector(client, "upload_counties")
    assert detail["id"] == "abc"


@respx.mock
def test_executions_and_script(client):
    respx.get(f"{BASE}/c1/executions").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": "e1", "status": "success"}],
            },
        )
    )
    rows = sc.list_executions(client, "c1")
    assert rows[0]["id"] == "e1"

    respx.get(f"{BASE}/c1/executions/e1/sql-script").mock(
        return_value=httpx.Response(200, json={"user_script": "SELECT 1"})
    )
    script = sc.get_execution_sql_script(client, "c1", "e1")
    assert script["user_script"] == "SELECT 1"


@respx.mock
def test_update_and_publish_script(client):
    patch_route = respx.patch(f"{BASE}/c1/sql-scripts/s1").mock(
        return_value=httpx.Response(200, json={"id": "s1", "user_script": "SELECT 2"})
    )
    updated = sc.update_sql_script(client, "c1", "s1", {"user_script": "SELECT 2"})
    assert updated["user_script"] == "SELECT 2"
    assert patch_route.called

    respx.post(f"{BASE}/c1/sql-scripts/s1/publish").mock(
        return_value=httpx.Response(200, json={"id": "s1"})
    )
    pub = sc.publish_sql_script(client, "c1", "s1")
    assert pub["id"] == "s1"


@pytest.mark.parametrize(
    "disposition",
    [
        'inline; filename="data.csv"',
        "attachment; filename=\"data.csv\"; filename*=UTF-8''data.csv",
    ],
    ids=["plain", "trailing-param"],
)
@respx.mock
def test_download_file_returns_bytes_and_filename(env_config, disposition):
    respx.get(f"{FAKE_BASE_URL}/api/files/f1/download").mock(
        return_value=httpx.Response(
            200, content=b"col\n1\n", headers={"content-disposition": disposition}
        )
    )
    content, name = sc.download_file(env_config, "f1")
    assert content == b"col\n1\n"
    assert name == "data.csv"


@respx.mock
def test_download_file_raises_on_error(env_config):
    respx.get(f"{FAKE_BASE_URL}/api/files/bad/download").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(KizenAPIError):
        sc.download_file(env_config, "bad")

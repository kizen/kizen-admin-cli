"""KizenClient: auth headers, error normalization, body handling."""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError, KizenClient
from tests.conftest import FAKE_BASE_URL, FAKE_BUSINESS_ID, FAKE_USER_ID, FIXTURES


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


@respx.mock
def test_auth_headers_injected(client):
    route = respx.get(f"{FAKE_BASE_URL}/api/ping").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client.get("/api/ping")
    sent = route.calls.last.request.headers
    assert sent["X-API-KEY"] == "test-api-key"
    assert sent["X-BUSINESS-ID"] == FAKE_BUSINESS_ID
    assert sent["X-USER-ID"] == FAKE_USER_ID
    assert sent["Accept"] == "application/json"


@respx.mock
def test_empty_body_returns_none(client):
    respx.delete(f"{FAKE_BASE_URL}/api/thing/1").mock(return_value=httpx.Response(204))
    assert client.delete("/api/thing/1") is None


@respx.mock
def test_drf_detail_error_message(client):
    respx.get(f"{FAKE_BASE_URL}/api/thing").mock(
        return_value=httpx.Response(400, json={"detail": "bad request body"})
    )
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    assert exc.value.status_code == 400
    assert "bad request body" in str(exc.value)


@respx.mock
def test_nested_errors_message_extracted(client):
    body = {
        "errors": [
            {"message": "field is required", "code": "required"},
            {"message": "must be unique"},
        ]
    }
    respx.post(f"{FAKE_BASE_URL}/api/thing").mock(
        return_value=httpx.Response(400, json=body)
    )
    with pytest.raises(KizenAPIError) as exc:
        client.post("/api/thing", json={})
    msg = str(exc.value)
    assert "field is required [required]" in msg
    assert "must be unique" in msg


@respx.mock
def test_html_404_body_kept_on_error(client):
    """Some endpoints 404 with an HTML page, not JSON (e.g. a wrong path such
    as the old trailing-slash execution-detail URL). The client must preserve
    the HTML body on the error rather than choking on the non-JSON payload."""
    html = (FIXTURES / "errors" / "html_404.html").read_text()
    respx.get(f"{FAKE_BASE_URL}/api/automation2/automation-execution/x/").mock(
        return_value=httpx.Response(
            404, text=html, headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/automation2/automation-execution/x/")
    assert exc.value.status_code == 404
    assert exc.value.body == html


@respx.mock
def test_401_message_names_env_and_fix(client):
    respx.get(f"{FAKE_BASE_URL}/api/thing").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    msg = str(exc.value)
    assert "testenv" in msg
    assert "kizen init" in msg


@respx.mock
def test_network_error_wrapped(client):
    respx.get(f"{FAKE_BASE_URL}/api/thing").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    assert exc.value.status_code == 0
    assert "network error" in str(exc.value)


@respx.mock
def test_top_level_list_error_message(client):
    """Branch-group validation errors come back as a bare list of strings."""
    body = ["Merge step's parent (s05_branch) must be a branch group initiator step."]
    respx.put(f"{FAKE_BASE_URL}/api/thing").mock(
        return_value=httpx.Response(400, json=body)
    )
    with pytest.raises(KizenAPIError) as exc:
        client.put("/api/thing", json={})
    assert str(exc.value) == (
        "HTTP 400: Merge step's parent (s05_branch) must be a branch group "
        "initiator step."
    )

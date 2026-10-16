"""KizenClient: auth headers, error normalization, body handling, retry."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.api.files import download_file
from tests.conftest import FAKE_BASE_URL, FAKE_BUSINESS_ID, FAKE_USER_ID, FIXTURES

THING = f"{FAKE_BASE_URL}/api/thing"
MAY_HAVE_APPLIED = (
    "the server may have applied this POST; read live state before retrying."
)


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", calls.append)
    return calls


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
def test_network_error_wrapped(client, sleeps):
    route = respx.get(THING).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    assert exc.value.status_code == 0
    assert str(exc.value) == "HTTP 0: network error: boom"
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


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


# --- retry ---------------------------------------------------------------


@respx.mock
def test_get_retries_a_transient_status(client, sleeps):
    route = respx.get(THING).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    assert client.get("/api/thing") == {"ok": True}
    assert route.call_count == 2
    assert sleeps == [1.0]


@pytest.mark.parametrize(("retry_after", "slept"), [("3", 3.0), ("60", 10.0)])
@respx.mock
def test_get_honours_retry_after_up_to_the_cap(client, sleeps, retry_after, slept):
    respx.get(THING).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": retry_after}),
            httpx.Response(200, json={}),
        ]
    )
    client.get("/api/thing")
    assert sleeps == [slept]


@respx.mock
def test_get_500_is_not_retried(client, sleeps):
    route = respx.get(THING).mock(return_value=httpx.Response(500))
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    assert exc.value.status_code == 500
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_get_gives_up_with_the_last_error_unchanged(client, sleeps):
    route = respx.get(THING).mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )
    with pytest.raises(KizenAPIError) as exc:
        client.get("/api/thing")
    assert route.call_count == 3
    assert str(exc.value) == "HTTP 503: unavailable"
    assert exc.value.body == {"detail": "unavailable"}


@respx.mock
def test_post_503_is_not_retried_and_says_it_may_have_applied(client, sleeps):
    route = respx.post(THING).mock(return_value=httpx.Response(503))
    with pytest.raises(KizenAPIError) as exc:
        client.post("/api/thing", json={})
    assert route.call_count == 1
    assert exc.value.status_code == 503
    assert exc.value.message.endswith(MAY_HAVE_APPLIED)


@respx.mock
def test_post_400_does_not_say_it_may_have_applied(client):
    respx.post(THING).mock(return_value=httpx.Response(400, json={"detail": "bad"}))
    with pytest.raises(KizenAPIError) as exc:
        client.post("/api/thing", json={})
    assert exc.value.message == "bad"


@pytest.mark.parametrize("error", [httpx.Response(503), httpx.ReadTimeout("timed out")])
@respx.mock
def test_read_only_post_is_not_retried_and_does_not_say_it_may_have_applied(
    client, sleeps, error
):
    route = respx.post(THING).mock(side_effect=[error])
    with pytest.raises(KizenAPIError) as exc:
        client.post("/api/thing", json={}, read_only=True)
    assert route.call_count == 1
    assert "may have applied" not in exc.value.message


@respx.mock
def test_backoff_holds_its_last_wait_past_the_table(client, sleeps, monkeypatch):
    monkeypatch.setattr("kizen_builder.api.client._MAX_ATTEMPTS", 4)
    route = respx.get(THING).mock(return_value=httpx.Response(503))
    with pytest.raises(KizenAPIError):
        client.get("/api/thing")
    assert route.call_count == 4
    assert sleeps == [1.0, 2.0, 2.0]


@respx.mock
def test_post_retries_a_connect_failure(client, sleeps):
    route = respx.post(THING).mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json={})]
    )
    assert client.post("/api/thing", json={}) == {}
    assert route.call_count == 2


@respx.mock
def test_post_read_timeout_is_not_retried_and_says_it_may_have_applied(client, sleeps):
    route = respx.post(THING).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(KizenAPIError) as exc:
        client.post("/api/thing", json={})
    assert route.call_count == 1
    assert exc.value.status_code == 0
    assert exc.value.message == f"network error: timed out — {MAY_HAVE_APPLIED}"


@respx.mock
def test_get_bytes_returns_the_raw_body_and_sends_auth(client):
    route = respx.get(THING).mock(
        return_value=httpx.Response(
            200, content=b"\x00raw", headers={"content-type": "application/zip"}
        )
    )
    content, headers = client.get_bytes("/api/thing")
    assert content == b"\x00raw"
    assert headers["content-type"] == "application/zip"
    sent = route.calls.last.request.headers
    assert sent["X-API-KEY"] == "test-api-key"
    assert sent["Accept"] == "*/*"


@respx.mock
def test_download_file_401_names_the_fix(env_config):
    respx.get(f"{FAKE_BASE_URL}/api/files/f1/download").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with pytest.raises(KizenAPIError) as exc:
        download_file(env_config, "f1")
    assert exc.value.status_code == 401
    assert "kizen init --env testenv" in exc.value.message


@respx.mock
def test_download_file_error_names_the_file(env_config):
    respx.get(f"{FAKE_BASE_URL}/api/files/f1/download").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(KizenAPIError) as exc:
        download_file(env_config, "f1")
    assert exc.value.status_code == 404
    assert exc.value.message == "downloading file f1: Not found."
    assert exc.value.body == {"detail": "Not found."}

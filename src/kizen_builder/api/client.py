"""Thin httpx wrapper that authenticates every call, retries reads, and
normalizes errors into a single exception type.

A profile authenticates with the three ``X-*`` headers. Helper mode sends only
``Authorization: Bearer``, from :class:`~kizen_builder.auth_helper.BearerHelperAuth`,
which refreshes the token and resends once on a 401 inside a single attempt;
the retries below wrap that whole exchange.

A GET is retried on a transport error or a 429/502/503/504. Any method is
retried when the connection was never made. A write is never retried once it
may have reached the server; its error says so instead.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from kizen_builder.auth_helper import BearerHelperAuth
from kizen_builder.config import ApiKeyAuth, EnvConfig, HelperAuth

_MAX_ATTEMPTS = 3
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_BACKOFF_S = (1.0, 2.0)
_RETRY_AFTER_CAP_S = 10.0
# Raised before the request left the machine, so retrying can't double a write.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class KizenAPIError(Exception):
    """Non-2xx response from the Kizen API, with status code + body attached."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


class KizenClient:
    """Context-managed HTTP client bound to a single environment."""

    def __init__(self, config: EnvConfig, timeout: float = 30.0) -> None:
        self._config = config
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth: httpx.Auth | None = None
        match config.auth:
            case ApiKeyAuth(api_key=api_key):
                headers = {
                    "X-API-KEY": api_key,
                    "X-BUSINESS-ID": config.business_id,
                    "X-USER-ID": config.user_id,
                    **headers,
                }
            case HelperAuth(argv=argv):
                auth = BearerHelperAuth(argv)
        self._client = httpx.Client(
            base_url=config.base_url, headers=headers, auth=auth, timeout=timeout
        )

    # --- context manager ------------------------------------------------

    def __enter__(self) -> KizenClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._client.close()

    def close(self) -> None:
        self._client.close()

    # --- verbs ----------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(
        self, path: str, json: Any = None, *, read_only: bool = False, **kwargs: Any
    ) -> Any:
        """``read_only`` marks a search sent as POST, so a lost response isn't
        reported as a write that may have applied. It is still not retried."""
        return self._request("POST", path, json=json, read_only=read_only, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("PATCH", path, json=json, **kwargs)

    def put(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("PUT", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def get_bytes(
        self, path: str, *, timeout: float | None = None
    ) -> tuple[bytes, httpx.Headers]:
        """GET a raw body (a file download) with the same retry and errors."""
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        resp = self._send("GET", path, headers={"Accept": "*/*"}, **kwargs)
        return resp.content, resp.headers

    def paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Follow DRF ``next`` links, returning the flat list of result dicts.

        ``params`` is applied to the first request only; the ``next`` URL
        already carries the query string forward, so we splice it into the
        path. A bare-list response is returned as is; any other shape stops
        the walk and keeps what was read so far.
        """
        items: list[dict[str, Any]] = []
        nxt: str | None = path
        first = True
        while nxt:
            resp = self.get(nxt, params=params if first else None)
            first = False
            if isinstance(resp, dict) and "results" in resp:
                items.extend(resp["results"])
                following = resp.get("next")
                if following:
                    parts = urlsplit(following)
                    nxt = parts.path + (f"?{parts.query}" if parts.query else "")
                else:
                    nxt = None
            elif isinstance(resp, list):
                items.extend(resp)
                nxt = None
            else:
                nxt = None
        return items

    # --- core -----------------------------------------------------------

    def _request(
        self, method: str, path: str, *, read_only: bool = False, **kwargs: Any
    ) -> Any:
        resp = self._send(method, path, read_only=read_only, **kwargs)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _send(
        self, method: str, path: str, *, read_only: bool = False, **kwargs: Any
    ) -> httpx.Response:
        write = method != "GET" and not read_only
        attempt = 0
        while True:
            attempt += 1
            retries_left = attempt < _MAX_ATTEMPTS
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                not_sent = isinstance(exc, _NOT_SENT)
                retryable = not_sent or (
                    method == "GET" and isinstance(exc, httpx.TransportError)
                )
                if retryable and retries_left:
                    time.sleep(_backoff(attempt))
                    continue
                message = f"network error: {exc}"
                if write and not not_sent:
                    message = _may_have_applied(message, method)
                raise KizenAPIError(0, message) from exc

            if resp.is_success:
                return resp
            if method == "GET" and resp.status_code in _RETRY_STATUSES and retries_left:
                time.sleep(_retry_delay(resp, attempt))
                continue
            raise self._error(method, path, resp, write=write)

    def _error(
        self, method: str, path: str, resp: httpx.Response, *, write: bool
    ) -> KizenAPIError:
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        message = _extract_error_message(body) or f"{method} {path} failed"
        if isinstance(self._config.auth, HelperAuth):
            message = _helper_auth_message(message, resp.status_code, body)
        elif resp.status_code in (401, 403):
            # Auth failure — surface the exact command the user can run to fix.
            label = self._config.name
            message = (
                f"{message} — credentials for '{label}' may be invalid or "
                f"revoked. Run `kizen init --env {label}` to re-enter them."
            )
        if write and resp.status_code in (502, 503, 504):
            message = _may_have_applied(message, method)
        return KizenAPIError(resp.status_code, message, body=body)


def _helper_auth_message(message: str, status: int, body: Any) -> str:
    """Helper mode's advice for an auth failure. `kizen init` doesn't apply:
    app-builder owns the credentials."""
    if status == 401:
        return f"{message} — Kizen rejected the token from the auth helper."
    # DRF gives every PermissionDenied the code `permission_denied`, so the
    # detail text is what singles out the OAuth-permission check (webapp
    # apps/oauth_provider/authentication.py).
    if (
        status == 403
        and isinstance(body, dict)
        and body.get("code") == "permission_denied"
        and "oauth" in str(body.get("detail", "")).lower()
    ):
        return (
            f"{message} — this Kizen user needs the OAuth authorizations "
            "permission to use kizen through app-builder."
        )
    return message


def _may_have_applied(message: str, method: str) -> str:
    return (
        f"{message} — the server may have applied this {method}; "
        "read live state before retrying."
    )


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    retry_after = resp.headers.get("retry-after", "").strip()
    if retry_after.isdigit():
        return min(float(retry_after), _RETRY_AFTER_CAP_S)
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    return _BACKOFF_S[min(attempt, len(_BACKOFF_S)) - 1]


def _extract_error_message(body: Any) -> str | None:
    if isinstance(body, dict):
        # Common DRF shapes
        if "detail" in body and isinstance(body["detail"], str):
            return body["detail"]
        if "errors" in body:
            return _format_value(body["errors"])
        # field-level / non_field_errors
        parts = [f"{k}: {_format_value(v)}" for k, v in body.items()]
        if parts:
            return "; ".join(parts)
    if isinstance(body, list) and body:
        return _format_value(body)
    if isinstance(body, str) and body:
        return body
    return None


def _format_value(v: Any) -> str:
    """Pull 'message' out of Kizen's nested error dicts so the user sees the
    useful text instead of the raw repr of a list of dicts."""
    if isinstance(v, list):
        return "; ".join(_format_value(item) for item in v)
    if isinstance(v, dict):
        msg = v.get("message")
        code = v.get("code")
        if msg and code:
            return f"{msg} [{code}]"
        if msg:
            return str(msg)
    return str(v)

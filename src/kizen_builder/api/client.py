"""Thin httpx wrapper that injects the three Kizen auth headers on every call
and normalizes errors into a single exception type.
"""

from __future__ import annotations

from typing import Any

import httpx

from kizen_builder.config import EnvConfig


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
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                **config.auth_headers(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
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

    def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("POST", path, json=json, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("PATCH", path, json=json, **kwargs)

    def put(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("PUT", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    # --- core -----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise KizenAPIError(0, f"network error: {exc}") from exc

        if resp.is_success:
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text

        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        message = _extract_error_message(body) or f"{method} {path} failed"
        if resp.status_code in (401, 403):
            # Auth failure — surface the exact command the user can run to fix.
            label = self._config.name
            message = (
                f"{message} — credentials for '{label}' may be invalid or "
                f"revoked. Run `kizen init --env {label}` to re-enter them."
            )
        raise KizenAPIError(resp.status_code, message, body=body)


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

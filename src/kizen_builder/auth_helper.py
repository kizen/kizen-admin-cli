"""Run the helper-mode auth command and cache the header it prints.

In helper mode (``$KIZEN_AUTH_HELPER``, see :mod:`kizen_builder.config`) a
wrapper owns OAuth. It gives us a command that prints exactly one line on
stdout, ``{"Authorization": "Bearer <token>"}``, refreshing the token first
when it is close to expiry. On failure it prints one line on stderr and
nothing on stdout, possibly still exiting 0, so empty stdout is a failure.

The header is cached for the life of the process, keyed by the command, and
re-fetched only when the API rejects it (:class:`BearerHelperAuth`). The token
never appears in an error message: errors quote the helper's stderr, never its
stdout.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Generator

import httpx

from kizen_builder.config import ConfigError

TIMEOUT_S = 15.0
_STDERR_LIMIT = 500
# "Bearer " plus a token of visible ASCII. Anything else (empty, whitespace,
# a CR/LF) is refused here rather than handed to httpx, whose header-validation
# error would quote the value.
_BEARER = re.compile(r"Bearer [\x21-\x7e]+")

_cache: dict[tuple[str, ...], str] = {}


class AuthHelperError(ConfigError):
    """The helper didn't produce a usable Authorization header.

    A ``ConfigError`` so the CLI's root group prints it as one ``error:`` line.
    """


def header(argv: tuple[str, ...]) -> str:
    """The cached Authorization value for ``argv``, running the helper once."""
    value = _cache.get(argv)
    if value is None:
        value = refresh(argv)
    return value


def refresh(argv: tuple[str, ...]) -> str:
    """Run the helper, replace the cached value, and return it."""
    value = _run(argv)
    _cache[argv] = value
    return value


def clear_cache() -> None:
    _cache.clear()


def _run(argv: tuple[str, ...]) -> str:
    name = argv[0]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise AuthHelperError(_message(name, "was not found", b"")) from None
    except subprocess.TimeoutExpired as exc:
        raise AuthHelperError(
            _message(name, f"timed out after {TIMEOUT_S:g}s", exc.stderr)
        ) from None
    except OSError as exc:
        raise AuthHelperError(
            _message(name, f"could not be run ({exc.strerror or exc})", b"")
        ) from None

    stderr = proc.stderr
    if proc.returncode != 0:
        raise AuthHelperError(
            _message(name, f"exited with status {proc.returncode}", stderr)
        )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        raise AuthHelperError(_message(name, "printed nothing on stdout", stderr))
    try:
        payload = json.loads(stdout)
    except ValueError:
        raise AuthHelperError(
            _message(name, "printed something other than JSON", stderr)
        ) from None
    if not isinstance(payload, dict):
        raise AuthHelperError(
            _message(name, "printed JSON that isn't an object", stderr)
        )
    value = payload.get("Authorization")
    if value is None:
        raise AuthHelperError(
            _message(name, "printed JSON with no Authorization key", stderr)
        )
    if not isinstance(value, str) or not _BEARER.fullmatch(value):
        raise AuthHelperError(
            _message(
                name, "printed an Authorization value that isn't a Bearer token", stderr
            )
        )
    return value


def _message(name: str, problem: str, stderr: bytes | str | None) -> str:
    """``auth helper `<name>` <problem>``, plus the helper's stderr if any."""
    text = (
        stderr.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes)
        else stderr or ""
    )
    said = " ".join(text.split())
    if len(said) > _STDERR_LIMIT:
        said = said[: _STDERR_LIMIT - 1] + "…"
    message = f"auth helper `{name}` {problem}"
    return f"{message}: {said}" if said else f"{message}."


class BearerHelperAuth(httpx.Auth):
    """Attach the helper's Authorization header; on a 401, refresh it once and
    resend once.

    A 401 on a Bearer request is rejected by authentication before the view
    runs, so resending a write after one can't apply it twice. The body is
    buffered up front so the resend can replay it.
    """

    requires_request_body = True

    def __init__(self, argv: tuple[str, ...]) -> None:
        self._argv = argv

    def __repr__(self) -> str:
        return f"BearerHelperAuth(argv={self._argv!r})"

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = header(self._argv)
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = refresh(self._argv)
            yield request

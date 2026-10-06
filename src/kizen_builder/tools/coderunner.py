"""Run code_step scripts against the live sandbox (`POST /api/coderunner/run`).

This is the primitive for *unit-testing* a code-step script before it goes
into an automation, with the same ``inputs.<name>`` / ``outputs.<name> = …``
namespace, so behaviour matches what you'd get inside an automation. Runs your
script in the same Lambda as a real ``code_step``; ``kizen.api`` calls in it
use real credentials and can write live data. The CLI asks y/N first
(``--yes`` skips).

Types are given by human ``data_type`` names (``number``, ``datetime``, …) or
their equivalent short codes (``n``, ``dt``, …). Neither is a ``field_type`` —
the server's wire ``t`` is a short-code registry, and passing a ``field_type``
name like ``integer`` straight through is rejected with ``Invalid Kizen type``.
We mirror Kizen's own production client, which accepts friendly names and maps
them to short codes (:data:`DATA_TYPE_TO_KIZEN` / :func:`to_kizen_type`),
defaulting anything unrecognized to ``s``.
"""

from __future__ import annotations

import json
from typing import Any

from kizen_builder.api import coderunner as coderunner_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config

# Friendly data_type name → Kizen short type code. Mirrors EXACTLY
# `app-builder viewer/src/remoteRunner.ts` toKizenType / DATA_TYPE_TO_KIZEN
# (the production coderunner client): human data_type names map to short codes,
# and anything unrecognized defaults to "s". The value set (s/n/b/d/dt/p/u/f/l)
# was independently confirmed against the live endpoint — there is no email or
# time-only code.
DATA_TYPE_TO_KIZEN: dict[str, str] = {
    "text": "s",
    "string": "s",
    "number": "n",
    "integer": "n",
    "boolean": "b",
    "date": "d",
    "datetime": "dt",
    "phone": "p",
    "phonenumber": "p",
    "uuid": "u",
    "file": "f",
    "list": "l",
}

# The short codes themselves (the values above), so `n` and `number` are
# equivalent and a short code is never re-mapped to "s".
_SHORT_CODES = set(DATA_TYPE_TO_KIZEN.values())

# Short code → human-readable description + value format, for help/rendering.
KNOWN_TYPE_CODES: dict[str, str] = {
    "s": "string",
    "n": "number (returned as a stringified float, e.g. '42.0')",
    "b": "boolean",
    "p": "phone",
    "u": "uuid (a bare id string, e.g. a record/object id — not {id: …})",
    "l": "list (v is a list of {t, v} dicts, not raw scalars)",
    "d": "date (ISO YYYY-MM-DD)",
    "dt": "datetime (ISO 8601, e.g. 2026-01-01T12:00:00[Z])",
    "f": "file (a file uuid the calling env can access)",
}

# Matches remoteRunner.ts SUPPORTED_RUNTIMES / DEFAULT_RUNTIME (and the remote
# code_runner configs.py default).
SUPPORTED_RUNTIMES = ("python-3-12", "python-3-13")
DEFAULT_RUNTIME = "python-3-13"


def to_kizen_type(data_type: str | None) -> str:
    """Resolve a friendly data_type name OR a short code to a short code.

    Mirrors ``remoteRunner.ts`` ``toKizenType``: a friendly name maps via
    :data:`DATA_TYPE_TO_KIZEN`; a token that is already a short code (``n``,
    ``dt``, …) is kept as-is; anything unrecognized defaults to ``s``. So
    ``number`` and ``n`` are equivalent, and ``--output greeting:string`` works.
    """
    t = (data_type or "").strip().lower()
    if t in _SHORT_CODES:
        return t
    return DATA_TYPE_TO_KIZEN.get(t, "s")


def _is_response_envelope(body: Any) -> bool:
    """True if a non-2xx body is actually a full CodeRunnerResponse.

    The endpoint returns HTTP 400 in two very different situations: a *request*
    validation failure (a field-keyed dict like ``{"inputs": {...}}`` or
    ``{"secrets": [...]}``, with no run metadata), and a *script* error (the
    normal response envelope with ``request_id``/``logs``/``http_requests`` and
    a populated ``error``). Only the former is a true rejection; the latter is a
    run that happened and failed, so it should render like any other result.
    """
    return isinstance(body, dict) and "request_id" in body and "error" in body


class CodeRunnerError(ValueError):
    """A local (pre-flight) problem building the coderunner request."""


def encode_input_value(code: str, raw: Any) -> Any:
    """Produce the ``v`` for a ``{t, v}`` input wire dict.

    Matches Kizen's production client: scalar values are sent as the raw
    **string** the user typed (``{t:'n', v:'21'}``) and the server coerces by
    the ``t`` code — no local number/bool coercion. JSON parsing happens *only*
    for the ``list`` (``l``) type, so a ``list`` can carry the real
    ``[{t, v}, …]`` structure the string form can't express. Every other type —
    crucially ``string`` — is sent verbatim, so a JSON blob stored in a string
    variable (e.g. a ``ticker_list`` code-step input) survives intact rather
    than being parsed and re-stringified with Python ``repr`` (single quotes),
    which would break a downstream ``json.loads``. A non-string value (e.g. a
    number or an already-structured list from a JSON ``--inputs-file``) is
    passed through untouched.
    """
    if not isinstance(raw, str):
        return raw
    if code == "l":
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            raise CodeRunnerError(
                f"value for a list ('l') input is not valid JSON: {exc}"
            ) from exc
    return raw


def resolve_runtime(runtime: str | None) -> str:
    """Default a missing runtime; hard-fail a present-but-unsupported one.

    Mirrors ``remoteRunner.ts``: an empty/None runtime falls back to
    :data:`DEFAULT_RUNTIME`; a supplied runtime that isn't in
    :data:`SUPPORTED_RUNTIMES` is a client-side config error, not something to
    round-trip (it would run on a different interpreter than declared).
    """
    rt = (runtime or "").strip() or DEFAULT_RUNTIME
    if rt not in SUPPORTED_RUNTIMES:
        raise CodeRunnerError(
            f"unsupported runtime {rt!r}. Supported runtimes: "
            f"{', '.join(SUPPORTED_RUNTIMES)}."
        )
    return rt


def build_payload(
    *,
    script: str,
    inputs: list[dict[str, Any]] | None = None,
    outputs: list[dict[str, Any]] | None = None,
    secrets: list[str] | None = None,
    runtime: str | None = None,
) -> dict[str, Any]:
    """Assemble the CodeRunnerRequestRequest wire body.

    ``inputs`` is a list of ``{"name", "code"|"type", "value"}`` dicts;
    ``outputs`` a list of ``{"name", "code"|"type"}``. The ``code``/``type`` is
    a friendly data_type name or a short code — both resolve via
    :func:`to_kizen_type`. Pure (no I/O), so it's the unit-test seam for
    encoding, type resolution, and runtime validation.
    """
    if not script or not script.strip():
        raise CodeRunnerError("user_script is empty — nothing to run.")
    rt = resolve_runtime(runtime)

    wire_inputs: dict[str, Any] = {}
    for item in inputs or []:
        name = item["name"]
        if name in wire_inputs:
            raise CodeRunnerError(f"duplicate input name {name!r}")
        code = to_kizen_type(item.get("code") or item.get("type"))
        wire_inputs[name] = {
            "t": code,
            "v": encode_input_value(code, item.get("value")),
        }

    output_types: dict[str, str] = {}
    for item in outputs or []:
        name = item["name"]
        if name in output_types:
            raise CodeRunnerError(f"duplicate output name {name!r}")
        output_types[name] = to_kizen_type(item.get("code") or item.get("type"))

    # Empty inputs/output_types are sent as explicit null (matches the
    # production client), not {} and not omitted — confirmed accepted live.
    payload: dict[str, Any] = {
        "runtime": rt,
        "user_script": script,
        "inputs": wire_inputs or None,
        "output_types": output_types or None,
    }
    if secrets:
        payload["secrets"] = list(secrets)
    return payload


def run_code_step(
    *,
    script: str,
    inputs: list[dict[str, Any]] | None = None,
    outputs: list[dict[str, Any]] | None = None,
    secrets: list[str] | None = None,
    runtime: str | None = None,
) -> dict[str, Any]:
    """Encode, POST, and normalize a coderunner run.

    Returns a dict with ``env`` plus the CodeRunnerResponse fields
    (``request_id``, ``duration_ms``, ``values``, ``logs``, ``http_requests``,
    ``error``) and the untouched ``raw`` response. Validation failures raise
    :class:`~kizen_builder.api.client.KizenAPIError` (HTTP 400, field-keyed
    body on ``.body``); the CLI pretty-prints those.
    """
    payload = build_payload(
        script=script,
        inputs=inputs,
        outputs=outputs,
        secrets=secrets,
        runtime=runtime,
    )
    config = load_env_config()
    with KizenClient(config) as client:
        try:
            resp = coderunner_api.run_code(client, payload)
        except KizenAPIError as exc:
            # A script that raises comes back as HTTP 400 carrying the full
            # response envelope (with logs + traceback) — that's a completed
            # run that failed, not a rejected request, so return it as a
            # result. A genuine request-validation 400 is re-raised.
            if _is_response_envelope(exc.body):
                resp = exc.body
            else:
                raise

    return _normalize_response(config.name, resp)


def _normalize_response(env: str, resp: Any) -> dict[str, Any]:
    """Shape a CodeRunnerResponse into the dict the CLI renders.

    ``http_requests`` is passed through as its wire dict
    (``{count, not_logged, requests: [...]}``); the CLI reads ``requests``.
    """
    resp = resp if isinstance(resp, dict) else {}
    return {
        "env": env,
        "request_id": resp.get("request_id"),
        "duration_ms": resp.get("duration_ms"),
        "values": resp.get("values") or {},
        "logs": resp.get("logs") or [],
        "http_requests": resp.get("http_requests") or {},
        "error": resp.get("error"),
        "raw": resp,
    }

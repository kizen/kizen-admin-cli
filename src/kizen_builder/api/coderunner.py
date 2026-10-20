"""Code Runner endpoint — run a Python script in the code_step sandbox.

``POST /api/coderunner/run`` executes a script in the same secure Lambda
sandbox that automation ``code_step`` steps use, standalone: no automation,
no record, no PUT. ``kizen.api`` calls in the script use real credentials and
can write live data, so the CLI asks y/N before calling it.

The request/response shapes are ``CodeRunnerRequestRequest`` /
``CodeRunnerResponse``. See :mod:`kizen_builder.tools.coderunner` for the
``{t, v}`` encoding and the tool-level normalization of the response.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient

# The server holds the request open until the script finishes, and ends a run
# at about 55 s ("Task timed out after 55.00 seconds", observed 2026-10-16).
# Waiting longer than that means a long run shows the server's own timeout
# rather than a client-side network error. 120 s leaves room for upload and
# latency without hanging for minutes if the server never answers.
CODERUNNER_TIMEOUT_S = 120.0


def run_code(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/coderunner/run. Returns the parsed CodeRunnerResponse.

    ``payload`` is the fully-formed wire body (``runtime``, ``user_script``,
    ``inputs``, ``output_types``, ``secrets``). Non-2xx responses raise
    :class:`~kizen_builder.api.client.KizenAPIError`; validation failures come
    back as HTTP 400 with a field-keyed error dict attached to
    ``KizenAPIError.body``.
    """
    return client.post(
        "/api/coderunner/run", json=payload, timeout=CODERUNNER_TIMEOUT_S
    )

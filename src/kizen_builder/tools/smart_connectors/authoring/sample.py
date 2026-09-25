"""Authoring: ``generate-sample`` — run the draft server-side to produce its
output sample, the gate in front of ``publish``."""

from __future__ import annotations

import time
from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors.authoring._helpers import _scopes


def generate_output_sample(
    connector: str,
    *,
    script_id: str | None = None,
    wait: bool = True,
    timeout: float = 300.0,
    poll_interval: float = 3.0,
) -> dict[str, Any]:
    """Run the draft server-side to produce its output sample.

    Writes no records — it populates the sample that ``publish`` requires and the
    ``headers`` that execution-variable scopes are validated against. Blocks
    until the script leaves ``in_progress`` unless ``wait=False``.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)
        if script_id is None:
            draft = detail.get("last_draft_script") or {}
            script_id = draft.get("id")
            if not script_id:
                raise PlanError(f"'{connector}' has no draft SQL script to run.")

        sc_api.start_sql_script(
            client,
            connector,
            script_id,
            source_file_id=(detail.get("source_file") or {}).get("id"),
        )
        script = sc_api.get_sql_script(client, connector, script_id)

        deadline = time.monotonic() + timeout
        while wait and script.get("state") == "in_progress":
            if time.monotonic() > deadline:
                break
            time.sleep(poll_interval)
            script = sc_api.get_sql_script(client, connector, script_id)

        detail = sc_api.get_smart_connector(client, connector)

    return {
        "connector": connector,
        "script_id": script_id,
        "state": script.get("state"),
        "error": script.get("error") or script.get("error_details"),
        "scopes": {k: len(v) for k, v in _scopes(detail).items()},
        "timed_out": bool(wait and script.get("state") == "in_progress"),
    }

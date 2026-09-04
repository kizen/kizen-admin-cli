"""Webhook connectors: building a correctly shaped sample reference file, and
firing the real inbound receiver."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors._common import _looks_like_uuid
from kizen_builder.tools.smart_connectors.authoring._helpers import _connector_ref

# The reference file a webhook connector's template generator validates against.
# Undiscoverable from the API — `get-file-template` just 400s on anything else —
# and `employee_id` has to be a real team-member UUID; an empty string fails.
WEBHOOK_SAMPLE_COLUMNS = ("timestamp", "employee_id", "querystring", "body")


def resolve_team_member(token: str) -> dict[str, Any]:
    """Resolve a team member by email, name, or UUID.

    Needed for a webhook sample file, whose ``employee_id`` column must hold a
    real team-member UUID.
    """
    if _looks_like_uuid(token):
        return {"id": token, "email": None, "full_name": None}
    from kizen_builder.tools.team import team_member_candidates

    config = load_env_config()
    with KizenClient(config) as client:
        pool = team_member_candidates(client, token)
    if not pool:
        raise PlanError(f"no team member matches '{token}' — try `kizen team search`")
    if len(pool) > 1:
        raise PlanError(
            f"'{token}' matches {len(pool)} team members "
            f"({', '.join(str(m.get('email')) for m in pool[:5])}) — be more specific"
        )
    return pool[0]


def build_webhook_sample(
    dest: str | os.PathLike[str],
    *,
    body: Any,
    employee: str,
    querystring: str = "",
    timestamp: str = "2026-01-01 00:00:00",
) -> dict[str, Any]:
    """Write a correctly shaped webhook reference CSV and return a summary.

    One row is enough: the generator reads the *shape*, and infers ``body``'s
    structure from the JSON in it — so the sample body should look like the real
    payloads, with every field you intend to read present.
    """
    member = resolve_team_member(employee)
    payload = body if isinstance(body, str) else json.dumps(body)
    # Validate here rather than letting a malformed body reach the generator,
    # which infers the whole JSON column type from this one value.
    try:
        parsed = json.loads(payload)
    except ValueError as exc:
        raise PlanError(f"the webhook body isn't valid JSON: {exc}") from exc

    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(WEBHOOK_SAMPLE_COLUMNS)
    writer.writerow([timestamp, member["id"], querystring, payload])
    path.write_text(buf.getvalue())

    return {
        "path": str(path),
        "employee_id": member["id"],
        "employee": member.get("email") or member.get("full_name") or member["id"],
        "body_keys": sorted(parsed) if isinstance(parsed, dict) else None,
        "columns": list(WEBHOOK_SAMPLE_COLUMNS),
    }


def plan_send_webhook(
    connector: str, body: Any, *, querystring: dict[str, str] | None = None
) -> dict[str, Any]:
    """Preview firing a connector's inbound webhook, and check it can do anything."""
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)

    blockers: list[str] = []
    if detail.get("connector_type") != "webhook":
        blockers.append(
            f"'{detail.get('api_name')}' is a "
            f"{detail.get('connector_type')} connector — only webhook connectors "
            f"have an inbound receiver. Other types run via `start-flow`"
        )
    if detail.get("status") != "operational":
        blockers.append(
            f"status is '{detail.get('status')}', not 'operational' — the request "
            f"is accepted but no execution will run. `smart-connectors activate` first"
        )
    if not (detail.get("live_script") or {}).get("id"):
        blockers.append("the connector has no published script to run")

    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "status": detail.get("status"),
        "cadence": detail.get("cadence"),
        "body": body,
        "querystring": querystring or {},
        "blockers": blockers,
    }


def apply_send_webhook(plan: dict[str, Any]) -> dict[str, Any]:
    """POST the real inbound webhook. Writes records — this is a live trigger."""
    config = load_env_config()
    with KizenClient(config) as client:
        resp = sc_api.trigger_webhook(
            client,
            plan["connector"],
            plan["body"],
            querystring=plan["querystring"] or None,
        )
    return {
        "connector": plan["connector_api_name"],
        "accepted": True,
        "response": resp,
        # Inbound requests are batched, not run per request.
        "cadence": plan.get("cadence"),
    }

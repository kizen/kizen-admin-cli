"""Authoring: ``start-flow`` — queue a dry or live execution, and catch the
two ways it silently does nothing."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.smart_connectors.authoring._helpers import _connector_ref


def plan_start_flow(connector: str, *, dry_run: bool) -> dict[str, Any]:
    """Preview a queued execution, and catch the two ways it silently does nothing."""
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)

    status = detail.get("status")
    ctype = detail.get("connector_type")
    loads = (detail.get("flow") or {}).get("loads") or []
    blockers: list[str] = []
    if ctype == "webhook":
        blockers.append(
            "webhook connectors aren't started this way — they run on a real "
            "inbound POST to /api/smart-connectors/{connector}/webhook, batched "
            "on the connector's cadence window"
        )
    if not dry_run and status != "operational":
        blockers.append(
            f"status is '{status}', not 'operational' — a live run would sit in "
            f"'queued' forever with no error. Run `smart-connectors activate` first"
        )
    if not loads:
        blockers.append(
            "the connector has no load steps, so a run would write nothing — "
            "configure them with `smart-connectors configure-flow`"
        )
    if not (detail.get("live_script") or {}).get("id"):
        blockers.append(
            "the connector has no published script — `smart-connectors push --publish`"
        )

    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "connector_type": ctype,
        "status": status,
        "is_dry_run": dry_run,
        "load_steps": len(loads),
        "blockers": blockers,
    }


def apply_start_flow(plan: dict[str, Any]) -> dict[str, Any]:
    config = load_env_config()
    with KizenClient(config) as client:
        resp = sc_api.start_connector_flow(
            client, plan["connector"], is_dry_run=plan["is_dry_run"]
        )
    return {
        "connector": plan["connector_api_name"],
        "is_dry_run": plan["is_dry_run"],
        # The response echoes the whole queued-run request; the id it hands back
        # is `execution_id` (not `id`), and is what `executions list` and
        # `executions get` key it by.
        "execution": resp.get("execution_id") or resp.get("id"),
        "queued": resp,
    }

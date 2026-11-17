"""Authoring: the connector-level SQL parameters a script reads from
``meta.parameters``."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.smart_connectors.authoring._helpers import _connector_ref
from kizen_builder.tools.smart_connectors.authoring.status import (
    ACTIVE_CONNECTOR_STATUSES,
)


def plan_set_sql_params(
    connector: str, params: dict[str, str], unset: list[str] | None = None
) -> dict[str, Any]:
    """Preview setting ``params`` and removing ``unset`` on the connector.

    The PATCH replaces ``sql_parameters`` whole, so the plan carries the full
    resulting dict: names not given keep their live values. Unsetting a name
    the connector doesn't have changes nothing. Like the UI, a change to an
    active connector also sets it inactive. The server doesn't require that
    (react-app SecretsAndParameters.tsx, read 2026-11-17).
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)

    current = detail.get("sql_parameters") or {}
    merged = {**current, **params}
    for name in unset or []:
        merged.pop(name, None)
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "from_params": current,
        "to_params": merged,
        "changed": merged != current,
        "status": detail.get("status"),
        "deactivates": detail.get("status") in ACTIVE_CONNECTOR_STATUSES,
    }


def apply_set_sql_params(plan: dict[str, Any]) -> dict[str, Any]:
    config = load_env_config()
    payload: dict[str, Any] = {"sql_parameters": plan["to_params"]}
    if plan["deactivates"]:
        payload["status"] = "inactive"
    with KizenClient(config) as client:
        updated = sc_api.update_smart_connector(client, plan["connector"], payload)
    return {
        "connector": updated.get("api_name") or plan["connector_api_name"],
        "sql_parameters": updated.get("sql_parameters"),
        "status": updated.get("status"),
    }

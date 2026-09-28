"""Shared helpers for the authoring surface: name/uuid resolution against
live custom objects and fields, the connector's output-table scopes, the
config-only template refresh, and the constants (connector types, the webhook
SQL-version pin, the per-type sample file shapes) every authoring command needs.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.tools.plans import PlanError

# Connector types the API accepts. Not in `/metadata` (which catalogs the
# enums *inside* a connector), so it's listed here and kept in step with
# `kizen docs show reference`.
CONNECTOR_TYPES = (
    "spreadsheet",
    "webhook",
    "schedule",
    "activity",
    "bulkaction",
    "polling_third_party_api",
    "direct_api_connection",
)

# Webhook connectors 500 on sample generation below this, regardless of the
# script. The declared floor is 3.1.x ("SQL version must be at least 3.1.x") but
# 3.1.x / 3.4.x / 3.6.x all fail; only 4.1.x works. Confirmed live 2026-07-28.
WEBHOOK_SQL_VERSION = "4.1.x"

# Types whose reference/sample file the connector needs before a template can be
# generated. Every type in practice — what differs is the file's required shape.
_SAMPLE_FILE_SHAPES = {
    "spreadsheet": "the real spreadsheet, or a representative slice of it",
    "webhook": "a CSV with columns timestamp, employee_id (a real team-member "
    "UUID — blank fails), querystring, body (a JSON string)",
    "schedule": "a CSV with one column, schedule_trigger_time",
    "activity": "any CSV — the server ignores its columns and introspects the "
    "activity type's own field schema instead",
    "bulkaction": "a CSV of the fields the bulk action selects",
}


def _connector_ref(detail: dict[str, Any]) -> str:
    """The identifier to use for follow-up calls: prefer the UUID."""
    return detail.get("id") or detail.get("api_name") or ""


def _object_lookup(client: KizenClient) -> tuple[dict[str, str], dict[str, str]]:
    """Return (api_name → uuid, uuid → api_name) for every custom object,
    plus built-in objects like ``client_client`` (contacts) that connectors
    can also seed, load into, or target as their primary object."""
    objs = co_api.list_objects(client, custom_only=False)
    by_api = {o["name"]: o["id"] for o in objs if o.get("name") and o.get("id")}
    by_id = {v: k for k, v in by_api.items()}
    return by_api, by_id


def _resolved(
    token: str, by_api: dict[str, str], by_id: dict[str, str], what: str
) -> str:
    """Resolve an api_name (or a UUID, passed through) against a name→uuid map."""
    if token in by_api:
        return by_api[token]
    if token in by_id:
        return token
    raise PlanError(f"{what} '{token}' not found. Available: {sorted(by_api)}")


def _field_lookup(client: KizenClient, object_id: str) -> dict[str, str]:
    """Return api_name → uuid for an object's live (non-deleted) fields.

    On the wire a field's api_name is ``name`` — ``display_name`` is the human
    label. (``tools.objects`` renames it to ``api_name`` for display; this reads
    the raw list.)
    """
    return {
        f["name"]: f["id"]
        for f in co_api.list_fields(client, object_id)
        if f.get("name") and f.get("id") and not f.get("deleted")
    }


def _scopes(detail: dict[str, Any]) -> dict[str, list[str]]:
    """The connector's recognized output tables → their column names.

    Populated by sample generation (``sql-scripts/{id}/start``), which is why
    nothing about execution variables can be configured before that runs.
    """
    headers = detail.get("headers") or {}
    return {
        scope: [name for c in cols if isinstance(c, dict) and (name := c.get("name"))]
        for scope, cols in headers.items()
        if isinstance(cols, list)
    }


def _sole_scope(scopes: dict[str, list[str]], what: str) -> str:
    if len(scopes) == 1:
        return next(iter(scopes))
    raise PlanError(
        f"{what} needs an explicit 'scope' — the connector writes "
        f"{len(scopes)} output tables ({sorted(scopes)})"
    )


def _fresh_template(
    client: KizenClient,
    connector: str,
    source_file_id: str,
    fallback_script_id: str,
) -> tuple[dict[str, Any], str]:
    """``get-file-template``, plus the draft to write it to.

    The call can fork a new draft carrying the template's own SQL, so snapshot
    any script you mean to keep *before* calling this.
    Returns ``(template, draft_id)``.
    """
    template = sc_api.get_file_template(client, connector, source_file_id)
    detail = sc_api.get_smart_connector(client, connector)
    draft_id = (detail.get("last_draft_script") or {}).get("id") or fallback_script_id
    return template, draft_id


def _config_keeping_sql(
    before: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    """The draft write that takes the template's ``config_metadata`` and keeps
    ``before``'s ``user_script`` and ``sql_version``."""
    payload: dict[str, Any] = {
        "config_metadata": template["config_metadata"],
        "user_script": before.get("user_script") or template.get("user_script") or "",
    }
    if before.get("sql_version"):
        payload["sql_version"] = before["sql_version"]
    return payload

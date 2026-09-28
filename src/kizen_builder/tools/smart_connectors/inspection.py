"""Read-only reshapes over ``api.smart_connectors`` for the CLI to render:
list/get/executions/scripts/events/metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.smart_connectors._common import _looks_like_uuid, save_file


def list_connectors(
    *,
    search: str | None = None,
    active: bool | None = None,
    connector_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        rows = sc_api.list_smart_connectors(
            client,
            search=search,
            active=active,
            connector_type=connector_type,
            status=status,
            ordering="name",
        )
    out = []
    for r in rows:
        co = r.get("custom_object") or {}
        out.append(
            {
                "id": r.get("id"),
                "api_name": r.get("api_name"),
                "name": r.get("name"),
                "connector_type": r.get("connector_type"),
                "status": r.get("status"),
                "custom_object": co.get("name") if isinstance(co, dict) else co,
                "used_count": (r.get("stats") or {}).get("used_count"),
                "last_used_at": r.get("last_used_at"),
            }
        )
    return out


def get_connector(identifier: str) -> dict[str, Any]:
    """Full connector detail (raw ``SmartConnectorReadDetail``)."""
    config = load_env_config()
    with KizenClient(config) as client:
        return sc_api.get_smart_connector(client, identifier)


def get_metadata() -> Any:
    config = load_env_config()
    with KizenClient(config) as client:
        return sc_api.get_metadata(client)


def list_executions(
    identifier: str,
    *,
    include_dry_run: bool | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        rows = sc_api.list_executions(
            client,
            identifier,
            include_dry_run=include_dry_run,
            status=status,
            search=search,
            ordering="-created",
        )
    out = []
    for r in rows:
        out.append(
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "trigger_type": r.get("trigger_type"),
                "is_dry_run": r.get("is_dry_run"),
                "started_by": _started_by(r.get("started_by")),
                "created": r.get("created"),
                "ended_at": r.get("ended_at"),
                # In full: the executor's real ClickHouse / validation error is
                # the whole reason to look at a failed run, and the list endpoint
                # is the only place it's exposed. The CLI truncates for the
                # table; JSON/CSV consumers get all of it.
                "error_details": r.get("error_details") or None,
            }
        )
    return out


def _started_by(started: Any) -> Any:
    if isinstance(started, dict):
        return started.get("display_name") or started.get("email") or started.get("id")
    return started


def get_execution(identifier: str, execution_id: str) -> dict[str, Any]:
    """One execution row as the API returns it, with ``started_by`` flattened
    the way ``list_executions`` does. Raises ``LookupError`` when the connector
    has no such execution."""
    config = load_env_config()
    with KizenClient(config) as client:
        row = sc_api.get_execution(client, identifier, execution_id)
    return {**row, "started_by": _started_by(row.get("started_by"))}


ExecutionFile = Literal["report", "output", "input"]

# `--file` choice → the execution row's S3Object field, and what to call it.
EXECUTION_FILES: dict[ExecutionFile, tuple[str, str]] = {
    "report": ("final_report", "Excel report"),
    "output": ("sql_output_zip", "SQL output zip"),
    "input": ("input_file", "input file"),
}


def download_execution_file(
    identifier: str,
    execution_id: str,
    kind: ExecutionFile = "report",
    *,
    dest: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Save one of an execution's files — its ``.xlsx`` results report, its
    SQL-output zip, or the input file it consumed — to local disk, unchanged.

    Raises ``LookupError`` when the run has no such file (a failed run has no
    report or output zip) and ``FileExistsError`` when the path exists and
    ``force`` is off.
    """
    field, label = EXECUTION_FILES[kind]
    config = load_env_config()
    with KizenClient(config) as client:
        row = sc_api.get_execution(client, identifier, execution_id)
    ref = row.get(field)
    if not (isinstance(ref, dict) and ref.get("id")):
        status = row.get("status") or "unknown"
        why = (
            "failed runs have no output zip or report."
            if status == "failed" and kind != "input"
            else f"it has no {label}."
        )
        raise LookupError(f"execution {execution_id} is `{status}`; {why}")
    saved = save_file(
        config,
        ref["id"],
        dest,
        fallback_name=ref.get("name") or f"{execution_id}_{field}",
        force=force,
    )
    return {
        "path": str(saved.path),
        "name": saved.name,
        "bytes": len(saved.content),
        "kind": kind,
    }


def get_execution_script(identifier: str, execution_id: str) -> dict[str, Any]:
    config = load_env_config()
    with KizenClient(config) as client:
        return sc_api.get_execution_sql_script(client, identifier, execution_id)


def list_scripts(identifier: str) -> list[dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        rows = sc_api.list_sql_scripts(client, identifier, ordering="-created")
    out = []
    for r in rows:
        out.append(
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "state": r.get("state"),
                "sql_version": r.get("sql_version"),
                "created": r.get("created"),
                "updated": r.get("updated"),
                "script_lines": len((r.get("user_script") or "").splitlines()),
            }
        )
    return out


def list_events(
    smart_connector_id: str,
    *,
    event_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    if not _looks_like_uuid(smart_connector_id):
        raise LookupError(
            "events-history requires the connector's UUID, not its api_name — "
            "run `kizen smart-connectors get <api_name>` to find the id."
        )
    config = load_env_config()
    with KizenClient(config) as client:
        return sc_api.list_events_history(
            client,
            smart_connector_id,
            event_type=event_type,
            date_from=date_from,
            date_to=date_to,
            ordering="created",
        )

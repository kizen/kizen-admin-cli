"""Automation CRUD against the Kizen API (automation2 namespace).

Each method takes a raw payload dict (built by the planner) and returns
the parsed JSON response. Errors surface as KizenAPIError.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from kizen_builder.api.client import KizenClient


def create_automation(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/automation2/automations. Returns the created automation including its UUID."""
    return client.post("/api/automation2/automations", json=payload)


def update_automation(
    client: KizenClient,
    automation_id: str,
    payload: dict[str, Any],
    last_revision: int | None = None,
) -> dict[str, Any]:
    """PUT /api/automation2/automations/{id}.

    Uses PUT (not PATCH) because PATCH does not allow step/trigger updates.
    ``last_revision`` is required for optimistic concurrency; if not provided,
    the current revision is fetched from the API first.
    """
    if last_revision is None:
        current = client.get(f"/api/automation2/automations/{automation_id}")
        last_revision = current.get("revision", 0)
    put_payload = {**payload, "last_revision": last_revision}
    return client.put(f"/api/automation2/automations/{automation_id}", json=put_payload)


def get_automation(client: KizenClient, automation_id: str) -> dict[str, Any]:
    """GET /api/automation2/automations/{id}."""
    return client.get(f"/api/automation2/automations/{automation_id}")


def get_metadata(client: KizenClient) -> dict[str, Any]:
    """GET /api/automation2/automations/metadata.

    The builder UI's own catalog for automation authoring — includes the
    live `model_name`/`business_plugin_app_id` pairing for `call_llm` /
    `file_content_extraction` / `audio_transcription` / condition
    `llm_decision` (under the `llm` key), Python runtime choices
    (`code_runners`), and a trigger/step support matrix
    (`support_matrix`). See automation.md "LLM & extraction destinations"
    for the worked example.
    """
    return client.get("/api/automation2/automations/metadata")


def list_mcp_step_options(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/mcp-servers/step-options: the MCP servers a `call_llm` step
    can attach, as its picker lists them (`{id, name, api_name, is_enabled,
    deleted, tool_count}`). Unpaginated; the server ignores `search`."""
    return client.get("/api/mcp-servers/step-options")


def list_run_tools_as_options(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/automation2/automations/run-tools-as-options: the service
    accounts a `call_llm` step can run its MCP tools as (`{id, name}`).
    Narrower than the service-account typeahead: a plugin's own service
    account isn't listed, and the write rejects it."""
    return client.get("/api/automation2/automations/run-tools-as-options")


def start_automation(
    client: KizenClient,
    api_name: str,
    *,
    record_id: str | None = None,
    client_id: str | None = None,
    variable_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST /api/automation2/automations/{api_name}/start.

    Triggers the automation on one entity. Uses api_name (not UUID) — the
    endpoint returns execution: null if a UUID is passed instead.

    The entity is identified by ``record_id`` (custom-object records) or
    ``client_id`` (contacts) — the StartAutomationRequest schema carries both.
    ``variable_overrides`` seeds automation variables for a run: a list of
    ``{"variable_name": <name>, "value": <string>}`` (VariableOverrideRequest);
    values are strings the server coerces by each variable's data_type.
    """
    body: dict[str, Any] = {}
    if record_id is not None:
        body["record_id"] = record_id
    if client_id is not None:
        body["client_id"] = client_id
    if variable_overrides:
        body["variable_overrides"] = variable_overrides
    return client.post(
        f"/api/automation2/automations/{api_name}/start",
        json=body,
    )


def get_execution(client: KizenClient, execution_id: str) -> dict[str, Any]:
    """GET /api/automation2/automation-execution/{execution_id}."""
    return client.get(f"/api/automation2/automation-execution/{execution_id}")


def get_execution_history(
    client: KizenClient, execution_id: str
) -> list[dict[str, Any]]:
    """GET /api/automation2/automation-execution/{execution_id}/history, every page."""
    return client.paginate(
        f"/api/automation2/automation-execution/{execution_id}/history"
    )


# ---------------------------------------------------------------------------
# Execution control (pause/resume/cancel/skip/debug) — confirmed live
# (2026-07-22) for pause/play/cancel against a real delayed
# execution (status flipped active -> paused -> active -> cancelled on the
# following GET each time). `debug-step` confirmed live 2026-08-13: it
# returns a full `LightAutomationHistory`, not an empty body — the schema
# `$ref`s the same shape for `debug-rerun`/`debug-restart` too. Only
# `debug-sendit` returned `null` in that same session.
#
# pause/play/cancel/debug-sendit all share one quirk: the request body is a
# full `LightReadAutomationExecutionRequest` — id/automation_id/client_id/
# record_id/status/trigger_history_id/debug_mode all "required" by the
# OpenAPI schema, even though the action itself is implied by the endpoint,
# not by anything in the body. In practice the values just need to echo the
# execution's own current GET (status included) — sending the
# pre-transition status works fine, so the field is presumably read-only in
# effect despite being schema-required. `execution_action_body()` builds it
# from a live `get_execution()` result.
# ---------------------------------------------------------------------------


def execution_action_body(execution: dict[str, Any]) -> dict[str, Any]:
    """Build the `LightReadAutomationExecutionRequest` body pause/play/
    cancel/debug-sendit all require, from a live `get_execution()` result."""
    return {
        "id": execution["id"],
        "automation_id": execution["automation_id"],
        "client_id": execution.get("client_id"),
        "record_id": execution.get("record_id"),
        "status": execution["status"],
        "trigger_history_id": execution.get("trigger_history_id"),
        "debug_mode": execution.get("debug_mode"),
    }


def pause_execution(
    client: KizenClient, execution_id: str, body: dict[str, Any]
) -> None:
    """POST .../automation-execution/{id}/pause. Body: execution_action_body(get_execution(...))."""
    client.post(
        f"/api/automation2/automation-execution/{execution_id}/pause", json=body
    )


def resume_execution(
    client: KizenClient, execution_id: str, body: dict[str, Any]
) -> None:
    """POST .../automation-execution/{id}/play (resume a paused execution)."""
    client.post(f"/api/automation2/automation-execution/{execution_id}/play", json=body)


def cancel_execution(
    client: KizenClient, execution_id: str, body: dict[str, Any]
) -> None:
    """POST .../automation-execution/{id}/cancel. Irreversible."""
    client.post(
        f"/api/automation2/automation-execution/{execution_id}/cancel", json=body
    )


def skip_and_resume_execution(
    client: KizenClient,
    execution_id: str,
    skip_step_id: str,
    continue_with_branch: str | None = None,
) -> None:
    """POST .../automation-execution/{id}/skip-and-resume — resume an
    execution paused on a step failure by skipping that step.
    Body: InlineFormRequest {skip_step_id, continue_with_branch?}."""
    body: dict[str, Any] = {"skip_step_id": skip_step_id}
    if continue_with_branch is not None:
        body["continue_with_branch"] = continue_with_branch
    client.post(
        f"/api/automation2/automation-execution/{execution_id}/skip-and-resume",
        json=body,
    )


def debug_sendit(client: KizenClient, execution_id: str, body: dict[str, Any]) -> None:
    """POST .../automation-execution/{id}/debug-sendit — run a debug-mode
    execution to completion. Body: execution_action_body(get_execution(...))."""
    client.post(
        f"/api/automation2/automation-execution/{execution_id}/debug-sendit", json=body
    )


def debug_rerun(client: KizenClient, execution_id: str, step_id: str) -> dict[str, Any]:
    """POST .../automation-execution/{id}/debug-rerun — re-execute one step;
    no subsequent steps are scheduled. Body: DebugRerunRequest {step_id}."""
    return client.post(
        f"/api/automation2/automation-execution/{execution_id}/debug-rerun",
        json={"step_id": step_id},
    )


def debug_restart(
    client: KizenClient, execution_id: str, step_id: str
) -> dict[str, Any]:
    """POST .../automation-execution/{id}/debug-restart — restart from a
    step; active histories are completed and subsequent steps ARE scheduled
    (unlike debug-rerun). Body: DebugRerunRequest {step_id}."""
    return client.post(
        f"/api/automation2/automation-execution/{execution_id}/debug-restart",
        json={"step_id": step_id},
    )


def debug_step(
    client: KizenClient,
    execution_id: str,
    action: str,
    history_id: str,
    continue_with_branch: str | None = None,
) -> dict[str, Any]:
    """POST .../automation-execution/{id}/debug-step — skip or execute one
    step of a debug-mode execution. Body: DebugStepRequest
    {action: execute|skip|debug, history_id, continue_with_branch?}."""
    body: dict[str, Any] = {"action": action, "history_id": history_id}
    if continue_with_branch is not None:
        body["continue_with_branch"] = continue_with_branch
    return client.post(
        f"/api/automation2/automation-execution/{execution_id}/debug-step", json=body
    )


def get_modification_history(
    client: KizenClient,
    automation_identifier: str,
    **params: Any,
) -> list[dict[str, Any]]:
    """GET .../automations/{id}/modification-history, transparently paginated.

    ``params`` passes through query filters as-is (date_from/date_to/
    event_type/search/... — see the endpoint's OpenAPI parameters for the
    full set); only non-None values are sent.
    """
    items: list[dict[str, Any]] = []
    path: str | None = (
        f"/api/automation2/automations/{automation_identifier}/modification-history"
    )
    query: dict[str, Any] | None = {k: v for k, v in params.items() if v is not None}
    while path:
        resp = client.get(path, params=query)
        query = None  # only apply on the first request; `next` already encodes it
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            if nxt:
                parts = urlsplit(nxt)
                path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                path = None
        elif isinstance(resp, list):
            items.extend(resp)
            path = None
        else:
            break
    return items


def get_failures_history(
    client: KizenClient,
    automation_identifier: str,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    """GET .../automations/{id}/histories/failures, transparently paginated."""
    items: list[dict[str, Any]] = []
    path: str | None = (
        f"/api/automation2/automations/{automation_identifier}/histories/failures"
    )
    query: dict[str, Any] | None = {
        k: v for k, v in {"page": page, "page_size": page_size}.items() if v is not None
    }
    while path:
        resp = client.get(path, params=query)
        query = None
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            if nxt:
                parts = urlsplit(nxt)
                path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                path = None
        elif isinstance(resp, list):
            items.extend(resp)
            path = None
        else:
            break
    return items


def list_executions(
    client: KizenClient,
    automation_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """GET /api/automation2/automation-execution filtered by automation_id."""
    result = client.get(
        "/api/automation2/automation-execution",
        params={"automation_id": automation_id, "size": min(limit, 100)},
    )
    if isinstance(result, list):
        return result
    return result.get("results", [])


def list_automations(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/automation2/automations, transparently paginated.

    Returns the flat list of automation dicts. Each dict includes `id` (UUID)
    and `api_name`.
    """
    items: list[dict[str, Any]] = []
    path: str | None = "/api/automation2/automations"
    while path:
        resp = client.get(path)
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            if nxt:
                parts = urlsplit(nxt)
                path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                path = None
        elif isinstance(resp, list):
            items.extend(resp)
            path = None
        else:
            break
    return items


def delete_automation(client: KizenClient, automation_id: str) -> dict[str, Any]:
    """DELETE /api/automation2/automations/{id}. Answers 204 No Content.

    Confirmed live (create → delete round trip against a throwaway automation).
    """
    resp = client.delete(f"/api/automation2/automations/{automation_id}")
    return resp if isinstance(resp, dict) else {}


def duplicate_automation(
    client: KizenClient, automation_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/automation2/automations/{id}/duplicate.

    Confirmed live. Quirk: the server does NOT honor a custom "name" in the
    payload — it always auto-names the copy "<original name> (copy #N)"
    (api_name gets a matching "_copy_N" suffix), regardless of what's sent.
    The --name CLI flag currently has no observed effect; kept for forward
    compatibility in case the server's behavior changes.
    """
    return client.post(
        f"/api/automation2/automations/{automation_id}/duplicate", json=payload
    )


# ---------------------------------------------------------------------------
# Folders (org/navigation sub-resource — automation2/folders)
#
# Confirmed live: list, get, create, update (PATCH — see update_folder), and
# delete all round-tripped successfully against a throwaway folder. The one
# surprise: update is PATCH, not PUT (PUT 405s: "Only patch is allowed"),
# unlike every other resource in this codebase. Moving automations between
# folders is its own endpoint (see move_automations).
# ---------------------------------------------------------------------------


def list_folders(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/automation2/folders, transparently paginated."""
    items: list[dict[str, Any]] = []
    path: str | None = "/api/automation2/folders"
    while path:
        resp = client.get(path)
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            if nxt:
                parts = urlsplit(nxt)
                path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                path = None
        elif isinstance(resp, list):
            items.extend(resp)
            path = None
        else:
            break
    return items


def get_folder(client: KizenClient, folder_id: str) -> dict[str, Any]:
    """GET /api/automation2/folders/{id}."""
    return client.get(f"/api/automation2/folders/{folder_id}")


def list_subfolders(client: KizenClient, folder_id: str) -> list[dict[str, Any]]:
    """GET /api/automation2/folders/{id}/subfolders, every page."""
    return client.paginate(f"/api/automation2/folders/{folder_id}/subfolders")


def create_folder(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/automation2/folders. Payload: {"name": ..., "parent_folder_id": ...}."""
    return client.post("/api/automation2/folders", json=payload)


def update_folder(
    client: KizenClient, folder_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/automation2/folders/{id}.

    Confirmed live: PUT here 405s with "Only patch is allowed" — unlike
    every other resource in this codebase (which use PUT), folders update
    via PATCH. The wire field for the parent is `parent_folder_id`, not
    `parent_id` (the latter is silently dropped by the serializer, so a
    create/update that only sets `parent_id` no-ops the parent instead of
    erroring).

    Server-side bug (confirmed live 2026-08-04): sending
    `parent_folder_id` without `name` in the same PATCH body 500s, even
    though PatchedWriteAutomationFolderRequest marks both fields optional.
    `tools/planners/automations.py`'s `plan_update_folder` works around this
    by always echoing the folder's current name alongside a parent change.
    """
    return client.patch(f"/api/automation2/folders/{folder_id}", json=payload)


def delete_folder(client: KizenClient, folder_id: str) -> dict[str, Any]:
    """DELETE /api/automation2/folders/{id}. Answers 204 No Content."""
    resp = client.delete(f"/api/automation2/folders/{folder_id}")
    return resp if isinstance(resp, dict) else {}


def move_automations(
    client: KizenClient, automation_ids: list[str], folder_id: str
) -> None:
    """POST /api/automation2/folders/move: move automations into a folder.

    Confirmed live 2026-10-30: the body is `{"automation_ids": [...],
    "folder_ids": [], "new_parent_folder_id": <id>}` and the answer is 200 with
    an empty body. The automation isn't rewritten: its revision stays put.
    Root is the `<business_root>` folder's id.
    """
    client.post(
        "/api/automation2/folders/move",
        json={
            "automation_ids": list(automation_ids),
            "folder_ids": [],
            "new_parent_folder_id": folder_id,
        },
    )

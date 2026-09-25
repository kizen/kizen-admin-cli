"""Smart-connector endpoints against the Kizen API.

Smart connectors are Kizen's data-ingestion / ETL layer: a connector owns one
or more **SQL scripts** that transform uploaded / webhook / scheduled input
against seeded Kizen data and write records back. This module is a thin httpx
wrapper — every function takes a ``KizenClient`` first and returns parsed
JSON; orchestration lives in ``tools/smart_connectors.py``.

Coverage is the read/inspect surface, the pieces the local dev loop needs
(list/get/metadata, executions, sql-scripts, events-history, draft-update +
publish), and the authoring path: create/update the connector, attach a
reference file, generate a script template from it, generate the output sample,
configure execution variables + load steps, and start a run. The inbound
``/webhook`` receiver (which *is* how a webhook connector is triggered, in place
of ``start-connector-flow``) is intentionally out of scope — firing production
webhooks is not something the CLI should make easy.

The ``pull`` command does NOT use the server's ``/dev-package`` /
``/connector-local-dev-package`` endpoints: verified live, one 500s and the
other only kicks off an async server-side generation job ({progress_status_id,
status}) rather than returning the package inline. Instead ``tools`` assembles
the local package client-side from data these functions return reliably — the
sql-script's ``user_script`` + ``config_metadata`` (which is the __config.json
payload: input_tables / seed_tables) plus the connector's uploaded input file,
downloaded via ``/api/files/{id}/download``.

``{connector}`` (``connector_identifier`` in the API) is a bare string, so it
accepts either the connector's UUID or its api_name. Note the one wrinkle:
``events-history`` keys off ``smart_connector_id`` (a UUID) rather than the
shared ``connector_identifier`` — so that call needs the UUID, not an api_name.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from kizen_builder.api.client import KizenClient

# Raw file transfer (a connector's reference file) lives in ``api.files``, which
# is the one module that doesn't speak JSON. Re-exported here — deliberately,
# hence the redundant alias — because ``pull`` has always reached for it through
# this module.
from kizen_builder.api.files import download_file as download_file

_BASE = "/api/smart-connectors"


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so we only send filters the caller set."""
    return {k: v for k, v in params.items() if v is not None}


def _paginate(
    client: KizenClient, path: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Follow DRF ``next`` links, returning the flat list of result dicts.

    ``params`` is applied to the first request only; the ``next`` URL already
    carries the query string forward, so we splice it into the path and stop
    passing ``params`` after the first page.
    """
    items: list[dict[str, Any]] = []
    nxt: str | None = path
    first = True
    while nxt:
        resp = client.get(nxt, params=params if first else None)
        first = False
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            following = resp.get("next")
            if following:
                parts = urlsplit(following)
                nxt = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                nxt = None
        elif isinstance(resp, list):
            items.extend(resp)
            nxt = None
        else:
            nxt = None
    return items


# ---------------------------------------------------------------------------
# Connector object
# ---------------------------------------------------------------------------


def list_smart_connectors(
    client: KizenClient,
    *,
    search: str | None = None,
    active: bool | None = None,
    connector_type: str | None = None,
    custom_object: str | None = None,
    status: str | None = None,
    ordering: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/smart-connectors — paginated list of ``SmartConnectorSlimmed``."""
    params = _clean_params(
        {
            "search": search,
            "active": active,
            "connector_type": connector_type,
            "custom_object": custom_object,
            "status": status,
            "ordering": ordering,
        }
    )
    return _paginate(client, _BASE, params or None)


def get_smart_connector(client: KizenClient, connector: str) -> dict[str, Any]:
    """GET /api/smart-connectors/{connector} — full ``SmartConnectorReadDetail``.

    Includes ``live_script`` / ``last_draft_script`` (the currently published
    and latest-draft SQL) alongside the connector's config.
    """
    return client.get(f"{_BASE}/{connector}")


def get_metadata(client: KizenClient) -> Any:
    """GET /api/smart-connectors/metadata — connector-type / matching-rule catalog.

    The API types this as a free-form object; returned as-is. It's the
    authoritative list of the enums the authoring path validates against:
    ``execution_variables.variable_data_types`` (with per-type input/output
    formats), ``matching_rules.*_actions``,
    ``field_mapping_rules.conflict_resolution``, ``cadence_choices``, and
    ``sql_versions``.
    """
    return client.get(f"{_BASE}/metadata")


def create_smart_connector(
    client: KizenClient, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/smart-connectors — create a connector (``SmartConnectorRequest``).

    The OpenAPI schema marks only ``name`` required; live, the server also
    demands ``custom_object`` and ``connector_type``, plus ``cadence`` for
    ``schedule`` and ``activity_object`` (an *activity type* id) for
    ``activity``. A new connector lands in ``status: "setup"`` — see
    ``update_smart_connector``.
    """
    return client.post(_BASE, json=payload)


def update_smart_connector(
    client: KizenClient, connector: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/smart-connectors/{connector} — partial update.

    Everything past creation is a PATCH against this endpoint:
    ``source_file_id``, ``execution_variables``, ``flow``, ``kizen_data_seeds``,
    and the ``status`` flip to ``operational``.
    """
    return client.patch(f"{_BASE}/{connector}", json=payload)


def get_file_template(
    client: KizenClient, connector: str, source_file_id: str
) -> dict[str, Any]:
    """POST .../get-file-template — generate a script + config from the reference file.

    Returns ``{"user_script": ..., "config_metadata": {...}}`` derived from the
    attached file's columns (plus any saved ``kizen_data_seeds``, which show up
    in ``config_metadata.seed_tables``). Nothing is written — PATCH the result
    onto a draft script yourself.

    ``source_file_id`` must be passed on **every** call, even when it hasn't
    changed: with an empty body the endpoint silently returns ``{}`` instead of
    erroring. Wrongly-shaped files 400 with a usable message (the required shape
    varies by connector type — see ``kizen docs show reference``).
    """
    resp = client.post(
        f"{_BASE}/{connector}/get-file-template",
        json={"source_file_id": source_file_id},
    )
    return resp if isinstance(resp, dict) else {}


def generate_execution_variables(client: KizenClient, connector: str) -> Any:
    """POST .../generate-execution-variables — suggest variables from the file's columns.

    Read-only in effect: it returns a list of ``ExecutionVariableRequest``-shaped
    dicts (inferred ``data_type`` / ``input_format`` / ``output_format``) without
    saving anything. PATCH them onto ``execution_variables`` to keep them.
    """
    return client.post(f"{_BASE}/{connector}/generate-execution-variables")


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


def list_executions(
    client: KizenClient,
    connector: str,
    *,
    include_dry_run: bool | None = None,
    status: str | None = None,
    search: str | None = None,
    ordering: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/smart-connectors/{connector}/executions — paginated run history."""
    params = _clean_params(
        {
            "include_dry_run": include_dry_run,
            "status": status,
            "search": search,
            "ordering": ordering,
        }
    )
    return _paginate(client, f"{_BASE}/{connector}/executions", params or None)


def get_execution_sql_script(
    client: KizenClient, connector: str, execution_id: str
) -> dict[str, Any]:
    """GET .../executions/{execution_id}/sql-script — the SQL used in one run.

    (There is no single-execution GET endpoint; this sub-resource is the only
    per-execution detail the API exposes.)
    """
    return client.get(f"{_BASE}/{connector}/executions/{execution_id}/sql-script")


# ---------------------------------------------------------------------------
# SQL scripts
# ---------------------------------------------------------------------------


def list_sql_scripts(
    client: KizenClient,
    connector: str,
    *,
    state: str | None = None,
    status: str | None = None,
    ordering: str | None = None,
) -> list[dict[str, Any]]:
    """GET .../sql-scripts — the connector's draft/live SQL scripts."""
    params = _clean_params({"state": state, "status": status, "ordering": ordering})
    return _paginate(client, f"{_BASE}/{connector}/sql-scripts", params or None)


def get_sql_script(
    client: KizenClient, connector: str, script_id: str
) -> dict[str, Any]:
    """GET .../sql-scripts/{script_id} — one ``SQLScript``."""
    return client.get(f"{_BASE}/{connector}/sql-scripts/{script_id}")


def download_output_sample(
    client: KizenClient,
    connector: str,
    script_id: str,
    *,
    sample_type: str = "reference",
) -> dict[str, Any]:
    """POST .../sql-scripts/{script_id}/download-output-sample.

    ``sample_type`` is ``"reference"`` (schema-only sample) or ``"last_run"``.
    Returns ``{"output_csv_file_url": <url>}``.
    """
    return client.post(
        f"{_BASE}/{connector}/sql-scripts/{script_id}/download-output-sample",
        json={"type": sample_type},
    )


def update_sql_script(
    client: KizenClient, connector: str, script_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH .../sql-scripts/{script_id} — partial update (e.g. ``user_script``).

    This edits the *draft*; nothing goes live until ``publish_sql_script``.
    """
    return client.patch(f"{_BASE}/{connector}/sql-scripts/{script_id}", json=payload)


def start_sql_script(
    client: KizenClient,
    connector: str,
    script_id: str,
    *,
    source_file_id: str | None = None,
) -> dict[str, Any]:
    """POST .../sql-scripts/{script_id}/start — generate the server-side output sample.

    Runs the draft against the reference file to produce the output sample and
    populate the connector's ``headers`` (the recognized output columns, keyed by
    scope). Both matter downstream: ``publish`` 400s with "Output sample file is
    not generated yet" until the sample exists, and ``execution_variables``
    validates each variable's ``scope`` against ``headers``.

    ``source_file_id`` in the body is the only thing that stamps the script's
    ``config_metadata.triggered.fileupload_file_id``, the file the executor
    reads. Without it the script keeps the file it was last triggered on, so a
    start after a reference-file swap leaves the executor on the old file.
    ``PATCH sql-scripts/{id}`` accepts the same field and ignores it. Confirmed
    live 2026-09-25.

    Generation is asynchronous — poll the script's ``state`` (``in_progress`` →
    ``success`` / ``failed``) via :func:`get_sql_script`.
    """
    body = {"source_file_id": source_file_id} if source_file_id else {}
    resp = client.post(f"{_BASE}/{connector}/sql-scripts/{script_id}/start", json=body)
    return resp if isinstance(resp, dict) else {}


def publish_sql_script(
    client: KizenClient, connector: str, script_id: str
) -> dict[str, Any]:
    """POST .../sql-scripts/{script_id}/publish — promote the draft to live.

    No request body; returns ``{"id": <uuid>}``. Requires a generated output
    sample first — see :func:`start_sql_script`.
    """
    resp = client.post(f"{_BASE}/{connector}/sql-scripts/{script_id}/publish")
    return resp if isinstance(resp, dict) else {}


# ---------------------------------------------------------------------------
# Running the flow
# ---------------------------------------------------------------------------


def start_connector_flow(
    client: KizenClient, connector: str, *, is_dry_run: bool
) -> dict[str, Any]:
    """POST .../start-connector-flow — queue an execution.

    ``is_dry_run=True`` validates the whole flow without writing records and
    works whatever the connector's ``status`` is. A live run needs
    ``status: "operational"`` — otherwise it sits in ``queued`` indefinitely with
    no error, which is the single most expensive trap on this path.

    Webhook connectors are not started this way; a real inbound POST to
    ``.../webhook`` is their trigger — see :func:`trigger_webhook`.

    The response echoes the queued-run request back, with the id under
    ``execution_id`` (not ``id``).
    """
    resp = client.post(
        f"{_BASE}/{connector}/start-connector-flow", json={"is_dry_run": is_dry_run}
    )
    return resp if isinstance(resp, dict) else {}


def trigger_webhook(
    client: KizenClient,
    connector: str,
    body: Any,
    *,
    querystring: dict[str, str] | None = None,
) -> Any:
    """POST .../{connector}/webhook — the real inbound receiver.

    This is how a webhook connector runs; ``start-connector-flow`` is for the
    other trigger types. Returns 201 immediately and processes asynchronously:
    inbound requests are **batched on the connector's cadence window**, not run
    per request, so expect up to a full cadence interval before an execution
    appears (the generated input filename embeds the batch's start/end unix
    timestamps).

    The endpoint accepts JSON, form, multipart, XML, or a bare query string;
    this sends JSON, with ``querystring`` appended as query params so the
    script's ``querystring`` column has something in it.
    """
    return client.post(
        f"{_BASE}/{connector}/webhook", json=body, params=querystring or None
    )


# ---------------------------------------------------------------------------
# Events history
# ---------------------------------------------------------------------------


def list_events_history(
    client: KizenClient,
    smart_connector_id: str,
    *,
    event_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_related: bool | None = None,
    ordering: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/smart-connectors/{smart_connector_id}/events-history.

    Note: this endpoint keys off ``smart_connector_id`` (a UUID), not the
    shared ``connector_identifier`` — pass the connector's UUID here, not an
    api_name. Result items are untyped in the API and returned as-is.
    """
    params = _clean_params(
        {
            "event_type": event_type,
            "date_from": date_from,
            "date_to": date_to,
            "include_related": include_related,
            "ordering": ordering,
        }
    )
    return _paginate(
        client, f"{_BASE}/{smart_connector_id}/events-history", params or None
    )

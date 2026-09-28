"""Dev loop: ``pull`` — assemble a local working directory (connector.sql +
__config.json + data/) from a connector's draft (or live) SQL script and its
uploaded input file / seeded objects. Assembled client-side; see
``api.smart_connectors`` for why the server ``/dev-package`` endpoints aren't
used.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors._common import _META_KEYS, MARKER_NAME
from kizen_builder.tools.smart_connectors.seeds import (
    ALL_RECORDS,
    _resolve_filter_group,
    _seed_rows,
)


def _pick_script(detail: dict[str, Any], *, use_live: bool) -> dict[str, Any]:
    """Choose the draft or live script object off a connector detail."""
    draft = detail.get("last_draft_script")
    live = detail.get("live_script")
    chosen = (live if use_live else draft) or draft or live
    if not chosen or not chosen.get("id"):
        raise LookupError(
            "connector has no "
            + ("live" if use_live else "draft")
            + " SQL script to pull."
        )
    return chosen


def _build_config(
    config_metadata: dict[str, Any], detail: dict[str, Any]
) -> dict[str, Any]:
    """Shape the sql-script's ``config_metadata`` into the dev-package
    ``__config.json`` the vendored runner consumes.

    ``config_metadata`` already carries ``input_tables``/``seed_tables``; we add
    the connector-level ``sql_parameters`` and the integration-secret keys the
    runner looks for, and default file paths so a table with no downloaded file
    still parses.
    """
    return {
        "input_tables": [dict(t) for t in config_metadata.get("input_tables", [])],
        "seed_tables": [dict(t) for t in config_metadata.get("seed_tables", [])],
        "integration_secrets": [],
        "sql_parameters": detail.get("sql_parameters") or {},
        "integration_secret_filenames": list(detail.get("integration_secrets") or []),
    }


def _current_execution(
    config_metadata: dict[str, Any], detail: dict[str, Any]
) -> dict[str, Any]:
    triggered = config_metadata.get("triggered") or {}
    row = dict.fromkeys(_META_KEYS)
    row["business_id"] = None  # set by caller (env business_id)
    row["connector_id"] = detail.get("id")
    for k in _META_KEYS:
        if k in triggered:
            row[k] = triggered[k]
    return row


def _flatten_field_value(value: Any) -> str:
    """Render a record's field value as the single cell a CSV column can hold.

    Kizen returns rich values for some field types — a dropdown is
    ``{id, name, code}``, a relationship is ``{id, name, display_name}``, a
    multi-select is a list of those. ClickHouse gets one string per column, so
    each collapses to its label, falling back to its id when it has no label
    (which is the useful half for a relationship: matching by label is what the
    load steps do anyway).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        return str(
            value.get("name") or value.get("display_name") or value.get("id") or ""
        )
    if isinstance(value, list):
        return ",".join(_flatten_field_value(v) for v in value)
    return str(value)


def _export_seed_data(
    client: KizenClient,
    detail: dict[str, Any],
    seed_tables: list[dict[str, Any]],
    data_dir: Path,
    *,
    limit: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Write each seeded object's rows to ``data/<seed name>`` for local runs.

    The rows come from the seed's saved filter group — whose stored
    ``config.query`` is already in the record-search format — or from every
    record for a null group, so the local copy is the same population the live
    run sees. Columns follow the seed table's
    ``columns_mapping`` exactly, since that's the schema the script was written
    against; ``kizen_id`` is the record's own id, which the server always
    includes.

    Best-effort by design: this is a local approximation of a server-side
    export, and rich field values are flattened (see
    :func:`_flatten_field_value`). Anything it can't produce becomes a warning
    rather than a failure — a missing seed CSV only breaks ``run``, not ``pull``.
    """
    from kizen_builder.api import records as records_api

    exported: list[dict[str, Any]] = []
    warnings: list[str] = []
    seeds_by_object_name = {
        (s.get("custom_object") or {}).get("name"): s for s in _seed_rows(detail)
    }

    for table in seed_tables:
        table_name = table.get("table_name")
        # The runtime opens the file under the seed table's `name` verbatim — it
        # appends no extension, so "patients.csv" and "webhooks" both mean what
        # they say.
        file_name = str(table.get("name") or table_name or "unnamed-seed-table")
        columns = [
            c.get("col") for c in (table.get("columns_mapping") or []) if c.get("col")
        ]
        seed = seeds_by_object_name.get(table_name)
        if seed is None or not columns:
            warnings.append(
                f"seed table '{table_name}' has no matching seed config to export "
                f"from — hand-author data/{file_name} before `run`."
            )
            continue

        try:
            # A null group seeds every record, so it searches with no query.
            group: dict[str, Any] = (
                _resolve_filter_group(
                    client, seed["custom_object_id"], seed["group_id"]
                )
                if seed["group_id"]
                else {"name": ALL_RECORDS}
            )
            rows = records_api.search_records(
                client,
                seed["custom_object_id"],
                filters=(group.get("config") or {}).get("query") or [],
                limit=limit,
            )
        except (KizenAPIError, PlanError, KeyError) as exc:
            warnings.append(
                f"could not export seed data for '{table_name}': {exc} — "
                f"hand-author data/{file_name} before `run`."
            )
            continue

        if limit is not None and len(rows) > limit:
            rows = rows[:limit]
            warnings.append(
                f"seed '{table_name}' was truncated to {limit} rows; raise it with "
                f"--seed-limit if the SQL depends on the full set."
            )

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for record in rows:
            writer.writerow(
                [
                    record.get("id")
                    if col == "kizen_id"
                    else _flatten_field_value(records_api.field_value(record, col))
                    for col in columns
                ]
            )
        (data_dir / file_name).write_text(buf.getvalue())
        exported.append(
            {
                "table": table_name,
                "file": file_name,
                "rows": len(rows),
                "columns": columns,
                "filter_group": group.get("name"),
            }
        )

    return exported, warnings


def pull_connector(
    identifier: str,
    dest: str | os.PathLike[str] | None = None,
    *,
    use_live: bool = False,
    overwrite: bool = False,
    seed_limit: int | None = 1000,
) -> dict[str, Any]:
    """Assemble a local working directory for a connector.

    ``seed_limit`` caps how many rows are exported per seeded object (``None``
    for all of them) — a seeded object can be the whole table, and the point of
    the local copy is to exercise the joins, not to mirror production.

    Returns a summary dict (paths written, warnings). Does not print.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, identifier)
        script_ref = _pick_script(detail, use_live=use_live)
        # Re-GET the full script for the freshest user_script + config_metadata.
        script = sc_api.get_sql_script(client, identifier, script_ref["id"])

        api_name = detail.get("api_name") or identifier
        workdir = Path(dest) if dest else Path.cwd() / api_name
        data_dir = workdir / "data"

        if workdir.exists() and any(workdir.iterdir()) and not overwrite:
            raise FileExistsError(
                f"{workdir} already exists and is not empty. Pass overwrite=True "
                f"(--force) to replace connector.sql / __config.json in place."
            )
        data_dir.mkdir(parents=True, exist_ok=True)

        config_metadata = script.get("config_metadata") or {}
        if isinstance(config_metadata, str):
            try:
                config_metadata = json.loads(config_metadata)
            except ValueError:
                config_metadata = {}

        # 1. connector.sql
        user_script = script.get("user_script") or ""
        (workdir / "connector.sql").write_text(user_script)

        # 2. __config.json (dev-package shape)
        cfg = _build_config(config_metadata, detail)

        # 3. download input file(s) and normalize them into data/
        warnings: list[str] = []
        downloaded: list[str] = []
        for table in cfg["input_tables"]:
            file_id = table.get("file_id")
            if not file_id:
                warnings.append(
                    f"input table '{table.get('table_name')}' has no uploaded "
                    f"file — provide one later with `smart-connectors add-input`."
                )
                continue
            try:
                content, fname = sc_api.download_file(config, file_id)
            except Exception as exc:  # noqa: BLE001 - surface as warning, keep going
                warnings.append(f"could not download input file {file_id}: {exc}")
                continue
            fname = fname or f"{table.get('table_name') or 'input'}.csv"
            raw_path = data_dir / fname
            raw_path.write_bytes(content)
            downloaded.append(fname)

        # 4. seed tables: export the live Kizen rows the script joins against, so
        #    `run` exercises the same joins locally.
        seeds_exported: list[dict[str, Any]] = []
        if cfg["seed_tables"]:
            seeds_exported, seed_warnings = _export_seed_data(
                client, detail, cfg["seed_tables"], data_dir, limit=seed_limit
            )
            warnings.extend(seed_warnings)

        # 4b. type-specific input guidance. The vendored runner (Kizen's own
        #     engine) already understands every connector type — it special-cases
        #     'webhooks' and 'schedule' input tables, and named collections for
        #     integration secrets. What differs by type is where the *sample
        #     input data* comes from. File-based types (spreadsheet upload, and
        #     bulkaction when a sample file exists) are pulled automatically; the
        #     rest need a representative input supplied locally.
        ctype = detail.get("connector_type")
        if ctype and ctype != "spreadsheet" and not downloaded:
            hint = {
                "webhook": "provide a sample webhook payload as CSV (columns "
                "timestamp, employee_id, querystring, body) via `add-input`",
                "bulkaction": "no bulk-action sample file was attached — export a "
                "sample of the selected records as CSV and `add-input` it",
                "schedule": "provide a schedule sample (column schedule_trigger_time) "
                "via `add-input`",
                "activity": "provide a sample activity export as CSV via `add-input`",
                "polling_third_party_api": "this connector pulls from an external "
                "API at runtime — supply a representative response as CSV via "
                "`add-input` to exercise the SQL locally",
                "direct_api_connection": "this connector pulls from an external "
                "API at runtime — supply a representative response as CSV via "
                "`add-input` to exercise the SQL locally",
            }.get(ctype)
            if hint:
                warnings.append(f"connector type '{ctype}': {hint}.")

        # 4c. integration secrets: the connector references named collections
        #     whose values live in Kizen. The engine reads them from JSON files
        #     in data/; we can't export secret values, so flag them.
        if detail.get("integration_secrets"):
            warnings.append(
                "connector uses integration secret(s) "
                f"{list(detail['integration_secrets'])} — create a JSON file per "
                "secret in data/ (keys with your dev values) before `run`, or the "
                "SQL's named-collection lookups will fail locally."
            )

        # Write config + current_execution before any normalization step reads it.
        (workdir / "__config.json").write_text(json.dumps(cfg, indent=2))
        cur = _current_execution(config_metadata, detail)
        cur["business_id"] = config.business_id
        (data_dir / "current_execution.json").write_text(json.dumps(cur, indent=2))

        # 5. normalize each downloaded input (Excel→CSV, header dedupe, patches
        #    __config.json file_path/name). Lazy import: needs the extra. If it
        #    isn't installed, leave the raw file in place and warn — the file is
        #    downloaded, just not yet runnable.
        for fname in downloaded:
            try:
                _normalize_input(str(data_dir / fname), str(workdir))
            except ModuleNotFoundError:
                warnings.append(
                    f"'{fname}' was downloaded but not normalized — the "
                    f"'connectors' extra isn't installed. Install it "
                    f"(`uv sync --extra connectors`) and re-run "
                    f"`smart-connectors add-input data/{fname}`, or `run`."
                )

        # 6. marker for run/push
        marker = {
            "connector_id": detail.get("id"),
            "connector_api_name": detail.get("api_name"),
            "connector_name": detail.get("name"),
            "script_id": script.get("id"),
            "script_status": script.get("status"),
            "env": config.name,
            "business_id": config.business_id,
        }
        (workdir / MARKER_NAME).write_text(json.dumps(marker, indent=2))

    return {
        "workdir": str(workdir),
        "connector": detail.get("api_name"),
        "script_id": script.get("id"),
        "script_status": script.get("status"),
        "sql_lines": len(user_script.splitlines()),
        "inputs_downloaded": downloaded,
        "seeds_exported": seeds_exported,
        "warnings": warnings,
    }


def _normalize_input(input_path: str, workdir: str) -> str:
    """Run a downloaded input file through the vendored normalizer, which also
    patches ``__config.json`` (Excel→CSV, header dedupe, timestamped name).

    The vendored helper prints progress (and a "python -m my-connector-package"
    hint that doesn't apply here) straight to stdout; capture and return it so
    the caller controls what the CLI shows.
    """
    import contextlib
    import io

    from kizen_builder.vendor.connector_runtime.process_new_input_file import (
        process_new_input_file,
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        process_new_input_file(input_path, workdir=workdir)
    return buf.getvalue()

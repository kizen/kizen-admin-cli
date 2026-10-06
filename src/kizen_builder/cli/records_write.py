"""`kizen records` — record mutations, plus the spec and `--field` parsing
they share.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import err_console, parse_json, read_text_file
from kizen_builder.cli.records import records_app
from kizen_builder.tools.planners import pipeline_stages as stage_planners
from kizen_builder.tools.planners import records as record_planners


def _coerce_cell(value: str) -> Any:
    """Parse a scalar cell/flag value; JSON-decode list/object literals.

    A value that starts with `[` or `{` is parsed as JSON so multi-select
    lists and explicit `{"id": ...}` refs can be authored inline; anything
    else stays a string (the record planner coerces by field type).
    """
    s = value.strip()
    if s[:1] in "[{":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def _parse_field_flags(fields: list[str]) -> dict[str, Any]:
    """Turn repeatable `--field name=value` into a record mapping."""
    mapping: dict[str, Any] = {}
    for item in fields:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise typer.BadParameter(f"--field must be name=value (got {item!r}).")
        mapping[name] = _coerce_cell(value)
    return mapping


def _read_records_spec(spec_file: str) -> tuple[list[dict[str, Any]], bool]:
    """Read a batch of record mappings from a CSV or JSON file (or stdin).

    JSON may be a single object or a list of objects. CSV uses the header row
    as field api_names; blank cells are skipped (so a sparse wide sheet only
    sets the columns it fills). Returns `(records, from_stdin)`.
    """
    if spec_file:
        text = read_text_file(spec_file, "--spec-file")
        is_csv = spec_file.lower().endswith(".csv")
        from_stdin = False
    else:
        if sys.stdin.isatty():
            err_console.print(
                "[red]error:[/red] no records provided. Pass --field, "
                "--spec-file, or pipe CSV/JSON to stdin."
            )
            raise typer.Exit(code=2)
        text = sys.stdin.read()
        is_csv = False
        from_stdin = True

    stripped = text.lstrip()
    if not is_csv and stripped[:1] in "[{":
        data = parse_json(text, f"--spec-file {spec_file}" if spec_file else "stdin")
        records = data if isinstance(data, list) else [data]
        if not records:
            err_console.print(
                "[red]error:[/red] no records found in the CSV/JSON input."
            )
            raise typer.Exit(code=2)
        return [dict(r) for r in records], from_stdin

    import csv
    import io

    reader = csv.DictReader(io.StringIO(text))
    records = []
    for row in reader:
        mapping = {
            k: _coerce_cell(v)
            for k, v in row.items()
            if k and (k == "id" or (v is not None and v.strip() != ""))
        }
        if mapping:
            records.append(mapping)
    if not records:
        err_console.print("[red]error:[/red] no records found in the CSV/JSON input.")
        raise typer.Exit(code=2)
    return records, from_stdin


@records_app.command(
    "create",
    epilog="Bulk spec shape (CSV or JSON rows): see `kizen docs show records`",
)
def records_create(
    object_api_name: str = typer.Argument(
        ..., help="Object api_name (e.g. client_client)."
    ),
    field: list[str] = typer.Option(
        [],
        "--field",
        "-f",
        help="Set a field: --field api_name=value (repeatable). One record.",
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a CSV or JSON file of records for a bulk create (or pipe to stdin).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Create one record (via --field) or many (via CSV/JSON spec).

    Field values are resolved against the live object schema: dropdown/status
    option labels become option ids, relationship values become record refs,
    and booleans/numbers are coerced. Provide a full wire `fields` list per
    record in a JSON spec if you need exact control.
    """
    from_stdin = False
    if field and spec_file:
        err_console.print("[red]error:[/red] pass --field or --spec-file, not both.")
        raise typer.Exit(code=2)
    if field:
        records = [_parse_field_flags(field)]
    else:
        records, from_stdin = _read_records_spec(spec_file)

    _run_mutation(
        lambda: record_planners.plan_create_records(object_api_name, records),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@records_app.command(
    "update",
    epilog="Bulk spec shape (CSV/JSON rows; each needs an id): see `kizen docs show records`",
)
def records_update(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    record_id: str = typer.Argument(
        None, help="Record UUID (single update). Omit for a bulk spec."
    ),
    field: list[str] = typer.Option(
        [],
        "--field",
        "-f",
        help="Set a field: --field api_name=value (repeatable).",
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a CSV or JSON file of records (each with an 'id') for a bulk update (or stdin).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Update one record (UUID + --field) or many (CSV/JSON spec with 'id').

    Only the fields you set are changed. Bulk specs identify each target row
    by an `id` column/key.
    """
    from_stdin = False
    if record_id:
        if spec_file:
            err_console.print(
                "[red]error:[/red] pass a record UUID or --spec-file, not both."
            )
            raise typer.Exit(code=2)
        if not field:
            err_console.print("[red]error:[/red] give at least one --field to change.")
            raise typer.Exit(code=2)
        mapping = _parse_field_flags(field)
        mapping["id"] = record_id
        records = [mapping]
    else:
        if field:
            err_console.print(
                "[red]error:[/red] --field needs a record UUID; use --spec-file for bulk."
            )
            raise typer.Exit(code=2)
        records, from_stdin = _read_records_spec(spec_file)

    _run_mutation(
        lambda: record_planners.plan_update_records(object_api_name, records),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@records_app.command(
    "upsert",
    epilog="Bulk spec shape (CSV/JSON rows; each needs lookup_value): see `kizen docs show records`",
)
def records_upsert(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    lookup_value: str = typer.Argument(
        None,
        help="Value to match an existing record (name/email). Omit for a bulk spec.",
    ),
    field: list[str] = typer.Option(
        [],
        "--field",
        "-f",
        help="Set a field: --field api_name=value (repeatable). One record.",
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help=(
            "Path to a CSV or JSON file of records (each with a 'lookup_value') "
            "for a bulk upsert (or stdin)."
        ),
    ),
    oncreate_unarchive: str = typer.Option(
        None,
        "--oncreate-unarchive",
        help="On create, if an archived record matches: prompt|unarchive|overwrite.",
    ),
    onupdate_conflict: str = typer.Option(
        None,
        "--onupdate-conflict",
        help="On update, let an archived-record naming conflict proceed: overwrite.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Create-or-update one record (lookup_value + --field) or many (CSV/JSON spec).

    Kizen matches `lookup_value` against the record's name field (email for
    contacts): a hit updates it, a miss creates it. This is the idempotent
    load primitive — re-running a `records create` load duplicates records,
    re-running `records upsert` with the same lookup values does not.
    """
    from_stdin = False
    if lookup_value:
        if spec_file:
            err_console.print(
                "[red]error:[/red] pass a lookup_value or --spec-file, not both."
            )
            raise typer.Exit(code=2)
        if not field:
            err_console.print("[red]error:[/red] give at least one --field to set.")
            raise typer.Exit(code=2)
        mapping = _parse_field_flags(field)
        mapping["lookup_value"] = lookup_value
        records = [mapping]
    else:
        if field:
            err_console.print(
                "[red]error:[/red] --field needs a lookup_value; use --spec-file for bulk."
            )
            raise typer.Exit(code=2)
        records, from_stdin = _read_records_spec(spec_file)

    _run_mutation(
        lambda: record_planners.plan_upsert_records(
            object_api_name,
            records,
            oncreate_unarchive=oncreate_unarchive,
            onupdate_archived_conflict=onupdate_conflict,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@records_app.command(
    "import",
    epilog="Modes, matching and partial success: see `kizen docs show records`",
)
def records_import(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a CSV or JSON file of records (or pipe to stdin).",
    ),
    mode: str = typer.Option(
        "upsert",
        "--mode",
        help="create | upsert (match on name) | update (match on id, else name).",
    ),
    resolution: str = typer.Option(
        "overwrite_except_null",
        "--resolution",
        help="overwrite | only_update_blank | only_add_options | overwrite_except_null "
        "(overwrite clears a field whose cell is blank, so every row must "
        "carry every column).",
    ),
    timeout: float = typer.Option(
        900.0, "--timeout", help="Seconds to wait for the import job to finish."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Load many records as one server-side job through Kizen's CSV uploader.

    Takes the same rows as `records create|upsert|update` (`lookup_value` is
    read as `name`) and waits for the job. Rows the server could only
    partly apply are listed, and they make the command exit 1. In upsert
    and update modes, a row whose name (or id) matches an archived record
    unarchives and updates it.
    """
    records, from_stdin = _read_records_spec(spec_file)
    _run_mutation(
        lambda: record_planners.plan_import_records(
            object_api_name, records, mode, resolution, timeout=timeout
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@records_app.command(
    "archive",
    context_settings={"allow_extra_args": True},
    epilog="Bulk spec shape (CSV/JSON rows; each needs an id): see `kizen docs show records`",
)
def records_archive(
    ctx: typer.Context,
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    record_id: str = typer.Argument(
        None, help="Record UUID (single archive). Omit for a bulk spec."
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a CSV or JSON file of records (each with an 'id') to archive (or stdin).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Archive one record (UUID) or many (CSV/JSON spec with 'id').

    This is the operation the UI's Archive button performs, batched and
    without Kizen's email notification. Data is retained; restore with
    `records unarchive`. `records list --output csv` is a valid spec.
    """
    if ctx.args:
        err_console.print(
            "[red]error:[/red] pass one record UUID. For several, use "
            "--spec-file or pipe CSV/JSON rows with an 'id' to stdin."
        )
        raise typer.Exit(code=2)
    from_stdin = False
    if record_id:
        if spec_file:
            err_console.print(
                "[red]error:[/red] pass a record UUID or --spec-file, not both."
            )
            raise typer.Exit(code=2)
        ids = [record_id]
    else:
        if not spec_file and sys.stdin.isatty():
            err_console.print(
                "[red]error:[/red] no records provided. Pass a record UUID, "
                "--spec-file, or pipe CSV/JSON to stdin."
            )
            raise typer.Exit(code=2)
        records, from_stdin = _read_records_spec(spec_file)
        ids = []
        for n, row in enumerate(records, 1):
            rid = str(row.get("id") or "").strip()
            if not rid:
                err_console.print(f"[red]error:[/red] row {n} has no 'id'.")
                raise typer.Exit(code=2)
            ids.append(rid)

    _run_mutation(
        lambda: record_planners.plan_archive_records(object_api_name, ids),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@records_app.command(
    "delete",
    hidden=True,
    add_help_option=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def records_delete() -> None:
    """Removed: use `records archive`."""
    err_console.print(
        "[red]error:[/red] records delete was removed; records are archived, "
        "not erased: use kizen records archive <object> <uuid> or --spec-file"
    )
    raise typer.Exit(code=2)


@records_app.command("unarchive")
def records_unarchive(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    record_id: list[str] = typer.Argument(
        None, help="One or more record UUIDs to unarchive."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Unarchive one or more records by UUID.

    The reverse of `records archive` (also reachable via `records upsert
    --oncreate-unarchive unarchive`).
    """
    ids = list(record_id or [])
    if not ids:
        err_console.print(
            "[red]error:[/red] pass at least one record UUID to unarchive."
        )
        raise typer.Exit(code=2)

    _run_mutation(
        lambda: record_planners.plan_unarchive_records(object_api_name, ids),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@records_app.command("set-field")
def records_set_field(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    record_id: list[str] = typer.Argument(
        None, help="One or more record UUIDs to update."
    ),
    field: str = typer.Option(..., "--field", help="Field api_name to set."),
    value: str = typer.Option(
        ...,
        "--value",
        help="New value (resolved against the field's type, same coercion as `records create`).",
    ),
    resolution: str = typer.Option(
        "overwrite",
        "--resolution",
        help="overwrite | add_only | remove_only | update_if_blank | overwrite_except_null "
        "(add_only/remove_only apply to multi-select fields).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Set one field to one value across many records in a single API call.

    Wraps the bulk-change-field-value endpoint — a single request instead of
    N per-record PATCHes. Targets an explicit list of record UUIDs; filtering
    a large record set to build that list first is `records list --filter`'s
    job (there's no server-side bulk-by-filter wired up here yet — see
    `kizen docs show automation`).
    """
    ids = list(record_id or [])
    if not ids:
        err_console.print("[red]error:[/red] pass at least one record UUID.")
        raise typer.Exit(code=2)

    _run_mutation(
        lambda: record_planners.plan_set_field(
            object_api_name, ids, field, value, field_resolution=resolution
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@records_app.command("move")
def records_move(
    object_api_name: str = typer.Argument(
        ..., help="Pipeline object api_name (or UUID)."
    ),
    record_id: str = typer.Argument(..., help="Record UUID to move."),
    stage: str = typer.Option(..., "--stage", help="Target stage name or UUID."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Move a pipeline record to a different stage."""
    _run_mutation(
        lambda: stage_planners.plan_move_record(object_api_name, record_id, stage),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

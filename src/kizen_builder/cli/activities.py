"""`kizen activities` — activity types / loggable definitions, plus the
object-resolution helpers the rest of the activity surface shares.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    _short,
    app,
    cli_errors,
    console,
    err_console,
    read_json_file,
)
from kizen_builder.tools import activities as act_tools
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools.planners import activities as act_planners


def _resolve_object_id(token: str) -> str:
    """Resolve one custom-object api_name (or raw UUID) to its UUID."""
    objs = obj_tools.list_objects()
    by_api = {o["api_name"]: o["id"] for o in objs}
    if token in by_api:
        return by_api[token]
    if token in {o["id"] for o in objs}:
        return token
    raise typer.BadParameter(f"object '{token}' not found. Available: {sorted(by_api)}")


def _resolve_linked_field(spec: str) -> tuple[str, str]:
    """Resolve `object_api_name.field_api_name` to (field_uuid, field_display_name).

    Used to add a Custom Field on an activity — one that surfaces an existing
    custom-object field (view-only or editable). Accepts a raw field UUID too
    (returns it with an empty display name).
    """
    if "." not in spec:
        # Bare UUID — pass through, no display name to derive.
        return spec, ""
    object_api, field_api = spec.split(".", 1)
    try:
        obj = obj_tools.get_object(object_api)
    except (LookupError, KizenAPIError) as e:
        raise typer.BadParameter(f"object '{object_api}' not found: {e}") from e
    match = next(
        (
            f
            for f in obj["fields"]
            if f.get("api_name") == field_api and not f.get("deleted")
        ),
        None,
    )
    if match is None:
        available = [f["api_name"] for f in obj["fields"] if not f.get("deleted")]
        raise typer.BadParameter(
            f"field '{field_api}' not found on '{object_api}'. Available: {available}"
        )
    return match["id"], match.get("display_name") or field_api


def _resolve_associated_objects(object_api_names: list[str]) -> list[dict[str, Any]]:
    """Resolve custom-object api_names to the `associated_objects` wire shape.

    Kizen expects `[{"custom_object": {"id": <uuid>}}, …]` for an activity's
    `selected_objects_associated` mode. Accepts api_names or raw UUIDs.
    """
    return [{"custom_object": {"id": _resolve_object_id(t)}} for t in object_api_names]


activities_app = typer.Typer(
    help=(
        "Read and edit activity types (loggable definitions), their fields, and "
        "read logged/scheduled instances. An 'activity' here is the type/template "
        "— logging and scheduling instances happen in the Kizen UI, not here."
    ),
    no_args_is_help=True,
)
app.add_typer(activities_app, name="activities")


@activities_app.command("list")
def activities_list(
    obj: str = typer.Option(
        "", "--object", help="Filter by custom object api_name (or UUID)."
    ),
    search: str = typer.Option("", "--search", help="Filter by name text."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List activity types in the configured env."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        object_id = _resolve_object_id(obj) if obj else None
        items = act_tools.list_activities(
            custom_object_id=object_id, search=search or None
        )

    def table() -> None:
        t = Table(title="Activity types")
        t.add_column("name")
        t.add_column("api_name")
        t.add_column("submissions", justify="right")
        t.add_column("association")
        t.add_column("editable", justify="center")
        t.add_column("id")
        for a in items:
            t.add_row(
                a.get("name") or "",
                a.get("api_name") or "",
                str(
                    a.get("n_submissions") if a.get("n_submissions") is not None else ""
                ),
                a.get("association_mode") or "",
                "✓" if a.get("is_editable") else "",
                a.get("id") or "",
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("name", "name"),
            out.Column("api_name", "api_name"),
            out.Column("n_submissions", "n_submissions"),
            out.Column("association_mode", "association_mode"),
            out.Column("is_editable", "is_editable"),
            out.Column("id", "id"),
        ],
    )


@activities_app.command("get")
def activities_get(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one activity type: metadata, fields, and visibility rules.

    CSV emits the field list (one row per field); the header, visibility rules,
    and associations are table/JSON only.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        result = act_tools.get_activity(identifier)

    def table() -> None:
        console.print(
            f"[bold]{result['name']}[/bold]  "
            f"[dim]({result['api_name']}, id={result['id']})[/dim]"
        )
        meta_bits = [
            f"association={result.get('association_mode')}",
            f"editable={result.get('is_editable')}",
            f"submissions={result.get('n_submissions')}",
        ]
        if result.get("description"):
            meta_bits.append(f"description={result['description']}")
        console.print("[dim]" + "  ".join(meta_bits) + "[/dim]")

        objs = result.get("custom_objects") or []
        if objs:
            console.print(
                "[dim]objects: "
                + ", ".join(o.get("name") or o.get("id") for o in objs)
                + "[/dim]"
            )

        fld_table = Table(title="Fields")
        fld_table.add_column("order", justify="right")
        fld_table.add_column("api_name")
        fld_table.add_column("display_name")
        fld_table.add_column("type")
        fld_table.add_column("req", justify="center")
        fld_table.add_column("hidden", justify="center")
        fld_table.add_column("options / linked field", style="dim")
        fld_table.add_column("id")
        for f in result["fields"]:
            opts = ", ".join(
                o["name"] for o in (f.get("options") or []) if o.get("name")
            )
            detail = f"→ {f['linked_field']}" if f.get("linked_field") else opts
            fld_table.add_row(
                str(f.get("order") if f.get("order") is not None else ""),
                f.get("api_name") or "",
                f.get("display_name") or "",
                f.get("field_type") or "",
                "✓" if f.get("is_required") else "",
                "✓" if f.get("is_hidden") else "",
                _short(detail, 40),
                f.get("id") or "",
            )
        console.print(fld_table)

        rules = result.get("visibility_rules")
        if rules:
            console.print(f"[bold]Visibility rules[/bold] ({len(rules)})")
            console.print_json(json.dumps(rules))
        else:
            console.print("[dim]no visibility rules[/dim]")

    out.render(
        fmt,
        json_data={k: v for k, v in result.items() if k != "raw"},
        table=table,
        csv_rows=result["fields"],
        csv_columns=[
            out.Column("order", "order"),
            out.Column("api_name", "api_name"),
            out.Column("display_name", "display_name"),
            out.Column("field_type", "field_type"),
            out.Column("is_required", "is_required"),
            out.Column("is_hidden", "is_hidden"),
            out.Column("id", "id"),
        ],
    )


@activities_app.command(
    "create",
    epilog="Spec shape (an ActivityDef, may include fields): see `kizen docs show activity`",
)
def activities_create(
    name: str = typer.Option("", "--name", help="Display name (single-activity mode)."),
    api_name: str = typer.Option(
        "", "--api-name", help="api_name (optional; Kizen derives one if omitted)."
    ),
    description: str = typer.Option("", "--description", help="Activity description."),
    association_mode: str = typer.Option(
        "",
        "--association-mode",
        help="all_objects_associated | selected_objects_associated | no_objects_associated.",
    ),
    editable: bool | None = typer.Option(
        None,
        "--editable/--not-editable",
        help="Whether logged instances can be edited.",
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Bulk mode: path to a JSON ActivityDef (with optional inline fields). "
        "Omit with stdin to read from stdin.",
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
    """Create one activity type from flags, or a JSON ActivityDef (--spec-file/stdin).

    Flag mode covers the common metadata; use a spec for inline fields,
    visibility rules, or object associations.
    """
    if spec_file and name:
        err_console.print(
            "[red]error:[/red] pass either --name flags or a --spec-file/stdin spec, not both."
        )
        raise typer.Exit(code=2)

    spec, from_stdin = (None, False) if name else _read_spec(spec_file, optional=True)
    if spec is not None:
        _run_mutation(
            lambda: act_planners.plan_create_activity(spec),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
            stdin_consumed=from_stdin,
        )
        return

    if not name:
        err_console.print(
            "[red]error:[/red] --name is required (or pass a spec via --spec-file/stdin)."
        )
        raise typer.Exit(code=2)

    spec_dict: dict[str, Any] = {"name": name}
    if api_name:
        spec_dict["api_name"] = api_name
    if description:
        spec_dict["description"] = description
    if association_mode:
        spec_dict["association_mode"] = association_mode
    if editable is not None:
        spec_dict["is_editable"] = editable

    _run_mutation(
        lambda: act_planners.plan_create_activity(spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@activities_app.command(
    "update",
    epilog="Advanced spec shape (ActivityDef changes / visibility rules): see `kizen docs show activity`",
)
def activities_update(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    name: str = typer.Option("", "--name", help="New display name."),
    description: str = typer.Option("", "--description", help="New description."),
    association_mode: str = typer.Option(
        "", "--association-mode", help="New association mode."
    ),
    obj: list[str] = typer.Option(
        [],
        "--object",
        help="Custom-object api_name to associate (repeatable). Sets association "
        "mode to selected_objects_associated unless --association-mode is given.",
    ),
    editable: bool | None = typer.Option(
        None, "--editable/--not-editable", help="Set the editable flag."
    ),
    visibility_rules_file: str = typer.Option(
        "",
        "--visibility-rules-file",
        help="Path to a JSON array of visibility-rule dicts to REPLACE the current set.",
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON dict of changes (advanced; keys map to the wire body).",
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
    """Update one activity type. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if spec_file:
        changes = read_json_file(spec_file, "--spec-file")
    if name:
        changes["name"] = name
    if description:
        changes["description"] = description
    if association_mode:
        changes["association_mode"] = association_mode
    if obj:
        changes["associated_objects"] = _resolve_associated_objects(obj)
        changes.setdefault("association_mode", "selected_objects_associated")
    if editable is not None:
        changes["is_editable"] = editable
    if visibility_rules_file:
        changes["visibility_rules"] = read_json_file(
            visibility_rules_file, "--visibility-rules-file"
        )

    if not changes:
        err_console.print("[red]error:[/red] no changes given.")
        raise typer.Exit(code=2)

    _run_mutation(
        lambda: act_planners.plan_update_activity(identifier, changes),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@activities_app.command("delete")
def activities_delete(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
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
    """Delete one activity type."""
    _run_mutation(
        lambda: act_planners.plan_delete_activity(identifier),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


# --- activity fields -------------------------------------------------------

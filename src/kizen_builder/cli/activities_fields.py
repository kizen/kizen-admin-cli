"""`kizen activities fields` — fields on an activity type, and their options."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
    err_console,
    warn_renamed_flag,
)
from kizen_builder.cli.activities import _resolve_linked_field, activities_app
from kizen_builder.tools import activities as act_tools
from kizen_builder.tools.planners import activities as act_planners

act_fields_app = typer.Typer(
    help="Manage the fields on an activity type.", no_args_is_help=True
)
activities_app.add_typer(act_fields_app, name="fields")


@act_fields_app.command("list")
def act_fields_list(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the fields on an activity type (same view as `activities get`)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        result = act_tools.get_activity(identifier)
    fields = result["fields"]

    def table() -> None:
        t = Table(title=f"Fields — {result['name']}")
        t.add_column("order", justify="right")
        t.add_column("api_name")
        t.add_column("display_name")
        t.add_column("type")
        t.add_column("req", justify="center")
        t.add_column("id")
        for f in fields:
            t.add_row(
                str(f.get("order") if f.get("order") is not None else ""),
                f.get("api_name") or "",
                f.get("display_name") or "",
                f.get("field_type") or "",
                "✓" if f.get("is_required") else "",
                f.get("id") or "",
            )
        console.print(t)

    out.render(
        fmt,
        json_data=fields,
        table=table,
        csv_rows=fields,
        csv_columns=[
            out.Column("order", "order"),
            out.Column("api_name", "api_name"),
            out.Column("display_name", "display_name"),
            out.Column("field_type", "field_type"),
            out.Column("is_required", "is_required"),
            out.Column("id", "id"),
        ],
    )


@act_fields_app.command(
    "create",
    epilog="Bulk spec shape (an ActivityFieldDef list): see `kizen docs show activity`",
)
def act_fields_create(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    api_name: str = typer.Option(
        "", "--api-name", help="Field api_name (optional; server derives one)."
    ),
    display_name: str = typer.Option(
        "", "--name", help="Field display name (single-field mode)."
    ),
    field_type: str = typer.Option(
        "", "--type", help="Field type (text, dropdown, rating, etc.)."
    ),
    linked_field: str = typer.Option(
        "",
        "--linked-field",
        help="Add a Custom Field surfacing an existing custom-object field, named "
        "'object_api_name.field_api_name' (implies --type activity_custom_field). "
        "View-only or editable back onto the record, per its config in Kizen.",
    ),
    description: str = typer.Option("", "--description", help="Field description."),
    required: bool = typer.Option(False, "--required", help="Mark the field required."),
    hidden: bool = typer.Option(False, "--hidden", help="Hide the field by default."),
    option: list[str] = typer.Option(
        [], "--option", help="Choice option (repeatable)."
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Bulk mode: JSON list of ActivityFieldDef objects (or an object with a "
        '"fields" list). Omit with stdin to read from stdin.',
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
    """Add one field, or many at once from a JSON spec, to an activity type.

    Two kinds of single field: a plain activity field (--type ...), or a Custom
    Field that surfaces an existing custom-object field (--linked-field
    object.field). Bulk mode (--spec-file/stdin) takes either or both.
    """
    if spec_file and (display_name or linked_field):
        err_console.print(
            "[red]error:[/red] pass either single-field flags or a bulk spec, not both."
        )
        raise typer.Exit(code=2)

    from_stdin = False
    if spec_file:
        spec_text = Path(spec_file).read_text()
    elif not display_name and not linked_field and not sys.stdin.isatty():
        spec_text = sys.stdin.read()
        from_stdin = True
    else:
        spec_text = ""

    if spec_text:
        try:
            spec = json.loads(spec_text)
        except json.JSONDecodeError as e:
            err_console.print(f"[red]error parsing JSON:[/red] {e}")
            raise typer.Exit(code=2) from e
        fields = spec.get("fields", spec) if isinstance(spec, dict) else spec
        if not isinstance(fields, list):
            err_console.print(
                '[red]error:[/red] spec must be a JSON list of fields (or {"fields": [...]}).'
            )
            raise typer.Exit(code=2)
        _run_mutation(
            lambda: act_planners.plan_create_activity_fields(identifier, fields),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
            stdin_consumed=from_stdin,
        )
        return

    if linked_field:
        if field_type:
            err_console.print(
                "[red]error:[/red] --linked-field sets the type; don't also pass --type."
            )
            raise typer.Exit(code=2)
        cof_id, cof_display = _resolve_linked_field(linked_field)
        field_dict: dict[str, Any] = {
            "name": display_name or cof_display,
            "field_type": "activity_custom_field",
            "custom_object_field": cof_id,
            "required": required,
            "hidden": hidden,
        }
    else:
        missing = [
            f for f, v in (("--name", display_name), ("--type", field_type)) if not v
        ]
        if missing:
            err_console.print(
                f"[red]error:[/red] single-field create needs {', '.join(missing)} "
                "(or use --linked-field object.field for a Custom Field)."
            )
            raise typer.Exit(code=2)
        field_dict = {
            "name": display_name,
            "field_type": field_type,
            "required": required,
            "hidden": hidden,
        }
        if option:
            field_dict["options"] = option
    if api_name:
        field_dict["api_name"] = api_name
    if description:
        field_dict["description"] = description

    _run_mutation(
        lambda: act_planners.plan_create_activity_fields(identifier, [field_dict]),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@act_fields_app.command("update")
def act_fields_update(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    field_api_name: str = typer.Argument(..., help="Field api_name to update."),
    name: str = typer.Option("", "--name", help="New display name."),
    description: str = typer.Option("", "--description", help="New description."),
    required: bool | None = typer.Option(
        None, "--required/--optional", help="Set the required flag."
    ),
    hidden: bool | None = typer.Option(
        None, "--hidden/--visible", help="Set the hidden flag."
    ),
    order: int = typer.Option(-1, "--order", help="New order index (>=0)."),
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
    """Update one field on an activity type. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    if description:
        changes["description"] = description
    if required is not None:
        changes["required"] = required
    if hidden is not None:
        changes["hidden"] = hidden
    if order >= 0:
        changes["order"] = order

    if not changes:
        err_console.print("[red]error:[/red] no changes given.")
        raise typer.Exit(code=2)

    _run_mutation(
        lambda: act_planners.plan_update_activity_field(
            identifier, field_api_name, changes
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@act_fields_app.command("delete")
def act_fields_delete(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    field_api_name: str = typer.Argument(..., help="Field api_name to delete."),
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
    """Delete one field from an activity type."""
    _run_mutation(
        lambda: act_planners.plan_delete_activity_field(identifier, field_api_name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


act_field_options_app = typer.Typer(
    help="Add or remove options on a select-type activity field.", no_args_is_help=True
)
act_fields_app.add_typer(act_field_options_app, name="options")


@act_field_options_app.command("add")
def act_field_options_add(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    field_api_name: str = typer.Argument(..., help="Field api_name."),
    option: list[str] = typer.Option(
        [], "--option", help="Option label to add (repeatable)."
    ),
    option_short: list[str] = typer.Option([], "-o", hidden=True),
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
    """Add options to a select-type activity field. Existing names are skipped."""
    if option_short:
        warn_renamed_flag("-o", "--option")
        option = [*option, *option_short]
    if not option:
        err_console.print("[red]error:[/red] pass at least one --option.")
        raise typer.Exit(code=2)
    _run_mutation(
        lambda: act_planners.plan_add_activity_field_options(
            identifier, field_api_name, option
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@act_field_options_app.command("remove")
def act_field_options_remove(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    field_api_name: str = typer.Argument(..., help="Field api_name."),
    option: str = typer.Argument(..., help="Option to remove (name, code, or UUID)."),
    remap_to: str = typer.Option(
        "",
        "--remap-to",
        help="Move records using the removed option onto this option first.",
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
    """Remove one option from a select-type activity field."""
    _run_mutation(
        lambda: act_planners.plan_remove_activity_field_option(
            identifier, field_api_name, option, remap_to=remap_to or None
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


# --- logged / scheduled instances (read-only) ------------------------------

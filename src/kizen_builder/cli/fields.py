"""`kizen fields` — custom fields and their options."""

from __future__ import annotations

from typing import Any

import typer

from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import app, err_console, warn_renamed_flag
from kizen_builder.tools.planners import fields as field_planners

fields_app = typer.Typer(
    help="Create and update fields on custom objects.", no_args_is_help=True
)
app.add_typer(fields_app, name="fields")


def _normalize_fields_spec(
    spec: Any, default_category: str
) -> list[tuple[dict[str, Any], str | None]]:
    """Turn a bulk fields spec into `(field_dict, category)` pairs.

    Accepts either a JSON list of field dicts, or an object
    `{"category": <default>, "fields": [...]}`. Each field dict is a
    FieldDef shape; a per-field `"category"` key (stripped before
    validation) overrides the spec-level default, which overrides the
    `--category` flag.
    """
    if isinstance(spec, dict) and "fields" in spec:
        spec_default = spec.get("category") or default_category
        items = spec["fields"]
    elif isinstance(spec, list):
        spec_default = default_category
        items = spec
    else:
        raise typer.BadParameter(
            "fields spec must be a JSON list of field objects, or an object "
            'with a "fields" list (optionally a "category" default).'
        )
    if not isinstance(items, list):
        raise typer.BadParameter('"fields" must be a JSON list.')

    out: list[tuple[dict[str, Any], str | None]] = []
    for item in items:
        if not isinstance(item, dict):
            raise typer.BadParameter("each field in the spec must be a JSON object.")
        field = dict(item)
        cat = field.pop("category", None) or spec_default or None
        out.append((field, cat))
    return out


@fields_app.command(
    "create",
    epilog="Bulk spec shape (a FieldDef list) with a copy-paste template: see `kizen docs show field`",
)
def fields_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    api_name: str = typer.Option(
        "", "--api-name", help="New field api_name (single-field mode)."
    ),
    display_name: str = typer.Option(
        "", "--name", help="Human display name (single-field mode)."
    ),
    field_type: str = typer.Option(
        "", "--type", help="Field type (text, dropdown, rating, etc.)."
    ),
    category: str = typer.Option(
        "", "--category", help="Display name of the target category."
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Bulk mode: path to a JSON list of field objects (or an object "
        'with a "fields" list). Omit with stdin to read the spec from stdin. '
        "One plan/confirm/apply for the whole batch.",
    ),
    description: str = typer.Option("", "--description", help="Field description."),
    hidden: bool = typer.Option(False, "--hidden", help="Hide the field by default."),
    option: list[str] = typer.Option(
        [],
        "--option",
        help="Choice option (repeatable; for dropdown/radio/checkboxes/choices).",
    ),
    status_options: str = typer.Option(
        "",
        "--status-options",
        help='JSON array of status stage objects, e.g. \'[{"name":"Complete","status":"won"}]\'.',
    ),
    relation_target: str = typer.Option(
        "",
        "--relation-target",
        help="api_name of the target object (for relationship fields).",
    ),
    relation_cardinality: str = typer.Option(
        "many_to_one",
        "--relation-cardinality",
        help="Cardinality from this object toward the target, in the same "
        "terms Kizen's UI uses: one_to_one, many_to_one (this object holds a "
        "single reference to the target; many records here can point to the "
        "same one), one_to_many (this object relates to many target "
        "records), many_to_many (multi-select on both sides).",
    ),
    relation_related_name: str = typer.Option(
        "",
        "--relation-related-name",
        help="Display label for the inverse relation field shown on the target object. "
        "Defaults to this object's name.",
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
    """Create one field, or many at once from a JSON spec (--spec-file/stdin).

    Single-field mode uses the --api-name/--name/--type/--category flags.
    Bulk mode (a --spec-file, or a piped spec) creates every field in the
    spec in one plan/confirm/apply. required/read-only are reserved for
    system fields, not custom fields, so they aren't exposed as options here.
    """
    # Bulk mode: an explicit --spec-file, or a piped spec when no --api-name
    # was given. Single-field flags and bulk mode are mutually exclusive —
    # reject the conflict before touching the file.
    if spec_file and api_name:
        err_console.print(
            "[red]error:[/red] pass either single-field flags (--api-name …) "
            "or a bulk spec (--spec-file/stdin), not both."
        )
        raise typer.Exit(code=2)

    spec, from_stdin = (
        (None, False) if api_name else _read_spec(spec_file, optional=True)
    )
    if spec is not None:
        pairs = _normalize_fields_spec(spec, category)
        _run_mutation(
            lambda: field_planners.plan_create_fields(object_api_name, pairs),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
            stdin_consumed=from_stdin,
        )
        return

    missing = [
        flag
        for flag, val in (
            ("--api-name", api_name),
            ("--name", display_name),
            ("--type", field_type),
        )
        if not val
    ]
    if missing:
        err_console.print(
            f"[red]error:[/red] single-field create needs {', '.join(missing)} "
            "(or pass a bulk spec via --spec-file/stdin)."
        )
        raise typer.Exit(code=2)

    field_dict: dict[str, Any] = {
        "name": display_name,
        "api_name": api_name,
        "field_type": field_type,
        "hidden": hidden,
    }
    if description:
        field_dict["description"] = description
    if option:
        field_dict["options"] = option
    if status_options:
        import json as _json

        try:
            field_dict["status_options"] = _json.loads(status_options)
        except _json.JSONDecodeError as e:
            err_console.print(
                f"[red]error:[/red] --status-options must be valid JSON: {e}"
            )
            raise typer.Exit(code=1) from e
    if relation_target:
        field_dict["relation"] = {
            "target_object": relation_target,
            "relation_type": relation_cardinality,
        }
        if relation_related_name:
            field_dict["relation"]["related_name"] = relation_related_name

    _run_mutation(
        lambda: field_planners.plan_create_field(
            object_api_name=object_api_name,
            field=field_dict,
            category=category,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@fields_app.command("update")
def fields_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    field_api_name: str = typer.Argument(..., help="Field api_name to update."),
    name: str = typer.Option("", "--name", help="New display name (optional)."),
    description: str = typer.Option(
        "", "--description", help="New description (use empty string to clear)."
    ),
    hidden: bool | None = typer.Option(
        None, "--hidden/--visible", help="Set hidden flag."
    ),
    category: str = typer.Option(
        "", "--category", help="Move to a different category (display name)."
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
    """Update one field. Only the flags you set are changed.

    required/read-only are reserved for system fields, not custom fields,
    so they aren't exposed as options here.
    """
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    if description:
        changes["description"] = description
    if hidden is not None:
        changes["hidden"] = hidden

    _run_mutation(
        lambda: field_planners.plan_update_field(
            object_api_name=object_api_name,
            field_api_name=field_api_name,
            changes=changes,
            category=category or None,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@fields_app.command("delete")
def fields_delete(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
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
    """Delete one custom field. This removes the field's data across all records."""
    _run_mutation(
        lambda: field_planners.plan_delete_field(object_api_name, field_api_name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


field_options_app = typer.Typer(
    help="Add or remove options on select-type fields (dropdown, status, etc.).",
    no_args_is_help=True,
)
fields_app.add_typer(field_options_app, name="options")


@field_options_app.command("add")
def field_options_add(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
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
    """Add one or more options to a select-type field. Existing names are skipped."""
    if option_short:
        warn_renamed_flag("-o", "--option")
        option = [*option, *option_short]
    if not option:
        err_console.print("[red]error:[/red] pass at least one --option.")
        raise typer.Exit(code=2)
    _run_mutation(
        lambda: field_planners.plan_add_field_options(
            object_api_name, field_api_name, option
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@field_options_app.command("remove")
def field_options_remove(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    field_api_name: str = typer.Argument(..., help="Field api_name."),
    option: str = typer.Argument(..., help="Option to remove (name, code, or UUID)."),
    remap_to: str = typer.Option(
        "",
        "--remap-to",
        help="Move records using the removed option onto this option (name/code/UUID) first.",
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
    """Remove one option from a select-type field.

    Without --remap-to, records currently set to the option lose that value.
    With --remap-to, they are reassigned before the option is dropped.
    """
    _run_mutation(
        lambda: field_planners.plan_remove_field_option(
            object_api_name, field_api_name, option, remap_to=remap_to or None
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

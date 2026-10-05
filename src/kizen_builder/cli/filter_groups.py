"""`kizen filter-groups` — per-object saved filters / segments."""

from __future__ import annotations

from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
    parse_json,
    read_text_file,
)
from kizen_builder.tools import saved_views as sv_tools
from kizen_builder.tools.planners import saved_views as sv_planners

filter_groups_app = typer.Typer(
    help="Per-object saved filters (segments): /filter-groups.", no_args_is_help=True
)
app.add_typer(filter_groups_app, name="filter-groups")


def _load_filter_spec(filter_json: str, filter_file: str) -> dict[str, Any] | None:
    if filter_json and filter_file:
        err_console.print("[red]error:[/red] pass --filter or --filter-file, not both.")
        raise typer.Exit(code=2)
    text = read_text_file(filter_file, "--filter-file") if filter_file else filter_json
    if not text:
        return None
    return parse_json(
        text, f"--filter-file {filter_file}" if filter_file else "--filter"
    )


@filter_groups_app.command("list")
def filter_groups_list(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    search: str = typer.Option(
        "", "--search", help="Filter by name (server-side search)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List filter groups saved on an object."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        items = sv_tools.list_saved_views(
            object_api_name, sv_planners.FILTER_GROUPS_BASE, search=search or None
        )

    def table() -> None:
        t = Table(title=f"Filter groups — {object_api_name}")
        t.add_column("name")
        t.add_column("hidden", justify="center")
        t.add_column("owner")
        t.add_column("id")
        for i in items:
            t.add_row(
                i.get("name") or "",
                "✓" if i.get("hidden") else "",
                i.get("owner") or "",
                i.get("id") or "",
            )
        console.print(t)

    out.render(fmt, json_data=items, table=table)


@filter_groups_app.command("get")
def filter_groups_get(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    filter_group: str = typer.Argument(..., help="Filter group UUID or exact name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one filter group's full config and sharing settings."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        item = sv_tools.find_saved_view(
            object_api_name, sv_planners.FILTER_GROUPS_BASE, filter_group
        )

    def table() -> None:
        console.print_json(data=item)

    out.render(fmt, json_data=item, table=table)


@filter_groups_app.command(
    "create",
    epilog="Filter shape (the filtering DSL): see `kizen docs show saved-views`",
)
def filter_groups_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    name: str = typer.Option(..., "--name", help="Filter group name."),
    filter_json: str = typer.Option(
        "",
        "--filter",
        help='Filter as JSON: a spec ({"all"|"any": [...]} of {"field","op","value"}) '
        'resolved against the object schema, or a raw {"query": [...]} block.',
    ),
    filter_file: str = typer.Option(
        "", "--filter-file", help="Path to a JSON filter (same format as --filter)."
    ),
    hidden: bool = typer.Option(
        False, "--hidden", help="Hide from the filter-group picker by default."
    ),
    owner: str = typer.Option(
        "", "--owner", help="Owning team member UUID (defaults to the API caller)."
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
    """Create one filter group (saved segment) on an object.

    Sharing defaults to visible to all team members (Admin role as admin) —
    the same default `dashboards create` uses. Note: the exact rendering of
    a filter group's config in the Kizen list-view UI hasn't been visually
    confirmed yet; it's built from the same filter DSL as `records list
    --filter` and automation condition steps.
    """
    spec: dict[str, Any] = {"name": name, "hidden": hidden}
    fc = _load_filter_spec(filter_json, filter_file)
    if fc is not None:
        spec["config"] = fc
    if owner:
        spec["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_create_filter_group(object_api_name, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@filter_groups_app.command(
    "update",
    epilog="Filter shape (the filtering DSL): see `kizen docs show saved-views`",
)
def filter_groups_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    filter_group: str = typer.Argument(..., help="Filter group UUID or exact name."),
    name: str = typer.Option("", "--name", help="New name."),
    filter_json: str = typer.Option(
        "", "--filter", help="New filter as JSON (same format as `create`)."
    ),
    filter_file: str = typer.Option(
        "", "--filter-file", help="Path to a JSON filter (same format as --filter)."
    ),
    hidden: bool | None = typer.Option(
        None, "--hidden/--visible", help="Set the hidden flag."
    ),
    owner: str = typer.Option("", "--owner", help="New owning team member UUID."),
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
    """Update one filter group. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    fc = _load_filter_spec(filter_json, filter_file)
    if fc is not None:
        changes["config"] = fc
    if hidden is not None:
        changes["hidden"] = hidden
    if owner:
        changes["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_update_filter_group(
            object_api_name, filter_group, changes
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@filter_groups_app.command("delete")
def filter_groups_delete(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    filter_group: str = typer.Argument(..., help="Filter group UUID or exact name."),
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
    """Delete one filter group."""
    _run_mutation(
        lambda: sv_planners.plan_delete_filter_group(object_api_name, filter_group),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

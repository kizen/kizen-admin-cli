"""`kizen quick-filters` — per-object quick-filter chips."""

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
    load_filter_spec,
)
from kizen_builder.cli.permissions import _resolve_role_id
from kizen_builder.tools import saved_views as sv_tools
from kizen_builder.tools.planners import saved_views as sv_planners

quick_filters_app = typer.Typer(
    help="Per-object quick-filter chips: /quick-filters.", no_args_is_help=True
)
app.add_typer(quick_filters_app, name="quick-filters")


@quick_filters_app.command("list")
def quick_filters_list(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    search: str = typer.Option(
        "", "--search", help="Filter by name (server-side search)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List quick filters saved on an object."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        items = sv_tools.list_saved_views(
            object_api_name, sv_planners.QUICK_FILTERS_BASE, search=search or None
        )

    def table() -> None:
        t = Table(title=f"Quick filters — {object_api_name}")
        t.add_column("name")
        t.add_column("owner")
        t.add_column("id")
        for i in items:
            t.add_row(i.get("name") or "", i.get("owner") or "", i.get("id") or "")
        console.print(t)

    out.render(fmt, json_data=items, table=table)


@quick_filters_app.command("get")
def quick_filters_get(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one quick filter's full config and sharing settings."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        item = sv_tools.find_saved_view(
            object_api_name, sv_planners.QUICK_FILTERS_BASE, quick_filter
        )

    def table() -> None:
        console.print_json(data=item)

    out.render(fmt, json_data=item, table=table)


@quick_filters_app.command(
    "create",
    epilog="Filter shape (the filtering DSL): see `kizen docs show saved-views`",
)
def quick_filters_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    name: str = typer.Option(..., "--name", help="Quick filter name."),
    filter_json: str = typer.Option(
        "",
        "--filter",
        help="Filter as JSON, same format as `filter-groups create --filter`.",
    ),
    filter_file: str = typer.Option(
        "", "--filter-file", help="Path to a JSON filter (same format as --filter)."
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
    """Create one quick filter on an object.

    Sharing defaults to visible to all team members. Use `apply-to-roles` /
    `apply-to-users` afterward to push visibility to specific roles/users
    without having to know the current sharing state.
    """
    spec: dict[str, Any] = {"name": name}
    fc = load_filter_spec(filter_json, filter_file)
    if fc is not None:
        spec["filters"] = fc
    if owner:
        spec["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_create_quick_filter(object_api_name, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@quick_filters_app.command(
    "update",
    epilog="Filter shape (the filtering DSL): see `kizen docs show saved-views`",
)
def quick_filters_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
    name: str = typer.Option("", "--name", help="New name."),
    filter_json: str = typer.Option(
        "", "--filter", help="New filter as JSON (same format as `create`)."
    ),
    filter_file: str = typer.Option(
        "", "--filter-file", help="Path to a JSON filter (same format as --filter)."
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
    """Update one quick filter. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    fc = load_filter_spec(filter_json, filter_file)
    if fc is not None:
        changes["filters"] = fc
    if owner:
        changes["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_update_quick_filter(
            object_api_name, quick_filter, changes
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@quick_filters_app.command("delete")
def quick_filters_delete(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
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
    """Delete one quick filter."""
    _run_mutation(
        lambda: sv_planners.plan_delete_quick_filter(object_api_name, quick_filter),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@quick_filters_app.command("apply-to-roles")
def quick_filters_apply_to_roles(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
    role: list[str] = typer.Option(
        ..., "--role", help="Role name or UUID (repeatable)."
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
    """Grant one or more roles visibility into this quick filter."""
    role_ids = [_resolve_role_id(r) for r in role]
    _run_mutation(
        lambda: sv_planners.plan_apply_quick_filter(
            object_api_name, quick_filter, role_ids=role_ids
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@quick_filters_app.command("apply-to-users")
def quick_filters_apply_to_users(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
    user: list[str] = typer.Option(
        ...,
        "--user",
        help="Team member UUID (repeatable). Run `kizen team search <name>` to find one.",
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
    """Grant one or more team members visibility into this quick filter."""
    _run_mutation(
        lambda: sv_planners.plan_apply_quick_filter(
            object_api_name, quick_filter, user_ids=user
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

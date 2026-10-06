"""`kizen columns` — per-object saved column layouts (column templates)."""

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
    read_json_file,
)
from kizen_builder.cli.permissions import _resolve_group_id, _resolve_role_id
from kizen_builder.tools import saved_views as sv_tools
from kizen_builder.tools.planners import saved_views as sv_planners

columns_app = typer.Typer(
    help="Per-object saved column layouts: /columns.", no_args_is_help=True
)
app.add_typer(columns_app, name="columns")


@columns_app.command("list")
def columns_list(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    search: str = typer.Option(
        "", "--search", help="Filter by name (server-side search)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List column templates saved on an object."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        items = sv_tools.list_saved_views(
            object_api_name, sv_planners.COLUMNS_BASE, search=search or None
        )

    def table() -> None:
        t = Table(title=f"Column templates — {object_api_name}")
        t.add_column("name")
        t.add_column("owner")
        t.add_column("id")
        for i in items:
            t.add_row(i.get("name") or "", i.get("owner") or "", i.get("id") or "")
        console.print(t)

    out.render(fmt, json_data=items, table=table)


@columns_app.command("get")
def columns_get(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one column template's full config and sharing settings."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        item = sv_tools.find_saved_view(
            object_api_name, sv_planners.COLUMNS_BASE, column_template
        )

    def table() -> None:
        console.print_json(data=item)

    out.render(fmt, json_data=item, table=table)


@columns_app.command(
    "create",
    epilog="Config-file shape (opaque configuration_json; copy from a live template): see `kizen docs show saved-views`",
)
def columns_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    name: str = typer.Option(..., "--name", help="Column template name."),
    config_file: str = typer.Option(
        "",
        "--config-file",
        help="Path to a JSON configuration_json blob. Opaque, undocumented shape — "
        "copy one from `columns get <object> <id> --json` and edit rather than "
        "authoring from scratch. Omit for an empty starter template.",
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
    """Create one column template (saved list-view column layout) on an object."""
    spec: dict[str, Any] = {"name": name}
    if config_file:
        spec["configuration_json"] = read_json_file(config_file, "--config-file")
    if owner:
        spec["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_create_column_template(object_api_name, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@columns_app.command(
    "update",
    epilog="Config-file shape (opaque configuration_json; copy from a live template): see `kizen docs show saved-views`",
)
def columns_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
    name: str = typer.Option("", "--name", help="New name."),
    config_file: str = typer.Option(
        "", "--config-file", help="Path to a new JSON configuration_json blob."
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
    """Update one column template. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    if config_file:
        changes["configuration_json"] = read_json_file(config_file, "--config-file")
    if owner:
        changes["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_update_column_template(
            object_api_name, column_template, changes
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@columns_app.command("delete")
def columns_delete(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
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
    """Delete one column template."""
    _run_mutation(
        lambda: sv_planners.plan_delete_column_template(
            object_api_name, column_template
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@columns_app.command("apply-to-roles")
def columns_apply_to_roles(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
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
    """Grant one or more roles visibility into this column template."""
    role_ids = [_resolve_role_id(r) for r in role]
    _run_mutation(
        lambda: sv_planners.plan_apply_column_template(
            object_api_name, column_template, role_ids=role_ids
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@columns_app.command("apply-to-users")
def columns_apply_to_users(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
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
    """Grant one or more team members visibility into this column template."""
    _run_mutation(
        lambda: sv_planners.plan_apply_column_template(
            object_api_name, column_template, user_ids=user
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@columns_app.command("apply-to-permission-groups")
def columns_apply_to_permission_groups(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
    group: list[str] = typer.Option(
        ..., "--group", help="Permission group name or UUID (repeatable)."
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
    """Grant one or more permission groups visibility into this column template."""
    group_ids = [_resolve_group_id(g) for g in group]
    _run_mutation(
        lambda: sv_planners.plan_apply_column_template(
            object_api_name, column_template, permission_group_ids=group_ids
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

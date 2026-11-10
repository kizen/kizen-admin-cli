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
from kizen_builder.cli.quick_filters import _grants
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


COLUMN_HELP = (
    "A field api_name to show as a column, in order (repeatable). The name "
    "column (`full_name` on contacts) is always kept, first if you leave it out."
)
COLUMNS_EPILOG = "Column rules and examples: see `kizen docs show saved-views`"


@columns_app.command("create", epilog=COLUMNS_EPILOG)
def columns_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    name: str = typer.Option(..., "--name", help="Column template name."),
    column: list[str] = typer.Option([], "--column", help=COLUMN_HELP),
    config_file: str = typer.Option(
        "",
        "--config-file",
        help="Path to a raw configuration_json, sent as-is. Instead of --column.",
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
    """Create one column template (saved list-view column layout) on an object.

    Sharing starts where the UI's does: private, with the Admin role as admin.
    Use `share` to open it up.
    """
    spec: dict[str, Any] = {"name": name}
    if column:
        spec["columns"] = column
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


@columns_app.command("update", epilog=COLUMNS_EPILOG)
def columns_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
    name: str = typer.Option("", "--name", help="New name."),
    column: list[str] = typer.Option(
        [], "--column", help=COLUMN_HELP + " Replaces the whole column list."
    ),
    config_file: str = typer.Option(
        "",
        "--config-file",
        help="Path to a raw configuration_json, sent as-is. Instead of --column.",
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
    if column:
        changes["columns"] = column
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


@columns_app.command("share")
def columns_share(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    column_template: str = typer.Argument(
        ..., help="Column template UUID or exact name."
    ),
    private: bool | None = typer.Option(
        None, "--private/--public", help="Set the template's private flag."
    ),
    all_level: str | None = typer.Option(
        None,
        "--all",
        help="Access for all team members: none, view, edit or admin.",
    ),
    role: list[str] = typer.Option(
        [],
        "--role",
        help="ROLE=LEVEL, role name or UUID (repeatable). none removes it.",
    ),
    user: list[str] = typer.Option(
        [], "--user", help="UUID=LEVEL for a team member (repeatable). none removes it."
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
    """Change who can view, edit or admin a column template.

    Only what you pass changes; other roles and users keep their access.
    """
    if all_level is not None and all_level not in sv_planners.PERMISSION_LEVELS:
        raise typer.BadParameter(
            f"'{all_level}': expected none|view|edit|admin", param_hint="--all"
        )
    roles = _grants(role, "--role", _resolve_role_id)
    users = _grants(user, "--user", lambda ref: ref)
    _run_mutation(
        lambda: sv_planners.plan_share_column_template(
            object_api_name,
            column_template,
            private=private,
            all_level=all_level,
            roles=roles,
            users=users,
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

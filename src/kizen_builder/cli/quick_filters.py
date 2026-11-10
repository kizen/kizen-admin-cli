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


CHIP_HELP = (
    "A chip to show, in order (repeatable): `field`, `relation:related_field`, "
    "or `relation:in_groups`. Users pick the values when they filter."
)
CHIPS_EPILOG = "Chip syntax and examples: see `kizen docs show saved-views`"


@quick_filters_app.command("create", epilog=CHIPS_EPILOG)
def quick_filters_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    name: str = typer.Option(..., "--name", help="Quick filter name."),
    chip: list[str] = typer.Option([], "--chip", help=CHIP_HELP),
    facets: bool = typer.Option(
        False, "--facets", help="Turn on quick_filtering_facets."
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

    Sharing starts where the UI's does: private, with the Admin role as admin.
    Use `share` to open it up, and `apply-to-roles` / `apply-to-users` to put
    its chips on people's record lists.
    """
    spec: dict[str, Any] = {"name": name, "chips": chip, "facets": facets}
    if owner:
        spec["owner"] = owner

    _run_mutation(
        lambda: sv_planners.plan_create_quick_filter(object_api_name, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@quick_filters_app.command("update", epilog=CHIPS_EPILOG)
def quick_filters_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
    name: str = typer.Option("", "--name", help="New name."),
    chip: list[str] = typer.Option(
        [], "--chip", help=CHIP_HELP + " Replaces the whole chip list."
    ),
    facets: bool | None = typer.Option(
        None, "--facets/--no-facets", help="Turn quick_filtering_facets on or off."
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
    if chip:
        changes["chips"] = chip
    if facets is not None:
        changes["facets"] = facets
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


def _grants(values: list[str], flag: str, resolve: Any) -> dict[str, str]:
    """Parse repeated `REF=LEVEL` values into {resolved id: level}."""
    out: dict[str, str] = {}
    for value in values:
        ref, sep, level = value.rpartition("=")
        if not sep or level not in sv_planners.PERMISSION_LEVELS:
            raise typer.BadParameter(
                f"'{value}': expected REF=none|view|edit|admin", param_hint=flag
            )
        out[resolve(ref)] = level
    return out


@quick_filters_app.command("share")
def quick_filters_share(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    quick_filter: str = typer.Argument(..., help="Quick filter UUID or exact name."),
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
    """Change who can view, edit or admin a quick filter template.

    Only what you pass changes; other roles and users keep their access.
    """
    if all_level is not None and all_level not in sv_planners.PERMISSION_LEVELS:
        raise typer.BadParameter(
            f"'{all_level}': expected none|view|edit|admin", param_hint="--all"
        )
    roles = _grants(role, "--role", _resolve_role_id)
    users = _grants(user, "--user", lambda ref: ref)
    _run_mutation(
        lambda: sv_planners.plan_share_quick_filter(
            object_api_name,
            quick_filter,
            private=private,
            all_level=all_level,
            roles=roles,
            users=users,
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

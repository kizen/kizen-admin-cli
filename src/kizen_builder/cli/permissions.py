"""`kizen roles` and `kizen permissions` — roles, permission groups, and the
access-level grid.
"""

from __future__ import annotations

import json
from pathlib import Path
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
)
from kizen_builder.tools import permissions as perm_tools
from kizen_builder.tools.planners import permissions as perm_planners

roles_app = typer.Typer(help="Read and manage roles.", no_args_is_help=True)
app.add_typer(roles_app, name="roles")

perms_app = typer.Typer(
    help="Read and manage permission groups + the permissions catalog.",
    no_args_is_help=True,
)
app.add_typer(perms_app, name="permissions")


def _resolve_role_id(ref: str) -> str:
    with cli_errors(LookupError):
        return perm_tools.resolve_role(ref)["id"]


def _resolve_group_id(ref: str) -> str:
    with cli_errors(LookupError):
        return perm_tools.resolve_group(ref)["id"]


@roles_app.command("list")
def roles_list(
    search: str = typer.Option(None, "--search", "-s", help="Filter by name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List roles (id, name, user count, # permission groups)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        items = perm_tools.list_roles(search=search)

    def table() -> None:
        t = Table(title="Roles")
        t.add_column("id", style="dim")
        t.add_column("name")
        t.add_column("users", justify="right")
        t.add_column("perm groups", justify="right")
        t.add_column("default", justify="center")
        for r in items:
            t.add_row(
                r.get("id") or "—",
                r.get("name") or "—",
                str(r.get("user_count")) if r.get("user_count") is not None else "—",
                str(len(r.get("permission_groups") or [])),
                "✓" if r.get("default_for_new_users") else "",
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("name", "name"),
            out.Column("user_count", "user_count"),
            out.Column("default_for_new_users", "default_for_new_users"),
        ],
    )


@roles_app.command("get")
def roles_get(
    role: str = typer.Argument(..., help="Role name or UUID."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one role: its default flag, app permissions, and attached groups."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        d = perm_tools.describe_role(role)

    def table() -> None:
        console.print(
            f"[bold]{d['name']}[/bold]  [dim](id={d['id']})[/dim]"
            + (
                "  [green]● default for new users[/green]"
                if d.get("default_for_new_users")
                else ""
            )
        )
        if d.get("permissions"):
            console.print(f"[dim]app permissions:[/dim] {', '.join(d['permissions'])}")
        t = Table(title="Permission groups")
        t.add_column("name")
        t.add_column("id", style="dim")
        t.add_column("levels (n/v/e/r)")
        for g in d["groups"]:
            s = g.get("summary") or {}
            t.add_row(
                g.get("name") or "—",
                g.get("id") or "",
                f"{s.get('nb_none', '?')}/{s.get('nb_view', '?')}/{s.get('nb_edit', '?')}/{s.get('nb_remove', '?')}",
            )
        console.print(t)
        if not d["groups"]:
            console.print("[dim]No permission groups attached.[/dim]")

    out.render(
        fmt,
        json_data=d,
        table=table,
        csv_rows=d["groups"],
        csv_columns=[out.Column("name", "name"), out.Column("id", "id")],
    )


@perms_app.command("groups")
def perms_groups(
    search: str = typer.Option(None, "--search", "-s", help="Filter by name."),
    json_out: bool = JSON_OPTION,
) -> None:
    """List permission groups (raw)."""
    with cli_errors():
        items = perm_tools.list_permission_groups(search=search)
    typer.echo(json.dumps(items, indent=2))


_LEVEL_STYLE = {"none": "dim", "view": "cyan", "edit": "yellow", "remove": "green"}
_LEVEL_COLS = ["none", "view", "edit", "remove"]
_LEVEL_HEAD = ["None", "View", "Create/Edit", "Delete/All"]


def _slider(level: str, allowed: list[str], affordance: str) -> str:
    """A compact 4-cell analog of the UI slider/toggle for one permission row."""
    if affordance == "checkbox":
        on = level != "none"
        return "[green]☑[/green]" if on else "[dim]☐[/dim]"
    idx = _LEVEL_COLS.index(level) if level in _LEVEL_COLS else 0
    cells = []
    for i, col in enumerate(_LEVEL_COLS):
        if col not in allowed:
            cells.append("[dim] · [/dim]")  # unreachable position
        elif i == idx:
            cells.append(f"[{_LEVEL_STYLE[level]}]▓█▓[/{_LEVEL_STYLE[level]}]")  # knob
        elif i < idx:
            cells.append(
                f"[{_LEVEL_STYLE[level]}]███[/{_LEVEL_STYLE[level]}]"
            )  # filled
        else:
            cells.append("[dim]───[/dim]")  # empty track
    return "".join(cells)


@perms_app.command("group")
def perms_group(
    group: str = typer.Argument(..., help="Permission group name or UUID."),
    fields: bool = typer.Option(
        False, "--fields", help="Include per-field permission rows (extra API calls)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
    raw_out: bool = typer.Option(
        False, "--raw", help="Emit the full Kizen API response as JSON."
    ),
) -> None:
    """Show one permission group as a sectioned permission map (mirrors the UI).

    Each row shows a None / View / Create·Edit / Delete·All slider; positions the
    permission can't take are dimmed. `--fields` adds per-field rows.
    """
    group_id = _resolve_group_id(group)
    if raw_out:
        with cli_errors():
            typer.echo(json.dumps(perm_tools.get_permission_group(group_id), indent=2))
        return

    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        d = perm_tools.describe_group(group_id, include_fields=fields)

    # Unresolved-name warnings go to stderr, so they surface in every output
    # format without corrupting piped table/CSV output. `--json` carries them in
    # the payload's "warnings" key as well.
    for w in d.get("warnings") or []:
        err_console.print(f"[yellow]warning:[/yellow] {w}")

    def table() -> None:
        s = d.get("summary") or {}
        console.print(
            f"[bold]{d['name']}[/bold]  [dim](id={d['id']}, users={d.get('user_count')}, "
            f"roles={d.get('role_count')})[/dim]"
        )
        console.print(
            f"[dim]levels:[/dim] [dim]none {s.get('nb_none', '?')}[/dim] · "
            f"[cyan]view {s.get('nb_view', '?')}[/cyan] · "
            f"[yellow]edit {s.get('nb_edit', '?')}[/yellow] · "
            f"[green]remove {s.get('nb_remove', '?')}[/green]"
        )
        console.print(f"[dim]slider →  {'  '.join(_LEVEL_HEAD)}[/dim]")
        for blk in d["blocks"]:
            header = blk["label"]
            if blk["area"] == "object":
                header = f"Custom Object — {header}"
            state = ""
            if blk["enabled"] is not None:
                state = (
                    " [green][on][/green]" if blk["enabled"] else " [dim][off][/dim]"
                )
            console.print(f"\n[bold]▸ {header}[/bold]{state}")
            t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 1))
            t.add_column("area", overflow="fold")
            t.add_column("level")
            t.add_column("slider")
            last_cat = None
            for row in blk["rows"]:
                cat = row.get("category")
                if cat and cat != last_cat and cat not in ("custom_fields",):
                    t.add_row(f"[dim italic]{cat}[/dim italic]", "", "")
                last_cat = cat
                lvl = row["level"]
                label = (
                    _LEVEL_HEAD[_LEVEL_COLS.index(lvl)] if lvl in _LEVEL_COLS else lvl
                )
                t.add_row(
                    f"  {row['label']}",
                    f"[{_LEVEL_STYLE.get(lvl, 'white')}]{label}[/{_LEVEL_STYLE.get(lvl, 'white')}]",
                    _slider(lvl, row["allowed"], row["affordance"]),
                )
            console.print(t)

    out.render(
        fmt,
        json_data=d,
        table=table,
        csv_rows=[
            {
                "block": b["label"],
                "area": b["area"],
                "permission": r["label"],
                "level": r["level"],
            }
            for b in d["blocks"]
            for r in b["rows"]
        ],
        csv_columns=[
            out.Column("block", "block"),
            out.Column("area", "area"),
            out.Column("permission", "permission"),
            out.Column("level", "level"),
        ],
    )


@perms_app.command("meta")
def perms_meta() -> None:
    """Show the permissions catalog / meta-data (raw)."""
    with cli_errors():
        result = perm_tools.get_meta_data()
    typer.echo(json.dumps(result, indent=2))


# --- role mutations --------------------------------------------------------


@roles_app.command("create")
def roles_create(
    name: str = typer.Option(..., "--name", help="Role name."),
    group: list[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Permission group name or UUID to attach (repeatable).",
    ),
    permission: list[str] = typer.Option(
        None, "--permission", help="App-level permission flag to grant (repeatable)."
    ),
    default_for_new_users: bool = typer.Option(
        False,
        "--default/--no-default",
        help="Assign this role to new users by default.",
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
    """Create a role bundling permission groups + app-level permission flags."""
    group_ids = [_resolve_group_id(g) for g in (group or [])]
    _run_mutation(
        lambda: perm_planners.plan_create_role(
            name=name,
            permissions=list(permission or []),
            permission_group_ids=group_ids,
            default_for_new_users=default_for_new_users,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@roles_app.command("update")
def roles_update(
    role: str = typer.Argument(..., help="Role name or UUID."),
    name: str = typer.Option(None, "--name", help="Rename the role."),
    group: list[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Replace the role's permission groups with these (name or UUID, repeatable).",
    ),
    default_for_new_users: bool = typer.Option(
        None, "--default/--no-default", help="Toggle default-for-new-users."
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
    """Update a role. Only the flags you pass change (--group replaces the set)."""
    role_id = _resolve_role_id(role)
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if group:
        changes["permission_groups"] = [_resolve_group_id(g) for g in group]
    if default_for_new_users is not None:
        changes["default_for_new_users"] = default_for_new_users
    _run_mutation(
        lambda: perm_planners.plan_update_role(role_id, changes),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@roles_app.command("delete")
def roles_delete(
    role: str = typer.Argument(..., help="Role name or UUID."),
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
    """Delete a role."""
    role_id = _resolve_role_id(role)
    _run_mutation(
        lambda: perm_planners.plan_delete_role(role_id),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


# --- permission group mutations --------------------------------------------


@perms_app.command(
    "group-create",
    epilog="Settings-file shape (a list of shaping ops): see `kizen docs show permission-group`",
)
def perms_group_create(
    name: str = typer.Option(..., "--name", help="Permission group name."),
    base: str = typer.Option(
        "default",
        "--base",
        help="'default' = fresh group at Kizen default levels; 'clone' = copy --from.",
    ),
    from_group: str = typer.Option(
        None,
        "--from",
        help="Template group (name or UUID) for --base clone / shape source.",
    ),
    settings_file: str = typer.Option(
        None,
        "--settings-file",
        help="JSON list of shaping ops applied after create (object/field/section).",
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
    """Create a permission group (full default structure, optionally shaped)."""
    settings = None
    if settings_file:
        settings = json.loads(Path(settings_file).read_text())
    template_id = _resolve_group_id(from_group) if from_group else None
    _run_mutation(
        lambda: perm_planners.plan_create_permission_group(
            name=name,
            base=base,
            template_id=template_id,
            settings=settings,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@perms_app.command(
    "group-update",
    epilog="Settings-file shape (a list of shaping ops): see `kizen docs show permission-group`",
)
def perms_group_update(
    group: str = typer.Argument(..., help="Permission group name or UUID."),
    settings_file: str = typer.Option(
        ...,
        "--settings-file",
        help="JSON list of shaping ops to apply (object/field/section) — same "
        "shape as `group-create --settings-file`.",
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
    """Raise/lower specific controls on an existing permission group.

    Each op calls `object-update` (object/field) or a section PATCH — never
    a full-group PUT — so the server normalizes cross-field rules for you.
    """
    group_id = _resolve_group_id(group)
    settings = json.loads(Path(settings_file).read_text())
    _run_mutation(
        lambda: perm_planners.plan_update_permission_group(group_id, settings),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@perms_app.command("group-delete")
def perms_group_delete(
    group: str = typer.Argument(..., help="Permission group name or UUID."),
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
    """Delete a permission group."""
    group_id = _resolve_group_id(group)
    _run_mutation(
        lambda: perm_planners.plan_delete_permission_group(group_id),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

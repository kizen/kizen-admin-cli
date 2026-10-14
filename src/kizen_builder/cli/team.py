"""`kizen team` — team-member lookup and role changes."""

from __future__ import annotations

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
from kizen_builder.tools import team as team_tools
from kizen_builder.tools.planners import team as team_planners

team_app = typer.Typer(
    help="Look up team members and change their roles.", no_args_is_help=True
)
app.add_typer(team_app, name="team")


@team_app.command("search")
def team_search(
    name: str = typer.Argument(..., help="Name or email fragment to search for."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results to return."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Search team members by name or email.

    Returns id, full_name, email, and title. The id is what you pass to
    owner/assigned_to fields and scheduled activity employee_id.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        results = team_tools.search_team(name, limit=limit)

    def table() -> None:
        t = Table(title=f'Team members matching "{name}"')
        t.add_column("id", style="dim")
        t.add_column("full_name")
        t.add_column("email")
        t.add_column("title")
        for m in results:
            t.add_row(
                m.get("id") or "—",
                m.get("full_name") or "—",
                m.get("email") or "—",
                m.get("title") or "—",
            )
        console.print(t)
        if not results:
            console.print("[dim]No team members found.[/dim]")

    out.render(
        fmt,
        json_data=results,
        table=table,
        csv_rows=results,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("full_name", "full_name"),
            out.Column("email", "email"),
            out.Column("title", "title"),
        ],
    )


@team_app.command("get")
def team_get(
    member: str = typer.Argument(..., help="Team member id, name, or email."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one team member, including the role(s) they hold.

    Resolves ``member`` by UUID directly, or by a case-insensitive name/email
    match against `team search` when it isn't one.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        d = team_tools.get_team_member(member)

    def table() -> None:
        console.print(
            f"[bold]{d.get('full_name') or '—'}[/bold]  [dim](id={d['id']})[/dim]"
        )
        console.print(
            f"[dim]email:[/dim] {d.get('email') or '—'}   "
            f"[dim]title:[/dim] {d.get('title') or '—'}"
        )
        t = Table(title="Roles")
        t.add_column("name")
        t.add_column("id", style="dim")
        for r in d["roles"]:
            t.add_row(r.get("name") or "—", r.get("id") or "—")
        console.print(t)
        if not d["roles"]:
            console.print("[dim]No roles assigned.[/dim]")

    out.render(
        fmt,
        json_data=d,
        table=table,
        csv_rows=d["roles"],
        csv_columns=[out.Column("name", "name"), out.Column("id", "id")],
    )


@team_app.command(
    "update",
    epilog="Endpoint behavior and the last-Admin guard: see `kizen docs show team`",
)
def team_update(
    member: str = typer.Argument(..., help="Team member id, name, or email."),
    role: list[str] = typer.Option(
        None,
        "--role",
        help="Replace the member's roles with these (name or UUID, repeatable).",
    ),
    add_role: list[str] = typer.Option(
        None, "--add-role", help="Role to add (name or UUID, repeatable)."
    ),
    remove_role: list[str] = typer.Option(
        None, "--remove-role", help="Role to remove (name or UUID, repeatable)."
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
    """Change a team member's roles. --role replaces the whole set.

    With --add-role/--remove-role, roles you don't name are left alone.
    Removing the business's last Admin is refused.
    """
    if role and (add_role or remove_role):
        err_console.print(
            "[red]error:[/red] pass either --role or --add-role/--remove-role, not both."
        )
        raise typer.Exit(code=2)
    if not (role or add_role or remove_role):
        err_console.print("[red]error:[/red] pass --role, or --add-role/--remove-role.")
        raise typer.Exit(code=2)

    with cli_errors(LookupError):
        set_ids = [perm_tools.resolve_role(r)["id"] for r in role] if role else None
        add_ids = [perm_tools.resolve_role(r)["id"] for r in add_role or []]
        remove_ids = [perm_tools.resolve_role(r)["id"] for r in remove_role or []]
    _run_mutation(
        lambda: team_planners.plan_update_team_member_roles(
            member, set_roles=set_ids, add=add_ids, remove=remove_ids
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

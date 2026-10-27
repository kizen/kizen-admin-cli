"""`kizen service-accounts` — integration service accounts and their
permission groups. No command reads, reveals or prints an API key.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from kizen_builder import output as out
from kizen_builder.api.client import KizenClient
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.cli.permissions import _resolve_group_id
from kizen_builder.config import load_env_config
from kizen_builder.tools import service_accounts as sa_tools
from kizen_builder.tools.planners import service_accounts as sa_planners

_EPILOG = "Endpoint behavior and quirks: see `kizen docs show service-account`"

sa_app = typer.Typer(
    help=(
        "Read and manage integration service accounts (run-tools-as "
        "identities). See `kizen docs show service-account`."
    ),
    no_args_is_help=True,
)
app.add_typer(sa_app, name="service-accounts")

_REF_HELP = "Service account name, api_name or UUID."
_DRY_RUN = typer.Option(False, "--dry-run", help="Show the plan without applying.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation prompt.")
_JSON_WRITE = typer.Option(
    False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
)


def _resolve_id(ref: str) -> str:
    with cli_errors(LookupError), KizenClient(load_env_config()) as client:
        return sa_tools.resolve_service_account(client, ref)["id"]


def _group_names(row: dict[str, Any]) -> str:
    return ", ".join(
        g.get("name") or g.get("id") for g in row.get("permission_groups") or []
    )


@sa_app.command("list", epilog=_EPILOG)
def sa_list(
    search: str = typer.Option(None, "--search", "-s", help="Filter by name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List service accounts with their groups and key count (previews only)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        items = sa_tools.list_service_accounts(search=search)

    def table() -> None:
        t = Table(title="Service accounts")
        for col in ("name", "api_name", "type", "status", "groups"):
            t.add_column(col)
        t.add_column("keys", justify="right")
        t.add_column("key preview", style="dim")
        for a in items:
            t.add_row(
                Text(a.get("name") or "—"),
                Text(a.get("api_name") or "—"),
                a.get("account_type") or "—",
                a.get("status") or "—",
                Text(_group_names(a) or "—"),
                str(a.get("api_token_count") or 0),
                a.get("primary_api_token_preview") or "—",
            )
        console.print(t)
        if not items:
            console.print("[dim]No service accounts.[/dim]")

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("name", "name"),
            out.Column("api_name", "api_name"),
            out.Column("account_type", "account_type"),
            out.Column("status", "status"),
            out.Column("groups", _group_names),
            out.Column("api_token_count", "api_token_count"),
            out.Column("primary_api_token_preview", "primary_api_token_preview"),
        ],
    )


@sa_app.command("get", epilog=_EPILOG)
def sa_get(
    account: str = typer.Argument(..., help=_REF_HELP),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one service account and its permission groups."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        d = sa_tools.get_service_account(account)

    def table() -> None:
        console.print(
            f"[bold]{escape(d.get('name') or '—')}[/bold]  [dim](id={d['id']})[/dim]",
            emoji=False,
        )
        for label, key in (
            ("api_name", "api_name"),
            ("type", "account_type"),
            ("status", "status"),
            ("email", "email"),
            ("keys", "api_token_count"),
            ("key preview", "primary_api_token_preview"),
            ("created", "created"),
            ("last login", "last_login"),
        ):
            value = d.get(key)
            console.print(
                f"[dim]{label}:[/dim] {escape(str(value if value is not None else '—'))}",
                emoji=False,
            )
        t = Table(title="Permission groups")
        t.add_column("name")
        t.add_column("id", style="dim")
        for g in d.get("permission_groups") or []:
            t.add_row(Text(g.get("name") or "—"), g.get("id") or "—")
        console.print(t)
        if not d.get("permission_groups"):
            console.print("[dim]No permission groups (the account is disabled).[/dim]")

    out.render(fmt, json_data=d, table=table)


@sa_app.command("history", epilog=_EPILOG)
def sa_history(
    account: str = typer.Argument(..., help=_REF_HELP),
    limit: int = typer.Option(25, "--limit", "-n", help="Max events to return."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show a service account's history events."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        events = sa_tools.list_service_account_history(account, limit=limit)

    def by(e: dict[str, Any]) -> str:
        who = e.get("initiator") or {}
        full = " ".join(p for p in (who.get("first_name"), who.get("last_name")) if p)
        return who.get("email") or full or "—"

    def table() -> None:
        if not events:
            console.print("No history.")
            return
        t = Table(title="History")
        t.add_column("when", style="dim")
        t.add_column("event")
        t.add_column("by")
        for e in events:
            t.add_row(
                e.get("created") or "—",
                Text(e.get("type_display_name") or e.get("type_name") or "—"),
                Text(by(e)),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=events,
        table=table,
        csv_rows=events,
        csv_columns=[
            out.Column("when", "created"),
            out.Column("event", "type_display_name"),
            out.Column("by", by),
        ],
    )


# --- mutations --------------------------------------------------------------


@sa_app.command("create", epilog=_EPILOG)
def sa_create(
    name: str = typer.Option(
        ..., "--name", help="Account name; Kizen derives api_name."
    ),
    group: list[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Permission group name or UUID (repeatable). With none, Kizen "
        "creates the account disabled.",
    ),
    dry_run: bool = _DRY_RUN,
    yes: bool = _YES,
    json_out: bool = _JSON_WRITE,
) -> None:
    """Create an integration service account. Its API key is never shown."""
    group_ids = [_resolve_group_id(g) for g in (group or [])]
    _run_mutation(
        lambda: sa_planners.plan_create_service_account(name, group_ids),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@sa_app.command("update", epilog=_EPILOG)
def sa_update(
    account: str = typer.Argument(..., help=_REF_HELP),
    name: str = typer.Option(
        None, "--name", help="Rename the account (api_name is kept)."
    ),
    api_name: str = typer.Option(None, "--api-name", help="Set the api_name."),
    group: list[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Replace the account's permission groups with these (name or UUID, "
        "repeatable).",
    ),
    clear_groups: bool = typer.Option(
        False,
        "--clear-groups",
        help="Remove every permission group; Kizen disables the account.",
    ),
    dry_run: bool = _DRY_RUN,
    yes: bool = _YES,
    json_out: bool = _JSON_WRITE,
) -> None:
    """Update an integration service account. Only the flags you pass change."""
    if group and clear_groups:
        err_console.print("[red]error:[/red] pass --group or --clear-groups, not both.")
        raise typer.Exit(code=2)
    account_id = _resolve_id(account)
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if api_name is not None:
        changes["api_name"] = api_name
    if group:
        changes["permission_groups"] = [_resolve_group_id(g) for g in group]
    elif clear_groups:
        changes["permission_groups"] = []
    _run_mutation(
        lambda: sa_planners.plan_update_service_account(account_id, changes),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@sa_app.command("delete", epilog=_EPILOG)
def sa_delete(
    account: str = typer.Argument(..., help=_REF_HELP),
    dry_run: bool = _DRY_RUN,
    yes: bool = _YES,
    json_out: bool = _JSON_WRITE,
) -> None:
    """Delete (archive) an integration service account. This is permanent."""
    account_id = _resolve_id(account)
    _run_mutation(
        lambda: sa_planners.plan_delete_service_account(account_id),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

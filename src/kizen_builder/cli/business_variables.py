"""`kizen business-variables` — the business's merge-field variables."""

from __future__ import annotations

import typer
from rich.table import Table
from rich.text import Text

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
from kizen_builder.tools import business_variables as bv_tools
from kizen_builder.tools.planners import business_variables as bv_planners

_EPILOG = "Endpoint behavior and quirks: see `kizen docs show business-variables`"

bv_app = typer.Typer(
    help="Read and write business variables (business-wide merge-field values).",
    no_args_is_help=True,
)
app.add_typer(bv_app, name="business-variables")

_DRY_RUN = typer.Option(False, "--dry-run", help="Show the plan without applying.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation prompt.")
_JSON_WRITE = typer.Option(
    False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
)


@bv_app.command("list", epilog=_EPILOG)
def bv_list(output: str = OUTPUT_OPTION, json_out: bool = JSON_OPTION) -> None:
    """List every business variable with its value."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        items = bv_tools.list_business_variables()

    def table() -> None:
        t = Table(title="Business variables")
        t.add_column("name")
        t.add_column("api_name")
        t.add_column("value")
        t.add_column("id", style="dim")
        for v in items:
            t.add_row(
                Text(v["name"]),
                Text(v["api_name"]),
                Text(v.get("value_string") or ""),
                v["id"],
            )
        console.print(t)
        if not items:
            console.print("[dim]No business variables.[/dim]")

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("name", "name"),
            out.Column("api_name", "api_name"),
            out.Column("value", "value_string"),
        ],
    )


@bv_app.command("set", epilog=_EPILOG)
def bv_set(
    api_name: str = typer.Argument(..., help="Variable api_name."),
    value: str = typer.Option(None, "--value", help="New value (may be empty)."),
    name: str = typer.Option(
        None, "--name", help="Display name. Required when creating."
    ),
    dry_run: bool = _DRY_RUN,
    yes: bool = _YES,
    json_out: bool = _JSON_WRITE,
) -> None:
    """Create a business variable, or change its value or name.

    Creating needs --name and --value. Other variables are left as they are.
    """
    if value is None and name is None:
        err_console.print("[red]error:[/red] pass --value and/or --name.")
        raise typer.Exit(2)
    _run_mutation(
        lambda: bv_planners.plan_set_business_variable(
            api_name, value=value, name=name
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@bv_app.command("delete", epilog=_EPILOG)
def bv_delete(
    api_name: str = typer.Argument(..., help="Variable api_name."),
    dry_run: bool = _DRY_RUN,
    yes: bool = _YES,
    json_out: bool = _JSON_WRITE,
) -> None:
    """Delete one business variable. Merge fields that use it stop resolving."""
    _run_mutation(
        lambda: bv_planners.plan_delete_business_variable(api_name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

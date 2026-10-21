"""`kizen envs` — what this directory is pinned to."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    console,
    err_console,
)
from kizen_builder.config import refuse_in_helper_mode
from kizen_builder.tools import envs as env_tools

envs_app = typer.Typer(
    help="Discover and inspect configured envs.", no_args_is_help=True
)
app.add_typer(envs_app, name="envs")


@envs_app.command("list")
def envs_list(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the Kizen env configured in this directory."""
    refuse_in_helper_mode("envs list", "the environment")
    fmt = out.resolve_format(output, json_out)
    items = env_tools.list_envs()

    if fmt is out.OutputFormat.TABLE and not items:
        err_console.print(
            "[yellow]No env configured.[/yellow] Run `kizen init --profile <name>` to add one."
        )
        raise typer.Exit(code=1)

    def table() -> None:
        t = Table(title="Kizen envs")
        t.add_column("", justify="center")  # pin marker
        t.add_column("label")
        t.add_column("business_id")
        t.add_column("base_url")
        t.add_column("source")
        t.add_column("complete?", justify="center")
        for env in items:
            pinned = env.get("pinned")
            t.add_row(
                "[green]●[/green]" if pinned else "",
                f"[bold]{env['label']}[/bold]" if pinned else env["label"],
                env["business_id"],
                env["base_url"],
                env.get("source", ""),
                "✓" if env["complete"] else "✗",
            )
        console.print(t)
        if any(e.get("pinned") for e in items):
            console.print("[dim]● = active profile (pinned to this directory)[/dim]")

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("pinned", lambda e: bool(e.get("pinned"))),
            out.Column("label", "label"),
            out.Column("business_id", "business_id"),
            out.Column("base_url", "base_url"),
            out.Column("source", "source"),
            out.Column("complete", "complete"),
        ],
    )

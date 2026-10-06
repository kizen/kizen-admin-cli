"""`kizen apply` — consume a plan from stdin or --plan-file and execute it."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.markup import escape
from rich.prompt import Confirm

from kizen_builder.cli._mutations import (
    _enrich_known_choice_failures,
    _render_plan,
    _render_result,
)
from kizen_builder.cli._shared import app, cli_errors, console, err_console
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.plans import PlanError


@app.command("apply")
def apply_cmd(
    plan_file: str = typer.Option(
        "", "--plan-file", help="Path to a plan JSON file. Default: read from stdin."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Apply a saved plan (the JSON a mutation verb emits with --dry-run --json).

    Reads the plan JSON from `--plan-file` or stdin. Confirms with the user
    (unless `--yes`), executes operations, prints results. A plan piped on
    stdin can't be confirmed interactively, so it needs `--yes`.

    Refuses (exit 2) a plan built for a different business than the one this
    folder resolves to. The ops are sent as they were planned, without
    re-checking live state; re-run the original verb to re-plan.
    """
    if plan_file:
        text = Path(plan_file).read_text()
    else:
        if sys.stdin.isatty():
            err_console.print(
                "[red]error:[/red] no plan provided. "
                "Pipe a plan JSON to stdin or pass --plan-file."
            )
            raise typer.Exit(code=2)
        text = sys.stdin.read()

    try:
        plan = plan_tools.plan_from_json(text)
    except Exception as e:  # noqa: BLE001
        err_console.print(f"[red]error parsing plan:[/red] {e}")
        raise typer.Exit(code=2) from e

    with cli_errors():
        try:
            config = plan_tools.resolve_apply_target(plan)
        except PlanError as e:
            err_console.print(f"[red]error:[/red] {escape(str(e))}", emoji=False)
            raise typer.Exit(code=2) from e
    if plan.business_id is None:
        err_console.print(
            "[yellow]warning:[/yellow] this plan predates business binding; "
            f"matched to profile '{config.name}' by name only."
        )

    preview_target = err_console if json_out else console
    _render_plan(plan, preview_target)
    preview_target.print(
        f"Writing to profile '{config.name}' (business_id {config.business_id})."
    )
    if not yes:
        if not plan_file:
            err_console.print(
                "[red]error:[/red] cannot prompt for confirmation after reading "
                "the plan from stdin. Re-run with --yes (or use --plan-file)."
            )
            raise typer.Exit(code=2)
        if not Confirm.ask(f"Apply {len(plan.operations)} op(s)?", default=False):
            console.print("[yellow]aborted[/yellow]")
            raise typer.Exit(code=1)

    with cli_errors():
        result = plan_tools.apply_plan(plan)

    _enrich_known_choice_failures(result)

    if json_out:
        typer.echo(plan_tools.result_to_json(result))
    else:
        _render_result(result)
    if not result.all_ok:
        raise typer.Exit(code=1)

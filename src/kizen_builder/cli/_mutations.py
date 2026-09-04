"""The mutation runner: plan → preview → confirm → apply.

Every create/update/delete command in this package funnels through
`_run_mutation`, which is what makes the approval gate uniform.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from kizen_builder.cli._shared import cli_errors, console, err_console
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners.automations import known_choices_addendum
from kizen_builder.tools.plans import PlanError


def _render_plan(plan: plan_tools.Plan, target: Console) -> None:
    """Rich-render a plan (header, summary, operations table)."""
    target.print(f"[bold]Plan {plan.id}[/bold]  [dim]→ env: {plan.env}[/dim]")
    target.print(plan.summary)
    table = Table(title="Operations")
    table.add_column("#", justify="right")
    table.add_column("action")
    table.add_column("kind")
    table.add_column("key")
    table.add_column("preview")
    for i, op in enumerate(plan.operations, 1):
        preview = ", ".join(f"{k}={v}" for k, v in op.preview.items() if k != "env")
        table.add_row(str(i), op.action, op.kind, op.key, preview)
    target.print(table)


def _render_result(result: plan_tools.ApplyResult) -> None:
    table = Table(title=f"Results — plan {result.plan_id}")
    table.add_column("status", justify="center")
    table.add_column("kind")
    table.add_column("key")
    table.add_column("server uuid")
    table.add_column("note")
    for r in result.results:
        symbol = {"ok": "✓", "skipped": "·", "failed": "✗", "adjusted": "~"}.get(
            r.status, "?"
        )
        table.add_row(
            symbol,
            r.kind,
            r.key,
            (r.server_uuid or ""),
            r.message or "",
        )
    console.print(table)


def _run_mutation(
    build_plan: Callable[[], plan_tools.Plan],
    *,
    dry_run: bool,
    yes: bool,
    json_out: bool,
    stdin_consumed: bool = False,
) -> None:
    """Shared flow for every create/update verb.

    Builds the plan (live-state validation), renders it, then either stops
    (`--dry-run`), or confirms and applies. With `--json` the preview
    goes to stderr so stdout carries only the machine-readable payload
    (plan JSON on --dry-run, apply results otherwise).
    """
    # Planning has two failures worth their own wording — `plan error:` for an
    # impossible change, one `spec error:` line per bad field for a malformed
    # spec (bad api_name casing, reserved field name, missing required config)
    # instead of a raw pydantic traceback. Everything else is the ordinary
    # `error:` line, so the inner handlers sit inside `cli_errors`.
    with cli_errors():
        try:
            plan = build_plan()
        except PlanError as e:
            err_console.print(f"[red]plan error:[/red] {e}")
            raise typer.Exit(code=1) from e
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(p) for p in err.get("loc", ())) or "spec"
                msg = err.get("msg", "invalid value")
                err_console.print(f"[red]spec error:[/red] {loc}: {msg}")
            raise typer.Exit(code=1) from e

    if dry_run:
        if json_out:
            typer.echo(plan_tools.plan_to_json(plan))
        else:
            _render_plan(plan, console)
            console.print(
                "[dim]Dry run — nothing applied. Re-run without --dry-run to "
                "apply (--yes skips the confirm), or save --dry-run --json "
                "output and feed it to `kizen apply`.[/dim]"
            )
        return

    preview_target = err_console if json_out else console
    _render_plan(plan, preview_target)

    all_skip = all(op.action == "skip" for op in plan.operations)
    if all_skip:
        preview_target.print("[yellow]no changes to apply[/yellow]")
    elif not yes:
        if stdin_consumed:
            err_console.print(
                "[red]error:[/red] cannot prompt for confirmation after reading "
                "the spec from stdin. Preview with --dry-run first, then re-run "
                "with --yes (or use --spec-file)."
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


def _enrich_known_choice_failures(result: plan_tools.ApplyResult) -> None:
    """Append whatever this repo already knows about a rejected enum value
    to a failed automation op's message. Called right after `apply_plan()`
    returns, by every caller of it (`_run_mutation` here, `apply_cmd` in
    `cli/apply.py`), before either output branch (`--json` or the Rich
    table) reads `.message`. A miss is silent: `known_choices_addendum`
    returns `None` for anything it doesn't recognize, so an unrelated 400 is
    untouched.
    """
    for r in result.results:
        if r.kind != "automation" or r.status != "failed" or r.raw is None:
            continue
        addendum = known_choices_addendum(r.raw)
        if addendum:
            r.message = f"{r.message} {addendum}" if r.message else addendum


def _read_spec(spec_file: str, what: str = "automation") -> tuple[dict[str, Any], bool]:
    """Read a JSON spec dict from --spec-file or stdin.

    `what` names the spec in error messages (e.g. "dashboard", "layout").
    Returns (spec, from_stdin) — from_stdin tells the runner it can no
    longer prompt interactively.
    """
    if spec_file:
        text = Path(spec_file).read_text()
        from_stdin = False
    else:
        if sys.stdin.isatty():
            err_console.print(
                f"[red]error:[/red] no {what} spec provided. "
                f"Pipe a JSON spec to stdin or pass --spec-file."
            )
            raise typer.Exit(code=2)
        text = sys.stdin.read()
        from_stdin = True
    try:
        return json.loads(text), from_stdin
    except json.JSONDecodeError as e:
        err_console.print(f"[red]error parsing JSON:[/red] {e}")
        raise typer.Exit(code=2) from e

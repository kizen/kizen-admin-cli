"""`kizen automations runs` — execution reads and runtime control
(pause/resume/cancel/skip-and-resume/debug-*).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._run_render import (
    history_duration,
    print_wait_outcome,
    render_execution_logs,
    wait_exit_code,
)
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    _short,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import automations as auto_tools
from kizen_builder.utils import is_uuid

runs_app = typer.Typer(
    help=(
        "Inspect automation runs (executions). `list` shows recent runs for "
        "an automation; `view <id>` shows one run — its summary plus the "
        "step-by-step trace (`--no-steps` for summary only, `--wait` to "
        "block until it finishes); `logs <id>` prints each step's "
        "`detailed_log`. Mirrors `gh run list` / `gh run view`."
    ),
    no_args_is_help=True,
)
autos_app.add_typer(runs_app, name="runs")


def _require_run_uuid(execution_id: str) -> None:
    """Runs are keyed by execution UUID, not the automation api_name.

    Passing an api_name (or a truncated id from the list table) otherwise
    yields an opaque HTTP 404; catch it early with a message that points at
    where a real id comes from.
    """
    if not is_uuid(execution_id):
        err_console.print(
            f"[red]error:[/red] '{execution_id}' is not an execution UUID. "
            "A run is identified by its execution_id (not the automation "
            "api_name) — copy one from the execution_id column of "
            "`kizen automations runs list <api_name>`."
        )
        raise typer.Exit(code=1)


def _history_error_str(e: dict[str, Any]) -> str:
    error = e.get("error")
    if isinstance(error, (dict, list)):
        return json.dumps(error)
    return str(error) if error else ""


@runs_app.command("view")
def runs_view(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    steps: bool = typer.Option(
        True,
        "--steps/--no-steps",
        help="Include the step-by-step trace below the summary (default: on).",
    ),
    wait: bool = typer.Option(
        False,
        "--wait/--no-wait",
        help="Block until the run reaches a terminal status before rendering.",
    ),
    timeout: float = typer.Option(
        900.0,
        "--timeout",
        help=("With --wait: seconds to wait before giving up (0 = no deadline)."),
    ),
    poll_interval: float = typer.Option(
        5.0,
        "--poll-interval",
        help="With --wait: seconds between polls.",
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Alias for --output json (includes step output/error blobs).",
    ),
) -> None:
    """Show one automation run: its summary (status, record, start/finish) and,
    unless `--no-steps`, the step-by-step trace with per-step durations.
    `--wait` blocks until the run finishes — a timeout or a `paused*` status
    is reported as "not done yet", never as a failure (`--timeout 900` by
    default: long enough that one worst-case gap between steps, measured at
    up to 10+ minutes on a real chain, doesn't trip it; `--timeout 0` waits
    indefinitely). Mirrors `gh run view`."""
    _require_run_uuid(execution_id)
    if wait and timeout < 0:
        raise typer.BadParameter("--timeout must be >= 0 (0 = no deadline).")
    if wait and poll_interval <= 0:
        raise typer.BadParameter("--poll-interval must be > 0.")
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        if wait:
            summary = auto_tools.wait_for_execution(
                execution_id, timeout=timeout, poll_interval=poll_interval
            )
        else:
            summary = auto_tools.get_execution(execution_id)
        entries = auto_tools.get_execution_history(execution_id) if steps else []

    keys = (
        "execution_id",
        "status",
        "automation_api_name",
        "record_id",
        "started_at",
        "finished_at",
    )

    def table() -> None:
        t = Table(title=f"Run {execution_id}")
        t.add_column("field", style="dim")
        t.add_column("value")
        for key in keys:
            val = summary.get(key)
            t.add_row(key, str(val) if val is not None else "[dim]—[/dim]")
        console.print(t)
        paused_on_step = summary.get("paused_on_step")
        if paused_on_step:
            console.print(
                "[yellow]paused on:[/yellow] "
                f"{paused_on_step.get('label') or paused_on_step.get('type')} "
                f"(id={paused_on_step.get('id')}, "
                f"branching={paused_on_step.get('branching_step')})"
            )
        if not steps:
            return
        st = Table(title="Steps")
        st.add_column("#", justify="right")
        st.add_column("kind")
        st.add_column("type")
        st.add_column("description")
        st.add_column("status", justify="center")
        st.add_column("duration", justify="right")
        st.add_column("error")
        for i, e in enumerate(entries, 1):
            st.add_row(
                str(i),
                e.get("kind") or "",
                e.get("type") or "",
                _short(e.get("description")),
                e.get("status") or "",
                history_duration(e),
                _history_error_str(e),
            )
        console.print(st)
        if not entries:
            console.print(
                "[dim]No step history yet — run may still be executing.[/dim]"
            )

    summary_json = {k: v for k, v in summary.items() if k != "raw"}
    if steps:
        json_data: Any = {**summary_json, "steps": entries}
        # CSV carries the per-step rows (the repeating, spreadsheet-shaped data).
        csv_rows = [{"_i": i, **e} for i, e in enumerate(entries, 1)]
        csv_columns = [
            out.Column("#", "_i"),
            out.Column("kind", "kind"),
            out.Column("type", "type"),
            out.Column("description", "description"),
            out.Column("status", "status"),
            out.Column("duration_ms", "duration_ms"),
            out.Column("started_at", "started_at"),
            out.Column("finished_at", "finished_at"),
            out.Column("error", _history_error_str),
            # Appended, not inserted — a positional CSV consumer reading the
            # pre-existing columns must not have them shift.
            out.Column("id", "id"),
        ]
    else:
        json_data = summary_json
        csv_rows = [summary]
        csv_columns = [out.Column(k, k) for k in keys]

    out.render(
        fmt,
        json_data=json_data,
        table=table,
        csv_rows=csv_rows,
        csv_columns=csv_columns,
    )

    if wait:
        if fmt is out.OutputFormat.TABLE:
            print_wait_outcome(execution_id, summary, timeout)
        exit_code = wait_exit_code(summary)
        if exit_code:
            raise typer.Exit(code=exit_code)


@runs_app.command("list")
def runs_list(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    limit: int = typer.Option(
        25, "--limit", "-n", help="Max runs to return (max 100)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List recent runs (executions) for an automation.

    `--output csv` emits the full run ids/timestamps (untruncated) — handy
    for auditing runs in a spreadsheet. Use `kizen automations runs view
    <id>` for one run's summary and step-by-step trace.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        results = auto_tools.list_executions(api_name, limit=limit)

    def table() -> None:
        # Show ids in full (not truncated): the execution_id is the argument
        # to `runs show`/`runs history`, and record_id feeds `records get`, so
        # both must be copy-pasteable straight from this table.
        t = Table(title=f"Runs — {api_name}")
        t.add_column("execution_id", style="dim", no_wrap=True)
        t.add_column("status")
        t.add_column("record_id", style="dim", no_wrap=True)
        t.add_column("started_at")
        for r in results:
            t.add_row(
                r.get("execution_id") or "—",
                r.get("status") or "—",
                r.get("record_id") or "—",
                r.get("started_at") or "—",
            )
        console.print(t)
        if not results:
            console.print("[dim]No runs found.[/dim]")

    out.render(
        fmt,
        json_data=results,
        table=table,
        csv_rows=results,
        csv_columns=[
            out.Column("execution_id", "execution_id"),
            out.Column("status", "status"),
            out.Column("automation_api_name", "automation_api_name"),
            out.Column("record_id", "record_id"),
            out.Column("started_at", "started_at"),
            out.Column("finished_at", "finished_at"),
            out.Column("debug_mode", "debug_mode"),
        ],
    )


@runs_app.command(
    "logs",
    epilog=(
        "Wire notes (execution statuses, per-step detailed_log shapes): see "
        "`kizen docs show automation-runtime`."
    ),
)
def runs_logs(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw per-step detailed_log blobs."
    ),
) -> None:
    """Print each step's `detailed_log` — the code_step stdout/traceback (and
    other per-step diagnostic detail `runs view`'s Steps table has no column
    for). No new API call: reads the same step history `runs view` already
    fetches."""
    _require_run_uuid(execution_id)
    with cli_errors():
        entries = auto_tools.get_execution_history(execution_id)
    render_execution_logs(entries, json_out=json_out)


# ---------------------------------------------------------------------------
# runs execution control — pause/resume/cancel/skip-and-resume/debug-*.
# Confirm-free like `automations start`: these act on an execution's own
# runtime state, not schema (standing decision — see CLAUDE.md). Confirmed
# live (2026-07-22) for pause/resume/cancel; debug-* wired from
# the public /api/docs/schema shapes but not live-exercised (see
# tools/automations.py).
# ---------------------------------------------------------------------------


def _run_execution_action(
    execution_id: str, call: Callable[[], dict[str, Any]]
) -> None:
    _require_run_uuid(execution_id)
    with cli_errors(LookupError):
        result = call()
    console.print(
        f"[green]ok[/green] — {execution_id}: "
        f"{result['status_before']!r} → {result['status_after']!r}"
    )


@runs_app.command("pause")
def runs_pause(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
) -> None:
    """Pause a running execution."""
    _run_execution_action(
        execution_id, lambda: auto_tools.pause_execution(execution_id)
    )


@runs_app.command("resume")
def runs_resume(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
) -> None:
    """Resume a paused execution."""
    _run_execution_action(
        execution_id, lambda: auto_tools.resume_execution(execution_id)
    )


@runs_app.command("cancel")
def runs_cancel(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
) -> None:
    """Cancel an execution. Irreversible."""
    _run_execution_action(
        execution_id, lambda: auto_tools.cancel_execution(execution_id)
    )


@runs_app.command("skip-and-resume")
def runs_skip_and_resume(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    skip_step: str = typer.Option(
        ..., "--skip-step", help="Step UUID to skip (from `runs view`)."
    ),
    branch: str = typer.Option(
        "",
        "--branch",
        help="Branch to continue on after skipping ('yes'/'no'), if applicable.",
    ),
) -> None:
    """Resume an execution paused on a step failure by skipping that step."""
    _require_run_uuid(execution_id)
    with cli_errors(LookupError):
        result = auto_tools.skip_and_resume_execution(
            execution_id, skip_step, branch or None
        )
    console.print(
        f"[green]ok[/green] — {execution_id}: "
        f"{result['status_before']!r} → {result['status_after']!r}"
    )


@runs_app.command("debug-sendit")
def runs_debug_sendit(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
) -> None:
    """Run a debug-mode execution to completion."""
    _run_execution_action(execution_id, lambda: auto_tools.debug_sendit(execution_id))


@runs_app.command("debug-rerun")
def runs_debug_rerun(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    step: str = typer.Option(
        ..., "--step", help="Step UUID to re-execute (from `runs view`)."
    ),
) -> None:
    """Re-execute one step; no subsequent steps are scheduled (history-only replay)."""
    _require_run_uuid(execution_id)
    with cli_errors(LookupError):
        result = auto_tools.debug_rerun(execution_id, step)
    console.print(
        f"[green]ok[/green] — {json.dumps(result['result'], indent=2, default=str)}"
    )


@runs_app.command("debug-restart")
def runs_debug_restart(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    step: str = typer.Option(
        ..., "--step", help="Step UUID to restart from (from `runs view`)."
    ),
) -> None:
    """Restart an execution from a step; subsequent steps ARE scheduled (unlike debug-rerun)."""
    _require_run_uuid(execution_id)
    with cli_errors(LookupError):
        result = auto_tools.debug_restart(execution_id, step)
    console.print(
        f"[green]ok[/green] — {json.dumps(result['result'], indent=2, default=str)}"
    )


@runs_app.command("debug-step")
def runs_debug_step(
    execution_id: str = typer.Argument(..., help="Run (execution) UUID."),
    history_id: str = typer.Option(
        ..., "--history", help="History-row id (from `runs view`)."
    ),
    action: str = typer.Option(..., "--action", help="One of: execute, skip, debug."),
    branch: str = typer.Option(
        "", "--branch", help="Branch to continue on ('yes'/'no'), if applicable."
    ),
) -> None:
    """Skip or execute one step of a debug-mode execution."""
    _require_run_uuid(execution_id)
    with cli_errors(LookupError):
        result = auto_tools.debug_step(execution_id, action, history_id, branch or None)
    console.print(
        f"[green]ok[/green] — {json.dumps(result['result'], indent=2, default=str)}"
    )

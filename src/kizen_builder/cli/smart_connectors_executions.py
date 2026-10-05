"""`kizen smart-connectors executions` — a connector's run history, one run's
detail, the SQL it ran, and its files (Excel report, SQL-output zip, input).
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

import typer
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typer.core import TyperGroup

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
    warn_renamed_flag,
)
from kizen_builder.cli.smart_connectors import smart_connectors_app
from kizen_builder.tools import smart_connectors as sc_tools

# Detail-verb habits from elsewhere (`automations runs view`).
_SYNONYMS = {"view": "get", "show": "get"}


class _ExecutionsGroup(TyperGroup):
    """Before this was a group, `executions <connector>` listed run history.
    Point that old form (a lone positional, plus any of `list`'s options) at
    `executions list`. Anything else keeps Typer's "No such command" / "Did
    you mean"."""

    def resolve_command(self, ctx: Any, args: list[str]) -> Any:
        name = args[0] if args else ""
        if (
            name
            and not name.startswith("-")
            and not ctx.resilient_parsing
            and self.get_command(ctx, name) is None
        ):
            if name in _SYNONYMS:
                ctx.fail(f"No such command '{name}'. Did you mean '{_SYNONYMS[name]}'?")
            if self._positionals(args) == 1 and not get_close_matches(
                name, self.commands
            ):
                ctx.fail(
                    f"No such command '{name}'. A connector's run history is now "
                    f"`smart-connectors executions list {name}`."
                )
        return super().resolve_command(ctx, args)

    def _positionals(self, args: list[str]) -> int:
        # The old form took `list`'s options, so `--status failed` is one
        # option, not a second positional.
        valued = {
            opt
            for p in self.commands["list"].params
            if p.param_type_name == "option" and not getattr(p, "is_flag", False)
            for opt in p.opts
        }
        count, skip = 0, False
        for arg in args:
            if skip:
                skip = False
            elif arg in valued:
                skip = True
            elif not arg.startswith("-"):
                count += 1
        return count


executions_app = typer.Typer(
    cls=_ExecutionsGroup,
    help=(
        "Inspect a connector's executions (runs). `list <connector>` shows its "
        "run history; `get <connector> <id>` shows one run — status, the full "
        "error, per-step record counts, and its files; `download` saves the "
        "run's Excel report, SQL-output zip, or input file; `sql` prints the "
        "SQL it ran."
    ),
    no_args_is_help=True,
)
smart_connectors_app.add_typer(executions_app, name="executions")

CONNECTOR_ARG = typer.Argument(..., help="Connector UUID or api_name.")
EXECUTION_ARG = typer.Argument(..., help="Execution UUID (from `executions list`).")


@executions_app.command("list")
def executions_list(
    connector: str = CONNECTOR_ARG,
    status: str = typer.Option(None, "--status", help="Filter by execution status."),
    search: str = typer.Option(None, "--search", "-s", help="Search executions."),
    include_dry_run: bool = typer.Option(
        False, "--include-dry-run", help="Include dry-run executions."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a connector's execution (run) history, most recent first.

    The `error` column is the executor's own failure message (the real
    ClickHouse or validation error). It's truncated here; `executions get`
    shows one run's in full, as do --json / --output csv.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        results = sc_tools.list_executions(
            connector,
            status=status,
            search=search,
            include_dry_run=include_dry_run or None,
        )

    def table() -> None:
        t = Table(title=f"Executions — {connector}")
        t.add_column("id", style="dim")
        t.add_column("status")
        t.add_column("trigger")
        t.add_column("dry_run")
        t.add_column("started_by")
        t.add_column("created")
        t.add_column("error", style="red", max_width=60, overflow="fold")
        for e in results:
            t.add_row(
                e.get("id") or "—",
                e.get("status") or "—",
                e.get("trigger_type") or "—",
                "yes" if e.get("is_dry_run") else "",
                Text(str(e.get("started_by") or "—")),
                str(e.get("created") or "—"),
                Text((e.get("error_details") or "").strip()),
            )
        console.print(t)
        if not results:
            console.print("[dim]No executions found.[/dim]")
        elif any(e.get("error_details") for e in results):
            console.print(
                "[dim]Full error text: `executions get <connector> <id>`, or "
                "re-run with --json (or --output csv).[/dim]"
            )

    out.render(
        fmt,
        json_data=results,
        table=table,
        csv_rows=results,
        csv_columns=[
            out.Column(k, k)
            for k in (
                "id",
                "status",
                "trigger_type",
                "is_dry_run",
                "started_by",
                "created",
                "ended_at",
                "error_details",
            )
        ],
    )


def _file_summary(ref: Any) -> str:
    if not (isinstance(ref, dict) and ref.get("id")):
        return "—"
    name = ref.get("name") or ref["id"]
    size = ref.get("size_formatted") or (
        f"{ref['size_bytes']} bytes" if ref.get("size_bytes") is not None else None
    )
    return f"{name} ({size})" if size else name


@executions_app.command("get")
def executions_get(
    connector: str = CONNECTOR_ARG,
    execution_id: str = EXECUTION_ARG,
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one execution: status, the full error, step counts, and its files.

    Kizen has no single-execution endpoint; this reads the executions list
    filtered to the one id. --json emits that row as the API returned it, with
    `started_by` flattened to a name.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        row = sc_tools.get_execution(connector, execution_id)

    def table() -> None:
        t = Table(title=f"Execution — {connector}", show_header=False)
        t.add_column("field", style="bold")
        t.add_column("value", overflow="fold")
        for label, key in (
            ("id", "id"),
            ("status", "status"),
            ("trigger", "trigger_type"),
        ):
            t.add_row(label, Text(str(row.get(key) or "—")))
        t.add_row("dry_run", "yes" if row.get("is_dry_run") else "no")
        for label, key in (
            ("started_by", "started_by"),
            ("created", "created"),
            ("ended_at", "ended_at"),
        ):
            t.add_row(label, Text(str(row.get(key) or "—")))
        error = (row.get("error_details") or "").strip()
        t.add_row("error", Text(error, style="red") if error else "—")
        console.print(t)

        steps = Table(title="Steps")
        for col in ("step", "status", "scope", "object"):
            steps.add_column(col)
        for col in ("valid", "invalid", "total"):
            steps.add_column(col, justify="right")
        for group in row.get("step_progress") or []:
            kind = str(group.get("type") or "—").removeprefix("smart_connector_")
            # A cancelled stage can carry no steps; keep it visible.
            for step in group.get("steps") or [{}]:
                co = step.get("custom_object")
                obj = (
                    co.get("object_name") or co.get("name")
                    if isinstance(co, dict)
                    else co
                )
                steps.add_row(
                    Text(kind),
                    Text(str(step.get("status") or group.get("status") or "—")),
                    Text(str(step.get("scope") or "—")),
                    Text(str(obj or "—")),
                    *(
                        str(step[k]) if step.get(k) is not None else "—"
                        for k in ("valid_records", "invalid_records", "total")
                    ),
                )
        if steps.rows:
            console.print(steps)

        files = "  ·  ".join(
            f"{kind}: {escape(_file_summary(row.get(field)))}"
            for kind, (field, _) in sc_tools.EXECUTION_FILES.items()
        )
        console.print(f"[bold]files[/bold]  {files}", emoji=False)
        console.print(
            "[dim]Save one: `smart-connectors executions download "
            f"{escape(connector)} {escape(execution_id)} "
            "--file report|output|input`.[/dim]",
            emoji=False,
        )

    out.render(fmt, json_data=row, table=table)


@executions_app.command("download")
def executions_download(
    connector: str = CONNECTOR_ARG,
    execution_id: str = EXECUTION_ARG,
    file: sc_tools.ExecutionFile = typer.Option(
        "report",
        "--file",
        help=(
            "report: the .xlsx results workbook, with per-row errors, warnings "
            "and status. output: the zip of SQL-output CSVs. input: the file "
            "the run consumed."
        ),
    ),
    dest: str = typer.Option(
        None, "--out", help="File or directory (default: ./<server filename>)."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite an existing file."
    ),
    force: bool = typer.Option(False, "--force", "-f", hidden=True),
    json_out: bool = JSON_OPTION,
) -> None:
    """Save a run's Excel report (the default), SQL-output zip, or input file.

    Writes the local file only, with the bytes exactly as Kizen serves them. A
    failed run has no report or output zip.
    """
    if force:
        warn_renamed_flag("--force", "--overwrite")
        overwrite = True

    with cli_errors(LookupError, OSError):
        res = sc_tools.download_execution_file(
            connector, execution_id, file, dest=dest, force=overwrite
        )

    if json_out:
        out.emit_json(res)
        return
    console.print(
        f"[green]saved[/green] {escape(res['path'])} "
        f"({res['bytes']} bytes, {res['kind']})",
        emoji=False,
    )


@executions_app.command("sql")
def executions_sql(
    connector: str = CONNECTOR_ARG,
    execution_id: str = EXECUTION_ARG,
) -> None:
    """Print the SQL script used in one execution."""
    with cli_errors():
        script = sc_tools.get_execution_script(connector, execution_id)
    if script.get("user_script"):
        console.print(script["user_script"], markup=False, emoji=False, soft_wrap=True)
    else:
        console.print("[dim](empty)[/dim]")

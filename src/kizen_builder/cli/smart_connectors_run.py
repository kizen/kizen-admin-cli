"""`kizen smart-connectors` — webhook samples, activation, and flow starts."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markup import escape

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    cli_errors,
    console,
    err_console,
    parse_json,
    read_json_file,
    read_text_file,
    warn_renamed_flag,
)
from kizen_builder.cli.smart_connectors import (
    _connector_errors,
    _preview_and_confirm,
    smart_connectors_app,
)
from kizen_builder.tools import smart_connectors as sc_tools


@smart_connectors_app.command("webhook-sample")
def smart_connectors_webhook_sample(
    dest: str = typer.Argument(..., help="Path to write the sample CSV to."),
    body: str = typer.Option(
        ...,
        "--body",
        "-b",
        help="A representative JSON payload, or @path to read one from a file.",
    ),
    employee: str | None = typer.Option(
        None,
        "--employee",
        help="Team member (email, name, or UUID) to attribute the sample to. "
        "Must be real — a blank employee_id fails validation. Required.",
    ),
    employee_short: str | None = typer.Option(None, "-e", hidden=True),
    querystring: str = typer.Option("", "--querystring", help="Sample query string."),
    timestamp: str = typer.Option(
        "2026-01-01 00:00:00", "--timestamp", help="Sample timestamp."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Write the reference CSV a webhook connector's template generator needs.

    The required shape (columns timestamp, employee_id, querystring, body) isn't
    discoverable from the API — `get-file-template` just rejects anything else.
    The generator infers the whole `body` JSON column from the one payload in
    here, so use a representative one with every field you intend to read.

    Then: `set-input <that file> --connector <c>`.
    """
    if employee_short is not None:
        warn_renamed_flag("-e", "--employee")
        employee = employee_short
    if employee is None:
        err_console.print("[red]error:[/red] pass --employee.")
        raise typer.Exit(code=2)
    payload = body
    if body.startswith("@"):
        payload = read_text_file(body[1:], "--body")
    with _connector_errors(FileNotFoundError):
        result = sc_tools.build_webhook_sample(
            dest,
            body=payload,
            employee=employee,
            querystring=querystring,
            timestamp=timestamp,
        )

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]wrote[/green] {result['path']} ({', '.join(result['columns'])})"
    )
    console.print(f"  attributed to {result['employee']}")
    if result["body_keys"]:
        console.print(
            f"  body keys the generator will type: {', '.join(result['body_keys'])}"
        )
    console.print(
        "[dim]Next: `smart-connectors set-input "
        f"{result['path']} --connector <c>`.[/dim]"
    )


@smart_connectors_app.command("send-webhook")
def smart_connectors_send_webhook(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    body: str = typer.Option(
        ...,
        "--body",
        "-b",
        help="JSON payload to POST, or @path to read one from a file.",
    ),
    query: list[str] = typer.Option(
        [], "--query", "-q", help="key=value query-string param (repeatable)."
    ),
    ignore_blockers: bool = typer.Option(
        False, "--ignore-blockers", help="Send even when the plan reports blockers."
    ),
    force: bool = typer.Option(False, "--force", hidden=True),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Fire a connector's real inbound webhook. This writes records.

    Webhook connectors have no dry run — the receiver is the trigger, and
    `start-flow` doesn't apply to them. Requests are batched on the connector's
    cadence rather than processed per request, so expect up to a full cadence
    interval before an execution shows up in `executions list`.
    """
    if force:
        warn_renamed_flag("--force", "--ignore-blockers")
        ignore_blockers = True

    if body.startswith("@"):
        parsed = read_json_file(body[1:], "--body")
    else:
        parsed = parse_json(body, "--body")

    params: dict[str, str] = {}
    for item in query:
        if "=" not in item:
            raise typer.BadParameter(f"--query expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        params[key] = value

    with _connector_errors():
        plan = sc_tools.plan_send_webhook(connector, parsed, querystring=params or None)

    if plan["blockers"] and not ignore_blockers:
        for blocker in plan["blockers"]:
            err_console.print(f"[red]blocked:[/red] {blocker}")
        err_console.print("[dim]Pass --ignore-blockers to send anyway.[/dim]")
        raise typer.Exit(code=1)

    def render(target: Console) -> None:
        target.print(
            f"[bold]{plan['connector_api_name']}[/bold] — [red]LIVE[/red] inbound "
            f"webhook (writes records), status {plan['status']}, batched every "
            f"{plan['cadence']}s"
        )
        target.print(f"  body: {json.dumps(plan['body'])[:200]}")
        for blocker in plan["blockers"]:
            target.print(f"[yellow]![/yellow] {blocker}")

    if not _preview_and_confirm(
        plan,
        render=render,
        action="send the webhook",
        dry_run=False,
        yes=yes,
        json_out=json_out,
    ):
        return

    with cli_errors():
        result = sc_tools.apply_send_webhook(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(f"[green]accepted[/green] by {result['connector']}")
    console.print(
        f"[dim]Processing is batched on the connector's cadence "
        f"({result['cadence']}s) — an execution should appear within that window: "
        f"`smart-connectors executions list {result['connector']}`.[/dim]"
    )


def _set_status(
    connector: str, status: str, *, dry_run: bool, yes: bool, json_out: bool
) -> None:
    """Preview, confirm, and apply one status change (`activate` / `deactivate`)."""
    with _connector_errors():
        plan = sc_tools.plan_set_status(connector, status)
    name = escape(plan["connector_api_name"] or connector)

    if not plan["changed"]:
        if json_out:
            out.emit_json(
                {
                    "connector": plan["connector_api_name"],
                    "status": status,
                    "changed": False,
                }
            )
        else:
            console.print(f"[dim]{name} is already '{status}'.[/dim]", emoji=False)
        return

    def render(target: Console) -> None:
        target.print(
            f"[bold]{name}[/bold]: status {escape(plan['from_status'] or 'none')} "
            f"→ [bold]{plan['to_status']}[/bold]",
            emoji=False,
        )
        if plan["to_status"] == "operational":
            if not plan["execution_variables"]:
                target.print(
                    "[yellow]![/yellow] no execution variables — the server "
                    "refuses to activate without them (`configure-flow`)"
                )
            if not plan["load_steps"]:
                target.print(
                    "[yellow]![/yellow] no load steps configured — the server "
                    "refuses to activate until each load step is fully "
                    "configured (`configure-flow`)"
                )
            if not plan["has_live_script"]:
                target.print(
                    "[yellow]![/yellow] no published script — publish one with "
                    "`push --publish` or runs will have nothing to execute"
                )

    if not _preview_and_confirm(
        plan,
        render=render,
        action=f"set status to {status}",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    ):
        return

    with cli_errors():
        result = sc_tools.apply_set_status(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]{escape(result['connector'] or connector)}[/green] is now "
        f"'{escape(result['status'] or 'unknown')}'",
        emoji=False,
    )


@smart_connectors_app.command("activate")
def smart_connectors_activate(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    status: str | None = typer.Option(None, "--status", hidden=True),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Flip a connector to `operational` so live runs actually execute.

    A connector created through the API starts in `setup`. Dry runs work in any
    status, but a live run of a connector that isn't `operational` sits in
    `queued` indefinitely with no error — which is why this is its own command.
    The server refuses to activate without execution variables and fully
    configured load steps.
    """
    if status == "inactive":
        warn_renamed_flag("--status inactive", "smart-connectors deactivate")
    elif status is not None:
        err_console.print(
            "[yellow]warning:[/yellow] --status is deprecated; operational is the "
            "default."
        )
    _set_status(
        connector, status or "operational", dry_run=dry_run, yes=yes, json_out=json_out
    )


@smart_connectors_app.command("deactivate")
def smart_connectors_deactivate(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Set a connector to `inactive` so no live run starts.

    Every edit still works while inactive, and so do dry runs. `activate`
    brings it back with nothing re-done. Deactivate first when one change spans
    the flow and the SQL, so no run lands between the two writes.
    """
    _set_status(connector, "inactive", dry_run=dry_run, yes=yes, json_out=json_out)


@smart_connectors_app.command("start-flow")
def smart_connectors_start_flow(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    write_records: bool = typer.Option(
        False,
        "--write-records",
        help="Write real records. Without this the run is a server-side dry run: "
        "the flow is validated and nothing is written.",
    ),
    live: bool = typer.Option(False, "--live", hidden=True),
    ignore_blockers: bool = typer.Option(
        False,
        "--ignore-blockers",
        help="Queue the run even when the plan reports blockers.",
    ),
    force: bool = typer.Option(False, "--force", hidden=True),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Queue an execution of the connector (dry run unless --write-records).

    Runs are asynchronous; watch one with `smart-connectors executions get`,
    which shows the executor's own error for a failed run.

    Webhook connectors aren't started this way — they run on a real inbound POST
    to their webhook endpoint, batched on the connector's cadence.
    """
    # A hard error, not an alias: an alias would keep `--live` meaning "write
    # records" here while it means "use the live script" on `pull`.
    if live:
        err_console.print(
            "[red]error:[/red] start-flow --live was renamed --write-records "
            "(it writes real records); re-run with --write-records."
        )
        raise typer.Exit(code=2)
    if force:
        warn_renamed_flag("--force", "--ignore-blockers")
        ignore_blockers = True

    with _connector_errors():
        plan = sc_tools.plan_start_flow(connector, dry_run=not write_records)

    if plan["blockers"] and not ignore_blockers:
        for blocker in plan["blockers"]:
            err_console.print(f"[red]blocked:[/red] {blocker}")
        err_console.print("[dim]Pass --ignore-blockers to queue the run anyway.[/dim]")
        raise typer.Exit(code=1)

    def render(target: Console) -> None:
        kind = (
            "[red]LIVE[/red] (writes records)"
            if write_records
            else "dry run (writes nothing)"
        )
        target.print(
            f"[bold]{plan['connector_api_name']}[/bold] — {kind}, "
            f"status {plan['status']}, {plan['load_steps']} load step(s)"
        )
        for blocker in plan["blockers"]:
            target.print(f"[yellow]![/yellow] {blocker}")

    # A dry run writes nothing, so it doesn't need the confirm — same reasoning
    # as the local `run`. A live run always does.
    if not _preview_and_confirm(
        plan,
        render=render,
        action="live run" if write_records else "dry run",
        dry_run=False,
        yes=yes or not write_records,
        json_out=json_out,
    ):
        return

    with cli_errors():
        result = sc_tools.apply_start_flow(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]queued[/green] {'live' if write_records else 'dry'} run of "
        f"{result['connector']} — execution {result['execution']}"
    )
    console.print(
        f"[dim]Watch it: `smart-connectors executions get {result['connector']} "
        f"{result['execution']}`; history: `smart-connectors executions list "
        f"{result['connector']}{' --include-dry-run' if not write_records else ''}`.[/dim]"
    )

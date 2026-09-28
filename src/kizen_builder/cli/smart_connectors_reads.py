"""`kizen smart-connectors` reads — connectors, scripts, output samples,
events. Executions are their own group, in `smart_connectors_executions`."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
)
from kizen_builder.cli.smart_connectors import _output_tables, smart_connectors_app
from kizen_builder.tools import smart_connectors as sc_tools


@smart_connectors_app.command("list")
def smart_connectors_list(
    search: str = typer.Option(
        None, "--search", "-s", help="Filter by name / api_name text."
    ),
    connector_type: str = typer.Option(
        None,
        "--type",
        help="spreadsheet|webhook|polling_third_party_api|direct_api_connection|schedule|bulkaction",
    ),
    status: str = typer.Option(
        None, "--status", help="setup|operational|need_attention|inactive"
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List smart connectors in the current env."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        results = sc_tools.list_connectors(
            search=search, connector_type=connector_type, status=status
        )

    def table() -> None:
        t = Table(title="Smart connectors")
        t.add_column("api_name")
        t.add_column("name")
        t.add_column("type")
        t.add_column("status")
        t.add_column("object")
        t.add_column("used", justify="right")
        t.add_column("id", style="dim")
        for c in results:
            t.add_row(
                c.get("api_name") or "—",
                c.get("name") or "—",
                c.get("connector_type") or "—",
                c.get("status") or "—",
                str(c.get("custom_object") or "—"),
                str(c.get("used_count") if c.get("used_count") is not None else "—"),
                c.get("id") or "—",
            )
        console.print(t)
        if not results:
            console.print("[dim]No smart connectors found.[/dim]")

    out.render(
        fmt,
        json_data=results,
        table=table,
        csv_rows=results,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("api_name", "api_name"),
            out.Column("name", "name"),
            out.Column("type", "connector_type"),
            out.Column("status", "status"),
            out.Column("object", "custom_object"),
            out.Column("used_count", "used_count"),
        ],
    )


@smart_connectors_app.command("get")
def smart_connectors_get(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one connector's detail, including its draft/live SQL scripts."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        detail = sc_tools.get_connector(connector)

    def table() -> None:
        co = detail.get("custom_object") or {}
        draft = detail.get("last_draft_script") or {}
        live = detail.get("live_script") or {}
        t = Table(title=f"Connector: {detail.get('name')}", show_header=False)
        t.add_column("field", style="bold")
        t.add_column("value")
        t.add_row("id", detail.get("id") or "—")
        t.add_row("api_name", detail.get("api_name") or "—")
        t.add_row("type", detail.get("connector_type") or "—")
        t.add_row("status", detail.get("status") or "—")
        t.add_row(
            "custom_object", (co.get("name") if isinstance(co, dict) else co) or "—"
        )
        t.add_row(
            "draft_script", f"{draft.get('id') or '—'} ({draft.get('status') or 'n/a'})"
        )
        t.add_row(
            "live_script", f"{live.get('id') or '—'} ({live.get('status') or 'n/a'})"
        )
        t.add_row("used_count", str((detail.get("stats") or {}).get("used_count") or 0))
        t.add_row("last_used_at", str(detail.get("last_used_at") or "—"))
        console.print(t)
        console.print(
            "[dim]Tip: `smart-connectors pull "
            f"{detail.get('api_name') or detail.get('id')}` to iterate on the SQL locally.[/dim]"
        )

    out.render(fmt, json_data=detail, table=table)


@smart_connectors_app.command("metadata")
def smart_connectors_metadata() -> None:
    """Dump the connector-type / matching-rule metadata catalog (JSON)."""
    with cli_errors():
        meta = sc_tools.get_metadata()
    out.emit_json(meta)


@smart_connectors_app.command("scripts")
def smart_connectors_scripts(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a connector's SQL scripts (draft + live)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        results = sc_tools.list_scripts(connector)

    def table() -> None:
        t = Table(title=f"SQL scripts — {connector}")
        t.add_column("id", style="dim")
        t.add_column("status")
        t.add_column("state")
        t.add_column("version")
        t.add_column("lines", justify="right")
        t.add_column("updated")
        for s in results:
            t.add_row(
                s.get("id") or "—",
                s.get("status") or "—",
                s.get("state") or "—",
                s.get("sql_version") or "—",
                str(s.get("script_lines") or 0),
                str(s.get("updated") or "—"),
            )
        console.print(t)

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
                "state",
                "sql_version",
                "script_lines",
                "created",
                "updated",
            )
        ],
    )


@smart_connectors_app.command("download-sample")
def smart_connectors_download_sample(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    live: bool = typer.Option(
        False, "--live", help="The live script's sample instead of the draft's."
    ),
    script_id: str = typer.Option(
        None, "--script", help="Script id (overrides the draft/live choice)."
    ),
    dest: str = typer.Option(
        None, "--out", help="File or directory (default: ./<server filename>)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing file."
    ),
    json_out: bool = JSON_OPTION,
) -> None:
    """Save a script's output-sample zip (one <scope>.csv per output table).

    Writes the local file only. The sample is the one `generate-sample` last
    produced for that script.
    """
    with cli_errors(LookupError, OSError):
        res = sc_tools.download_sample(
            connector, use_live=live, script_id=script_id, dest=dest, force=force
        )

    if json_out:
        out.emit_json(res)
        return
    console.print(
        f"[green]saved[/green] {res['path']} ({res['bytes']} bytes, "
        f"{res['script_status'] or 'script'} {res['script_id']})"
    )
    if res["outputs"] is not None:
        console.print(f"  output tables: {_output_tables(res['outputs'])}")
    for w in res["warnings"]:
        console.print(f"  [yellow]![/yellow] {w}")


@smart_connectors_app.command("events")
def smart_connectors_events(
    smart_connector_id: str = typer.Argument(
        ..., help="Connector UUID (api_name is NOT accepted here)."
    ),
    event_type: str = typer.Option(None, "--event-type", help="Filter by event type."),
    date_from: str = typer.Option(None, "--from", help="ISO datetime lower bound."),
    date_to: str = typer.Option(None, "--to", help="ISO datetime upper bound."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a connector's event history (audit trail). Requires the UUID."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        results = sc_tools.list_events(
            smart_connector_id,
            event_type=event_type,
            date_from=date_from,
            date_to=date_to,
        )

    def table() -> None:
        # events-history items are untyped; render the common fields if present.
        t = Table(title=f"Events — {smart_connector_id}")
        t.add_column("created")
        t.add_column("event_type")
        t.add_column("summary")
        for ev in results:
            t.add_row(
                str(ev.get("created") or ev.get("timestamp") or "—"),
                str(ev.get("event_type") or ev.get("type") or "—"),
                str(
                    ev.get("message")
                    or ev.get("summary")
                    or ev.get("description")
                    or ""
                )[:80],
            )
        console.print(t)
        if not results:
            console.print("[dim]No events found.[/dim]")

    out.render(fmt, json_data=results, table=table)

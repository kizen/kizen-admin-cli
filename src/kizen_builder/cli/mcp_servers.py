"""`kizen mcp-servers` — the tool sets AI steps and MCP clients use."""

from __future__ import annotations

import typer
from rich.table import Table
from rich.text import Text

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
)
from kizen_builder.tools import mcp_servers as mcp_tools
from kizen_builder.tools.planners import mcp_servers as mcp_planners

EPILOG = "See `kizen docs show mcp-server`"
SERVER_ARG = typer.Argument(..., help="MCP server UUID or api_name.")

mcp_servers_app = typer.Typer(
    help="Read, create, update, and delete MCP servers.", no_args_is_help=True
)
app.add_typer(mcp_servers_app, name="mcp-servers")


def _flag(value: object) -> str:
    return "✓" if value else ""


def _upstream_names(server: dict) -> str:
    return ", ".join(u.get("name") or "" for u in server.get("upstream_servers") or [])


@mcp_servers_app.command("list", epilog=EPILOG)
def mcp_servers_list(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the MCP servers in the configured env."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        items = mcp_tools.list_mcp_servers()
    columns = [
        out.Column("api_name", "api_name"),
        out.Column("name", "name"),
        out.Column("tools", "tool_count"),
        out.Column("upstreams", _upstream_names),
        out.Column("enabled", "is_enabled"),
        out.Column("default", "is_default_for_business"),
        out.Column("system", "is_system_managed"),
        out.Column("id", "id"),
    ]

    def table() -> None:
        t = Table(title="MCP servers")
        t.add_column("api_name")
        t.add_column("name")
        t.add_column("tools", justify="right")
        t.add_column("upstreams")
        t.add_column("enabled", justify="center")
        t.add_column("default", justify="center")
        t.add_column("system", justify="center")
        t.add_column("id")
        for s in items:
            t.add_row(
                s.get("api_name") or "",
                Text(s.get("name") or ""),
                str(s.get("tool_count", "")),
                Text(_upstream_names(s)),
                _flag(s.get("is_enabled")),
                _flag(s.get("is_default_for_business")),
                _flag(s.get("is_system_managed")),
                s.get("id") or "",
            )
        console.print(t)

    out.render(fmt, json_data=items, table=table, csv_rows=items, csv_columns=columns)


@mcp_servers_app.command("get", epilog=EPILOG)
def mcp_servers_get(
    server: str = SERVER_ARG,
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Emit the full API response — a valid `mcp-servers update` spec. "
        "Implies JSON.",
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one MCP server, its endpoint URL, its tools and its upstreams.

    The endpoint URL is what an MCP client connects to; the CLI never calls it.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        detail = mcp_tools.get_mcp_server_detail(server)

    if raw:
        out.emit_json(detail["raw"])
        return

    def table() -> None:
        t = Table(show_header=False, box=None)
        t.add_column(style="bold")
        t.add_column()
        for label, value in (
            ("name", detail["name"]),
            ("api_name", detail["api_name"]),
            ("description", detail["description"]),
            ("enabled", detail["is_enabled"]),
            ("default", detail["is_default_for_business"]),
            ("system", detail["is_system_managed"]),
            ("endpoint_url", detail["endpoint_url"]),
            ("owner", detail["owner"]),
            ("id", detail["id"]),
        ):
            t.add_row(label, Text("" if value is None else str(value)))
        console.print(t)
        tools = Table(title="Tools")
        tools.add_column("group")
        tools.add_column("name")
        for tool in detail["tools"]:
            tools.add_row(tool["group"] or "", tool["name"] or "")
        console.print(tools)
        if detail["upstreams"]:
            ups = Table(title="Upstreams")
            ups.add_column("server")
            ups.add_column("tools", justify="right")
            for u in detail["upstreams"]:
                ups.add_row(
                    Text(u.get("server_name") or u.get("server") or ""),
                    "all"
                    if u.get("include_all")
                    else str(len(u.get("tool_ids") or [])),
                )
            console.print(ups)

    out.render(
        fmt,
        json_data={k: v for k, v in detail.items() if k != "raw"},
        table=table,
        csv_rows=detail["tools"],
        csv_columns=[out.Column("group", "group"), out.Column("name", "name")],
    )


def _tool_rows(catalog: dict) -> list[dict]:
    return [
        {
            "group": g.get("key"),
            "name": t.get("name"),
            "read_only": (t.get("annotations") or {}).get("readOnlyHint"),
            "description": (t.get("description") or "")[:80],
        }
        for g in catalog.get("groups") or []
        for t in g.get("tools") or []
    ]


@mcp_servers_app.command("tools", epilog=EPILOG)
def mcp_servers_tools(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the tools a server can serve — the valid `tool_names`.

    Upstream server tools: `kizen mcp-servers upstreams`.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        catalog = mcp_tools.tool_catalog()
    rows = _tool_rows(catalog)
    columns = [
        out.Column("group", "group"),
        out.Column("name", "name"),
        out.Column("read_only", "read_only"),
        out.Column("description", "description"),
    ]

    def table() -> None:
        t = Table(title="MCP tools")
        for c in columns:
            t.add_column(c.header)
        for r in rows:
            t.add_row(
                r["group"] or "",
                r["name"] or "",
                _flag(r["read_only"]),
                Text(r["description"]),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=catalog.get("groups") or [],
        table=table,
        csv_rows=rows,
        csv_columns=columns,
    )


@mcp_servers_app.command("upstreams", epilog=EPILOG)
def mcp_servers_upstreams(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List upstream MCP servers and their tools — the valid `upstreams` tool names.

    Create and authorize them in Kizen (Data › MCP Servers › Upstream Servers).
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        servers = mcp_tools.list_upstream_servers(with_tools=True)
    rows = [
        {
            "upstream": s.get("api_name"),
            "name": t.get("name"),
            "enabled": t.get("is_enabled"),
            "description": (t.get("description") or "")[:80],
        }
        for s in servers
        for t in s["tools"]
    ]
    columns = [
        out.Column("upstream", "upstream"),
        out.Column("name", "name"),
        out.Column("enabled", "enabled"),
        out.Column("description", "description"),
    ]

    def table() -> None:
        t = Table(title="Upstream servers")
        t.add_column("api_name")
        t.add_column("name")
        t.add_column("status")
        t.add_column("auth")
        t.add_column("enabled", justify="center")
        t.add_column("tools", justify="right")
        t.add_column("id")
        for s in servers:
            t.add_row(
                s.get("api_name") or "",
                Text(s.get("name") or ""),
                (s.get("connection") or {}).get("status") or "",
                (s.get("auth") or {}).get("type") or "",
                _flag(s.get("is_enabled")),
                str(s.get("tool_count") or 0),
                s.get("id") or "",
            )
        console.print(t)
        tools = Table(title="Upstream tools")
        for c in columns:
            tools.add_column(c.header)
        for r in rows:
            tools.add_row(
                r["upstream"] or "",
                Text(r["name"] or ""),
                _flag(r["enabled"]),
                Text(r["description"]),
            )
        console.print(tools)

    out.render(fmt, json_data=servers, table=table, csv_rows=rows, csv_columns=columns)


def _history_rows(events: list[dict]) -> list[dict]:
    return [
        {
            "when": e.get("created"),
            "event": e.get("type_display_name"),
            "by": mcp_tools.initiator_label(e),
            "changed": ", ".join(mcp_tools.changed_keys(e)) or "—",
        }
        for e in events
    ]


@mcp_servers_app.command("history", epilog=EPILOG)
def mcp_servers_history(
    server: str = SERVER_ARG,
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show a server's change history, newest first."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        events = mcp_tools.list_history(server)
    rows = _history_rows(events)
    columns = [
        out.Column("when", "when"),
        out.Column("event", "event"),
        out.Column("by", "by"),
        out.Column("changed", "changed"),
    ]

    def table() -> None:
        if not rows:
            console.print(
                "no events yet (Kizen records them a second or so after a change)"
            )
            return
        t = Table(title="History")
        for c in columns:
            t.add_column(c.header)
        for r in rows:
            t.add_row(*(Text(str(r[c.header] or "")) for c in columns))
        console.print(t)

    out.render(fmt, json_data=events, table=table, csv_rows=rows, csv_columns=columns)


SPEC_FILE_OPTION = typer.Option(
    "", "--spec-file", help="Path to a JSON MCPServerDef. Default: read from stdin."
)
DRY_RUN_OPTION = typer.Option(
    False, "--dry-run", help="Show the plan without applying."
)
YES_OPTION = typer.Option(
    False, "--yes", "-y", help="Skip the y/N confirmation prompt."
)
MUTATION_JSON_OPTION = typer.Option(
    False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
)


@mcp_servers_app.command("create", epilog=EPILOG)
def mcp_servers_create(
    spec_file: str = SPEC_FILE_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = MUTATION_JSON_OPTION,
) -> None:
    """Create an MCP server from a JSON MCPServerDef.

    `name` and `api_name` are required. Tool names are checked against
    `kizen mcp-servers tools`, and `upstreams` against the upstream servers.
    With no `sharing_settings`, it starts as the UI's New MCP Server dialog
    does: no access for all team members and no role or team member grants.
    """
    spec, from_stdin = _read_spec(spec_file, what="MCP server")
    _run_mutation(
        lambda: mcp_planners.plan_create_mcp_server(spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@mcp_servers_app.command("update", epilog=EPILOG)
def mcp_servers_update(
    server: str = SERVER_ARG,
    spec_file: str = SPEC_FILE_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = MUTATION_JSON_OPTION,
) -> None:
    """Update an MCP server from a JSON MCPServerDef.

    Only the keys the spec sets are compared; omitting `upstreams` keeps them.
    `get --raw` output works as a spec: its read-only keys are ignored. A
    Kizen-managed server takes only
    `{"is_default_for_business": true}`, which makes it the default again.
    """
    spec, from_stdin = _read_spec(spec_file, what="MCP server")
    _run_mutation(
        lambda: mcp_planners.plan_update_mcp_server(server, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@mcp_servers_app.command("delete", epilog=EPILOG)
def mcp_servers_delete(
    server: str = SERVER_ARG,
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = MUTATION_JSON_OPTION,
) -> None:
    """Delete an MCP server. Kizen deletes it outright, with its history."""
    _run_mutation(
        lambda: mcp_planners.plan_delete_mcp_server(server),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

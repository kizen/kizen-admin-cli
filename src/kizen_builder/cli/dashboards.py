"""`kizen dashboards` — dashboards / homepages."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.tools import dashboards as dash_tools
from kizen_builder.tools import dashlet_templates as dash_tpl
from kizen_builder.tools.planners import dashboards as dash_planners

dashboards_app = typer.Typer(
    help="Read, create, and update dashboards and homepages.", no_args_is_help=True
)
app.add_typer(dashboards_app, name="dashboards")


@dashboards_app.command("list")
def dashboards_list(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List dashboards and homepages in the configured env."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        items = dash_tools.list_dashboards()

    def table() -> None:
        t = Table(title="Dashboards & homepages (mine)")
        t.add_column("api_name")
        t.add_column("name")
        t.add_column("owner")
        t.add_column("dashlets", justify="right")
        t.add_column("pub", justify="center")
        t.add_column("id")
        for d in items:
            t.add_row(
                d.get("api_name") or "",
                d.get("name") or "",
                d.get("owner") or "",
                str(
                    d.get("dashlets_count")
                    if d.get("dashlets_count") is not None
                    else ""
                ),
                "✓" if d.get("published") else "",
                (d.get("id") or ""),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("api_name", "api_name"),
            out.Column("name", "name"),
            out.Column("owner", "owner"),
            out.Column("dashlets_count", "dashlets_count"),
            out.Column("published", "published"),
            out.Column("hidden", "hidden"),
            out.Column("id", "id"),
        ],
    )


@dashboards_app.command("get")
def dashboards_get(
    dashboard: str = typer.Argument(..., help="Dashboard UUID or api_name."),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Emit the full raw API payload (dashlet configs included) — the "
        "template source for `dashboards create/update`. Implies JSON.",
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one dashboard/homepage plus a summary of its dashlets.

    `--raw` dumps the complete API payload (every dashlet's full `config`
    and `layout`) so you can copy a dashlet as a template for a mutation.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        result = dash_tools.get_dashboard_detail(dashboard)

    if raw:
        out.emit_json(result["raw"])
        return

    def table() -> None:
        console.print(
            f"[bold]{result.get('name')}[/bold]  "
            f"[dim]({result.get('api_name')}, {result.get('type')}, "
            f"id={(result.get('id') or '')})[/dim]"
        )
        dl_table = Table(title="Dashlets")
        dl_table.add_column("name")
        dl_table.add_column("report_type")
        dl_table.add_column("chart_type")
        dl_table.add_column("pos", justify="center")
        dl_table.add_column("id")
        for dl in result["dashlets"]:
            pos = f"{dl.get('x')},{dl.get('y')} {dl.get('w')}×{dl.get('h')}"
            dl_table.add_row(
                dl.get("name") or "",
                dl.get("report_type") or "",
                dl.get("chart_type") or "",
                pos,
                (dl.get("id") or ""),
            )
        console.print(dl_table)

    out.render(
        fmt,
        json_data={k: v for k, v in result.items() if k != "raw"},
        table=table,
        csv_rows=result["dashlets"],
        csv_columns=[
            out.Column("name", "name"),
            out.Column("report_type", "report_type"),
            out.Column("chart_type", "chart_type"),
            out.Column("custom_object", "custom_object"),
            out.Column("x", "x"),
            out.Column("y", "y"),
            out.Column("w", "w"),
            out.Column("h", "h"),
            out.Column("id", "id"),
        ],
    )


@dashboards_app.command(
    "create",
    epilog="Spec shape (a DashboardDef + dashlets): see `kizen docs show dashboard`",
)
def dashboards_create(
    spec_file: str = typer.Option(
        "", "--spec-file", help="Path to a JSON DashboardDef. Default: read from stdin."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Create a dashboard/homepage and its dashlets from a JSON DashboardDef.

    Generate each dashlet's `config` with `kizen dashboards dashlet-config`
    (or copy one from a live dashlet via `dashboards get <id> --raw`). The plan
    creates the dashboard, then each dashlet against the new dashboard id.
    """
    spec_dict, from_stdin = _read_spec(spec_file, what="dashboard")
    _run_mutation(
        lambda: dash_planners.plan_create_dashboard(spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@dashboards_app.command(
    "update",
    epilog="Spec shape (a DashboardDef; dashlets diffed by id): see `kizen docs show dashboard`",
)
def dashboards_update(
    dashboard: str = typer.Argument(..., help="Dashboard UUID or api_name."),
    spec_file: str = typer.Option(
        "", "--spec-file", help="Path to a JSON DashboardDef. Default: read from stdin."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Update a dashboard's metadata and/or dashlets from a JSON DashboardDef.

    Metadata changes PATCH the dashboard. Dashlets are diffed by `id`: those
    with an existing `id` are updated, those without are created. Dashlets
    present live but missing from the spec are left untouched.
    """
    spec_dict, from_stdin = _read_spec(spec_file, what="dashboard")
    _run_mutation(
        lambda: dash_planners.plan_update_dashboard(dashboard, spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@dashboards_app.command("duplicate")
def dashboards_duplicate(
    dashboard: str = typer.Argument(..., help="Dashboard UUID or api_name."),
    name: str = typer.Option(
        "",
        "--name",
        help="Name for the copy. Default: the server's '<name> (copy #N)'.",
    ),
    api_name: str = typer.Option(
        "",
        "--api-name",
        help="api_name for the copy. Default: the server's '<api_name>_copy_N'.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Copy a dashboard, its dashlets and its sharing.

    The copy is unpublished; publish it with `dashboards update`.
    """
    _run_mutation(
        lambda: dash_planners.plan_duplicate_dashboard(
            dashboard, name=name or None, api_name=api_name or None
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@dashboards_app.command(
    "dashlet-config",
    epilog="Then drop the config into a DashboardDef — see `kizen docs show dashboard`",
)
def dashboards_dashlet_config(
    dashlet_type: str = typer.Option(
        "",
        "--type",
        "-t",
        help="Dashlet type to generate. Omit to list every available type.",
    ),
    object_ref: str = typer.Option(
        "",
        "--object",
        help="Object api_name (custom object / pipeline / activity type, per "
        "the type). Resolved live to a UUID; omit for a <OBJECT_UUID> placeholder.",
    ),
    field_ref: str = typer.Option(
        "",
        "--field",
        help="Field api_name on --object. Resolved live; omit for a "
        "<FIELD_UUID> placeholder.",
    ),
    report_type: str = typer.Option(
        "", "--report-type", help="Override report_type (metric families only)."
    ),
    chart_type: str = typer.Option(
        "", "--chart-type", help="Override chart_type (metric families only)."
    ),
    metric_type: str = typer.Option(
        "", "--metric-type", help="Override metric_type (metric families only)."
    ),
    frequency: str = typer.Option(
        "", "--frequency", help="day/week/month — required for chart_type=line."
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Wrap the config in a full DashboardDef ready for `dashboards create`.",
    ),
) -> None:
    """Print a valid dashlet `config` for a named type, ready to edit.

    The config comes straight from the `tools.dashboards` builders, so it
    can't drift from what `dashboards create` accepts — and unlike "copy a
    live dashlet", it works on an env with no populated dashboards. Pass
    `--object`/`--field` as **api_names** (never UUIDs) to bake real ids
    in; omit them for `<...>` placeholders you fill later.
    """
    if not dashlet_type:
        t = Table(title="Dashlet types (kizen dashboards dashlet-config --type <t>)")
        t.add_column("type")
        t.add_column("report/chart")
        t.add_column("takes")
        t.add_column("what it is")
        for dt in dash_tpl.available_types():
            takes = []
            if dt.takes_object:
                takes.append(f"--object ({dt.object_kind})")
            if dt.takes_field:
                takes.append("--field")
            note = " [dim](homepage only)[/dim]" if dt.homepage_only else ""
            t.add_row(
                dt.key,
                f"{dt.report_type}/{dt.chart_type or '∅'}",
                ", ".join(takes) or "—",
                dt.summary + note,
            )
        console.print(t)
        return

    with cli_errors(LookupError, ValueError):
        gen = dash_tpl.generate(
            dashlet_type,
            object_ref=object_ref or None,
            field_ref=field_ref or None,
            report_type=report_type or None,
            chart_type=chart_type or None,
            metric_type=metric_type or None,
            frequency=frequency or None,
        )

    config = gen.config
    if dash_tpl.has_placeholders(config):
        err_console.print(
            "[dim]note: config has <...> placeholders — replace them, or "
            "re-run with --object/--field (api_names) to resolve live.[/dim]"
        )
    if gen.homepage_only:
        err_console.print(
            "[dim]note: custom-object dashlet — only valid on a "
            "type=homepage dashboard.[/dim]"
        )

    payload = (
        dash_tpl.wrap_as_dashboard(
            dashlet_type, config, custom_object=gen.custom_object
        )
        if full
        else config
    )
    out.emit_json(payload)

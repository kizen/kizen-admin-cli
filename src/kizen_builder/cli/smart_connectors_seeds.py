"""`kizen smart-connectors seeds` — expose rows from other Kizen objects to a
connector's SQL as `kizen.<object>` views.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
    warn_renamed_flag,
)
from kizen_builder.cli.smart_connectors import (
    _connector_errors,
    _object_option,
    _preview_and_confirm,
    smart_connectors_app,
)
from kizen_builder.tools import smart_connectors as sc_tools
from kizen_builder.tools.plans import PlanError

seeds_app = typer.Typer(
    help=(
        "Seed a connector with rows from other Kizen objects, exposed to its SQL "
        "as a `kizen.<object>` view so incoming data can be joined against what's "
        "already in Kizen. A seed covers every record of the seeded object, or "
        "only those in a saved filter group (segment) given with --filter-group."
    ),
    no_args_is_help=True,
)
smart_connectors_app.add_typer(seeds_app, name="seeds")


@seeds_app.command("list")
def smart_connectors_seeds_list(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a connector's seeded objects and the columns each one exposes.

    `in_script` is the one to check: a seed that isn't in the script yet is
    inert — the `kizen.<object>` view doesn't exist until the config is
    refreshed (which `seeds add` does by default).
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        results = sc_tools.list_seeds(connector)

    def table() -> None:
        t = Table(title=f"Data seeds — {connector}")
        t.add_column("object")
        t.add_column("filter group")
        t.add_column("SQL view")
        t.add_column("columns")
        t.add_column("in script")
        for s in results:
            t.add_row(
                str(s.get("custom_object") or "—"),
                str(s.get("filter_group") or "—"),
                str(s.get("view") or "—"),
                ", ".join(c for c in (s.get("columns") or []) if c) or "—",
                "yes" if s.get("in_script") else "[yellow]no[/yellow]",
            )
        console.print(t)
        if not results:
            console.print(
                "[dim]No seeds. Add one with `smart-connectors seeds add "
                f"{connector} --object <o>`.[/dim]"
            )

    out.render(
        fmt,
        json_data=results,
        table=table,
        csv_rows=results,
        csv_columns=[
            out.Column(k, k)
            for k in ("custom_object", "filter_group", "group_id", "view", "in_script")
        ],
    )


@seeds_app.command("add")
def smart_connectors_seeds_add(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    obj: str | None = typer.Option(
        None, "--object", help="Object api_name (or UUID) to seed from. Required."
    ),
    obj_short: str | None = typer.Option(None, "-o", hidden=True),
    group: str | None = typer.Option(
        None,
        "--filter-group",
        help="Saved filter group (segment) name or UUID on that object — see "
        "`kizen filter-groups list <object>`. NOT a field category. Omit it to "
        "seed all records.",
    ),
    group_old: str | None = typer.Option(None, "--group", "-g", hidden=True),
    field: list[str] = typer.Option(
        [],
        "--field",
        "-f",
        help="Field api_name to bring along (repeatable). kizen_id always comes.",
    ),
    regenerate: bool = typer.Option(
        True,
        "--regenerate/--no-regenerate",
        help="Refresh the draft's config so the kizen.<object> view exists "
        "(default). Your SQL is preserved. Without this the seed is inert.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without writing."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Seed a connector from another Kizen object's records.

    Adding a seed the connector already has for that object replaces it.
    """
    obj = _object_option(obj, obj_short)
    if group_old is not None:
        warn_renamed_flag("--group", "--filter-group")
        group = group_old
    with _connector_errors():
        plan = sc_tools.plan_add_seed(
            connector,
            custom_object=obj,
            group=group,
            fields=list(field) or None,
            regenerate=regenerate,
        )

    def render(target: Console) -> None:
        t = Table(
            title=("Replace" if plan["replacing"] else "Add")
            + f" data seed — {plan['connector_api_name']}",
            show_header=False,
        )
        t.add_column("field", style="bold")
        t.add_column("value")
        t.add_row("object", plan["custom_object"])
        t.add_row("filter group", plan["filter_group"])
        t.add_row(
            "fields", ", ".join(plan["fields"]) if plan["fields"] else "kizen_id only"
        )
        t.add_row("SQL view", plan["view"])
        t.add_row(
            "refresh script config",
            "yes" if plan["regenerate"] else "[yellow]no[/yellow]",
        )
        target.print(t)
        coverage = plan.get("coverage")
        if coverage and coverage["segment"] < coverage["total"]:
            target.print(
                f"[yellow]![/yellow] the segment covers {coverage['segment']} of "
                f"{coverage['total']} records — records outside it read as not "
                "found to the SQL, so a match-or-create connector re-creates them"
            )
        elif group and not coverage:
            target.print(
                "[dim]couldn't count the segment's records to check coverage[/dim]"
            )
        if not plan["regenerate"]:
            target.print(
                "[yellow]![/yellow] without a refresh the view won't exist for the "
                "SQL — a saved seed alone does nothing"
            )

    if not _preview_and_confirm(
        plan,
        render=render,
        action=f"seed from {plan['custom_object']}",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    ):
        return
    _apply_seed_change(plan, json_out=json_out)


@seeds_app.command("remove")
def smart_connectors_seeds_remove(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    obj: str | None = typer.Option(
        None, "--object", help="Seeded object api_name (or UUID) to drop. Required."
    ),
    obj_short: str | None = typer.Option(None, "-o", hidden=True),
    regenerate: bool = typer.Option(
        True,
        "--regenerate/--no-regenerate",
        help="Refresh the draft's config so the view stops being advertised.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without writing."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Stop seeding a connector from an object.

    SQL that still selects from the removed `kizen.<object>` view will fail on
    the next run — check the script before removing.
    """
    obj = _object_option(obj, obj_short)
    with _connector_errors():
        plan = sc_tools.plan_remove_seed(connector, obj, regenerate=regenerate)

    def render(target: Console) -> None:
        target.print(
            f"[bold]{plan['connector_api_name']}[/bold]: stop seeding "
            f"[bold]{plan['custom_object']}[/bold] — {plan['view']} will no longer "
            f"be available to the SQL ({len(plan['payload'])} seed(s) left)"
        )

    if not _preview_and_confirm(
        plan,
        render=render,
        action=f"stop seeding {plan['custom_object']}",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    ):
        return
    _apply_seed_change(plan, json_out=json_out)


def _apply_seed_change(plan: dict[str, Any], *, json_out: bool) -> None:
    """Shared tail for `seeds add` / `seeds remove`."""
    with cli_errors(PlanError):
        result = sc_tools.apply_seed_change(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]saved[/green] {result['seeds']} seed(s) on {result['connector']}"
    )
    if result.get("refreshed"):
        console.print(
            f"  script {result['script_id']} config refreshed — views: "
            f"{', '.join(f'kizen.{t}' for t in result['seed_tables'] if t) or 'none'}"
            + ("" if result.get("kept_user_script") else " (script was empty)")
        )
    if result.get("warning"):
        console.print(f"  [yellow]![/yellow] {result['warning']}")

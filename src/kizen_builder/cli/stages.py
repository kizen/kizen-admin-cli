"""`kizen objects stages` — pipeline stages, a sub-resource of a
pipeline-type object (not field options).
"""

from __future__ import annotations

from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.cli.objects import objects_app
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools.planners import pipeline_stages as stage_planners

objects_stages_app = typer.Typer(
    help=(
        "Manage a pipeline's stages — status, chance-to-close, order. These "
        "live at a dedicated endpoint distinct from the object's mirrored "
        "'stage' field; `fields options` cannot manage them (see `objects get` "
        "for a read-only view of the same data)."
    ),
    no_args_is_help=True,
)
objects_app.add_typer(objects_stages_app, name="stages")


@objects_stages_app.command("list")
def stages_list(
    pipeline: str = typer.Argument(..., help="Pipeline object api_name (or UUID)."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a pipeline's stages (name, status, chance-to-close, order)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        obj = obj_tools.get_object(pipeline)
    if obj.get("object_type") != "pipeline":
        err_console.print(
            f"[red]error:[/red] '{pipeline}' is not a pipeline object "
            f"(object_type={obj.get('object_type')!r})."
        )
        raise typer.Exit(code=1)
    items = obj.get("stages") or []

    def table() -> None:
        t = Table(title=f"Stages — {obj.get('api_name')}")
        t.add_column("order", justify="right")
        t.add_column("name")
        t.add_column("status")
        t.add_column("chance to close", justify="right")
        t.add_column("id")
        for s in items:
            pct = s.get("percentage_chance_to_close")
            t.add_row(
                str(s.get("order") if s.get("order") is not None else ""),
                s.get("name") or "",
                s.get("status") or "",
                f"{pct}%" if pct is not None else "",
                s.get("id") or "",
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("order", "order"),
            out.Column("name", "name"),
            out.Column("status", "status"),
            out.Column("percentage_chance_to_close", "percentage_chance_to_close"),
            out.Column("id", "id"),
        ],
    )


@objects_stages_app.command("create")
def stages_create(
    pipeline: str = typer.Argument(..., help="Pipeline object api_name (or UUID)."),
    name: str = typer.Option(..., "--name", help="Stage display name."),
    status: str | None = typer.Option(
        None,
        "--status",
        help="open | won | lost | disqualified. Kizen's default when omitted: open.",
    ),
    pct: int | None = typer.Option(
        None, "--pct", help="Percentage chance to close (0-100)."
    ),
    order: int | None = typer.Option(
        None, "--order", help="Position among stages (defaults to last)."
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
    """Create one stage on a pipeline object."""
    _run_mutation(
        lambda: stage_planners.plan_create_stage(
            pipeline,
            name,
            status=status,
            percentage_chance_to_close=pct,
            order=order,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@objects_stages_app.command("update")
def stages_update(
    pipeline: str = typer.Argument(..., help="Pipeline object api_name (or UUID)."),
    stage: str = typer.Argument(..., help="Stage name or UUID."),
    name: str = typer.Option("", "--name", help="New display name."),
    status: str = typer.Option(
        "", "--status", help="New status: open | won | lost | disqualified."
    ),
    pct: int | None = typer.Option(
        None, "--pct", help="New percentage chance to close (0-100)."
    ),
    order: int | None = typer.Option(
        None, "--order", help="New position among stages."
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
    """Update one stage on a pipeline object. Only the flags you set are changed."""
    changes: dict[str, Any] = {}
    if name:
        changes["name"] = name
    if status:
        changes["status"] = status
    if pct is not None:
        changes["percentage_chance_to_close"] = pct
    if order is not None:
        changes["order"] = order
    if not changes:
        err_console.print("[red]error:[/red] no changes given.")
        raise typer.Exit(code=2)

    _run_mutation(
        lambda: stage_planners.plan_update_stage(pipeline, stage, changes),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@objects_stages_app.command("remove")
def stages_remove(
    pipeline: str = typer.Argument(..., help="Pipeline object api_name (or UUID)."),
    stage: str = typer.Argument(..., help="Stage name or UUID to remove."),
    move_to: str = typer.Option(
        ...,
        "--move-to",
        help="Stage (name or UUID) to migrate the removed stage's records onto.",
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
    """Remove one stage from a pipeline, migrating its records to --move-to."""
    _run_mutation(
        lambda: stage_planners.plan_remove_stage(pipeline, stage, move_to),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )

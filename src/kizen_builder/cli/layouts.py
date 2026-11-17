"""`kizen layouts` — record layouts attached to a custom object."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    FULL_OPTION,
    JSON_OPTION,
    MUTATION_JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
)
from kizen_builder.tools import layouts as layout_tools
from kizen_builder.tools.planners import layouts as layout_planners

layouts_app = typer.Typer(
    help="Read and update record layouts for a custom object.", no_args_is_help=True
)
app.add_typer(layouts_app, name="layouts")


@layouts_app.command("list")
def layouts_list(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the record layouts defined on a custom object."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        items = layout_tools.list_layouts(object_api_name)

    def table() -> None:
        t = Table(title=f"Layouts — {object_api_name}")
        t.add_column("name")
        t.add_column("active", justify="center")
        t.add_column("order", justify="right")
        t.add_column("blocks", justify="right")
        t.add_column("id")
        for lo in items:
            t.add_row(
                lo.get("name") or "",
                "✓" if lo.get("active") else "",
                str(lo.get("order") if lo.get("order") is not None else ""),
                str(lo.get("block_count")),
                (lo.get("id") or ""),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("name", "name"),
            out.Column("active", "active"),
            out.Column("order", "order"),
            out.Column("block_count", "block_count"),
            out.Column("id", "id"),
        ],
    )


@layouts_app.command("get")
def layouts_get(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    name: str = typer.Option(
        None, "--name", help="Layout name (default: the first / Standard View)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one record layout: its column/block structure and full config."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        result = layout_tools.get_layout(object_api_name, layout_name=name)

    def table() -> None:
        console.print(
            f"[bold]{result.get('name')}[/bold]  "
            f"[dim](id={(result.get('id') or '')}, "
            f"active={result.get('active')})[/dim]"
        )
        blk_table = Table(title="Blocks")
        blk_table.add_column("grp", justify="right")
        blk_table.add_column("col", justify="right")
        blk_table.add_column("width")
        blk_table.add_column("type")
        blk_table.add_column("internalName")
        blk_table.add_column("auto", justify="center")
        for b in result["blocks"]:
            blk_table.add_row(
                str(b.get("group")),
                str(b.get("column")),
                b.get("width") or "",
                b.get("type") or "",
                b.get("internalName") or "",
                "✓" if b.get("autoInclude") else "",
            )
        console.print(blk_table)

    out.render(
        fmt,
        json_data={k: v for k, v in result.items() if k != "raw"},
        table=table,
        csv_rows=result["blocks"],
        csv_columns=[
            out.Column("group", "group"),
            out.Column("column", "column"),
            out.Column("width", "width"),
            out.Column("type", "type"),
            out.Column("internalName", "internalName"),
            out.Column("displayName", "displayName"),
            out.Column("autoInclude", "autoInclude"),
        ],
        keep_empty=True,
    )


@layouts_app.command(
    "update",
    epilog="Spec shape (a LayoutDef; PUT-replaces the layout): see `kizen docs show layout`",
)
def layouts_update(
    object_api_name: str = typer.Argument(..., help="Object api_name."),
    spec_file: str = typer.Option(
        "", "--spec-file", help="Path to a JSON LayoutDef. Default: read from stdin."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Replace a record layout from a JSON LayoutDef (--spec-file or stdin).

    Layouts are PUT-replace: the spec's `config` fully replaces the target
    layout (default 'Standard View', override with the spec's `name`). Block
    `id`s are injected automatically; non-`fields` blocks pass through
    opaquely. Start from `kizen layouts get <object> -o json`.
    """
    spec_dict, from_stdin = _read_spec(spec_file, what="layout")
    _run_mutation(
        lambda: layout_planners.plan_update_layout(object_api_name, spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
        stdin_consumed=from_stdin,
    )

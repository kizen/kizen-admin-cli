"""`kizen filters` — the filter DSL's per-type operator reference."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    console,
    err_console,
)

filters_app = typer.Typer(
    help="Look up valid --filter operators (no live env needed).", no_args_is_help=True
)
app.add_typer(filters_app, name="filters")


@filters_app.command("ops")
def filters_ops(
    field_type: str = typer.Argument(
        "", help="A field_type (e.g. text, dropdown, date). Omit to list every type."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List the ops a --filter JSON clause can use, per field type.

    Field types come from `kizen objects get <object>` (each field's
    `field_type` column). Sourced directly from the filtering module's own
    op tables, so this can't drift from what --filter actually accepts.
    """
    from kizen_builder import filtering

    fmt = out.resolve_format(output, json_out)
    try:
        result = filtering.field_type_ops(field_type or None)
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=2) from e

    def table() -> None:
        t = Table()
        t.add_column("field_type")
        t.add_column("valid ops")
        rows = {field_type: result} if field_type else result
        for ft, ops in rows.items():
            t.add_row(ft, ", ".join(ops))
        console.print(t)
        console.print(
            "\n[dim]Not covered here: the pipeline 'stage' field's own conditions "
            "(time_in_stage/entered_stage/left_stage) and narrower overrides on "
            "a few default fields (created, updated, owner, email_status). See "
            "`kizen docs show filters`.[/dim]"
        )

    csv_rows = (
        [{"field_type": ft, "ops": ", ".join(ops)} for ft, ops in result.items()]
        if not field_type
        else [{"field_type": field_type, "ops": ", ".join(result)}]
    )
    out.render(
        fmt,
        json_data={field_type: result} if field_type else result,
        table=table,
        csv_rows=csv_rows,
        csv_columns=[out.Column("field_type", "field_type"), out.Column("ops", "ops")],
    )

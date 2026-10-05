"""`kizen records` — record reads."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
    parse_json,
    read_text_file,
)
from kizen_builder.tools import records as record_tools

records_app = typer.Typer(
    help="Read individual records from a custom object.", no_args_is_help=True
)
app.add_typer(records_app, name="records")


@records_app.command("get")
def records_get(
    object_api_name: str = typer.Argument(
        ..., help="Object api_name (e.g. document_set)."
    ),
    record_id: str = typer.Argument(
        None, help="Record UUID. Omit and use --name to look up by name."
    ),
    name: str = typer.Option(
        "", "--name", help="Look up the record by its name field instead of UUID."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one record from a custom object, including all field values.

    Identify the record by its UUID (positional) or by `--name` (exact,
    case-insensitive match on the name field; errors if the name is ambiguous).
    CSV flattens the record to a single wide row (`id` plus one column
    per field) — the same shape as `records list --output csv`.
    """
    fmt = out.resolve_format(output, json_out)
    if bool(record_id) == bool(name):
        err_console.print(
            "[red]error:[/red] pass either a record UUID or --name, not both (and not neither)."
        )
        raise typer.Exit(code=2)
    with cli_errors(LookupError, ValueError):
        if name:
            result = record_tools.get_record_by_name(object_api_name, name)
        else:
            result = record_tools.get_record(object_api_name, record_id)

    resolved_id = result.get("id") or record_id

    def table() -> None:
        console.print(f"[bold]{object_api_name}[/bold]  [dim](id={resolved_id})[/dim]")
        t = Table(title="Fields")
        t.add_column("field", style="dim")
        t.add_column("value")

        skip_keys = {"env", "object_api_name", "object_id"}
        for key, value in result.items():
            if key in skip_keys:
                continue
            if isinstance(value, list):
                if not value:
                    t.add_row(key, "[dim](empty)[/dim]")
                for item in value:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("filename") or ""
                        url = (
                            item.get("url")
                            or item.get("download_url")
                            or item.get("file")
                            or ""
                        )
                        if url:
                            t.add_row(key, f"{name}  [link={url}]{url}[/link]")
                        else:
                            t.add_row(key, json.dumps(item))
                    else:
                        t.add_row(key, str(item))
            elif isinstance(value, dict):
                t.add_row(key, json.dumps(value))
            elif value is None:
                t.add_row(key, "[dim]null[/dim]")
            else:
                t.add_row(key, str(value))

        console.print(t)

    out.render(
        fmt,
        json_data=result,
        table=table,
        csv_rows=[result],
        csv_columns=out.record_csv_columns([result]),
    )


@records_app.command("list")
def records_list(
    object_api_name: str = typer.Argument(
        ..., help="Object api_name (e.g. client_client)."
    ),
    search: str = typer.Option(
        "", "--search", "-s", help="Text search term (filters by name/display fields)."
    ),
    filter_json: str = typer.Option(
        "",
        "--filter",
        help=(
            'Structured filter as JSON. Either a spec — {"all"|"any": [...]} '
            'groups of {"field", "op", "value"} conditions, with field/option '
            "names resolved against the live schema — or raw Kizen filter "
            'groups ({"query": [...]}) passed through as-is. Run '
            "`kizen filters ops [field_type]` to see which ops a field's "
            "type supports."
        ),
    ),
    filter_file: str = typer.Option(
        "", "--filter-file", help="Path to a JSON filter (same format as --filter)."
    ),
    fields: str = typer.Option(
        "",
        "--fields",
        help=(
            "Comma-separated field api_names to fetch and show as table "
            "columns (e.g. --fields ticker_symbol,purchase_price). Must be "
            "api_names, not display labels or field UUIDs; an unrecognized "
            "name is rejected before any request is sent."
        ),
    ),
    limit: int = typer.Option(100, "--limit", "-n", help="Max records to return."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List records from a custom object (up to --limit).

    The table shows id and name for each record, plus one column per
    `--fields` entry if given; `--output json` and `--output csv` show the
    same set (id, name, and the requested fields — full field data if
    `--fields` is omitted). Use --search for text matching, --filter for
    structured field conditions, or `records get` to inspect a single
    record.
    """
    fmt = out.resolve_format(output, json_out)
    if filter_json and filter_file:
        err_console.print("[red]error:[/red] pass --filter or --filter-file, not both.")
        raise typer.Exit(code=2)

    filters = None
    filter_text = (
        read_text_file(filter_file, "--filter-file") if filter_file else filter_json
    )
    if filter_text:
        from kizen_builder import filtering

        spec = parse_json(
            filter_text, f"--filter-file {filter_file}" if filter_file else "--filter"
        )
        # A bad filter is a usage error (exit 2) with its own wording; failing
        # to reach Kizen while resolving the filter's fields is not.
        with cli_errors():
            try:
                filters = filtering.render_search_filters(spec, object_api_name)
            except (ValueError, LookupError) as e:
                err_console.print(f"[red]filter error:[/red] {e}")
                raise typer.Exit(code=2) from e

    field_names = [f.strip() for f in fields.split(",") if f.strip()] or None

    with cli_errors(LookupError):
        records = record_tools.search_records(
            object_api_name,
            filters=filters,
            search=search or None,
            limit=limit,
            **({"field_names": field_names} if field_names else {}),
        )

    def _record_name(r: dict[str, Any]) -> str:
        name = r.get("name") or ""
        if not name:
            for fdata in r.get("fields", {}).values():
                if fdata.get("name") == "name":
                    name = str(fdata.get("value") or "")
                    break
        return name

    # The always-present id/name columns, plus one column per --fields entry
    # (in the order given, deduped against id/name and against itself).
    extra_columns: list[str] = []
    seen_extra: set[str] = set()
    for name in field_names or []:
        if name in ("id", "name") or name in seen_extra:
            continue
        seen_extra.add(name)
        extra_columns.append(name)

    def table() -> None:
        console.print(
            f"[bold]{object_api_name}[/bold]  [dim]({len(records)} record(s))[/dim]"
        )
        t = Table()
        t.add_column("id", style="dim")
        t.add_column("name")
        for col in extra_columns:
            t.add_column(col)
        for r in records:
            row = [(r.get("id") or ""), _record_name(r)]
            if extra_columns:
                field_map = out.record_field_map(r)
                row.extend(out.cell_str(field_map.get(col)) for col in extra_columns)
            t.add_row(*row)
        console.print(t)

    out.render(
        fmt,
        json_data=records,
        table=table,
        csv_rows=records,
        csv_columns=out.record_csv_columns(records),
    )


@records_app.command("related")
def records_related(
    record_id: str = typer.Argument(
        ..., help="Record UUID (any object — no object identifier needed)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List a record's related pipeline records.

    Works across objects — pass any record's UUID. Same data code steps
    hand-roll today via a second per-relationship-field lookup.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        results = record_tools.related_records(record_id)

    def table() -> None:
        console.print(
            f"[bold]related pipeline records[/bold]  [dim]({len(results)} record(s))[/dim]"
        )
        t = Table()
        t.add_column("id", style="dim")
        t.add_column("name")
        t.add_column("object")
        t.add_column("stage")
        for r in results:
            stage = r.get("stage")
            t.add_row(
                r.get("id") or "",
                r.get("display_name") or r.get("name") or "",
                r.get("object_name") or "",
                (stage.get("name") if isinstance(stage, dict) else "") or "",
            )
        console.print(t)

    out.render(fmt, json_data=results, table=table)


@records_app.command("field-values")
def records_field_values(
    record_id: str = typer.Argument(
        ..., help="Record UUID (any object — no object identifier needed)."
    ),
    field: str = typer.Argument(
        ..., help="Field UUID, or 'object_api_name.field_api_name'."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Pull all values from a summarized relationship field on one record.

    Same data code steps hand-roll today via a second per-relationship-field
    API call.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError, ValueError):
        results = record_tools.field_values(record_id, field)

    def table() -> None:
        console.print(
            f"[bold]field values[/bold]  [dim]({len(results)} value(s))[/dim]"
        )
        t = Table()
        t.add_column("id", style="dim")
        t.add_column("name")
        for v in results:
            if isinstance(v, dict):
                t.add_row(
                    v.get("id") or "", v.get("name") or v.get("display_name") or ""
                )
            else:
                t.add_row("", str(v))
        console.print(t)

    out.render(fmt, json_data=results, table=table)

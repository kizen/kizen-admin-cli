"""The one renderer every `where-used` command prints through."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text

from kizen_builder import output as out
from kizen_builder.cli._shared import console

_COLUMNS = ("section", "name", "where", "id")


def _name_cell(row: dict[str, Any]) -> Text:
    cell = Text(row["name"])
    if row.get("can_view") is False:
        cell.append(" (you can't open it)", style="dim")
    return cell


def render_where_used(
    result: dict[str, Any], fmt: out.OutputFormat, target_label: str
) -> None:
    def table() -> None:
        for c in result["counts"]:
            if c["count"]:
                console.print(Text(f"{c['count']} {c['label']}"))
        if not result["used_by"]:
            console.print(Text(f"Kizen reports nothing using {target_label}."))
            return
        t = Table(title="Used by")
        for header in _COLUMNS:
            t.add_column(header, style="dim" if header == "id" else None)
        for row in result["used_by"]:
            t.add_row(
                Text(row["section"]),
                _name_cell(row),
                Text(row["where"]),
                Text(row["id"]),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=result,
        table=table,
        csv_rows=result["used_by"],
        csv_columns=[out.Column(c, c) for c in _COLUMNS],
    )

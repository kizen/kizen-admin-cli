"""Unified output layer for the ``kizen`` CLI.

Every read command emits one of three formats:

* ``table`` — rich tables to stdout, for humans at the terminal (default);
  piped, the same rows without borders or padding.
* ``json`` — pretty JSON, for agents and API-style consumers.
* ``csv``  — ANSI-free CSV to stdout, for spreadsheets and piping;
  the workhorse format for ``records list`` and automation executions.

Format selection is centralized here so every command shares one flag
surface and one dispatch path. The rich tables themselves stay in the
command bodies (passed to :func:`render` as a ``table`` callable) — this
module owns *which* format runs and the JSON/CSV emitters, not the bespoke
table styling.

Design notes:

* ``--output/-o`` is canonical; ``--json`` is kept as a back-compat alias
  (it is documented in CLAUDE.md and baked into agent workflows).
* JSON and CSV go to stdout with no rich markup so they pipe cleanly;
  errors already go to stderr in the command bodies.
* :func:`for_agent` is the one check for "a program is reading this, not a
  person": true when the stream isn't a terminal, overridden either way by
  ``KIZEN_OUTPUT=terminal|agent``. Everything that prints differently when
  piped asks it, rather than calling ``isatty()`` itself.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import IO, Any

import typer

from kizen_builder.config import ConfigError

OUTPUT_ENV = "KIZEN_OUTPUT"


class OutputFormat(StrEnum):
    """The three supported render formats."""

    TABLE = "table"
    JSON = "json"
    CSV = "csv"


def resolve_format(output: str | None, json_flag: bool = False) -> OutputFormat:
    """Merge the ``--output`` option with the legacy ``--json`` alias.

    ``--json`` (still accepted everywhere) wins when set, so old muscle
    memory and existing agent scripts keep working. Otherwise ``--output``
    decides, defaulting to a human-facing table.

    Raises :class:`typer.BadParameter` on an unknown ``--output`` value so
    the CLI reports it as a usage error (exit 2) rather than crashing.
    """
    if json_flag:
        return OutputFormat.JSON
    if output is None:
        return OutputFormat.TABLE
    try:
        return OutputFormat(output.lower())
    except ValueError as e:
        choices = ", ".join(f.value for f in OutputFormat)
        raise typer.BadParameter(
            f"invalid output format {output!r}; choose one of: {choices}"
        ) from e


def for_agent(stream: IO[str] | None = None) -> bool:
    """Whether output to ``stream`` (default: the current stdout) should take
    its piped form: ``KIZEN_OUTPUT=agent`` → true, ``KIZEN_OUTPUT=terminal`` →
    false, unset → true unless the stream is a terminal.

    Raises :class:`ConfigError` for any other ``KIZEN_OUTPUT`` value.
    """
    mode = os.environ.get(OUTPUT_ENV, "")
    if mode == "agent":
        return True
    if mode == "terminal":
        return False
    if mode:
        raise ConfigError(
            f"{OUTPUT_ENV}={mode!r} is not a choice; use 'terminal' or 'agent'."
        )
    stream = sys.stdout if stream is None else stream
    isatty = getattr(stream, "isatty", None)
    return not (isatty is not None and isatty())


@dataclass
class Column:
    """One CSV column: a header plus how to pull its value from a row.

    ``value`` is either a dict key (str) or a callable taking the row and
    returning a cell value. Cells are stringified via :func:`cell_str` so
    money/list/dict field values flatten predictably.
    """

    header: str
    value: str | Callable[[dict[str, Any]], Any]

    def extract(self, row: dict[str, Any]) -> Any:
        if callable(self.value):
            return self.value(row)
        return row.get(self.value)


def cell_str(value: Any) -> str:
    """Flatten one value to a plain CSV cell (no markup, no surprises).

    * ``None`` → empty string.
    * money-style dicts (``{amount, currency, ...}``) → the amount.
    * other dicts → a ``name``/``filename`` if present, else compact JSON.
    * lists → each item flattened and joined with ``"; "``.
    * everything else → ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        if "amount" in value:
            return cell_str(value["amount"])
        for key in ("name", "filename", "display_name", "value"):
            if key in value and not isinstance(value[key], (dict, list)):
                return cell_str(value[key])
        return json.dumps(value, default=str, separators=(",", ":"))
    if isinstance(value, list):
        return "; ".join(cell_str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_csv(rows: Sequence[dict[str, Any]], columns: Sequence[Column]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c.header for c in columns])
    for row in rows:
        writer.writerow([cell_str(c.extract(row)) for c in columns])
    return buf.getvalue()


def emit_json(data: Any) -> None:
    """Print pretty JSON to stdout (``default=str`` for stray non-JSON types)."""
    typer.echo(json.dumps(data, indent=2, default=str))


def emit_csv(rows: Sequence[dict[str, Any]], columns: Sequence[Column]) -> None:
    """Print CSV to stdout — header row always emitted, even for empty results."""
    # csv already writes a trailing newline per row; strip our own to avoid a
    # blank line at the end when echoed.
    typer.echo(_write_csv(rows, columns).rstrip("\n"))


def render(
    fmt: OutputFormat,
    *,
    json_data: Any,
    table: Callable[[], None],
    csv_rows: Sequence[dict[str, Any]] | None = None,
    csv_columns: Sequence[Column] | None = None,
) -> None:
    """Dispatch one command's output to the selected format.

    ``json_data`` is emitted verbatim for JSON. ``table`` is the command's
    existing rich-rendering closure, run only for table format. CSV needs
    ``csv_rows`` + ``csv_columns``; a command with no meaningful CSV shape
    (e.g. a free-form detail blob) may omit them, in which case CSV falls
    back to JSON with a note on stderr.
    """
    if fmt is OutputFormat.JSON:
        emit_json(json_data)
    elif fmt is OutputFormat.CSV:
        if csv_columns is None:
            # No tabular shape defined — degrade to JSON rather than error.
            from kizen_builder.cli import err_console  # local import: avoid cycle

            err_console.print(
                "[yellow]note:[/yellow] this command has no CSV form; emitting JSON."
            )
            emit_json(json_data)
        else:
            emit_csv(csv_rows or [], csv_columns)
    else:
        table()


# ---------------------------------------------------------------------------
# Record flattening — the meat of CSV for `records list` / `records get`
# ---------------------------------------------------------------------------


def record_field_map(record: dict[str, Any]) -> dict[str, Any]:
    """Map field ``name`` → value for one record's ``fields`` dict.

    Records come back as ``{"fields": {<uuid>: {name, value, ...}}}``. This
    collapses that to ``{<field_name>: <value>}`` for flat CSV columns, and
    is reused by ``cli/records.py`` to build the ``--fields`` table columns
    so there is one field-lookup implementation, not two.
    """
    out: dict[str, Any] = {}
    for fdata in (record.get("fields") or {}).values():
        if not isinstance(fdata, dict):
            continue
        name = fdata.get("name")
        if name:
            out[name] = fdata.get("value")
    return out


def record_csv_columns(records: Sequence[dict[str, Any]]) -> list[Column]:
    """Build stable CSV columns for a set of records.

    Leading ``id`` column, then one column per distinct field ``name`` in
    first-seen order (the union across all records, so ragged records still
    line up). Each field column reads through the record's flattened field
    map.
    """
    columns: list[Column] = [Column("id", lambda r: r.get("id") or "")]
    seen: set[str] = set()
    for record in records:
        for name in record_field_map(record):
            if name not in seen:
                seen.add(name)

                # Bind name via default arg to avoid late-binding in the loop.
                # A `def` rather than a `lambda` here: mypy can't infer the
                # type of a lambda's extra default-valued parameter against
                # `Column.value`'s narrower `Callable[[dict[str, Any]], Any]`.
                def _cell(r: dict[str, Any], n: str = name) -> Any:
                    return record_field_map(r).get(n)

                columns.append(Column(name, _cell))
    return columns

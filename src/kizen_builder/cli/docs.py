"""`kizen docs` — the manual, served from the installed package."""

from __future__ import annotations

import sys

import typer
from rich.table import Table

from kizen_builder import docs as docs_res
from kizen_builder import output as out
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    console,
    err_console,
)

docs_app = typer.Typer(
    help="Read the documentation that ships with this CLI.",
    no_args_is_help=True,
)
app.add_typer(docs_app, name="docs")


@docs_app.command("show")
def docs_show(
    topic: str = typer.Argument(
        ...,
        help="Topic name, e.g. operating, commands, reference, automation. See `kizen docs list`.",
    ),
    rendered: bool | None = typer.Option(
        None,
        "--rendered/--raw",
        help="Force rich rendering, or the raw markdown file. "
        "Default: rendered in a terminal, raw when piped or captured "
        "(KIZEN_OUTPUT=terminal|agent overrides the default).",
    ),
) -> None:
    """Print one documentation topic.

    `operating` is the manual — the approval gate and the rules for acting on
    live state. `commands` maps the command surface. `reference` covers API
    quirks and wire formats. Everything else is a spec-file shape.

    Piped or captured output is the raw markdown file, byte for byte, so it
    greps cleanly.
    """
    try:
        path = docs_res.topic_path(topic)
    except docs_res.DocsUnavailable as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except LookupError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    text = path.read_text()
    if rendered is None:
        rendered = not out.for_agent()
    if not rendered:
        # Bypass Console so the output is byte-faithful and pipe-safe.
        sys.stdout.write(text)
        return
    from rich.markdown import Markdown

    console.print(Markdown(text))


@docs_app.command("list")
def docs_list(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List every topic `kizen docs show` accepts."""
    try:
        topics = docs_res.list_topics()
        guides = [t for t in topics if t in docs_res.GUIDE_TOPICS]
    except docs_res.DocsUnavailable as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    fmt = out.resolve_format(output, json_out)
    rows = [{"topic": t, "kind": "guide" if t in guides else "surface"} for t in topics]

    def table() -> None:
        t = Table(title="kizen docs")
        t.add_column("topic")
        t.add_column("kind")
        for row in rows:
            t.add_row(row["topic"], row["kind"])
        console.print(t)
        console.print(
            "\n[dim]A surface topic is everything about one kind of Kizen entity: "
            "the spec shape a --spec-file command expects, plus its wire formats "
            "and quirks. Each command's --help names its topic.[/dim]"
        )

    out.render(
        fmt,
        json_data=rows,
        table=table,
        csv_rows=rows,
        csv_columns=[out.Column("topic", "topic"), out.Column("kind", "kind")],
    )


@docs_app.command("path")
def docs_path() -> None:
    """Print the filesystem path of the packaged docs tree."""
    try:
        console.print(str(docs_res.docs_root()))
    except docs_res.DocsUnavailable as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

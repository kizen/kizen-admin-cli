"""Objects every command module needs: the root Typer app, its callback and
the group that ends any command on one `error:` line, the two consoles (which
print tables as plain rows when piped), the shared output-format options,
`cli_errors()`, `exit_spec_errors()`, `exit_spec_problems()`, the user-file
readers (`read_text_file`, `read_json_file`, `parse_json`),
`warn_renamed_flag()`, and `_short`.

Nothing here imports a command module, so every other module in the
package can import this one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typer.core import TyperGroup

from kizen_builder import __version__
from kizen_builder.api.client import KizenAPIError
from kizen_builder.config import ConfigError, set_profile_override
from kizen_builder.output import for_agent
from kizen_builder.tools.findings import SpecProblems


class _RootGroup(TyperGroup):
    """End any command on one `error:` line instead of a traceback.

    `invoke` turns the expected errors (`_ALWAYS_EXPECTED`) into exit 1
    wherever a command raises them. `main` is the last resort for anything
    else, also exit 1; `KIZEN_DEBUG=1` lets it through with its traceback.
    `typer.Exit`, `Abort` and usage errors reach neither: Typer handles them
    inside `main` and calls `sys.exit`.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except _ALWAYS_EXPECTED as e:
            _print_error(e)
            raise typer.Exit(code=1) from e

    def main(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().main(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if os.environ.get("KIZEN_DEBUG") == "1":
                raise
            _print_error(
                f"internal: {type(e).__name__}: {e} "
                "(re-run with KIZEN_DEBUG=1 for the traceback)"
            )
            sys.exit(1)


app = typer.Typer(
    cls=_RootGroup,
    help=(
        "Kizen Admin CLI — drive a Kizen environment from the conversation. "
        "The working directory's .kizen/profile pin selects the environment. "
        "Read commands are safe. Mutation verbs (create/update) build a plan "
        "from live state, show it, and confirm before applying; "
        "--dry-run previews without applying. Commands that execute live "
        "(`start`, `runs`, `roundtrip --execute`, `code test`) ask first, "
        "except `runs pause`."
    ),
    epilog=(
        "New here? Run `kizen docs show operating` before making changes — it "
        "covers the approval gate and the rules for acting on live state. "
        "`kizen docs list` shows every available topic. "
        "Piped or captured, tables print as plain `| a | b |` rows and docs as "
        "raw markdown; KIZEN_OUTPUT=terminal or KIZEN_OUTPUT=agent picks either "
        "form anywhere."
    ),
    no_args_is_help=True,
)


class _Console(Console):
    """A Console that prints a `Table` as plain rows when `for_agent()` says
    its stream is read by a program.

    The piped form is a `title:` line, a `| h1 | h2 |` header and one
    `| c1 | c2 |` line per row: no borders, no padding, no wrapping, no
    styles. Everything that isn't a table prints as usual.
    """

    def print(self, *objects: Any, **kwargs: Any) -> None:
        if any(isinstance(o, Table) for o in objects) and for_agent(self.file):
            objects = tuple(
                Text(self._flat_table(o)) if isinstance(o, Table) else o
                for o in objects
            )
            kwargs["soft_wrap"] = True
            kwargs.pop("crop", None)
        super().print(*objects, **kwargs)

    def _flat_table(self, table: Table) -> str:
        lines = []
        if table.title:
            lines.append(f"{self._plain(table.title)}:")
        if table.show_header:
            lines.append(_row(self._plain(c.header) for c in table.columns))
        cells = [list(c.cells) for c in table.columns]
        for i in range(table.row_count):
            lines.append(_row(self._plain(col[i]) for col in cells))
        return "\n".join(lines)

    def _plain(self, cell: RenderableType) -> str:
        """One cell as one line of plain text; a multi-line cell is joined
        with `; ` and a `|` is escaped so the row keeps its shape."""
        if isinstance(cell, str):
            text = self.render_str(cell).plain
        elif isinstance(cell, Text):
            text = cell.plain
        else:
            scratch = Console(width=self.width, color_system=None, file=io.StringIO())
            scratch.print(cell)
            text = scratch.file.getvalue()  # type: ignore[attr-defined]
        joined = "; ".join(s for line in text.splitlines() if (s := line.strip()))
        return joined.replace("|", "\\|")


def _row(cells: Iterable[str]) -> str:
    return "| " + " | ".join(cells) + " |"


console = _Console(width=220)
err_console = _Console(stderr=True, width=220)

# Shared output-format controls for read commands. `--output/-o` is
# canonical; `--json` is kept as a documented back-compat alias (see
# kizen_builder.output.resolve_format).
OUTPUT_OPTION = typer.Option(
    None, "--output", "-o", help="Output format: table (default), json, or csv."
)
JSON_OPTION = typer.Option(False, "--json", help="Alias for --output json.")
# Every mutation verb's --json and --full.
MUTATION_JSON_OPTION = typer.Option(
    False,
    "--json",
    help="Emit JSON (plan with --dry-run, results otherwise). `raw` holds the "
    "server response's top-level values; `--full` gives the whole body.",
)
FULL_OPTION = typer.Option(
    False, "--full", help="With --json, keep the whole server response in `raw`."
)

# Every command that talks to Kizen fails the same way: one `error: <message>`
# line on stderr, exit code 1. `ConfigError` (no usable credentials for this
# directory) and `KizenAPIError` (the API said no) are expected on any command,
# so `_RootGroup` catches them app-wide; `cli_errors()` adds more per call site.
_ALWAYS_EXPECTED: tuple[type[Exception], ...] = (ConfigError, KizenAPIError)


def _print_error(e: object) -> None:
    """Print `error: <e>` as one stderr line, `<e>` as literal text."""
    err_console.print(
        f"[red]error:[/red] {escape(str(e))}", emoji=False, soft_wrap=True
    )


@contextlib.contextmanager
def cli_errors(*also: type[Exception]) -> Iterator[None]:
    """Render an expected failure as `error: <message>` and exit 1.

    Wrap the call that can fail rather than the whole function body, when only
    part of a command talks to Kizen::

        with cli_errors(LookupError):
            obj = obj_tools.get_object(api_name)

    Anything beyond `ConfigError`/`KizenAPIError` has to be named. That is
    deliberate: which failures a command *expects* differs per command —
    `LookupError` where it resolves a name to a UUID, `PlanError` where it
    builds a plan — and widening the tuple to cover everything would report
    real bugs as user error.
    """
    try:
        yield
    except _ALWAYS_EXPECTED + also as e:
        _print_error(e)
        raise typer.Exit(code=1) from e


def exit_spec_errors(e: ValidationError) -> NoReturn:
    """Print one `spec error: <loc>: <msg>` line per bad field, then exit 1."""
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "spec"
        err_console.print(
            f"[red]spec error:[/red] {escape(loc)}: {escape(err['msg'])}", emoji=False
        )
    raise typer.Exit(code=1) from e


def exit_spec_problems(e: SpecProblems, json_out: bool = False) -> NoReturn:
    """Print every finding, then exit 1. Each error is a `spec error:` line
    and each warning a `warning:` line, with a `see:` line under it naming
    the command to run. With `json_out`, stdout gets ``{"ok": false,
    "errors": [...], "warnings": [...]}`` instead."""
    if json_out:
        typer.echo(json.dumps(e.as_dict(), indent=2, default=str))
        raise typer.Exit(code=1) from e
    for label, findings in (
        ("[red]spec error:[/red]", e.errors),
        ("[yellow]warning:[/yellow]", e.warnings),
    ):
        for f in findings:
            err_console.print(
                f"{label} {escape(f.text())}", emoji=False, soft_wrap=True
            )
            if f.hint:
                err_console.print(f"  see: {escape(f.hint)}", emoji=False)
    raise typer.Exit(code=1) from e


def warn_renamed_flag(old: str, new: str) -> None:
    """Warn that a renamed flag's old spelling was used.

    A renamed flag keeps its old spelling as a hidden `typer.Option` beside the
    new one; the command merges the two in its body and calls this when the old
    one was passed::

        overwrite: bool = typer.Option(False, "--overwrite", help="..."),
        force: bool = typer.Option(False, "--force", "-f", hidden=True),
        ...
        if force:
            warn_renamed_flag("--force", "--overwrite")
            overwrite = True

    The warning goes to stderr only, so `--json` stdout stays parseable.
    """
    err_console.print(
        f"[yellow]warning:[/yellow] {escape(old)} is deprecated; use {escape(new)}.",
        emoji=False,
    )


def read_text_file(path: str, flag: str) -> str:
    """Read a user-named file. A missing or unreadable one prints
    `error: <flag> <path>: <reason>` and exits 2."""
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError) as e:
        reason = getattr(e, "strerror", None) or str(e)
        _print_error(f"{flag} {path}: {reason}")
        raise typer.Exit(code=2) from e


def parse_json(text: str, source: str) -> Any:
    """Parse user-supplied JSON. Malformed input prints
    `error: <source>: invalid JSON: <msg>` and exits 2."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        _print_error(f"{source}: invalid JSON: {e}")
        raise typer.Exit(code=2) from e


def read_json_file(path: str, flag: str) -> Any:
    """`read_text_file`, then `parse_json`."""
    return parse_json(read_text_file(path, flag), f"{flag} {path}")


def load_filter_spec(filter_json: str, filter_file: str) -> Any:
    """The JSON from a `--filter`/`--filter-file` pair, or None when neither
    is set. Passing both prints an `error:` line and exits 2."""
    if filter_json and filter_file:
        err_console.print("[red]error:[/red] pass --filter or --filter-file, not both.")
        raise typer.Exit(code=2)
    text = read_text_file(filter_file, "--filter-file") if filter_file else filter_json
    if not text:
        return None
    return parse_json(
        text, f"--filter-file {filter_file}" if filter_file else "--filter"
    )


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"kizen-builder {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    profile: str = typer.Option(
        None,
        "--profile",
        "-p",
        "--env",
        "-e",
        help=(
            "Profile to target. Normally the working directory's .kizen/profile "
            "pin decides; this overrides it, but a pinned directory still "
            "refuses a profile whose business_id doesn't match the pin."
        ),
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the installed version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Kizen Admin CLI — the active env is pinned to the directory by default."""
    set_profile_override(profile)
    for_agent()  # a bad KIZEN_OUTPUT fails here, before any command runs


def _short(text: Any, limit: int = 90) -> str:
    """Clip long cell text (e.g. a call_llm prompt) for the table view; the
    full value stays in JSON/CSV output."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"

"""Objects every command module needs: the root Typer app, its callback and
the group that ends any command on one `error:` line, the two consoles, the
shared output-format options, `cli_errors()`, the user-file readers
(`read_text_file`, `read_json_file`, `parse_json`), `warn_renamed_flag()`, and
`_short`.

Nothing here imports a command module, so every other module in the
package can import this one.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from typer.core import TyperGroup

from kizen_builder import __version__
from kizen_builder.api.client import KizenAPIError
from kizen_builder.config import ConfigError, set_profile_override


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
        "`kizen docs list` shows every available topic."
    ),
    no_args_is_help=True,
)
console = Console(width=220)
err_console = Console(stderr=True, width=220)

# Shared output-format controls for read commands. `--output/-o` is
# canonical; `--json` is kept as a documented back-compat alias (see
# kizen_builder.output.resolve_format).
OUTPUT_OPTION = typer.Option(
    None, "--output", "-o", help="Output format: table (default), json, or csv."
)
JSON_OPTION = typer.Option(False, "--json", help="Alias for --output json.")

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


def _short(text: Any, limit: int = 90) -> str:
    """Clip long cell text (e.g. a call_llm prompt) for the table view; the
    full value stays in JSON/CSV output."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"

"""Flag names mean one thing across the whole CLI.

The tree walk covers every command, so a new command that reuses a retired
name fails here rather than shipping a flag that reads two ways.
"""

from __future__ import annotations

import typer

import kizen_builder.cli as cli
from kizen_builder.cli._shared import warn_renamed_flag

# `--live` read as both "write real records" and "use the live script";
# `--force` as both "overwrite local files" and "ignore plan blockers".
RETIRED = {"--live", "--force"}


def _iter_commands(command, path):
    """Walk the resolved Click command tree, yielding (path, command)."""
    yield path, command
    for name, sub in getattr(command, "commands", {}).items():
        yield from _iter_commands(sub, path + [name])


def test_no_visible_option_uses_a_retired_name():
    root = typer.main.get_command(cli.app)
    found = [
        f"`kizen {' '.join(path)}` {name}"
        for path, command in _iter_commands(root, [])
        for param in command.params
        if not getattr(param, "hidden", False)
        for name in [*param.opts, *param.secondary_opts]
        if name in RETIRED
    ]
    assert not found, "retired flag name is visible:\n" + "\n".join(found)


def test_warn_renamed_flag_writes_to_stderr_only(capsys):
    warn_renamed_flag("--force", "--overwrite")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "warning: --force is deprecated; use --overwrite." in captured.err

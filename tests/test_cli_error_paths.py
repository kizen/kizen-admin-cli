"""User mistakes and API refusals end on one `error:` line, never a traceback.

A crash under CliRunner lands in `result.exception`, never as `Traceback` in
stderr, so these assert `isinstance(result.exception, SystemExit)`: the
command exited on purpose rather than raising.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli

runner = CliRunner()


def _plain(text: str) -> str:
    # CI forces a terminal, so rich colours the `error:` tag and Typer boxes
    # its usage errors.
    return re.sub(r"\x1b\[[0-9;]*m", "", text).replace("│", "")


def _error_lines(result) -> list[str]:
    return [line for line in _plain(result.stderr).splitlines() if line.strip()]


def _assert_one_error_line(result, code: int) -> str:
    assert result.exit_code == code, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    lines = _error_lines(result)
    assert len(lines) == 1, lines
    assert lines[0].startswith("error:"), lines[0]
    return lines[0]


# (argv with {path} where the file goes, the flag the error must name)
FILE_COMMANDS = [
    (["automations", "update", "--spec-file", "{path}", "--dry-run"], "--spec-file"),
    (["records", "list", "obj", "--filter-file", "{path}"], "--filter-file"),
    (
        ["permissions", "group-create", "--name", "x", "--settings-file", "{path}"],
        "--settings-file",
    ),
    (["fields", "create", "obj", "--spec-file", "{path}", "--dry-run"], "--spec-file"),
    (
        ["columns", "create", "obj", "--name", "x", "--config-file", "{path}"],
        "--config-file",
    ),
    (["smart-connectors", "send-webhook", "c", "--body", "@{path}"], "--body"),
]


def _argv(template: list[str], path: str) -> list[str]:
    return [a.replace("{path}", path) for a in template]


@pytest.mark.parametrize(("argv", "flag"), FILE_COMMANDS)
def test_missing_file_exits_2_on_one_line(tmp_path, argv, flag):
    missing = str(tmp_path / "nope.json")
    result = runner.invoke(cli.app, _argv(argv, missing))
    line = _assert_one_error_line(result, 2)
    assert line == f"error: {flag} {missing}: No such file or directory"


@pytest.mark.parametrize(("argv", "flag"), FILE_COMMANDS)
def test_malformed_json_file_exits_2_on_one_line(tmp_path, argv, flag):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = runner.invoke(cli.app, _argv(argv, str(bad)))
    line = _assert_one_error_line(result, 2)
    assert line.startswith(f"error: {flag} {bad}: invalid JSON: ")


def test_apply_missing_plan_file_exits_2_on_one_line(tmp_path):
    missing = str(tmp_path / "plan.json")
    result = runner.invoke(cli.app, ["apply", "--plan-file", missing])
    line = _assert_one_error_line(result, 2)
    assert line == f"error: --plan-file {missing}: No such file or directory"


def test_unreadable_file_names_the_reason_once(tmp_path):
    result = runner.invoke(
        cli.app, ["automations", "update", "--spec-file", str(tmp_path), "--dry-run"]
    )
    line = _assert_one_error_line(result, 2)
    assert line == f"error: --spec-file {tmp_path}: Is a directory"


def test_optional_spec_with_empty_stdin_falls_back_to_flags():
    # `fields create` reads a piped spec only when no single-field flag is
    # given; an empty pipe means "use the flags", which are missing here.
    result = runner.invoke(cli.app, ["fields", "create", "obj"], input="")
    assert result.exit_code == 2, result.output
    assert "single-field create needs" in result.stderr

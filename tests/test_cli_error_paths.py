"""User mistakes and API refusals end on one `error:` line, never a traceback.

A crash under CliRunner lands in `result.exception`, never as `Traceback` in
stderr, so these assert `isinstance(result.exception, SystemExit)`: the
command exited on purpose rather than raising.
"""

from __future__ import annotations

import re

import pytest
import typer
from rich.prompt import Confirm
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli._shared import _RootGroup
from kizen_builder.config import ConfigError
from kizen_builder.tools import objects as obj_tools

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


@pytest.mark.parametrize(
    "exc",
    [
        ConfigError("No environment specified."),
        KizenAPIError(400, "bad [/data]"),
    ],
)
def test_expected_error_outside_cli_errors_exits_1(monkeypatch, exc):
    def boom():
        raise exc

    monkeypatch.setattr(obj_tools, "list_objects", boom)
    result = runner.invoke(
        cli.app, ["activities", "update", "x", "--object", "foo", "--dry-run"]
    )
    line = _assert_one_error_line(result, 1)
    assert line == f"error: {exc}"


def test_unexpected_error_is_one_internal_line(monkeypatch):
    def boom():
        raise ValueError("wat [/x]")

    monkeypatch.setattr(obj_tools, "list_objects", boom)
    result = runner.invoke(
        cli.app, ["activities", "update", "x", "--object", "foo", "--dry-run"]
    )
    line = _assert_one_error_line(result, 1)
    assert line == (
        "error: internal: ValueError: wat [/x] "
        "(re-run with KIZEN_DEBUG=1 for the traceback)"
    )
    assert result.stdout == ""


def test_kizen_debug_lets_the_unexpected_error_through(monkeypatch):
    err = ValueError("wat")

    def boom():
        raise err

    monkeypatch.setattr(obj_tools, "list_objects", boom)
    monkeypatch.setenv("KIZEN_DEBUG", "1")
    result = runner.invoke(
        cli.app, ["activities", "update", "x", "--object", "foo", "--dry-run"]
    )
    assert result.exception is err


# --- the root group leaves Typer's own exits alone ------------------------
# A throwaway app with the same group class, so each exit path can be raised
# directly. `exit-2` is how a confirm gate refuses when it can't prompt.

probe = typer.Typer(cls=_RootGroup)


@probe.command("exit-2")
def _exit_2() -> None:
    typer.echo("can't prompt", err=True)
    raise typer.Exit(code=2)


@probe.command("confirm")
def _confirm() -> None:
    if not Confirm.ask("go?", default=False):
        typer.echo("aborted")
        raise typer.Exit(code=1)


@probe.command("abort")
def _abort() -> None:
    raise typer.Abort()


@probe.command("count")
def _count(n: int) -> None:
    typer.echo(n)


def test_root_group_passes_typer_exit_through():
    result = runner.invoke(probe, ["exit-2"])
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert _error_lines(result) == ["can't prompt"]


def test_root_group_passes_a_declined_prompt_through():
    result = runner.invoke(probe, ["confirm"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in result.stdout
    assert "internal" not in result.stderr


def test_root_group_passes_abort_through():
    result = runner.invoke(probe, ["abort"])
    assert result.exit_code == 1
    assert "Aborted" in _plain(result.stderr)
    assert "internal" not in result.stderr


def test_root_group_passes_usage_errors_through():
    result = runner.invoke(probe, ["count", "x"])
    assert result.exit_code == 2
    assert "Invalid value" in _plain(result.stderr)
    assert "internal" not in result.stderr


def test_real_app_usage_error_and_help_unchanged():
    usage = runner.invoke(cli.app, ["records", "list"])
    assert usage.exit_code == 2
    assert "Missing argument" in _plain(usage.stderr)
    assert "error:" not in usage.stderr
    help_ = runner.invoke(cli.app, ["--help"])
    assert help_.exit_code == 0
    assert help_.exception is None

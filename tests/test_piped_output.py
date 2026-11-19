"""Piped output: `output.for_agent()`, the flat table form, and the apply
output that leads with its results.

CliRunner's stdout isn't a terminal, so every invocation here sees the piped
form unless `KIZEN_OUTPUT=terminal` says otherwise.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
import respx
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder import output as out
from kizen_builder.cli._shared import _Console
from kizen_builder.config import ConfigError
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools import records as record_tools
from kizen_builder.tools.planners import fields as field_planners
from kizen_builder.tools.plans import ApplyResult, OperationResult, Plan, PlanOperation
from tests.conftest import load_fixture, mock_automation_lookup
from tests.test_first_build_examples_plan import (  # noqa: F401  (fixture)
    EXAMPLES,
    first_build_business,
)

runner = CliRunner()

BOX_DRAWING = re.compile("[─-╿]")
SRC = Path(__file__).parent.parent / "src" / "kizen_builder"


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# --- for_agent ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "tty", "expected"),
    [
        (None, True, False),
        (None, False, True),
        ("agent", True, True),
        ("terminal", False, False),
    ],
)
def test_for_agent_follows_the_stream_unless_kizen_output_says(
    monkeypatch, env, tty, expected
):
    if env is None:
        monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    else:
        monkeypatch.setenv(out.OUTPUT_ENV, env)
    assert out.for_agent(_Stream(tty)) is expected


def test_for_agent_rejects_any_other_value(monkeypatch):
    monkeypatch.setenv(out.OUTPUT_ENV, "rich")
    with pytest.raises(ConfigError, match="'terminal' or 'agent'"):
        out.for_agent(_Stream(True))


def test_a_bad_kizen_output_fails_every_command_on_one_error_line(monkeypatch):
    monkeypatch.setenv(out.OUTPUT_ENV, "rich")
    result = runner.invoke(cli.app, ["docs", "list"])
    assert result.exit_code == 1
    assert "error:" in result.stderr and "'terminal' or 'agent'" in result.stderr


def test_for_agent_is_the_only_check_that_picks_an_output_form():
    """stdin checks decide whether to read a spec or prompt, not how output
    looks; `cli/init.py` asks the console before prompting."""
    pattern = re.compile(r"std(out|err)\.isatty\(\)|\.is_terminal\b")
    hits = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*.py")
        if path.name != "output.py" and path.relative_to(SRC) != Path("cli/init.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


def test_docs_show_follows_kizen_output(monkeypatch):
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    result = runner.invoke(cli.app, ["docs", "show", "operating"])
    assert result.exit_code == 0, result.output
    assert "# Operating a Kizen environment" not in result.output


# --- the flat table -----------------------------------------------------------


def _print(table: Table, tty: bool = False) -> str:
    stream = _Stream(tty)
    _Console(width=220, file=stream).print(table)
    return stream.getvalue()


def test_a_piped_table_is_a_title_a_header_and_one_line_per_row(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    t = Table(title="Operations")
    t.add_column("#", justify="right")
    t.add_column("name", style="dim")
    t.add_column("note")
    t.add_row("1", "[bold]a[/bold]", Text("first\n\nsecond  "))
    t.add_row("2", "", None)

    assert _print(t) == (
        "Operations:\n| # | name | note |\n| 1 | a | first; second |\n| 2 |  |  |\n"
    )


def test_a_pipe_inside_a_cell_is_escaped(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    t = Table()
    t.add_column("name")
    t.add_column("v")
    t.add_row("Acme | West", "x")
    assert _print(t) == "| name | v |\n| Acme \\| West | x |\n"


def test_a_table_without_a_header_or_title_prints_only_rows(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    t = Table(show_header=False, box=None)
    t.add_column("k")
    t.add_column("v")
    t.add_row("name", escape("[literal]"))
    assert _print(t) == "| name | [literal] |\n"


def test_a_terminal_still_gets_rich_borders_at_220_columns(monkeypatch):
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    t = Table(title="Operations")
    t.add_column("key")
    t.add_row("x" * 300)
    lines = _print(t, tty=False).splitlines()
    assert BOX_DRAWING.search(lines[1])
    assert max(len(line) for line in lines) == 220


def test_err_console_follows_stderr(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    stream = _Stream(tty=True)
    console = _Console(stderr=True, width=220, file=stream)
    t = Table()
    t.add_column("a")
    t.add_row("1")
    console.print(t)
    assert BOX_DRAWING.search(stream.getvalue())


# --- commands, piped ----------------------------------------------------------


@pytest.fixture
def piped_commands(monkeypatch, first_build_business):  # noqa: F811
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    monkeypatch.setattr(
        obj_tools, "get_object", lambda api_name: load_fixture("objects/patients.json")
    )
    monkeypatch.setattr(
        record_tools,
        "search_records",
        lambda *a, **k: load_fixture("records/list_tax_lot.json"),
    )


DRY_RUN = [
    "automations",
    "create",
    "--spec-file",
    str(EXAMPLES / "closing_soon.automation.json"),
    "--dry-run",
]


@pytest.mark.usefixtures("piped_commands")
@pytest.mark.parametrize(
    "argv", [["objects", "get", "patients"], ["records", "list", "tax_lot"], DRY_RUN]
)
def test_piped_commands_print_no_box_drawing(argv):
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert "| " in result.stdout
    assert not BOX_DRAWING.search(result.stdout)
    assert all(line == line.rstrip() for line in result.stdout.splitlines())


def test_piped_automations_get_prints_no_box_drawing(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    raw = load_fixture("automations/webhook_auth_triggers.raw.json")
    with respx.mock:
        mock_automation_lookup(raw)
        result = runner.invoke(cli.app, ["automations", "get", raw["api_name"]])
    assert result.exit_code == 0, result.output
    assert "Triggers:\n| type | description | auth |" in result.stdout
    assert not BOX_DRAWING.search(result.stdout)


@pytest.mark.usefixtures("piped_commands")
def test_piped_dry_run_is_at_least_40_percent_shorter(monkeypatch):
    """The measurement the change was made for: the same plan, terminal form
    against piped form."""
    piped = runner.invoke(cli.app, DRY_RUN)
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    terminal = runner.invoke(cli.app, DRY_RUN)
    assert piped.exit_code == terminal.exit_code == 0, terminal.output
    print(f"terminal {len(terminal.stdout)} chars, piped {len(piped.stdout)} chars")
    assert len(piped.stdout) <= 0.6 * len(terminal.stdout)


# --- apply --------------------------------------------------------------------


def _plan() -> Plan:
    return Plan.build(
        env="testenv",
        business_id=None,
        summary="test plan",
        operations=[
            PlanOperation(
                action="create",
                kind="field",
                key="invoice.total",
                preview={"api_name": "total"},
                payload={"name": "total"},
                parent_object_uuid="11111111-1111-4111-8111-111111111111",
            )
        ],
    )


def _result(status: str):
    def apply(plan):
        return ApplyResult(
            plan_id=plan.id,
            env=plan.env,
            results=[
                OperationResult(
                    key="invoice.total",
                    kind="field",
                    action="create",
                    status=status,
                    server_uuid="srv-1",
                    message="done",
                )
            ],
        )

    return apply


UPDATE = ["fields", "update", "invoice", "total", "--name", "Total"]


@pytest.fixture
def mutation(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)
    plan = _plan()
    monkeypatch.setattr(field_planners, "plan_update_field", lambda **kw: plan)
    monkeypatch.setattr(plan_tools, "apply_plan", _result("ok"))
    return plan


def test_piped_apply_leads_with_its_results(mutation):
    result = runner.invoke(cli.app, [*UPDATE, "--yes"])
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"Plan {mutation.id} → env: testenv, 1 op(s)\n"
        f"Results — plan {mutation.id}:\n"
        "| status | kind | key | server uuid | note |\n"
        "| ok | field | invoice.total | srv-1 | done |\n"
    )


def test_piped_apply_with_json_sends_the_one_line_to_stderr(mutation):
    result = runner.invoke(cli.app, [*UPDATE, "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert result.stderr == f"Plan {mutation.id} → env: testenv, 1 op(s)\n"
    assert json.loads(result.stdout)["results"][0]["status"] == "ok"


def test_piped_dry_run_keeps_the_operations_table(mutation):
    result = runner.invoke(cli.app, [*UPDATE, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Operations:\n| # | action | kind | key | preview |" in result.stdout


def test_a_prompted_apply_shows_the_operations_table(mutation, terminal):
    result = runner.invoke(cli.app, UPDATE, input="y\n")
    assert result.exit_code == 0, result.output
    assert "| 1 | create | field | invoice.total | api_name=total |" in result.stdout


def test_a_terminal_apply_keeps_the_table_and_symbols(monkeypatch, mutation):
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    result = runner.invoke(cli.app, [*UPDATE, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Operations" in result.stdout and "✓" in result.stdout


def test_saved_plan_apply_leads_with_its_results(mutation, tmp_path, monkeypatch):
    monkeypatch.setattr(plan_tools, "resolve_apply_target", _apply_target)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan_tools.plan_to_json(mutation))
    result = runner.invoke(cli.app, ["apply", "--plan-file", str(plan_file), "--yes"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"Plan {mutation.id} → env: testenv, 1 op(s)\n")
    assert "| ok | field |" in result.stdout


def _apply_target(plan):
    class _Config:
        name = "testenv"
        business_id = "b"

    return _Config()


@pytest.mark.parametrize(
    ("status", "exit_code"), [("failed", 1), ("skipped", 0), ("adjusted", 0)]
)
def test_piped_results_name_the_status(monkeypatch, mutation, status, exit_code):
    monkeypatch.setattr(plan_tools, "apply_plan", _result(status))
    result = runner.invoke(cli.app, [*UPDATE, "--yes"])
    assert result.exit_code == exit_code, result.output
    assert f"| {status} | field |" in result.stdout

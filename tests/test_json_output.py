"""JSON output: one formatter (`output.dumps_json`), one-space indent and no
empty values when piped, two-space indent and every key at a terminal, and the
outputs that keep every key because they are edited or sent back.

CliRunner's stdout isn't a terminal, so every invocation here sees the piped
form unless `KIZEN_OUTPUT=terminal` says otherwise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder import output as out
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners import fields as field_planners
from kizen_builder.tools.plans import ApplyResult, Plan, PlanOperation
from tests.conftest import load_fixture, mock_automation_lookup

runner = CliRunner()

CLI = Path(__file__).parent.parent / "src" / "kizen_builder" / "cli"
AUTOMATION = load_fixture("automations/two_code_steps.raw.json")


@pytest.fixture
def piped(monkeypatch):
    monkeypatch.delenv(out.OUTPUT_ENV, raising=False)


@pytest.fixture
def terminal_output(monkeypatch):
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")


# --- one formatter ------------------------------------------------------------


def test_no_command_formats_json_itself():
    """Every JSON a command prints goes through `output`; `json.dump` (to a
    file) is still allowed."""
    pattern = re.compile(r"json\.dumps\(|print_json\(|model_dump_json\(")
    hits = [
        f"{path.name}:{n}"
        for path in CLI.glob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


DATA = {
    "id": "a1",
    "name": "",
    "owner": None,
    "tags": [],
    "meta": {"note": None, "extra": {}},
    "count": 0,
    "active": False,
    "rows": [None, {"x": None, "y": 1}, []],
}


def test_piped_json_is_indented_by_one_and_drops_empty_values(piped):
    text = out.dumps_json(DATA)
    assert text.startswith('{\n "id"')
    assert json.loads(text) == {
        "id": "a1",
        "count": 0,
        "active": False,
        "rows": [None, {"y": 1}, []],
    }


def test_terminal_json_is_indented_by_two_and_keeps_every_key(terminal_output):
    text = out.dumps_json(DATA)
    assert text.startswith('{\n  "id"')
    assert json.loads(text) == DATA


def test_keep_empty_changes_only_whitespace(piped):
    text = out.dumps_json(DATA, keep_empty=True)
    assert text.startswith('{\n "id"')
    assert json.loads(text) == DATA


def test_key_order_is_kept_and_sets_print_sorted(piped):
    text = out.dumps_json({"z": 1, "a": {"c", "b"}, "m": 2})
    assert list(json.loads(text)) == ["z", "a", "m"]
    assert json.loads(text)["a"] == ["b", "c"]


def test_objects_get_json_is_smaller_piped_with_the_same_non_empty_values(
    monkeypatch, piped
):
    monkeypatch.setattr(
        obj_tools, "get_object", lambda api_name: load_fixture("objects/patients.json")
    )
    piped_out = runner.invoke(cli.app, ["objects", "get", "patients", "--json"])
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    terminal_out = runner.invoke(cli.app, ["objects", "get", "patients", "--json"])
    assert piped_out.exit_code == terminal_out.exit_code == 0
    full = json.loads(terminal_out.stdout)
    assert json.loads(piped_out.stdout) == out._without_empties(full)
    print(f"terminal {len(terminal_out.stdout)}, piped {len(piped_out.stdout)}")
    assert len(piped_out.stdout) < 0.9 * len(terminal_out.stdout)


# --- never pruned -------------------------------------------------------------


def _invoke_automation(argv: list[str]) -> str:
    with respx.mock:
        mock_automation_lookup(AUTOMATION)
        result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    return result.stdout


def _first_step_key() -> str:
    with respx.mock:
        mock_automation_lookup(AUTOMATION)
        result = runner.invoke(
            cli.app, ["automations", "show", AUTOMATION["api_name"], "--json"]
        )
    return next(
        s["key"] for s in json.loads(result.stdout)["payload"]["steps"] if "key" in s
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["automations", "get", AUTOMATION["api_name"], "--raw"],
        ["automations", "show", AUTOMATION["api_name"], "--json"],
        ["automations", "steps", "get", AUTOMATION["api_name"], None],
    ],
    ids=["get --raw", "show --json", "steps get"],
)
def test_wire_json_keeps_every_key_piped(monkeypatch, piped, argv):
    argv = [a if a is not None else _first_step_key() for a in argv]
    piped_text = _invoke_automation(argv)
    monkeypatch.setenv(out.OUTPUT_ENV, "terminal")
    terminal_text = _invoke_automation(argv)
    assert json.loads(piped_text) == json.loads(terminal_text)
    assert re.search(r": (null|\[\]|\{\}|\"\")", piped_text)
    assert len(piped_text) < len(terminal_text)


def test_a_compact_plan_with_empty_values_round_trips_through_apply(
    monkeypatch, tmp_path, piped
):
    plan = Plan.build(
        env="testenv",
        business_id=None,
        summary="test plan",
        operations=[
            PlanOperation(
                action="create",
                kind="field",
                key="invoice.total",
                preview={"api_name": "total"},
                payload={"name": "total", "description": "", "options": [], "x": None},
                parent_object_uuid="11111111-1111-4111-8111-111111111111",
            )
        ],
    )
    applied: list[Plan] = []

    def apply(p: Plan) -> ApplyResult:
        applied.append(p)
        return ApplyResult(plan_id=p.id, env=p.env, results=[])

    monkeypatch.setattr(field_planners, "plan_update_field", lambda **kw: plan)
    monkeypatch.setattr(plan_tools, "apply_plan", apply)
    monkeypatch.setattr(plan_tools, "resolve_apply_target", _apply_target)

    dry = runner.invoke(
        cli.app,
        [
            "fields",
            "update",
            "invoice",
            "total",
            "--name",
            "Total",
            "--dry-run",
            "--json",
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert dry.stdout.startswith('{\n "id"')
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(dry.stdout)

    result = runner.invoke(cli.app, ["apply", "--plan-file", str(plan_file), "--yes"])
    assert result.exit_code == 0, result.output
    assert applied == [plan]


def _apply_target(plan):
    class _Config:
        name = "testenv"
        business_id = "b"

    return _Config()

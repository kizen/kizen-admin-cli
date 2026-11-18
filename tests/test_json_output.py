"""JSON output: one formatter (`output.dumps_json`), one-space indent and no
empty values when piped, two-space indent and every key at a terminal, and the
outputs that keep every key because they are edited or sent back.

CliRunner's stdout isn't a terminal, so every invocation here sees the piped
form unless `KIZEN_OUTPUT=terminal` says otherwise.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder import output as out
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners import automations as auto_planners
from kizen_builder.tools.planners import fields as field_planners
from kizen_builder.tools.plans import ApplyResult, OperationResult, Plan, PlanOperation
from tests.conftest import load_fixture, mock_automation_lookup, plain

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


def test_piped_summary_json_is_indented_by_one_and_drops_empty_values(piped):
    text = out.dumps_json(DATA, prune=True)
    assert text.startswith('{\n "id"')
    assert json.loads(text) == {
        "id": "a1",
        "count": 0,
        "active": False,
        "rows": [None, {"y": 1}, []],
    }


def test_terminal_json_is_indented_by_two_and_keeps_every_key(terminal_output):
    text = out.dumps_json(DATA, prune=True)
    assert text.startswith('{\n  "id"')
    assert json.loads(text) == DATA


def test_unpruned_json_changes_only_whitespace(piped):
    text = out.dumps_json(DATA)
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


# An empty side of a diff is the change itself.
CLEARED = [
    {"path": "steps[1].description", "before": "Old text", "after": ""},
    {"path": "steps[1].tags", "before": [], "after": None},
]
RUN_ID = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
CODE_STEP_LOG = {
    "id": "e1",
    "kind": "step",
    "type": "code_step",
    "status": "completed",
    "error": None,
    "detailed_log": {"inputs": {"email": ""}, "values": {"score": None}, "logs": []},
}


def _diff(monkeypatch):
    result = {"api_name": "x", "revision": 4, "diff": CLEARED}
    monkeypatch.setattr(auto_planners, "diff_automation", lambda spec: result)
    return ["automations", "diff", "x", "--json"], result


def _roundtrip(monkeypatch):
    result = {
        "env": "testenv",
        "api_name": "x",
        "revision_before": 4,
        "n_steps": 1,
        "n_triggers": 1,
        "validation_problems": [],
        "payload": {"steps": [{"description": None, "tags": []}]},
        "executed": True,
        "revision_after": 5,
        "drift": CLEARED,
    }
    monkeypatch.setattr(auto_tools, "roundtrip_automation", lambda *a, **k: result)
    return ["automations", "roundtrip", "x", "--execute", "--yes", "--json"], result


def _step_patch(monkeypatch):
    result = {
        "revision_before": 4,
        "payload": {"steps": [{"key": "s1", "description": ""}]},
        "report": {},
        "validation_problems": [],
        "diff": CLEARED,
    }
    monkeypatch.setattr(auto_tools, "patch_steps", lambda *a, **k: result)
    return ["automations", "steps", "remove", "x", "s1", "--dry-run", "--json"], result


def _field_patch(monkeypatch):
    result = {
        "api_name": "x",
        "revision_before": 4,
        "field": "folder",
        "before": None,
        "after": {"id": "f1"},
        "no_op": False,
    }
    monkeypatch.setattr(auto_tools, "list_folders", lambda: [{"id": "f1", "name": "F"}])
    monkeypatch.setattr(auto_tools, "move_to_folder", lambda *a, **k: result)
    return ["automations", "move", "x", "--folder", "f1", "--dry-run", "--json"], result


def _run_logs(monkeypatch):
    monkeypatch.setattr(
        auto_tools, "get_execution_history", lambda *a, **k: [CODE_STEP_LOG]
    )
    argv = ["automations", "runs", "logs", RUN_ID, "--json"]
    return argv, [{"index": 1, **CODE_STEP_LOG}]


def _run_view(monkeypatch):
    summary = {"execution_id": RUN_ID, "status": "completed", "record_id": None}
    monkeypatch.setattr(auto_tools, "get_execution", lambda *a, **k: summary)
    monkeypatch.setattr(
        auto_tools, "get_execution_history", lambda *a, **k: [CODE_STEP_LOG]
    )
    argv = ["automations", "runs", "view", RUN_ID, "--json"]
    return argv, {**summary, "steps": [CODE_STEP_LOG]}


@pytest.mark.parametrize(
    "setup",
    [_diff, _roundtrip, _step_patch, _field_patch, _run_logs, _run_view],
    ids=["diff", "roundtrip", "steps patch", "field patch", "runs logs", "runs view"],
)
def test_diffs_logs_and_payloads_keep_every_key_piped(monkeypatch, piped, setup):
    argv, expected = setup(monkeypatch)
    # Exit code aside: roundtrip exits 1 when it finds drift.
    result = runner.invoke(cli.app, argv, input='{"api_name": "x"}')
    assert json.loads(result.stdout) == expected, result.output


def test_roundtrip_payload_prints_a_saved_null(monkeypatch, piped):
    _roundtrip(monkeypatch)
    result = runner.invoke(cli.app, ["automations", "roundtrip", "x", "--payload"])
    assert '"description": null' in result.stdout
    assert '"tags": []' in result.stdout


# --- apply results ------------------------------------------------------------

RAW = {
    "id": "msg-1",
    "name": "Welcome",
    "base_message_id": "tpl-1",
    "number": 3,
    "craft_json": {"ROOT": {"nodes": ["a"]}},
    "recipients": [{"id": "r1"}],
    "subject": "",
}


@pytest.fixture
def created(monkeypatch, piped):
    plan = Plan.build(
        env="testenv",
        business_id=None,
        summary="test plan",
        operations=[
            PlanOperation(
                action="create",
                kind="field",
                key="invoice.total",
                preview={},
                payload={},
            )
        ],
    )

    def apply(p: Plan) -> ApplyResult:
        return ApplyResult(
            plan_id=p.id,
            env=p.env,
            results=[
                OperationResult(
                    key="invoice.total",
                    kind="field",
                    action="create",
                    status="ok",
                    server_uuid="msg-1",
                    raw=RAW,
                ),
                OperationResult(
                    key="invoice.other",
                    kind="field",
                    action="update",
                    status="ok",
                    raw={"id": "f2", "number_archived": 4},
                ),
            ],
        )

    monkeypatch.setattr(field_planners, "plan_update_field", lambda **kw: plan)
    monkeypatch.setattr(plan_tools, "apply_plan", apply)


UPDATE = ["fields", "update", "invoice", "total", "--name", "Total", "--yes", "--json"]


@pytest.mark.usefixtures("created")
def test_apply_results_keep_the_top_level_values_of_raw():
    result = runner.invoke(cli.app, UPDATE)
    assert result.exit_code == 0, result.output
    first, second = json.loads(result.stdout)["results"]
    assert first["server_uuid"] == "msg-1"
    assert first["raw"] == {
        "id": "msg-1",
        "name": "Welcome",
        "base_message_id": "tpl-1",
        "number": 3,
    }
    assert first["raw_trimmed"] is True
    assert list(first)[-2:] == ["raw", "raw_trimmed"]
    assert second["raw"] == {"id": "f2", "number_archived": 4}
    assert "raw_trimmed" not in second


@pytest.mark.usefixtures("created")
def test_full_keeps_the_whole_server_response():
    result = runner.invoke(cli.app, [*UPDATE, "--full"])
    assert result.exit_code == 0, result.output
    first = json.loads(result.stdout)["results"][0]
    assert first["raw"] == RAW
    assert "raw_trimmed" not in first
    assert first["message"] is None


def test_every_mutation_verb_passes_full_through():
    """`--full` exists wherever an apply result is printed."""
    missing = []
    for path in CLI.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_run_mutation"
                and "full" not in {k.arg for k in node.keywords}
            ):
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


@pytest.mark.parametrize(
    "argv",
    [
        ["fields", "update", "--help"],
        ["objects", "create", "--help"],
        ["apply", "--help"],
    ],
)
def test_mutation_json_help_says_where_the_whole_body_is(argv, terminal_output):
    result = runner.invoke(cli.app, argv, env={"COLUMNS": "200"})
    assert result.exit_code == 0
    text = plain(result.stdout)
    assert "--full" in text
    assert "top-level values" in text

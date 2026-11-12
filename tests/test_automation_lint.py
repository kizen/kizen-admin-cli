"""`kizen automations lint`: offline checks on a whole spec."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools.automation_lint import lint_spec

runner = CliRunner()
EXAMPLES = sorted(
    (Path(__file__).parent / "fixtures" / "examples").glob("*/*automation*.json")
)


def _spec(*steps, **top):
    return {
        "name": "Lint me",
        "api_name": "lint_me",
        "target_object": "deals",
        "steps": [
            {"order": i, "parent_key": steps[i - 1]["key"] if i else None, **s}
            for i, s in enumerate(steps)
        ],
        **top,
    }


def _activity(**block):
    return {
        "key": "act",
        "step_type": "schedule_activity",
        "action_schedule_activity": {
            "activity_type": "call",
            "schedule": {"type": "immediately"},
            "assigned_to": {"assignment_type": "owner"},
            **block,
        },
    }


def _var(name, data_type="string", **extra):
    return {
        "key": f"init_{name}",
        "step_type": "initialize_variable",
        "action_initialize_variable": {
            "sources": [],
            "variable": {"name": name, "data_type": data_type, **extra},
        },
    }


def _only(findings, **match):
    hits = [f for f in findings if all(f.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, findings
    return hits[0]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_shipped_example_specs_lint_clean(path):
    result = lint_spec(json.loads(path.read_text()))
    assert result == {"ok": True, "errors": [], "warnings": []}


def test_a_wrong_choice_names_step_path_allowed_values_and_nearest():
    spec = _spec(
        _activity(
            schedule={
                "type": "with_delay",
                "with_delay": {"delay_type": "days", "delay_value": 2},
            }
        )
    )
    result = lint_spec(spec)
    assert not result["ok"]
    f = _only(result["errors"], step="act")
    assert f["path"] == "action_schedule_activity.schedule.with_delay.delay_type"
    assert f["got"] == "days"
    assert f["allowed"] == ["minute", "hour", "day"]
    assert f["suggestion"] == "day"
    assert f["hint"] == "kizen automations step-types show schedule_activity"


def test_a_schedule_type_without_its_block_is_an_error():
    result = lint_spec(_spec(_activity(schedule={"type": "with_delay"})))
    f = _only(result["errors"], step="act")
    assert "needs a 'with_delay' block" in f["message"]


def test_a_missing_companion_key_is_a_warning_not_an_error():
    spec = _spec(
        _activity(
            schedule={
                "type": "with_delay",
                "with_delay": {
                    "delay_type": "day",
                    "delay_value": 1,
                    "time_offset": "specific_time",
                },
            },
            assigned_to={"assignment_type": "specific_role"},
        )
    )
    result = lint_spec(spec)
    assert result["ok"]
    paths = {w["path"] for w in result["warnings"]}
    assert paths == {
        "action_schedule_activity.schedule.with_delay.time",
        "action_schedule_activity.assigned_to.role_id",
    }


def test_every_problem_is_reported_in_one_pass():
    spec = _spec(
        _activity(schedule={"type": "sometime"}),
        {"key": "each", "step_type": "loop", "step_loop": {"array_variable": "xs"}},
        {"key": "mail", "step_type": "send_emial"},
    )
    spec["steps"][2]["parent_key"] = "nowhere"
    errors = lint_spec(spec)["errors"]
    assert _only(errors, step="act")["allowed"] == [
        "immediately",
        "with_delay",
        "on_or_around_date",
    ]
    unwired = _only(errors, step="mail", path="step_type")
    assert "schedule_activity" in unwired["allowed"]
    assert unwired["hint"] == "kizen automations step-types list"
    messages = " | ".join(e["message"] for e in errors)
    assert "dangling parent_key 'nowhere'" in messages
    assert "loop 'each' has 0 next_or_finish children" in messages
    # Pydantic's own "unknown step_type" line would repeat the unwired finding.
    assert "unknown step_type" not in messages


def test_an_undeclared_variable_is_an_error_listing_the_declared_ones():
    spec = _spec(_var("other"), _activity(target_variable="activity_id"))
    f = _only(lint_spec(spec)["errors"], step="act")
    assert f["path"] == "action_schedule_activity.target_variable"
    assert f["allowed"] == ["other"]

    spec = _spec(_var("activity_id", "uuid"), _activity(target_variable="activity_id"))
    assert lint_spec(spec)["ok"]


def test_a_variable_of_the_wrong_kind_is_an_error():
    spec = _spec(_var("n", "number"), _activity(target_variable="n"))
    f = _only(lint_spec(spec)["errors"], step="act")
    assert "single string or uuid" in f["message"]

    spec = _spec(
        _var("found", "entity", data_subtype="x"),
        {
            "key": "find",
            "step_type": "search_records",
            "action_search_records": {
                "custom_object": "deals",
                "filter_type": "all_records",
                "destination_variable": "found",
            },
        },
    )
    f = _only(lint_spec(spec)["errors"], step="find")
    assert "must be an array variable" in f["message"]


def test_variable_references_inside_lists_are_followed():
    spec = _spec(
        {
            "key": "code",
            "step_type": "code_step",
            "action_code_step": {
                "script": "pass",
                "inputs": [],
                "outputs": [
                    {
                        "name": "result",
                        "output_type": "variable",
                        "variable_name": "out",
                    }
                ],
            },
        }
    )
    f = _only(lint_spec(spec)["errors"], step="code")
    assert f["path"] == "action_code_step.outputs[0].variable_name"


def test_step_orders_with_a_gap_warn():
    spec = _spec(_activity())
    spec["steps"][0]["order"] = 4
    result = lint_spec(spec)
    assert result["ok"]
    assert "0 to 0" in result["warnings"][0]["message"]


def test_a_missing_target_object_is_still_reported_beside_graph_problems():
    spec = _spec(_activity())
    del spec["target_object"]
    spec["steps"].append(copy.deepcopy(spec["steps"][0]))
    messages = " | ".join(e["message"] for e in lint_spec(spec)["errors"])
    assert "target_object" in messages or "duplicate step keys" in messages
    assert "duplicate step keys" in messages


def test_a_non_object_spec_is_one_error():
    result = lint_spec([1, 2])
    assert not result["ok"] and len(result["errors"]) == 1


# --- CLI ------------------------------------------------------------------


def test_cli_lint_ok_and_failing(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_spec(_activity())))
    r = runner.invoke(cli.app, ["automations", "lint", "--spec-file", str(good)])
    assert r.exit_code == 0, r.output
    assert r.output.startswith("ok: no errors")

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            _spec(
                _activity(
                    schedule={
                        "type": "with_delay",
                        "with_delay": {"delay_type": "days"},
                    }
                )
            )
        )
    )
    r = runner.invoke(cli.app, ["automations", "lint", "--spec-file", str(bad)])
    assert r.exit_code == 1
    assert (
        "error: step 'act' action_schedule_activity.schedule.with_delay.delay_type: "
        "'days' is not one of: minute, hour, day (did you mean 'day'?)"
    ) in r.output
    assert "see: kizen automations step-types show schedule_activity" in r.output

    r = runner.invoke(
        cli.app, ["automations", "lint", "--spec-file", str(bad), "--json"]
    )
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False


def test_cli_lint_reads_stdin():
    r = runner.invoke(
        cli.app, ["automations", "lint"], input=json.dumps(_spec(_activity()))
    )
    assert r.exit_code == 0, r.output

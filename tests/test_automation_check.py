"""Offline automation spec checks: every problem in one dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
import typer
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.tools.automation_check import check_automation
from kizen_builder.tools.findings import Finding, SpecProblems

runner = CliRunner()
EXAMPLES = sorted(
    (Path(__file__).parent / "fixtures" / "examples").glob("*/*automation*.json")
)


def _spec(*steps, **top):
    return {
        "name": "Check me",
        "api_name": "check_me",
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


def _errors(spec):
    with pytest.raises(SpecProblems) as exc:
        check_automation(spec)
    return exc.value.errors


def _only(findings, **match):
    hits = [f for f in findings if all(getattr(f, k) == v for k, v in match.items())]
    assert len(hits) == 1, findings
    return hits[0]


def _raises(problems: SpecProblems):
    def build():
        raise problems

    return build


_PROBLEMS = SpecProblems(
    [
        Finding(
            "'days' is not one of: minute, hour, day",
            path="action_schedule_activity.schedule.with_delay.delay_type",
            step="act",
            index=0,
            got="days",
            allowed=["minute", "hour", "day"],
            suggestion="day",
            hint="kizen automations step-types show schedule_activity",
        ),
        Finding("required key is missing", path="name"),
    ],
    [Finding("step orders should run 0 to 0", path="steps")],
)


def test_findings_print_one_line_each_with_a_see_line(capsys):
    with pytest.raises(typer.Exit) as exc:
        _run_mutation(_raises(_PROBLEMS), dry_run=True, yes=False, json_out=False)
    assert exc.value.exit_code == 1
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "spec error: step 'act' action_schedule_activity.schedule.with_delay."
        "delay_type: 'days' is not one of: minute, hour, day (did you mean 'day'?)",
        "  see: kizen automations step-types show schedule_activity",
        "spec error: name: required key is missing",
        "warning: steps: step orders should run 0 to 0",
    ]


def test_findings_json_goes_to_stdout(capsys):
    with pytest.raises(typer.Exit) as exc:
        _run_mutation(_raises(_PROBLEMS), dry_run=True, yes=False, json_out=True)
    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    body = json.loads(captured.out)
    assert body["ok"] is False
    assert body["errors"][0] == {
        "message": "'days' is not one of: minute, hour, day",
        "path": "action_schedule_activity.schedule.with_delay.delay_type",
        "step": "act",
        "index": 0,
        "got": "days",
        "allowed": ["minute", "hour", "day"],
        "suggestion": "day",
        "hint": "kizen automations step-types show schedule_activity",
    }
    assert body["errors"][1] == {"message": "required key is missing", "path": "name"}
    assert body["warnings"] == [
        {"message": "step orders should run 0 to 0", "path": "steps"}
    ]


def test_a_finding_without_a_key_names_the_step_by_position():
    assert Finding("bad", path="order", index=2).text() == "step #2 order: bad"


# --- model, unwired types, unknown keys -------------------------------------


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_shipped_example_specs_pass(path):
    check_automation(json.loads(path.read_text()))


def test_a_wrong_choice_names_step_path_allowed_values_and_nearest():
    spec = _spec(
        _activity(
            schedule={
                "type": "with_delay",
                "with_delay": {"delay_type": "days", "delay_value": 2},
            }
        )
    )
    f = _only(_errors(spec), step="act")
    assert f.index == 0
    assert f.path == "action_schedule_activity.schedule.with_delay.delay_type"
    assert f.got == "days"
    assert f.allowed == ["minute", "hour", "day"]
    assert f.suggestion == "day"
    assert f.hint == "kizen automations step-types show schedule_activity"


def test_a_schedule_type_without_its_block_is_an_error():
    f = _only(_errors(_spec(_activity(schedule={"type": "with_delay"}))), step="act")
    assert "needs a 'with_delay' block" in f.message


def test_an_unwired_type_is_said_once_with_the_wired_list():
    spec = _spec(
        _activity(),
        {"key": "mail", "step_type": "send_emial"},
        triggers=[{"trigger_type": "manul"}],
    )
    errors = _errors(spec)
    step = _only(errors, step="mail")
    assert step.path == "step_type"
    assert "schedule_activity" in step.allowed
    assert step.suggestion == "notify_member_via_email"
    assert step.hint == "kizen automations step-types list"
    trigger = _only(errors, path="triggers[0].trigger_type")
    assert trigger.suggestion == "manual"
    # Pydantic's own "unknown step_type" line would repeat it.
    assert not any("unknown step_type" in e.message for e in errors)


def test_a_move_stage_step_points_at_change_field_value():
    f = _only(_errors(_spec({"key": "move", "step_type": "update_pipeline_status"})))
    assert "no move-stage step" in f.message
    assert f.suggestion == "change_field_value"


def test_an_unknown_key_gets_the_nearest_key():
    spec = _spec(_activity(), object="deals")
    del spec["target_object"]
    errors = _errors(spec)
    f = _only(errors, path="object")
    assert (f.message, f.got, f.suggestion) == (
        "unknown key",
        "object",
        "target_object",
    )
    assert "target_object" in f.allowed

    f = _only(_errors(_spec(_activity(), trigger_type="manual")), path="trigger_type")
    assert f.suggestion == "type"

    step = {**_activity(), "parnet_key": None}
    f = _only(_errors(_spec(step)), step="act")
    assert (f.path, f.suggestion) == ("parnet_key", "parent_key")


def test_variables_at_the_top_points_at_initialize_variable():
    f = _only(_errors(_spec(_activity(), variables=[{"name": "x"}])))
    assert "initialize_variable step" in f.message
    assert f.hint == "kizen automations step-types show initialize_variable"


def test_a_block_key_the_planner_drops_is_an_error():
    step = {
        "key": "set",
        "step_type": "change_field_value",
        "action_change_field_value": {
            "field_ref": "deals.amount",
            "specific_field_value": "1",
            "fieldd": "x",
        },
    }
    f = _only(_errors(_spec(step)))
    assert f.path == "action_change_field_value.fieldd"
    assert "would be dropped" in f.message
    assert f.suggestion == "field"


def test_a_non_object_spec_is_one_error():
    assert len(_errors([1, 2])) == 1


def test_a_bad_spec_makes_no_live_call():
    spec = _spec(_activity(), {"key": "mail", "step_type": "send_emial"}, objct="x")
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(
            cli.app, ["automations", "create", "--dry-run"], input=json.dumps(spec)
        )
    assert result.exit_code == 1, result.output
    assert router.calls.call_count == 0
    assert "spec error: step 'mail' step_type:" in result.stderr
    assert "spec error: objct: unknown key" in result.stderr

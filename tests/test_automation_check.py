"""Offline automation spec checks: every problem in one dry-run."""

from __future__ import annotations

import copy
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
_PLACEHOLDER = "replace the placeholder with a real value"
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
def test_shipped_example_specs_pass_once_their_placeholders_are_filled(path):
    try:
        warnings = check_automation(json.loads(path.read_text())).warnings
    except SpecProblems as e:
        assert {f.message for f in e.errors} == {_PLACEHOLDER}, e.errors
        warnings = e.warnings
    assert warnings == []


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


# --- variables, placeholders, selectors, order --------------------------------


def _var(name, data_type="string", **extra):
    return {
        "key": f"init_{name}",
        "step_type": "initialize_variable",
        "action_initialize_variable": {
            "sources": [],
            "variable": {"name": name, "data_type": data_type, **extra},
        },
    }


def _three_problems():
    """A bad step type, an undeclared variable and an unknown key."""
    return _spec(
        _activity(target_variable="activity_id"),
        {"key": "mail", "step_type": "send_emial"},
        objct="x",
    )


def test_three_independent_problems_print_together_with_no_live_call():
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(
            cli.app,
            ["automations", "create", "--dry-run"],
            input=json.dumps(_three_problems()),
        )
    assert result.exit_code == 1, result.output
    assert router.calls.call_count == 0
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("spec error")]
    assert lines == [
        "spec error: step 'mail' step_type: 'send_emial' is not a step type this "
        "CLI wires (did you mean 'notify_member_via_email'?)",
        "spec error: objct: unknown key (did you mean 'target_object'?)",
        "spec error: step 'act' action_schedule_activity.target_variable: variable "
        "'activity_id' is not declared by an initialize_variable step",
    ]


def test_dry_run_json_puts_findings_under_errors_and_warnings():
    spec = _three_problems()
    spec["steps"][0]["action_schedule_activity"]["assigned_to"] = {
        "assignment_type": "specific_role"
    }
    result = runner.invoke(
        cli.app,
        ["automations", "create", "--dry-run", "--json"],
        input=json.dumps(spec),
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert set(body) == {"ok", "errors", "warnings"}
    assert body["ok"] is False
    assert [e["path"] for e in body["errors"]] == [
        "step_type",
        "objct",
        "action_schedule_activity.target_variable",
    ]
    variable = body["errors"][2]
    assert variable == {
        "message": "variable 'activity_id' is not declared by an "
        "initialize_variable step",
        "path": "action_schedule_activity.target_variable",
        "step": "act",
        "index": 0,
        "got": "activity_id",
        "allowed": [],
        "hint": "kizen automations step-types show initialize_variable",
    }
    (warning,) = body["warnings"]
    assert warning["path"] == "action_schedule_activity.assigned_to.role_id"


def test_an_undeclared_variable_lists_the_declared_ones():
    spec = _spec(_var("activity_ids"), _activity(target_variable="activity_id"))
    f = _only(_errors(spec), step="act")
    assert f.path == "action_schedule_activity.target_variable"
    assert f.allowed == ["activity_ids"]
    assert f.suggestion == "activity_ids"

    spec = _spec(_var("activity_id", "uuid"), _activity(target_variable="activity_id"))
    check_automation(spec)


def test_a_variable_of_the_wrong_kind_is_an_error():
    spec = _spec(_var("n", "number"), _activity(target_variable="n"))
    assert "single string or uuid" in _only(_errors(spec), step="act").message

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
    assert "must be an array variable" in _only(_errors(spec), step="find").message


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
    f = _only(_errors(spec), step="code")
    assert f.path == "action_code_step.outputs[0].variable_name"


def test_a_placeholder_says_replace_it_and_names_the_lookup():
    spec = _spec(_activity(activity_type="<activity api_name>"))
    spec["name"] = "<automation name>"
    errors = _errors(spec)
    f = _only(errors, step="act")
    assert f.message == _PLACEHOLDER
    assert f.got == "<activity api_name>"
    assert f.hint == "kizen activities list"
    assert _only(errors, path="name").got == "<automation name>"


def test_html_and_the_root_folder_are_not_placeholders():
    spec = _spec(_activity(note="hi", html_note="<p>hi</p>"), folder="<business_root>")
    check_automation(spec)


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
    warnings = check_automation(spec).warnings
    assert {w.path for w in warnings} == {
        "action_schedule_activity.schedule.with_delay.time",
        "action_schedule_activity.assigned_to.role_id",
    }
    assert {w.hint for w in warnings} == {
        "kizen automations step-types show schedule_activity"
    }


def test_a_team_member_id_without_an_assignment_type_warns():
    spec = _spec(_activity(assigned_to={"employee_id": "x"}))
    (w,) = check_automation(spec).warnings
    assert w.path == "action_schedule_activity.assigned_to.assignment_type"
    assert "set it to 'team_member'" in w.message


def test_step_orders_with_a_gap_warn():
    spec = _spec(_activity())
    spec["steps"][0]["order"] = 4
    (w,) = check_automation(spec).warnings
    assert "0 to 0" in w.message


def test_warnings_print_with_the_plan_and_dont_block_it(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation

    spec = {
        **_spec(
            {
                "key": "set",
                "step_type": "change_field_value",
                "action_change_field_value": {
                    "field_ref": "patients.mrn",
                    "specific_field_value": "A-1",
                },
            }
        ),
        "target_object": "patients",
    }
    spec["steps"][0]["order"] = 3
    (op,) = plan_create_automation(spec).operations
    assert op.preview["warnings"] == [
        "steps: step orders should run 0 to 0 with no gaps or duplicates; got [3]"
    ]


# --- graph problems -------------------------------------------------------


def _set_mrn(key, parent=None, order=0, **extra):
    return {
        "key": key,
        "step_type": "change_field_value",
        "order": order,
        "parent_key": parent,
        "action_change_field_value": {
            "field_ref": "patients.mrn",
            "specific_field_value": "A-1",
        },
        **extra,
    }


def test_every_graph_problem_is_listed(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation

    spec = {
        **_spec(
            _set_mrn("a"),
            _set_mrn("b", "ghost", 1),
            _set_mrn("c", None, 2, parent_branch="yes"),
            {
                "key": "jump",
                "step_type": "go_to_automation_step",
                "order": 3,
                "parent_key": "a",
                "action_go_to_automation_step": {"step_key": "nowhere"},
            },
        ),
        "target_object": "patients",
    }
    with pytest.raises(SpecProblems) as e:
        plan_create_automation(spec)
    assert [(f.path, f.message) for f in e.value.errors] == [
        ("steps", "expected exactly 1 root step, found 2 (['a', 'c'])"),
        ("steps", "step 'b' has dangling parent_key 'ghost'"),
        (
            "steps",
            "step 'c' sets parent_yes_no='yes' but has no parent_key; a branch "
            "needs the condition or goal it hangs off",
        ),
        ("steps", "step 'jump' go_to target 'nowhere' does not exist"),
    ]


def test_trigger_orders_are_checked_before_any_live_call():
    spec = _spec(_activity())
    spec["triggers"] = [
        {"trigger_type": "manual"},
        {"trigger_type": "activity_logged", "trigger_activity_logged": {}},
    ]
    f = _only(_errors(spec), path="triggers")
    assert f.message.startswith("trigger orders must be sequential from 0 to 1")


# --- defaults ---------------------------------------------------------------


def _left_out(spec, *keys):
    spec = copy.deepcopy(spec)
    for key in keys:
        spec.pop(key, None)
    return spec


def test_a_missing_api_name_defaults_from_the_name():
    spec = _left_out(_spec(_activity()), "api_name")
    spec["name"] = "Follow Up: Deals!"
    checked = check_automation(spec)
    assert checked.auto.api_name == "follow_up_deals"
    assert checked.defaults == ["api_name: 'follow_up_deals' (from name)"]

    spec["api_name"] = "mine"
    checked = check_automation(spec)
    assert (checked.auto.api_name, checked.defaults) == ("mine", [])


def test_a_missing_step_order_defaults_to_its_position():
    spec = _spec(_set_mrn("a"), _set_mrn("b", "a", 5))
    del spec["steps"][0]["order"]
    checked = check_automation(spec)
    assert [s.order for s in checked.auto.steps] == [0, 5]
    assert checked.defaults == ["step order from position: 'a' 0"]


def test_the_preview_names_what_was_defaulted(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation

    a, b = _set_mrn("a"), _set_mrn("b", "a", 1)
    spec = {**_spec(a, b), "target_object": "patients"}
    (explicit,) = plan_create_automation(spec).operations
    assert "defaults" not in explicit.preview

    for step in spec["steps"]:
        del step["order"]
    (defaulted,) = plan_create_automation(_left_out(spec, "api_name")).operations
    assert defaulted.preview["defaults"] == [
        "api_name: 'check_me' (from name)",
        "step order from position: 'a' 0, 'b' 1",
    ]
    assert defaulted.payload == explicit.payload

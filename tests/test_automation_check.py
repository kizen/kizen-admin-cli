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
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools import step_catalog
from kizen_builder.tools.automation_check import check_automation
from kizen_builder.tools.findings import Finding, SpecProblems
from tests.conftest import FIXTURES

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
    assert step.message.endswith(
        "it could be 'notify_member_via_email' or 'send_related_contact_email'"
    )
    assert step.suggestion is None
    assert step.hint == "kizen automations step-types list"
    trigger = _only(errors, path="triggers[0].trigger_type")
    assert trigger.suggestion == "manual"
    # Pydantic's own "unknown step_type" line would repeat it.
    assert not any("unknown step_type" in e.message for e in errors)


@pytest.mark.parametrize(
    ("section", "given", "meant"),
    [
        ("steps", "wait", "delay"),
        ("triggers", "field_changed", "field_updated"),
        ("triggers", "date_field", "on_or_around_date"),
    ],
)
def test_an_alias_suggests_its_type_for_steps_and_triggers(section, given, meant):
    if section == "steps":
        spec = _spec({"key": "s", "step_type": given})
        f = _only(_errors(spec), step="s")
    else:
        spec = _spec(_activity(), triggers=[{"trigger_type": given}])
        f = _only(_errors(spec), path="triggers[0].trigger_type")
    assert f.suggestion == meant


def test_a_trigger_typo_is_not_offered_a_step_alias():
    spec = _spec(_activity(), triggers=[{"trigger_type": "wait"}])
    f = _only(_errors(spec), path="triggers[0].trigger_type")
    assert f.suggestion is None or f.suggestion in step_catalog.TRIGGER_TYPES


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


def test_a_misnamed_block_suggests_the_steps_own_block():
    step = {"key": "wait", "step_type": "delay", "action_delay": {"minutes": 1}}
    f = _only(_errors(_spec(step)), step="wait")
    assert (f.path, f.suggestion) == ("action_delay", "step_delay")

    step = {**_activity(), "step_typ": "x"}
    f = _only(_errors(_spec(step)), path="step_typ")
    assert f.suggestion == "step_type"


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
    assert f.suggestion == "field_ref"


def test_a_block_key_suggestion_comes_from_the_steps_own_block():
    # The builder also reads modify_related_entities' `value_type`, which
    # difflib would pick for `value`.
    step = {
        "key": "set",
        "step_type": "change_field_value",
        "action_change_field_value": {"field_ref": "deals.amount", "value": "1"},
    }
    f = _only(_errors(_spec(step)))
    assert (f.got, f.suggestion) == ("value", "specific_field_value")
    assert "value_type" in f.allowed


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
        "CLI wires; it could be 'notify_member_via_email' or "
        "'send_related_contact_email'",
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


def _update_var(name, **block):
    return {
        "key": f"set_{name}",
        "step_type": "update_variable",
        "action_update_variable": {
            "variable": {"name": name, "data_type": "string"},
            "sources": [],
            **block,
        },
    }


def test_an_array_variable_step_needs_an_aggregation_mode():
    spec = _spec(_var("tags", is_array=True), _update_var("tags"))
    errors = _errors(spec)
    for key, step_type in (
        ("init_tags", "initialize_variable"),
        ("set_tags", "update_variable"),
    ):
        f = _only(errors, step=key)
        assert f.path == f"action_{step_type}.array_aggregation_mode"
        assert f.allowed == ["add_first_non_blank", "add_all"]
        assert (
            f.message
            == f"variable 'tags' is an array, so {step_type} needs array_aggregation_mode"
        )

    init = _var("tags", is_array=True)
    init["action_initialize_variable"]["array_aggregation_mode"] = "add_all"
    check_automation(_spec(init, _update_var("tags", array_aggregation_mode="add_all")))
    check_automation(_spec(_var("tag"), _update_var("tag")))


def test_a_field_change_in_a_global_automation_is_an_error():
    change = {
        "key": "flag",
        "step_type": "change_field_value",
        "action_change_field_value": {
            "field_ref": "flagged",
            "specific_field_value": True,
        },
    }
    spec = _spec(change, type="global", target_object=None)
    f = _only(_errors(spec), step="flag")
    assert f.path == "step_type"
    assert "modify_related_entities" in f.message
    assert f.hint == "kizen automations step-types show modify_related_entities"

    assert not [e for e in _errors_or_none(_spec(change)) if e.step == "flag"]


def test_an_existing_field_change_in_a_global_automation_only_warns():
    """A step already on the live automation doesn't block an unrelated
    update, such as a rename."""
    change = {
        "key": "flag",
        "id": "0b8f3c6e-9d2a-4c51-8e7f-2a6d1c4b9e30",
        "step_type": "change_field_value",
        "action_change_field_value": {
            "field_ref": "flagged",
            "specific_field_value": True,
        },
    }
    checked = check_automation(_spec(change, type="global", target_object=None))
    w = _only(checked.warnings, step="flag")
    assert "global automation has none" in w.message


def _errors_or_none(spec):
    try:
        check_automation(spec)
    except SpecProblems as e:
        return e.errors
    return []


def test_steps_add_refuses_a_field_change_in_a_global_automation():
    from kizen_builder.tools.automations import check_step_spec

    change = {
        "step_type": "change_field_value",
        "action_change_field_value": {"field_ref": "flagged"},
    }
    with pytest.raises(SpecProblems) as exc:
        check_step_spec(change, {"type": "global", "steps": []})
    assert "global automation has none" in exc.value.errors[0].message
    assert check_step_spec(change, {"type": "record_based", "steps": []}) == []


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


def test_the_plan_maps_each_spec_key_to_the_key_a_read_gives(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation

    steps = _set_mrn("set"), _set_mrn("set_again", parent="set", order=1)
    spec = {**_spec(*steps), "target_object": "patients"}
    (op,) = plan_create_automation(spec).operations
    assert op.preview["step_keys"] == {
        "set": "s00_change_field_value",
        "set_again": "s01_change_field_value",
    }


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
        {"trigger_type": "manual", "order": 0},
        {"trigger_type": "activity_logged", "order": 0, "trigger_activity_logged": {}},
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


def test_a_missing_trigger_order_defaults_to_its_position():
    logged = {"trigger_type": "activity_logged", "trigger_activity_logged": {}}
    spec = _spec(_activity(), triggers=[{"trigger_type": "manual"}, logged])
    checked = check_automation(spec)
    assert [t.order for t in checked.auto.triggers] == [0, 1]
    assert checked.defaults == [
        "trigger order from position: 'manual' 0, 'activity_logged' 1"
    ]

    # With no manual trigger, the planner puts one first and numbers the rest.
    checked = check_automation(_spec(_activity(), triggers=[logged]))
    assert checked.defaults == []


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


# --- steps add / edit -------------------------------------------------------

_LIVE = sorted(FIXTURES.glob("automations/*.raw.json"))


@pytest.mark.parametrize("path", _LIVE, ids=lambda p: p.name.split(".")[0])
def test_steps_get_output_passes_the_step_checks(path):
    """`steps edit` starts from `steps get`, so a live step sent back
    unchanged must raise nothing."""
    from kizen_builder.tools.automations import check_step_spec
    from kizen_builder.translate import live_to_payload

    payload = live_to_payload(json.loads(path.read_text()))
    for step in payload["steps"]:
        patch = {k: v for k, v in step.items() if k != "type"}
        check_step_spec({**patch, "step_type": step["type"]}, payload)


def _steps(monkeypatch, argv, spec, steps=()):
    payload = {"steps": list(steps)}
    monkeypatch.setattr(
        auto_tools,
        "patch_steps",
        lambda _api, mutate, execute: {
            "report": mutate(payload, {}),
            "revision_before": 1,
            "payload": payload,
            "validation_problems": [],
        },
    )
    return runner.invoke(
        cli.app, ["automations", "steps", *argv, "--dry-run"], input=json.dumps(spec)
    )


def test_steps_add_lists_every_problem(monkeypatch):
    spec = _activity(target_variable="activity_id", fieldd=1)
    result = _steps(monkeypatch, ["add", "auto", "--root"], spec)
    assert result.exit_code == 1
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("spec error")]
    assert lines == [
        "spec error: step 'act' action_schedule_activity.fieldd: unknown key; the "
        "planner doesn't read it, so it would be dropped",
        "spec error: step 'act' action_schedule_activity.target_variable: variable "
        "'activity_id' is not declared by an initialize_variable step",
    ]

    result = _steps(monkeypatch, ["add", "auto", "--root", "--json"], spec)
    assert [e["path"] for e in json.loads(result.stdout)["errors"]] == [
        "action_schedule_activity.fieldd",
        "action_schedule_activity.target_variable",
    ]


def test_steps_edit_checks_against_the_live_variables(monkeypatch):
    live = [
        {"key": "act", "type": "schedule_activity"},
        {
            "key": "init",
            "type": "initialize_variable",
            "action_initialize_variable": {
                "variable": {"name": "activity_ids", "data_type": "string"}
            },
        },
    ]
    patch = {"action_schedule_activity": {"target_variable": "activity_id"}}
    result = _steps(monkeypatch, ["edit", "auto", "act"], patch, live)
    assert result.exit_code == 1
    assert "(did you mean 'activity_ids'?)" in result.stderr


def test_steps_add_reports_warnings_and_still_validates(monkeypatch):
    monkeypatch.setattr(auto_tools, "build_wire_step", lambda spec, *_: spec)
    spec = _activity(assigned_to={"employee_id": "x"})
    result = _steps(monkeypatch, ["add", "auto", "--root", "--json"], spec)
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    (warning,) = out["warnings"]
    assert "set it to 'team_member'" in warning
    assert "warnings" not in out["report"]

    result = _steps(monkeypatch, ["add", "auto", "--root"], spec)
    assert result.exit_code == 0, result.output
    assert "warning: " in result.stderr and "team_member" in result.stderr


# --- shapes the server rejects -------------------------------------------

_CONDITIONS = "ae78e103-ab10-47d5-9dbc-730c26ae4882"  # encounters, many_to_many
_PATIENT_REL = "889f65e2-dd31-42f0-a088-c3c1dd8ea484"  # encounters, many_to_one
_PATIENT_DOB = "de05fa11-6ea1-400c-8349-e4c24543eaa1"  # patients.date_of_birth


def _encounter_spec(*steps):
    return {**_spec(*steps), "target_object": "encounters"}


def _code(inputs=(), outputs=()):
    return {
        "key": "code",
        "step_type": "code_step",
        "action_code_step": {
            "script": "outputs = {}",
            "inputs": list(inputs),
            "outputs": list(outputs),
        },
    }


def _plan_problems(spec):
    from kizen_builder.tools.planners.automations import plan_create_automation

    with pytest.raises(SpecProblems) as e:
        plan_create_automation(spec)
    return e.value.errors


def _planned_step(spec):
    from kizen_builder.tools.planners.automations import plan_create_automation

    (op,) = plan_create_automation(spec).operations
    return op.payload["steps"][0]


def test_a_related_field_input_on_a_to_many_relationship_names_the_binding(
    patch_live_lookups,
):
    rel = {"name": "conds", "input_type": "related_field", "related_field": "x"}
    spec = _encounter_spec(_code([{**rel, "field": _CONDITIONS}]))
    (f,) = _plan_problems(spec)
    assert f.step == "code"
    assert f.path == "action_code_step.inputs[0].field"
    assert f.got == "conditions"
    assert "to-many relationship (many_to_many)" in f.message
    assert (
        "input_type 'field' on 'conditions', data_type 'entity' and is_list true"
        in f.message
    )


def test_a_related_field_output_on_a_to_many_relationship_is_an_error(
    patch_live_lookups,
):
    out = {"name": "cond", "output_type": "related_field", "related_field": "x"}
    spec = _encounter_spec(_code(outputs=[{**out, "field": _CONDITIONS}]))
    (f,) = _plan_problems(spec)
    assert f.path == "action_code_step.outputs[0].field"
    assert "modify_related_entities" in f.message


def test_a_single_value_related_field_builds_from_ids_or_refs(patch_live_lookups):
    by_id = {
        "name": "dob",
        "input_type": "related_field",
        "field": _PATIENT_REL,
        "related_field": _PATIENT_DOB,
    }
    by_ref = {
        "name": "dob",
        "input_type": "related_field",
        "field_ref": "encounters.patient_rel",
        "related_field_ref": "patients.date_of_birth",
    }
    built = [
        _planned_step(_encounter_spec(_code([i])))["action_code_step"]["inputs"]
        for i in (by_id, by_ref)
    ]
    assert built[0] == built[1]
    assert built[0] == [
        {
            "name": "dob",
            "input_type": "related_field",
            "field": {"id": _PATIENT_REL},
            "related_field": {"id": _PATIENT_DOB},
        }
    ]


def test_the_to_many_binding_the_finding_names_builds(patch_live_lookups):
    conds = {
        "name": "conds",
        "input_type": "field",
        "field_ref": "encounters.conditions",
        "data_type": "entity",
        "is_list": True,
    }
    (built,) = _planned_step(_encounter_spec(_code([conds])))["action_code_step"][
        "inputs"
    ]
    assert built["field"] == {"id": _CONDITIONS}


def _entity_var(**var):
    return {
        "key": "init",
        "step_type": "initialize_variable",
        "action_initialize_variable": {
            "variable": {"name": "patient", "data_type": "entity", **var}
        },
    }


_PATIENTS = "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"


def test_an_entity_variable_without_data_subtype_points_at_objects_list():
    f = _only(_errors(_encounter_spec(_entity_var())), step="init")
    assert f.path == "action_initialize_variable.variable.data_subtype"
    assert f.hint == "kizen objects list"


def test_an_entity_variable_takes_an_object_api_name(patch_live_lookups):
    by_name = _planned_step(_encounter_spec(_entity_var(data_subtype="patients")))
    by_id = _planned_step(_encounter_spec(_entity_var(data_subtype=_PATIENTS)))
    assert by_name == by_id
    variable = by_id["action_initialize_variable"]["variable"]
    assert variable["data_subtype"] == _PATIENTS


def _on_date(**block):
    on_date = {
        "field_ref": "deals.close_date",
        "date_offset": "days_after",
        "date_offset_days": 1,
        **block,
    }
    return _activity(
        schedule={"type": "on_or_around_date", "on_or_around_date": on_date}
    )


def test_a_date_schedule_without_time_is_an_error_with_no_live_call():
    f = _only(_errors(_spec(_on_date())), step="act")
    assert f.path == "action_schedule_activity.schedule.on_or_around_date"
    assert "needs `time`" in f.message


@pytest.mark.parametrize(
    "block",
    [
        {"time": "09:00"},
        {"time_offset": "hours_before", "time_offset_value": 2},
        {"date_offset": "on_day_and_time"},
    ],
    ids=["time", "relative", "on_day_and_time"],
)
def test_a_date_schedule_that_needs_no_time_passes(block):
    check_automation(_spec(_on_date(date_offset_days=1, **block)))


def _date_trigger(**block):
    on_date = {"field_ref": "deals.close_date", **block}
    return [{"trigger_type": "on_or_around_date", "trigger_on_or_around_date": on_date}]


@pytest.mark.parametrize(
    ("block", "needs"),
    [({}, "`time` and `period`"), ({"time": "09:00"}, "`period`")],
    ids=["no-time", "no-period"],
)
def test_a_date_trigger_without_time_is_an_error(block, needs):
    triggers = _date_trigger(date_offset="days_before", date_offset_days=1, **block)
    spec = _spec(_set_mrn("a"), triggers=triggers)
    f = _only(_errors(spec), path="triggers[0].trigger_on_or_around_date")
    assert f"needs {needs} (" in f.message


@pytest.mark.parametrize(
    "block",
    [
        {
            "date_offset": "days_before",
            "date_offset_days": 1,
            "time": "09:00",
            "period": "am",
        },
        {"date_offset": "on_day_and_time"},
    ],
    ids=["time", "on_day_and_time"],
)
def test_a_date_trigger_that_needs_no_time_passes(block):
    check_automation(_spec(_set_mrn("a"), triggers=_date_trigger(**block)))


def _static_var(**source):
    return {
        "key": "greeting",
        "step_type": "initialize_variable",
        "action_initialize_variable": {
            "variable": {"name": "greeting", "data_type": "string"},
            "sources": [{"source_type": "static", "value": "hello", **source}],
        },
    }


def test_a_static_source_without_source_subtype_is_an_error():
    f = _only(_errors(_spec(_static_var())), step="greeting")
    assert f.path == "action_initialize_variable.sources.0"
    assert "a static source needs source_subtype" in f.message


def test_a_static_source_with_source_subtype_passes():
    check_automation(_spec(_static_var(source_subtype="string")))


def _goal(**block):
    return {
        "key": "wait",
        "step_type": "goal",
        "step_goal": {"wait_type": "delay", "delay_amount": 2, **block},
    }


def _change(**block):
    return {
        "key": "set",
        "step_type": "change_field_value",
        "action_change_field_value": {"field_ref": "deals.name", **block},
    }


def _modify(**item):
    return {
        "key": "copy",
        "step_type": "modify_related_entities",
        "action_modify_related_entities": {
            "object_to_modify": "line_items",
            "automation_target_relationship_fields": ["deals.line_items"],
            "fields_to_modify": [{"field_to_modify": "line_items.name", **item}],
        },
    }


_STATIC_SUBTYPES = [
    "string",
    "number",
    "boolean",
    "date",
    "datetime",
    "phone_number",
    "email",
    "uuid",
    "employee",
    "entity",
]


_CHANGE_TYPES = [
    "specific_value",
    "related_object_field",
    "this_record_field",
    "variable",
    "entity_variable_field",
    "blank",
]
_VALUE_TYPES = [
    "specific_value",
    "context_entity_value",
    "variable",
    "entity_variable_field",
    "blank",
]


# The values agents guessed in the docs eval, each a 400 at apply.
@pytest.mark.parametrize(
    "step, path, got, allowed",
    [
        (
            _goal(delay_type="day"),
            "step_goal.delay_type",
            "day",
            ["minutes", "hours", "days", ""],
        ),
        (
            _change(change_type="static", specific_field_value="x"),
            "action_change_field_value.change_type",
            "static",
            _CHANGE_TYPES,
        ),
        (
            _change(actions=[{"field_ref": "deals.name", "change_type": "static"}]),
            "action_change_field_value.actions.0.change_type",
            "static",
            _CHANGE_TYPES,
        ),
        (
            _modify(change_type="static", specific_field_value="x"),
            "action_modify_related_entities.fields_to_modify.0.change_type",
            "static",
            _CHANGE_TYPES,
        ),
        (
            _modify(value_type="specific_field_value", specific_field_value="x"),
            "action_modify_related_entities.fields_to_modify.0.value_type",
            "specific_field_value",
            _VALUE_TYPES,
        ),
        (
            _static_var(source_subtype="string", array_separator=","),
            "action_initialize_variable.sources.0.array_separator",
            ",",
            ["comma", "newline"],
        ),
        (
            _static_var(source_subtype="text"),
            "action_initialize_variable.sources.0.source_subtype",
            "text",
            _STATIC_SUBTYPES,
        ),
    ],
    ids=[
        "goal_delay_type",
        "change_type",
        "actions_change_type",
        "fields_to_modify_change_type",
        "value_type",
        "array_separator",
        "subtype",
    ],
)
def test_a_guessed_enum_value_names_the_step_path_and_valid_values(
    step, path, got, allowed
):
    f = _only(_errors(_spec(step)), step=step["key"])
    assert (f.path, f.got, f.allowed) == (path, got, allowed)


def test_source_subtype_is_checked_against_its_source_type():
    def source(source_type, subtype):
        return _static_var(source_type=source_type, source_subtype=subtype)

    # An activity trigger's subtypes aren't a static source's.
    f = _only(_errors(_spec(source("logged_activity_trigger", "string"))))
    assert "logged_by" in f.allowed
    check_automation(_spec(source("logged_activity_trigger", "logged_by")))
    check_automation(_spec(source("activity_past_due_trigger", "assignee")))
    # The server gives no list for these, so anything passes the check.
    check_automation(_spec(source("field_value", "this_record_field")))
    check_automation(_spec(source("trigger_time", "anything")))


def test_a_read_goal_with_a_blank_delay_type_passes():
    check_automation(_spec(_goal(wait_type="never", delay_type="")))


def test_a_blank_choice_prints_as_empty_quotes():
    f = _only(_errors(_spec(_goal(delay_type="day"))))
    assert f.message == "'day' is not one of: minutes, hours, days, \"\""


def _patients_spec(*steps, **top):
    return {**_spec(*steps, **top), "target_object": "patients"}


def _patient_date_trigger(field):
    return _date_trigger(field_ref=f"patients.{field}", date_offset="on_day_and_time")


def _patient_date_activity(field):
    on_date = {"field_ref": f"patients.{field}", "date_offset": "on_day_and_time"}
    return _activity(
        schedule={"type": "on_or_around_date", "on_or_around_date": on_date}
    )


def test_on_day_and_time_on_a_date_field_trigger_is_an_error(patch_live_lookups):
    spec = _patients_spec(
        _set_mrn("a"), triggers=_patient_date_trigger("date_of_birth")
    )
    (f,) = _plan_problems(spec)
    assert f.path == "triggers[0].trigger_on_or_around_date"
    assert f.got == "date_of_birth"
    assert f.message == (
        "on_day_and_time needs a datetime field; 'date_of_birth' is date. "
        "Use on_day with time and period, or a datetime field."
    )


def test_on_day_and_time_on_a_date_field_schedule_is_an_error(patch_live_lookups):
    (f,) = _plan_problems(_patients_spec(_patient_date_activity("date_of_birth")))
    assert f.step == "act"
    assert f.path == "action_schedule_activity.schedule.on_or_around_date"
    assert "'date_of_birth' is date. Use on_day with time," in f.message


def test_on_day_and_time_on_a_date_field_goal_trigger_is_an_error(
    patch_live_lookups,
):
    goal = {
        "key": "wait",
        "step_type": "goal",
        "step_goal": {
            "wait_type": "never",
            "triggers": _patient_date_trigger("date_of_birth"),
        },
    }
    (f,) = _plan_problems(_patients_spec(goal))
    assert f.step == "wait"
    assert f.path == "step_goal.triggers[0].trigger_on_or_around_date"
    assert "'date_of_birth' is date" in f.message


def test_on_day_and_time_on_a_datetime_field_builds(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation

    spec = _patients_spec(
        _patient_date_activity("created"), triggers=_patient_date_trigger("created")
    )
    (op,) = plan_create_automation(spec).operations
    trigger = op.payload["triggers"][1]["trigger_on_or_around_date"]
    assert trigger["date_offset"] == "on_day_and_time"


def test_on_day_and_time_findings_on_trigger_and_step_come_together(
    patch_live_lookups, monkeypatch
):
    from kizen_builder.tools.planners.automations import LiveContext

    by_id = LiveContext.field_by_id

    def live_shape(self, object_ref, field_id):
        # The /fields endpoint names a field by `name`, not `api_name`.
        field = by_id(self, object_ref, field_id)
        return field and {**field, "name": field["api_name"], "api_name": None}

    monkeypatch.setattr(LiveContext, "field_by_id", live_shape)
    spec = _patients_spec(
        _patient_date_activity("date_of_birth"),
        triggers=_patient_date_trigger("date_of_birth"),
    )
    paths = [(f.path, f.got) for f in _plan_problems(spec)]
    assert paths == [
        ("triggers[0].trigger_on_or_around_date", "date_of_birth"),
        ("action_schedule_activity.schedule.on_or_around_date", "date_of_birth"),
    ]


def test_on_day_and_time_leaves_an_unreadable_field_to_the_server(
    patch_live_lookups, monkeypatch
):
    """A failed /fields read doesn't fail a dry run the check only advises on."""
    from kizen_builder.api.client import KizenAPIError
    from kizen_builder.tools.planners.automations import (
        LiveContext,
        plan_create_automation,
    )

    def refused(self, object_ref, field_id):
        raise KizenAPIError(403, "You do not have permission")

    monkeypatch.setattr(LiveContext, "field_by_id", refused)
    spec = _patients_spec(
        _patient_date_activity("date_of_birth"),
        triggers=_patient_date_trigger("date_of_birth"),
    )
    (op,) = plan_create_automation(spec).operations
    assert op.payload["triggers"][1]["trigger_on_or_around_date"]["date_offset"] == (
        "on_day_and_time"
    )


@pytest.mark.parametrize("days", [{}, {"date_offset_days": 0}], ids=["none", "zero"])
def test_a_days_after_trigger_needs_a_day_count(days):
    block = {"date_offset": "days_after", "time": "09:00", "period": "am", **days}
    spec = _spec(_set_mrn("a"), triggers=_date_trigger(**block))
    f = _only(_errors(spec), path="triggers[0].trigger_on_or_around_date")
    assert f.message.startswith("date_offset_days must be at least 1 with days_before")


def test_a_days_before_schedule_needs_a_day_count():
    spec = _spec(
        _on_date(date_offset="days_before", date_offset_days=None, time="9:00")
    )
    f = _only(_errors(spec), step="act")
    assert f.path == "action_schedule_activity.schedule.on_or_around_date"
    assert "use on_day for the day itself" in f.message


def test_a_schedule_day_count_from_a_variable_needs_no_static_count():
    on_date = {"date_offset_days": None, "time": "09:00"}
    check_automation(_spec(_on_date(date_offset_type="variable", **on_date)))


_TWO_DESTINATIONS = [{"field_ref": "deals.summary"}, {"field_ref": "deals.notes"}]


def _llm(step_type, **block):
    return {
        "key": "llm",
        "step_type": step_type,
        f"action_{step_type}": {
            "model_name": "kizen/lite",
            "prompt": "Summarize the deal.",
            "destinations": _TWO_DESTINATIONS,
            **block,
        },
    }


@pytest.mark.parametrize(
    "step_type", ["call_llm", "file_content_extraction", "audio_transcription"]
)
def test_two_llm_destinations_need_is_advanced(step_type):
    f = _only(_errors(_spec(_llm(step_type))), step="llm")
    assert f.path == f"action_{step_type}"
    assert f.message == "more than one destination needs is_advanced: true"


def test_two_llm_destinations_with_is_advanced_pass():
    check_automation(_spec(_llm("call_llm", is_advanced=True)))


_DATA_TYPE_REFUSALS = {
    "file_content_extraction": (
        "data_type 'doc' isn't one the builder uses: file_content_extraction "
        "always sends 'image', for PDF, JPG, JPEG and PNG files alike, and a step "
        "saved as 'doc' fails when it runs. Leave data_type out; the CLI sends "
        "'image'."
    ),
    "audio_transcription": (
        "data_type 'doc' isn't one the builder uses: audio_transcription always "
        "sends 'audio'. Leave data_type out; the CLI sends 'audio'."
    ),
}


@pytest.mark.parametrize("step_type", sorted(_DATA_TYPE_REFUSALS))
def test_an_llm_file_step_refuses_another_data_type(step_type):
    f = _only(_errors(_spec(_llm(step_type, data_type="doc"))), step="llm")
    assert f.path == f"action_{step_type}.data_type"
    assert f.message == _DATA_TYPE_REFUSALS[step_type]


@pytest.mark.parametrize(
    ("step_type", "data_type"),
    [("file_content_extraction", "image"), ("audio_transcription", "audio")],
)
def test_an_llm_file_step_takes_its_own_data_type(step_type, data_type):
    block = {"is_advanced": True, "data_type": data_type}
    assert check_automation(_spec(_llm(step_type, **block))).warnings == []


@pytest.mark.parametrize(
    ("step", "path", "message"),
    [
        (
            _static_var(),
            "action_initialize_variable.sources.0",
            "a static source needs source_subtype",
        ),
        (
            _on_date(),
            "action_schedule_activity.schedule.on_or_around_date",
            "needs `time`",
        ),
        (
            _on_date(date_offset_days=None, time="09:00"),
            "action_schedule_activity.schedule.on_or_around_date",
            "date_offset_days must be at least 1",
        ),
        (
            _llm("call_llm"),
            "action_call_llm",
            "more than one destination needs is_advanced: true",
        ),
        (
            _llm("file_content_extraction", is_advanced=True, data_type="doc"),
            "action_file_content_extraction.data_type",
            "file_content_extraction always sends 'image'",
        ),
    ],
    ids=["static_source", "schedule_time", "day_count", "is_advanced", "data_type"],
)
def test_steps_edit_checks_the_block_rules(monkeypatch, step, path, message):
    key, step_type = step["key"], step["step_type"]
    patch = {k: v for k, v in step.items() if k not in ("key", "step_type")}
    live = [{"key": key, "type": step_type}]
    result = _steps(monkeypatch, ["edit", "auto", key, "--json"], patch, live)
    assert result.exit_code == 1
    (error,) = json.loads(result.stdout)["errors"]
    assert (error["step"], error["path"]) == (key, path)
    assert "index" not in error
    assert message in error["message"]


def test_steps_edit_block_rules_pass_the_fixed_shape():
    from kizen_builder.tools.automations import check_step_spec

    fixed = _llm("call_llm", is_advanced=True)
    assert check_step_spec(fixed, {"steps": []}) == []


def test_steps_edit_catches_on_day_and_time_on_a_date_field(patch_live_lookups):
    from kizen_builder.tools.automations import normalize_step_patch

    step = _patient_date_activity("date_of_birth")
    patch = {"action_schedule_activity": step["action_schedule_activity"]}
    payload = {"name": "a", "api_name": "a", "type": "record_based", "steps": []}
    with pytest.raises(SpecProblems) as e:
        normalize_step_patch(patch, "act", "schedule_activity", payload, "patients")
    (f,) = e.value.errors
    assert f.path == "action_schedule_activity.schedule.on_or_around_date"
    assert "'date_of_birth' is date" in f.message


def test_a_bare_related_field_ref_is_refused(patch_live_lookups):
    from kizen_builder.tools.planners.automations import plan_create_automation
    from kizen_builder.tools.plans import PlanError

    dob = {
        "name": "dob",
        "input_type": "related_field",
        "field_ref": "encounters.patient_rel",
        "related_field_ref": "date_of_birth",
    }
    with pytest.raises(PlanError, match="must be `object.field` or a field UUID"):
        plan_create_automation(_encounter_spec(_code([dob])))


# --- filter_config ------------------------------------------------------------


def _filter_step(step_type, filter_config):
    if step_type == "condition":
        return {
            "key": "check",
            "step_type": "condition",
            "step_condition": {"type": "custom_filter", "filter_config": filter_config},
        }
    return {
        "key": "check",
        "step_type": "search_records",
        "action_search_records": {
            "custom_object": "deals",
            "filter_type": "custom_filter",
            "filter_config": filter_config,
            "destination_variable": "found",
        },
    }


@pytest.mark.parametrize(
    ("step_type", "block"),
    [("condition", "step_condition"), ("search_records", "action_search_records")],
)
def test_a_bare_filter_clause_prints_the_wrapped_form(step_type, block):
    clause = {"field": "status", "op": "=", "value": "Won"}
    errors = _errors(_spec(_filter_step(step_type, clause)))
    f = _only(errors, path=f"{block}.filter_config")
    assert f.message == (
        "a clause needs a group around it: "
        '{"all": [{"field": "status", "op": "=", "value": "Won"}]}'
    )
    assert f.step == "check"


def test_a_wrong_op_in_a_nested_group_names_its_path():
    fc = {"any": [{"all": [{"field": "status", "op": "equal", "value": 1}]}]}
    f = _only(
        _errors(_spec(_filter_step("condition", fc))),
        path="step_condition.filter_config.any.0.all.0.op",
    )
    assert f.got == "equal" and "=" in f.allowed


@pytest.mark.parametrize(
    ("fc", "message"),
    [
        ({"query": "x"}, "Input should be a valid list"),
        ({"filters": []}, 'filter_config is a group, {"all": [...]} or {"any": [...]}'),
        ({"all": [], "any": []}, "List should have at least 1 item"),
    ],
    ids=["query-not-a-list", "neither-shape", "empty-group"],
)
def test_a_malformed_filter_is_a_finding(fc, message):
    errors = _errors(_spec(_filter_step("condition", fc)))
    assert any(message in f.message for f in errors), errors


def test_the_wire_filter_form_and_unknown_clause_keys_pass():
    fc = {
        "and": True,
        "invalid": False,
        "query": [
            {
                "id": "query-0",
                "and": True,
                "filters": [{"type": "variable", "lhs_variable_name": "score"}],
            }
        ],
    }
    check_automation(_spec(_filter_step("condition", fc)))


def test_the_filter_ops_are_the_ones_the_compiler_takes():
    from typing import get_args

    from kizen_builder import filtering
    from kizen_builder.models.spec.automations_shared import FilterOp

    assert sorted(get_args(FilterOp)) == filtering.VALID_SPEC_OPS


def _related(**block):
    return {
        "key": "make",
        "step_type": "create_related_entity",
        "action_create_related_entity": {
            "target_custom_object": "accounts",
            "new_entity_name": "n",
            "new_entity_name_html": "<p>n</p>",
            "new_entity_owner_type": "assign_from_context_record",
            **block,
        },
    }


def _math(subtype="addition", **arg):
    return {
        "key": "sum",
        "step_type": "math_operator",
        "action_math_operator": {
            "subtype": subtype,
            "variable": "total",
            "simple_builder_arguments": [
                {
                    "argument_type": "constant",
                    "operation_type": "regular",
                    "constant": 1,
                },
                {"argument_type": "constant", "operation_type": "regular", **arg},
            ],
        },
    }


def _notify(step_type, **block):
    return {"key": "tell", "step_type": step_type, f"action_{step_type}": block}


_TEXT = ("owner", "last_active", "last_active_role", "team_selector_field", "employee")

_WRONG_CHOICES = [
    *(
        (
            _notify("notify_member_via_text", team_member={"type": t}, content="hi"),
            "action_notify_member_via_text.team_member.type",
            "employee",
        )
        for t in ("employees", "team_member")
    ),
    (
        _notify(
            "notify_member_via_email",
            team_member={"type": "owner"},
            cc_team_member={"type": "team_member"},
        ),
        "action_notify_member_via_email.cc_team_member.type",
        "employees",
    ),
    (
        {
            "key": "give",
            "step_type": "assign_team_member",
            "action_assign_team_member": {"type": "owner"},
        },
        "action_assign_team_member.type",
        "team_selector_field",
    ),
    (
        _math(subtype="subtraction"),
        "action_math_operator.subtype",
        "multiplication",
    ),
    (
        _math(argument_type="number", constant=2),
        "action_math_operator.simple_builder_arguments.1.argument_type",
        "constant",
    ),
    (
        _related(new_entity_owner_type="owner"),
        "action_create_related_entity.new_entity_owner_type",
        "newly_assigned_owner",
    ),
    (
        _related(new_entity_owner_sub_type="round_robin"),
        "action_create_related_entity.new_entity_owner_sub_type",
        "round_robin_any_role",
    ),
    (
        _related(context_entity_field_resolution="replace"),
        "action_create_related_entity.context_entity_field_resolution",
        "update_if_blank",
    ),
    (
        _related(existing_record_found_action="unarchive"),
        "action_create_related_entity.existing_record_found_action",
        "create_append_suffix",
    ),
]


@pytest.mark.parametrize(("step", "path", "allowed"), _WRONG_CHOICES)
def test_a_wrong_choice_in_a_typed_block_names_the_choices(step, path, allowed):
    f = _only(_errors(_spec(step)), path=path)
    assert allowed in f.allowed and f.step == step["key"]


@pytest.mark.parametrize(
    ("fixture", "step_type"),
    [
        ("create_and_modify_related.raw.json", "create_related_entity"),
        ("kitchen_sink_triggers.raw.json", "math_operator"),
    ],
)
def test_a_typed_block_takes_every_key_a_read_gives(fixture, step_type):
    """A read's expanded objects and lists check clean, so a roundtrip
    spec needs no edits."""
    read = json.loads((FIXTURES / "automations" / fixture).read_text())
    cfg = f"action_{step_type}"
    blocks = [s[cfg] for s in read["steps"] if s["step_type"] == step_type]
    assert blocks
    for block in blocks:
        step = {"key": "s", "step_type": step_type, cfg: block}
        check_automation(_spec(step))


@pytest.mark.parametrize(
    ("subtype", "operation", "message"),
    [
        ("addition", "inverse", "addition takes operation_type regular or negative"),
        (
            "multiplication",
            "negative",
            "multiplication takes operation_type regular or inverse",
        ),
    ],
)
def test_a_math_operation_its_subtype_refuses_is_a_finding(subtype, operation, message):
    step = _math(subtype, constant=2)
    step["action_math_operator"]["simple_builder_arguments"][1]["operation_type"] = (
        operation
    )
    f = _only(
        _errors(_spec(step)), path="action_math_operator.simple_builder_arguments"
    )
    assert f.message.startswith(message)
    assert f.message.endswith("(argument 1)")


def test_a_math_argument_needs_its_type_and_operation():
    step = _math(constant=2)
    del step["action_math_operator"]["simple_builder_arguments"][1]["operation_type"]
    _only(
        _errors(_spec(step)),
        path="action_math_operator.simple_builder_arguments.1.operation_type",
    )


def _bound(inputs=(), outputs=(), step_type="code_step"):
    block = {"inputs": list(inputs), "outputs": list(outputs)}
    if step_type == "code_step":
        block["script"] = "pass"
    else:
        block |= {"plugin_app_api_name": "p", "action_step_api_name": "s"}
    return {"key": "run", "step_type": step_type, f"action_{step_type}": block}


def test_a_wrong_input_type_names_the_types_and_the_nearest():
    step = _bound([{"name": "score", "input_type": "varible", "variable_name": "v"}])
    f = _only(_errors(_spec(step)), path="action_code_step.inputs.0.input_type")
    assert f.suggestion == "variable"
    assert f.allowed == ["field", "variable", "related_field", "static_value"]


@pytest.mark.parametrize(
    ("step", "path", "got"),
    [
        (
            _bound(outputs=[{"name": "out", "output_type": "static_value"}]),
            "action_code_step.outputs.0.output_type",
            "static_value",
        ),
        (
            _bound([{"name": "cfg", "input_type": "business_plugin_config"}]),
            "action_code_step.inputs.0.input_type",
            "business_plugin_config",
        ),
        (
            _bound(outputs=[{"name": "out", "conflict_resolution": "replace"}]),
            "action_code_step.outputs.0.conflict_resolution",
            "replace",
        ),
        (
            _bound([{"name": "n", "data_type": "text"}]),
            "action_code_step.inputs.0.data_type",
            "text",
        ),
    ],
    ids=["static-output", "plugin-config-on-code", "resolution", "data-type"],
)
def test_a_wrong_code_binding_choice_is_a_finding(step, path, got):
    assert _only(_errors(_spec(step)), path=path).got == got


def test_a_code_binding_that_is_not_an_object_is_a_finding():
    f = _only(_errors(_spec(_bound(["score"]))), path="action_code_step.inputs.0")
    assert f.message == "each entry is an object with a name and an input_type"


def test_a_plugin_step_takes_the_install_config_input():
    step = _bound(
        [{"name": "settings", "input_type": "business_plugin_config"}],
        step_type="plugin_code_step",
    )
    check_automation(_spec(step))


def test_each_notify_step_offers_its_own_recipient_types():
    from kizen_builder.tools import step_catalog

    def choices(step_type, path):
        rows = step_catalog.describe(step_type)["fields"]
        return next(r["choices"] for r in rows if r["path"] == path)

    assert choices("notify_member_via_text", "team_member.type") == list(_TEXT)
    assert "employees" in choices("notify_member_via_email", "team_member.type")
    rows = step_catalog.describe("notify_member_via_email")["fields"]
    cc = next(r for r in rows if r["path"] == "cc_team_member")
    assert cc["same_as"] == "team_member"


_NEW = {"a_key_kizen_added_later": 1}


@pytest.mark.parametrize(
    "step",
    [
        _math(constant=2, **_NEW),
        _bound(
            [{"name": "xy", "input_type": "static_value", "static_value": 1, **_NEW}]
        ),
        _notify(
            "notify_member_via_email",
            team_member={"type": "owner", **_NEW},
            email_template_id="m",
        ),
        _filter_step("condition", {"all": [{"field": "s", "op": "=", "value": 1}]}),
    ],
    ids=["math_argument", "code_input", "recipient", "filter"],
)
def test_a_typed_block_still_passes_a_key_it_does_not_declare(step):
    """extra="allow" stays: a key the server accepts but the model doesn't
    know yet is passed through, not refused. A step block's own top-level
    keys are still held to what its planner reads."""
    check_automation(_spec(step))
    trigger = {
        "trigger_type": "field_updated",
        "trigger_field_updated": {
            "field_ref": "deals.status",
            "to_match_mode": "specific_value",
            "to_value": {"id": "1", **_NEW},
            **_NEW,
        },
    }
    check_automation(_spec(step, triggers=[trigger]))

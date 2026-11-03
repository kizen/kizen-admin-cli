"""Visibility rules written by name expand to the rule the Kizen editor writes.

The expected expansions are the editor's own: `activities/site_visit_report.json`
is a type built in the UI (cli-testing, 2026-07-17) whose second rule ORs
four filters on the `visit_outcome` dropdown, an `=` among them. A short rule's filter
must equal that `=` filter exactly, view_model included. No UI-built checkbox
rule exists to capture; its expected filter is the editor's `is_not_blank` filter
(rule 1: the same two view_model steps, a boolean value) with the checkbox
tokens from the editor's condition config (`is_checked`: `=` and true).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
import kizen_builder.tools.planners.activities as pa
from kizen_builder.cli import activities as cli_activities
from kizen_builder.tools.activities import describe_visibility_rule
from kizen_builder.tools.planners.activities import (
    plan_create_activity,
    plan_update_activity,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import load_fixture

CAPTURE = load_fixture("activities/site_visit_report.json")
UI_RULE = CAPTURE["visibility_rules"][1]
UI_EQUALS = UI_RULE["rule"]["filters"][0]
SUCCESSFUL = "4061e95b-f995-4ffb-97f0-63ff7d09902d"
NEEDS_FOLLOW_UP = "0885b8da-e654-4ddb-b3d5-84a9ab7ac327"
FOLLOWUP_DATE = "7c9a9e8c-2a31-4fc9-80aa-6b57a87998d8"
VISIT_SUMMARY = "c0db056b-f2ac-4436-ab35-7927432ab91d"
FLAG = "00000000-0000-4000-8000-0000000000f1"


def _short(*options: str, show: list[str] | None = None) -> dict[str, Any]:
    return {
        "show": show or ["followup_date"],
        "when": {"field": "visit_outcome", "is_any_of": list(options)},
    }


@pytest.fixture
def activity(monkeypatch):
    current = {**copy.deepcopy(CAPTURE), "raw": {}}
    current["fields"].append(
        {"id": FLAG, "api_name": "flag", "field_type": "checkbox", "options": None}
    )
    monkeypatch.setattr(pa, "get_activity", lambda ident, include_fields=True: current)
    return current


def _sent(rules: list[Any]) -> list[Any]:
    (op,) = plan_update_activity(
        "site_visit_report", {"visibility_rules": rules}
    ).operations
    return op.payload["visibility_rules"]


def test_a_short_rule_expands_to_the_editors_rule(activity):
    assert _sent([_short("Successful")]) == [
        {
            "rule": {"and": False, "filters": [UI_EQUALS]},
            "layout_items": UI_RULE["layout_items"],
        }
    ]


def test_each_option_is_its_own_equals_filter_ored(activity):
    second = json.loads(json.dumps(UI_EQUALS).replace(SUCCESSFUL, NEEDS_FOLLOW_UP))
    (rule,) = _sent([_short("Successful", NEEDS_FOLLOW_UP)])
    assert rule["rule"] == {"and": False, "filters": [UI_EQUALS, second]}


def test_shown_fields_keep_their_order_and_take_uuids(activity):
    (rule,) = _sent([_short("Successful", show=[VISIT_SUMMARY, "followup_date"])])
    assert rule["layout_items"] == [
        {"field_id": VISIT_SUMMARY},
        {"field_id": FOLLOWUP_DATE},
    ]


def test_raw_rules_pass_through_beside_short_ones(activity):
    raw = CAPTURE["visibility_rules"]
    sent = _sent([raw[0], _short("Successful"), *raw[1:]])
    assert sent[0] == raw[0] and sent[2:] == raw[1:]
    assert sent[1]["rule"]["filters"] == [UI_EQUALS]


def test_a_linked_field_is_tested_by_its_record_fields_type(activity):
    activity["fields"].append(
        {
            "id": "00000000-0000-4000-8000-0000000000c1",
            "api_name": "branch_tier",
            "field_type": "activity_custom_field",
            "custom_object_field": {"id": "c0", "field_type": "radio"},
            "options": [{"id": "00000000-0000-4000-8000-0000000000c2", "name": "Gold"}],
        }
    )
    (rule,) = _sent(
        [
            {
                "show": ["followup_date"],
                "when": {"field": "branch_tier", "is_any_of": ["Gold"]},
            }
        ]
    )
    (f,) = rule["rule"]["filters"]
    assert f["activity_field_id"] == "00000000-0000-4000-8000-0000000000c1"
    assert f["view_model"][0][1]["error_response"] == {"field_type": "radio"}


@pytest.mark.parametrize("checked", [True, False])
def test_a_checkbox_rule_is_one_equals_filter_on_true_or_false(activity, checked):
    ui = json.loads(
        json.dumps(CAPTURE["visibility_rules"][0]["rule"]["filters"][0])
        .replace(VISIT_SUMMARY, FLAG)
        .replace('"longtext"', '"checkbox"')
    )
    ui["condition"], ui["value"] = "=", checked
    ui["view_model"][1] = ["condition", "is_checked" if checked else "is_not_checked"]
    (rule,) = _sent(
        [{"show": ["followup_date"], "when": {"field": "flag", "is_checked": checked}}]
    )
    assert rule == {
        "rule": {"and": False, "filters": [ui]},
        "layout_items": [{"field_id": FOLLOWUP_DATE}],
    }
    state = "checked" if checked else "not checked"
    assert (
        describe_visibility_rule(rule, activity["fields"])
        == f"show followup_date when flag is {state}"
    )


def test_a_raw_rule_without_view_model_is_refused(activity):
    bare = copy.deepcopy(UI_RULE)
    del bare["rule"]["filters"][2]["view_model"]
    message = (
        "visibility rule 2: filter 3 has no view_model, and Kizen's editor then "
        'lists none of the type\'s rules. Write the rule by name ({"show": [...], '
        '"when": {...}}), or copy one the editor saved from kizen activities get '
        "--json"
    )
    with pytest.raises(PlanError) as e:
        _sent([CAPTURE["visibility_rules"][0], bare])
    assert str(e.value) == message
    with pytest.raises(PlanError) as e:
        plan_create_activity(
            {"name": "Site Visit", "visibility_rules": [UI_RULE, bare]}
        )
    assert str(e.value) == message


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            _short("Successful", show=["nope"]),
            "visibility rule 2: no field 'nope' on site_visit_report. "
            "kizen activities get site_visit_report lists them",
        ),
        (
            _short("Won"),
            "visibility rule 2: 'Won' isn't an option of visit_outcome. "
            "Options: Successful, Needs Follow-up",
        ),
        (
            {
                "show": ["followup_date"],
                "when": {"field": "satisfaction", "is_any_of": ["4"]},
            },
            "visibility rule 2: satisfaction is a rating field; rules can test "
            "dropdown, radio, yesnomaybe and checkbox fields",
        ),
        (
            {
                "show": ["visit_summary"],
                "when": {"field": "branch_city", "is_any_of": ["X"]},
            },
            "visibility rule 2: branch_city is a text field; rules can test "
            "dropdown, radio, yesnomaybe and checkbox fields",
        ),
        (
            {"show": ["followup_date"], "when": {"field": "flag", "is_any_of": ["Y"]}},
            'visibility rule 2: flag is a checkbox field; test it with "is_checked": '
            "true or false",
        ),
        (
            {"show": ["followup_date"], "when": {"field": "flag", "is_checked": "yes"}},
            'visibility rule 2: flag is a checkbox field; test it with "is_checked": '
            "true or false",
        ),
        (
            {
                "show": ["followup_date"],
                "when": {"field": "visit_outcome", "is_checked": True},
            },
            "visibility rule 2: visit_outcome is a dropdown field; test it with "
            "is_any_of",
        ),
        (
            {"show": ["followup_date"], "when": {"is_any_of": ["Successful"]}},
            "visibility rule 2: when.field names the field to test",
        ),
        (
            _short("Successful", show=["visit_outcome"]),
            "visibility rule 2: visit_outcome can't be shown by its own answer",
        ),
        (
            {**_short("Successful"), "hide": ["notes"]},
            "visibility rule 2: unknown key 'hide'. A rule takes show and when",
        ),
    ],
)
def test_plan_errors_name_the_rule(activity, entry, message):
    with pytest.raises(PlanError) as e:
        _sent([CAPTURE["visibility_rules"][0], entry])
    assert str(e.value) == message


def test_dry_run_says_what_each_rule_does(activity):
    (op,) = plan_update_activity(
        "site_visit_report",
        {"visibility_rules": [_short("Successful", "Needs Follow-up"), UI_RULE]},
    ).operations
    assert op.preview["diff"]["visibility_rules"] == (
        "raw rule; raw rule; raw rule; raw rule → "
        "show followup_date when visit_outcome is any of Successful, "
        "Needs Follow-up; raw rule"
    )


def test_only_rules_in_the_short_shape_are_described():
    fields = CAPTURE["fields"]
    assert [
        describe_visibility_rule(r, fields) for r in CAPTURE["visibility_rules"]
    ] == [
        None,  # is_not_blank
        None,  # = and != on one field
        None,  # >= on a rating
        None,  # three date filters, AND'd
    ]
    one = {
        "rule": {"and": False, "filters": [UI_EQUALS]},
        "layout_items": UI_RULE["layout_items"],
    }
    assert (
        describe_visibility_rule(one, fields)
        == "show followup_date when visit_outcome is any of Successful"
    )


def test_get_prints_short_shaped_rules_in_words(monkeypatch):
    one = {
        "rule": {"and": False, "filters": [UI_EQUALS]},
        "layout_items": UI_RULE["layout_items"],
    }
    shown = {**copy.deepcopy(CAPTURE), "visibility_rules": [one, UI_RULE]}
    for key in ("description", "custom_objects", "is_editable", "n_submissions"):
        shown.setdefault(key, None)
    monkeypatch.setattr(cli_activities.act_tools, "get_activity", lambda ident: shown)
    result = CliRunner().invoke(cli.app, ["activities", "get", "site_visit_report"])
    assert result.exit_code == 0, result.output
    assert (
        "1. show followup_date when visit_outcome is any of Successful" in result.output
    )
    assert '"condition": "!="' in result.output.split("2.", 1)[1]


def test_create_refuses_short_rules_and_sends_raw_ones(monkeypatch):
    monkeypatch.setattr(pa, "list_activities", lambda: [])
    with pytest.raises(PlanError) as e:
        plan_create_activity(
            {
                "name": "Site Visit",
                "api_name": "site_visit",
                "visibility_rules": [_short("A")],
            }
        )
    assert str(e.value) == (
        "visibility rules by name need the fields to exist; create the type, then "
        "run kizen activities update site_visit --visibility-rules-file <f>"
    )
    plan = plan_create_activity({"name": "Site Visit", "visibility_rules": [UI_RULE]})
    assert plan.operations[0].payload["visibility_rules"] == [UI_RULE]

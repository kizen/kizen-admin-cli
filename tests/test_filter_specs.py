"""JSON filter specs: from_spec compilation, normalization, CLI rendering."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from kizen_builder.filtering import (
    Field,
    VariableReferenceError,
    as_filter_config,
    field_type_ops,
    filter_context,
    from_spec,
    normalize_filter_config,
    render_search_filters,
)
from tests.conftest import load_fixture
from tests.filter_stub import StubClient


def _clauses(spec, obj="policies_policy"):
    groups = render_search_filters(spec, obj, client=StubClient())
    return [c for g in groups for c in g["filters"]]


def test_spec_condition_compiles_and_resolves():
    (clause,) = _clauses({"all": [{"field": "fdropdown", "op": "=", "value": "dd1"}]})
    assert clause["type"] == "fields_v2"
    assert clause["field"].startswith('"custom"::')
    # option label resolved to its uuid
    assert clause["value"] == "82cd3986-8f04-4de3-acde-9c7f96aa73de"


def test_spec_nested_groups():
    groups = render_search_filters(
        {
            "all": [
                {"field": "ftext", "op": "contains", "value": "x"},
                {
                    "any": [
                        {"field": "finteger", "op": ">", "value": 5},
                        {"field": "finteger", "op": "is_blank"},
                    ]
                },
            ]
        },
        "policies_policy",
        client=StubClient(),
    )
    assert len(groups) == 2
    assert groups[0]["and"] is True
    assert groups[1]["and"] is False


def test_spec_no_value_op_rejects_value():
    with pytest.raises(ValueError, match='takes no "value"'):
        from_spec({"field": "ftext", "op": "is_blank", "value": True})


def test_spec_list_op_requires_list():
    with pytest.raises(ValueError, match="requires a list"):
        from_spec({"field": "fdropdown", "op": "is_any_of", "value": "dd1"})


def test_spec_unknown_op_lists_valid_ops():
    with pytest.raises(ValueError, match="Valid ops:"):
        from_spec({"field": "ftext", "op": "regex", "value": "x"})


def test_spec_group_must_be_only_key():
    with pytest.raises(ValueError, match="exactly"):
        from_spec({"all": [], "any": []})


def test_spec_ui_unsupported_condition_rejected():
    """UI-supportability validation still applies to spec-built filters."""
    with pytest.raises(ValueError, match="does not support"):
        _clauses({"all": [{"field": "flongtext", "op": "contains", "value": "x"}]})


def test_raw_query_passes_through():
    raw = {
        "and": True,
        "query": [
            {
                "and": True,
                "filters": [
                    {
                        "type": "fields",
                        "field": "name",
                        "condition": "=",
                        "value": "x",
                        "subtype": "non_custom",
                    }
                ],
            }
        ],
    }
    groups = render_search_filters(raw, "policies_policy", client=StubClient())
    assert groups[0]["filters"][0]["field"] == "name"
    assert groups[0]["id"] == "query-0"  # normalization added the id


# ---------------------------------------------------------------------------
# normalize_filter_config
# ---------------------------------------------------------------------------


def test_normalize_assigns_missing_group_ids_and_keeps_existing():
    cfg = normalize_filter_config(
        {
            "query": [
                {"and": True, "filters": []},
                {"id": "query-custom", "and": False, "filters": []},
            ]
        }
    )
    assert [g["id"] for g in cfg["query"]] == ["query-0", "query-custom"]
    assert cfg["and"] is True
    assert cfg["invalid"] is False


def test_normalize_rejects_null_clause_value():
    with pytest.raises(ValueError, match="null"):
        normalize_filter_config(
            {"query": [{"filters": [{"condition": "is_blank", "value": None}]}]}
        )


def test_normalize_preserves_unknown_clause_keys():
    clause = {
        "type": "variable",
        "lhs_variable_name": "score",
        "condition": "<=",
        "rhs_value": "5",
        "view_model": [],
    }
    cfg = normalize_filter_config({"query": [{"filters": [clause]}]})
    assert cfg["query"][0]["filters"][0] == clause


def test_normalize_rejects_missing_query():
    with pytest.raises(ValueError, match='"query"'):
        normalize_filter_config({"and": True})


def test_field_type_ops_text():
    ops = field_type_ops("text")
    assert "contains" in ops
    assert "starts_with" in ops
    assert "is_any_of" not in ops  # not a text-field op
    assert "is_checked" not in ops  # checkbox-only, must not bleed into text


def test_field_type_ops_checkbox_gets_is_checked_alias():
    ops = field_type_ops("checkbox")
    assert ops == ["=", "is_checked", "not_checked"]


def test_field_type_ops_no_arg_lists_every_field_type():
    all_ops = field_type_ops()
    assert "text" in all_ops
    assert "dropdown" in all_ops
    assert all_ops["text"] == field_type_ops("text")


def test_field_type_ops_unknown_type_lists_valid_choices():
    with pytest.raises(ValueError, match="unknown field_type"):
        field_type_ops("bogus")


def test_field_type_ops_every_listed_op_actually_compiles():
    """Every op field_type_ops() advertises for a type must be usable in a
    real --filter spec against that type — the whole point of deriving this
    from the op tables instead of hand-maintaining a second list."""
    from tests.filter_stub import StubClient

    sample_values = {
        "=": "x",
        "!=": "x",
        "<": 1,
        "<=": 1,
        ">": 1,
        ">=": 1,
        "contains": "x",
        "not_contains": "x",
        "starts_with": "x",
        "ends_with": "x",
        "not_starts_with": "x",
        "not_ends_with": "x",
        "month_equals": 1,
        "is_any_of": ["dd1"],
        "not_any_of": ["dd1"],
        "has_any": ["dd1"],
        "between": [1, 2],
    }
    no_value_ops = {"is_blank", "not_blank", "is_me", "is_checked", "not_checked"}
    field_by_type = {
        "text": "ftext",
        "checkbox": "fcheckbox",
        "dropdown": "fdropdown",
    }
    for field_type, field in field_by_type.items():
        for op in field_type_ops(field_type):
            spec = {"field": field, "op": op}
            if op not in no_value_ops:
                spec["value"] = sample_values[op]
            render_search_filters(
                {"all": [spec]}, "policies_policy", client=StubClient()
            )


# ---------------------------------------------------------------------------
# "variable": a field compared to an automation variable (condition steps)
#
# The expected clauses are the UI's own, from two automations built in the
# Kizen UI on 2026-10-14 (see the fixtures). Lookups are faked from the same
# objects.
# ---------------------------------------------------------------------------

_ENCOUNTERS = "98649f6d-b809-4c9a-980d-8ee54708a8a6"
_CONDITIONS = "76572554-4933-4b8b-b66a-f38fdc986953"
_PIPELINE = "8c3f0149-6915-4370-940f-2e61848d6842"
_BRANCHES = "95ca0064-bcf7-44bb-9e5c-b350a3847278"


def _field(name, field_id, field_type, is_default=False, related_object=None):
    relation = {"fetch_url": "standard", "related_object": related_object}
    return {
        "name": name,
        "id": field_id,
        "field_type": field_type,
        "is_default": is_default,
        "options": [],
        "relation": relation if related_object else None,
    }


class _CapturedSchema:
    OBJECTS = {
        _ENCOUNTERS: {
            "name": "encounters",
            "object_type": "standard",
            "fetch_url": "standard",
        },
        _PIPELINE: {
            "name": "object_with_workflow",
            "object_type": "pipeline",
            "fetch_url": "pipeline",
        },
        _CONDITIONS: {
            "name": "conditions",
            "object_type": "standard",
            "description": (
                "Represents a clinical condition, problem, or diagnosis "
                "associated with a patient. Maps to FHIR R4 Condition resource."
            ),
            "entity_name": "Condition",
            "object_name": "Conditions",
        },
        _BRANCHES: {
            "name": "branches",
            "object_type": "standard",
            "description": None,
            "entity_name": "Branch",
            "object_name": "Branches",
        },
    }
    FIELDS = {
        _ENCOUNTERS: [
            _field(
                "encounter_class", "4e83924c-3c21-450b-8bbc-5e99d72f5501", "dropdown"
            ),
            _field(
                "special_arrangements",
                "e70a46ea-dac6-4e75-8896-0c8ce972ea95",
                "checkboxes",
            ),
            _field(
                "owner", "e5e01b14-47a2-4965-b424-cfff326863c6", "team_selector", True
            ),
            _field(
                "conditions",
                "ae78e103-ab10-47d5-9dbc-730c26ae4882",
                "relationship",
                related_object=_CONDITIONS,
            ),
            _field("notes", "8d1d5c2e-3f2a-4b55-9a59-0c6d2b7f2a10", "text"),
        ],
        _PIPELINE: [
            _field(
                "related_branch_records",
                "558e8df0-eab1-4501-92c3-d5229404219c",
                "relationship",
                related_object=_BRANCHES,
            ),
        ],
    }

    def custom_object(self, ref):
        if ref in ("client_client", "contacts-object-id"):
            return {"id": "contacts-object-id", "name": ref, "fetch_url": "client"}
        for obj_id, obj in self.OBJECTS.items():
            if ref in (obj_id, obj["name"]):
                return {"id": obj_id, **obj}
        raise LookupError(ref)

    def get_field(self, obj, name):
        if obj in ("client_client", "contacts-object-id") and name == "tags":
            return {"id": "f6a89dd0-7e37-42fb-92f6-eef59ddade10", "name": "tags"}
        obj_id = self.custom_object(obj)["id"]
        return next(
            (f for f in self.FIELDS[obj_id] if name in (f["name"], f["id"])), None
        )


def _captured(path):
    raw = load_fixture(f"automations/{path}")
    (step,) = [s for s in raw["steps"] if s["step_type"] == "condition"]
    variables = {
        s["action_initialize_variable"]["variable"]["name"]: {
            "data_type": s["action_initialize_variable"]["variable"]["data_type"],
            "is_array": s["action_initialize_variable"]["variable"]["is_array"],
            "data_subtype": (
                s["action_initialize_variable"]["variable"]["data_subtype"] or {}
            ).get("id"),
        }
        for s in raw["steps"]
        if s["step_type"] == "initialize_variable"
    }
    clauses = step["step_condition"]["filter_config"]["query"][0]["filters"]
    # `description` is UI display text the server doesn't need.
    return [{k: v for k, v in c.items() if k != "description"} for c in clauses], (
        variables
    )


def _compile(spec, obj, variables):
    with filter_context(obj, client=_CapturedSchema()):
        (clause,) = as_filter_config(from_spec({"all": [spec]}, variables))["query"][0][
            "filters"
        ]
    return clause


def test_variable_clauses_match_the_ui_capture():
    expected, variables = _captured("condition_variable_rhs.raw.json")
    # The UI wrote `false` on the one rule it edited from a CLI-built one;
    # rules it adds fresh carry `true`.
    expected[0]["view_model"][0][1]["vars"][0] = ["fields_settings_search", True]
    specs = [
        {"field": "encounter_class", "op": "is_any_of", "variable": "zz_strings"},
        {"field": "special_arrangements", "op": "not_any_of", "variable": "zz_uuids"},
        {"field": "owner", "op": "is_any_of", "variable": "zz_uuids"},
        {"field": "conditions", "op": "is_any_of", "variable": "zz_conditions"},
        {"field": "owner", "op": "not_any_of", "variable": "zz_member"},
    ]
    for spec, clause in zip(specs, expected, strict=True):
        assert _compile(spec, _ENCOUNTERS, variables) == clause


def test_variable_clause_on_a_pipeline_object_matches_the_ui_capture():
    (expected,), variables = _captured("condition_variable_rhs_pipeline.raw.json")
    spec = {
        "field": "related_branch_records",
        "op": "is_any_of",
        "variable": "branches",
    }
    assert _compile(spec, _PIPELINE, variables) == expected


def test_variable_needs_the_declarations_only_a_condition_step_passes():
    with pytest.raises(ValueError) as e:
        from_spec({"field": "fdropdown", "op": "is_any_of", "variable": "allowed"})
    assert str(e.value) == (
        '"variable" compares to an automation variable and only works in a '
        "condition step's filter_config"
    )
    with pytest.raises(ValueError, match="only works in a condition step"):
        render_search_filters(
            {"all": [{"field": "fdropdown", "op": "is_any_of", "variable": "x"}]},
            "policies_policy",
            client=StubClient(),
        )


def test_variable_and_value_are_exclusive():
    with pytest.raises(ValueError) as e:
        from_spec(
            {"field": "f", "op": "is_any_of", "value": ["a"], "variable": "v"},
            variables={},
        )
    assert str(e.value) == 'a condition takes "value" or "variable", not both'


def test_variable_takes_only_the_any_of_ops():
    with pytest.raises(ValueError, match="is_any_of, not_any_of"):
        from_spec({"field": "f", "op": "=", "variable": "v"}, variables={})


_STRINGS = {"strs": {"data_type": "string", "is_array": True}}


@pytest.mark.parametrize(
    ("spec", "variables", "message"),
    [
        (
            {"field": "encounter_class", "op": "is_any_of", "variable": "nope"},
            _STRINGS,
            "variable 'nope' isn't declared in this automation (declared arrays: strs)",
        ),
        (
            {"field": "encounter_class", "op": "is_any_of", "variable": "nope"},
            {"one": {"data_type": "string", "is_array": False}},
            "variable 'nope' isn't declared in this automation (declared arrays: none)",
        ),
        (
            {"field": "encounter_class", "op": "is_any_of", "variable": "one"},
            {"one": {"data_type": "string", "is_array": False}},
            "variable 'one' isn't an array — comparing a field to a variable needs "
            "an array variable",
        ),
        (
            {"field": "encounter_class", "op": "is_any_of", "variable": "nums"},
            {"nums": {"data_type": "number", "is_array": True}},
            "field 'encounter_class' (dropdown) can't be compared to a number array "
            "variable",
        ),
        (
            {"field": "conditions", "op": "is_any_of", "variable": "recs"},
            {
                "recs": {
                    "data_type": "entity",
                    "is_array": True,
                    "data_subtype": _BRANCHES,
                }
            },
            "field 'conditions' (relationship) can't be compared to an entity "
            "(branches) array variable",
        ),
        (
            {"field": "conditions", "op": "is_any_of", "variable": "recs"},
            {"recs": {"data_type": "entity", "is_array": True}},
            "field 'conditions' (relationship) can't be compared to an entity "
            "array variable",
        ),
        (
            {"field": "notes", "op": "is_any_of", "variable": "strs"},
            _STRINGS,
            "field 'notes' (text) can't be compared to a variable. Field types that "
            "can: checkboxes, dropdown, relationship, team_selector",
        ),
    ],
)
def test_variable_declaration_errors(spec, variables, message):
    with pytest.raises(VariableReferenceError) as e:
        _compile(spec, _ENCOUNTERS, variables)
    assert str(e.value) == message


def test_variable_rule_refused_on_contacts():
    spec = {"field": "tags", "op": "is_any_of", "variable": "strs"}
    with pytest.raises(VariableReferenceError, match="isn't supported on Contacts"):
        _compile(spec, "client_client", _STRINGS)


# ---------------------------------------------------------------------------
# The pipeline stage field's own ops: time_in_stage, entered_stage, left_stage
# ---------------------------------------------------------------------------

_STAGE_1 = "a82570ce-c4e4-4548-bbb7-8b77f94bbb9a"


def _dsl_clause(expr):
    with filter_context("policies_policy", client=StubClient()):
        return expr.as_dict()


@pytest.mark.parametrize(
    ("spec", "expr"),
    [
        (
            {"op": "time_in_stage", "value": "Stage 1", "more_than": 14},
            Field("stage").time_in_stage("Stage 1", more_than=14, units="days"),
        ),
        (
            {
                "op": "time_in_stage",
                "value": _STAGE_1,
                "less_than": 0,
                "units": "hours",
            },
            Field("stage").time_in_stage(_STAGE_1, less_than=0, units="hours"),
        ),
        (
            {"op": "entered_stage", "value": "stage 1", "earlier_than": "2026-07-14"},
            Field("stage").entered_stage("Stage 1", earlier_than=date(2026, 7, 14)),
        ),
        (
            {"op": "left_stage", "value": "Stage 1", "later_than_or_on": "2026-10-30"},
            Field("stage").left_stage("Stage 1", later_than_or_on=date(2026, 10, 30)),
        ),
    ],
)
def test_stage_op_compiles_to_the_dsl_clause(spec, expr):
    spec = {"field": "stage", **spec}
    expected = _dsl_clause(expr)
    assert expected["value"] == _STAGE_1
    (clause,) = _clauses({"all": [spec]})
    assert clause == expected
    with filter_context("policies_policy", client=StubClient()):
        cfg = as_filter_config(from_spec({"all": [spec]}))
    assert cfg["query"][0]["filters"] == [expected]


def test_time_in_stage_clause_is_the_probed_wire_shape():
    (clause,) = _clauses(
        {
            "field": "stage",
            "op": "time_in_stage",
            "value": "Stage 1",
            "more_than": 4,
            "units": "days",
        }
    )
    assert clause == {
        "type": "fields_v2",
        "subtype": "non_custom",
        "field": "stage",
        "overwrite": {"type": "stage"},
        "condition": "time_in_stage",
        "value": _STAGE_1,
        "comparison_condition": ">",
        "comparison_value": "4",
        "comparison_type": "days",
    }


_ENTERED_ONE_OF = (
    "on, not_on, earlier_than, earlier_than_or_on, later_than, later_than_or_on"
)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (
            {"op": "time_in_stage", "value": "Stage 1"},
            'op "time_in_stage" takes exactly one of: more_than, less_than',
        ),
        (
            {"op": "time_in_stage", "value": "Stage 1", "more_than": 1, "less_than": 2},
            'op "time_in_stage" takes exactly one of: more_than, less_than',
        ),
        (
            {"op": "entered_stage", "value": "Stage 1"},
            f'op "entered_stage" takes exactly one of: {_ENTERED_ONE_OF}',
        ),
        (
            {"op": "left_stage", "value": "Stage 1", "on": "2026-10-30", "not_on": "x"},
            f'op "left_stage" takes exactly one of: {_ENTERED_ONE_OF}',
        ),
        (
            {"op": "time_in_stage", "value": "Stage 1", "more_than": 1.5},
            'op "time_in_stage": more_than must be a whole number of days, hours '
            "or weeks, got 1.5",
        ),
        (
            {"op": "time_in_stage", "value": "Stage 1", "less_than": -1},
            'op "time_in_stage": less_than must be a whole number of days, hours '
            "or weeks, got -1",
        ),
        (
            {"op": "time_in_stage", "value": "Stage 1", "more_than": "5"},
            'op "time_in_stage": more_than must be a whole number of days, hours '
            'or weeks, got "5"',
        ),
        (
            {"op": "time_in_stage", "value": "Stage 1", "more_than": True},
            'op "time_in_stage": more_than must be a whole number of days, hours '
            "or weeks, got true",
        ),
        (
            {
                "op": "time_in_stage",
                "value": "Stage 1",
                "more_than": 1,
                "units": "months",
            },
            'op "time_in_stage": units must be one of days, hours, weeks, got "months"',
        ),
        (
            {"op": "entered_stage", "value": "Stage 1", "on": "10/30/2026"},
            'op "entered_stage": on must be a date as YYYY-MM-DD, got "10/30/2026"',
        ),
        (
            {"op": "entered_stage", "value": "Stage 1", "on": "2026-02-30"},
            'op "entered_stage": on must be a date as YYYY-MM-DD, got "2026-02-30"',
        ),
        (
            {"op": "left_stage", "value": "Stage 1", "later_than": "2026-10-30T00:00"},
            'op "left_stage": later_than must be a date as YYYY-MM-DD, '
            'got "2026-10-30T00:00"',
        ),
        (
            {"op": "time_in_stage", "value": ["Stage 1"], "more_than": 1},
            'op "time_in_stage" takes one stage in "value", not a list',
        ),
        (
            {"op": "entered_stage", "on": "2026-10-30"},
            'op "entered_stage" requires a "value"',
        ),
        (
            {
                "op": "entered_stage",
                "value": "Stage 1",
                "on": "2026-10-30",
                "units": "days",
            },
            "unknown keys in condition: ['units']",
        ),
        (
            {"op": "=", "value": "Stage 1", "more_than": 1},
            "unknown keys in condition: ['more_than']",
        ),
    ],
)
def test_stage_op_refusals(spec, message):
    with pytest.raises(ValueError) as e:
        from_spec({"field": "stage", **spec})
    assert str(e.value) == message


def test_stage_op_cannot_compare_to_a_variable():
    with pytest.raises(ValueError, match='can\'t compare to a "variable"'):
        from_spec(
            {"field": "stage", "op": "time_in_stage", "variable": "v", "more_than": 1},
            variables={},
        )


def test_stage_op_refused_off_the_stage_field():
    with pytest.raises(ValueError, match="does not support condition 'time_in_stage'"):
        _clauses(
            {
                "field": "fdropdown",
                "op": "time_in_stage",
                "value": "dd1",
                "more_than": 1,
            }
        )


class _NamedStub(StubClient):
    def custom_object(self, api_name):
        return {**super().custom_object(api_name), "name": "policies_policy"}

    def get_field(self, obj_id, name):
        if name == _STAGE_FIELD_ID:
            name = "stage"
        return super().get_field(obj_id, name)


_STAGE_FIELD_ID = "94de5e17-04f5-490d-9510-793e01d38ffa"


@pytest.mark.parametrize("field", ["stage", _STAGE_FIELD_ID])
@pytest.mark.parametrize(
    "spec",
    [
        {"op": "=", "value": "Stage 1"},
        {"op": "time_in_stage", "value": "Stage 1", "more_than": 1},
    ],
)
def test_stage_field_by_name_or_uuid_gets_the_stage_clause(field, spec):
    (by_field,) = render_search_filters(
        {"field": field, **spec}, "policies_policy", client=_NamedStub()
    )[0]["filters"]
    (by_name,) = _clauses({"field": "stage", **spec})
    assert by_field == by_name
    assert by_field["overwrite"] == {"type": "stage"}

    with pytest.raises(ValueError, match="'Stage 9' isn't a stage of"):
        render_search_filters(
            {"field": field, **spec, "value": "Stage 9"},
            "policies_policy",
            client=_NamedStub(),
        )


@pytest.mark.parametrize(
    "spec",
    [
        {"field": "stage", "op": "=", "value": "Stage 9"},
        {"field": "stage", "op": "is_any_of", "value": ["Stage 1", "Stage 9"]},
        {"field": "stage", "op": "time_in_stage", "value": "Stage 9", "more_than": 1},
        {"field": "stage", "op": "left_stage", "value": "Stage 9", "on": "2026-10-30"},
    ],
)
def test_unknown_stage_is_refused(spec):
    with pytest.raises(ValueError) as e:
        render_search_filters({"all": [spec]}, "policies_policy", client=_NamedStub())
    assert str(e.value) == (
        "stage 'Stage 9' isn't a stage of 'policies_policy'. Stages: Stage 1"
    )


def test_stage_uuid_from_another_object_is_refused():
    with pytest.raises(ValueError, match="isn't a stage of"):
        _clauses(
            {
                "field": "stage",
                "op": "=",
                "value": "8c3f0149-6915-4370-940f-2e61848d6842",
            }
        )


def test_stage_resolves_by_name_case_insensitively_or_by_uuid():
    by_name, by_uuid = _clauses(
        {
            "all": [
                {"field": "stage", "op": "=", "value": "STAGE 1"},
                {"field": "stage", "op": "!=", "value": _STAGE_1},
            ]
        }
    )
    assert by_name["value"] == by_uuid["value"] == _STAGE_1


def test_non_stage_options_still_pass_through():
    (clause,) = _clauses({"field": "fdropdown", "op": "=", "value": "not-an-option"})
    assert clause["value"] == "not-an-option"


def test_stage_ops_are_valid_spec_ops_but_not_a_field_type_row():
    from kizen_builder.filtering import VALID_SPEC_OPS

    assert {"time_in_stage", "entered_stage", "left_stage"} <= set(VALID_SPEC_OPS)
    for ops in field_type_ops().values():
        assert not {"time_in_stage", "entered_stage", "left_stage"} & set(ops)


def test_ui_built_stage_clauses_normalize_unchanged():
    raw = load_fixture("automations/stage_time_filter_config.json")
    assert normalize_filter_config(copy.deepcopy(raw)) == raw

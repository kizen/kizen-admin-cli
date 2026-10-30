"""Golden tests for the automation plan builder: spec in -> wire payload out.

Encodes the branching model (parent_key + parent_yes_no), the
condition-step action_on_failure rule, manual-trigger auto-injection,
field_ref resolution, and the unsupported-type error contract.
"""

from __future__ import annotations

import json

import pytest

from kizen_builder.models.spec import AutomationDef
from kizen_builder.models.spec.automations_triggers import trigger_config_field
from kizen_builder.tools.planners.automations import (
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    LiveContext,
    _assign_team_member_problem,
    _block_field_for,
    _build_automation_payload,
    _reject_unhonoured_keys,
    diff_automation,
    plan_create_automation,
    plan_update_automation,
)
from kizen_builder.tools.plans import PlanError
from tests import builder_keys
from tests.conftest import FIXTURES, load_fixture


def _build(spec: dict) -> dict:
    """Validate a spec and run the payload builder against fixture state."""
    auto = AutomationDef.model_validate(spec)
    return _build_automation_payload(auto, LiveContext())


# A raw condition rule that needs no field lookup.
NAME_IS_X = {
    "and": True,
    "query": [
        {
            "and": True,
            "filters": [
                {
                    "type": "fields",
                    "field": "name",
                    "subtype": "non_custom",
                    "condition": "=",
                    "value": "x",
                }
            ],
        }
    ],
}

BRANCHING_SPEC = {
    "api_name": "branching_test",
    "name": "Branching Test",
    "type": "record_based",
    "target_object": "patients",
    "triggers": [
        {
            "trigger_type": "new_entity_created",
            "order": 0,
            "trigger_new_entity_created": {},
        }
    ],
    "steps": [
        {
            "key": "check",
            "step_type": "condition",
            "order": 0,
            "parent_key": None,
            "step_condition": {
                "type": "custom_filter",
                "filter_config": NAME_IS_X,
            },
        },
        {
            "key": "stop_yes",
            "step_type": "stop_execution",
            "order": 1,
            "parent_key": "check",
            "parent_branch": "yes",
        },
        {
            "key": "stop_no",
            "step_type": "stop_execution",
            "order": 2,
            "parent_key": "check",
            "parent_branch": "no",
        },
    ],
}


def test_branching_uses_parent_key_and_parent_yes_no(patch_live_lookups):
    payload = _build(BRANCHING_SPEC)
    by_key = {s["key"]: s for s in payload["steps"]}
    assert by_key["check"]["parent_key"] is None
    assert by_key["stop_yes"]["parent_key"] == "check"
    assert by_key["stop_yes"]["parent_yes_no"] == "yes"
    assert by_key["stop_no"]["parent_yes_no"] == "no"
    # yes_step_ids must never appear — the live API 500s on it
    assert "yes_step_ids" not in by_key["check"]
    assert "no_step_ids" not in by_key["check"]


def test_condition_action_on_failure_forced_to_notify_pause(patch_live_lookups):
    """The API rejects the default notify_continue on condition steps."""
    payload = _build(BRANCHING_SPEC)
    check = next(s for s in payload["steps"] if s["key"] == "check")
    assert check["action_on_failure"] == "notify_pause"


def test_step_and_trigger_id_omitted_when_not_in_spec(patch_live_lookups):
    """A step/trigger with no `id` in the spec gets none in the payload —
    the server assigns a fresh one, same as before this field existed."""
    payload = _build(BRANCHING_SPEC)
    for step in payload["steps"]:
        assert "id" not in step
    for trigger in payload["triggers"]:
        assert "id" not in trigger


def test_step_and_trigger_id_forwarded_when_set_in_spec(patch_live_lookups):
    """A spec seeded from a live read (e.g. `kizen automations show`) can set
    `id` on a step/trigger it isn't changing, to keep that step's identity —
    and its execution history — across the update instead of rotating."""
    spec = dict(BRANCHING_SPEC)
    spec["triggers"] = [{**BRANCHING_SPEC["triggers"][0], "id": "trigger-uuid-123"}]
    spec["steps"] = [
        dict(s, id="check-step-uuid-456") if s["key"] == "check" else s
        for s in BRANCHING_SPEC["steps"]
    ]
    payload = _build(spec)
    by_key = {s["key"]: s for s in payload["steps"]}
    assert by_key["check"]["id"] == "check-step-uuid-456"
    assert "id" not in by_key["stop_yes"]
    assert "id" not in by_key["stop_no"]
    by_type = {t["type"]: t for t in payload["triggers"]}
    assert by_type["new_entity_created"]["id"] == "trigger-uuid-123"
    # the auto-injected manual trigger is genuinely new — no id of its own
    assert "id" not in by_type["manual"]


def test_goal_step_nested_trigger_id_forwarded_when_set_in_spec(patch_live_lookups):
    """A goal step's own wait-until triggers go through `_step_goal`, a
    separate code path from top-level triggers — it needs its own id-echo
    coverage."""
    spec = dict(BRANCHING_SPEC)
    spec["steps"] = [
        BRANCHING_SPEC["steps"][0],
        {
            "key": "goal",
            "step_type": "goal",
            "order": 1,
            "parent_key": "check",
            "parent_branch": "yes",
            "step_goal": {
                "wait_type": "delay",
                "delay_type": "minutes",
                "delay_amount": 5,
                "triggers": [
                    {
                        "trigger_type": "activity_logged",
                        "order": 0,
                        "id": "goal-trigger-uuid-789",
                        "trigger_activity_logged": {"activity_type_id": "act-1"},
                    }
                ],
            },
        },
    ]
    payload = _build(spec)
    goal = next(s for s in payload["steps"] if s["key"] == "goal")
    assert goal["step_goal"]["triggers"][0]["id"] == "goal-trigger-uuid-789"


def test_stop_execution_emits_config_block(patch_live_lookups):
    payload = _build(BRANCHING_SPEC)
    stop = next(s for s in payload["steps"] if s["key"] == "stop_yes")
    assert stop["action_stop_execution"] == {}


@pytest.mark.parametrize(
    "action",
    [
        "stop_and_fail",
        "stop_and_complete",
        "stop_and_cancel",
        "pause_and_error",
        "pause",
    ],
)
def test_stop_execution_all_five_action_options(patch_live_lookups, action):
    """All 5 values are real, confirmed-live wire shapes (captured from one
    automation with one stop_execution step per UI dropdown option — see
    automation.md's stop_execution section) — each must round-trip through
    the planner rather than being silently dropped to {} or rejected by the
    (previously too-narrow) Literal type."""
    spec = dict(BRANCHING_SPEC)
    spec["steps"] = [
        dict(s, action_stop_execution={"action": action, "notify": True})
        if s["key"] == "stop_yes"
        else s
        for s in spec["steps"]
    ]
    payload = _build(spec)
    stop = next(s for s in payload["steps"] if s["key"] == "stop_yes")
    assert stop["action_stop_execution"] == {"action": action, "notify": True}


def test_modify_related_entities_relationship_field_ref_resolves(patch_live_lookups):
    """Regression for the reported bug: `relationship_field_ref` (the
    documented spec field) was silently ignored by the builder, which only
    ever read `automation_target_relationship_fields` — a user following the
    docs got a 400 ("automation_target_relationship_fields should be set")
    with no indication why. Now folded in as a single-hop convenience alias,
    and list entries also accept dotted 'object.field' refs, not just UUIDs.
    """
    patients = load_fixture("objects/patients.json")
    encounters_rel = next(
        f for f in patients["fields"] if f["api_name"] == "encounters"
    )
    spec = {
        "api_name": "mre_relationship_ref_test",
        "name": "MRE Relationship Ref Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "modify",
                "step_type": "modify_related_entities",
                "order": 0,
                "parent_key": None,
                "action_modify_related_entities": {
                    "object_to_modify": "encounters",
                    "relationship_field_ref": "patients.encounters",
                    "fields_to_modify": [],
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_modify_related_entities"]
    assert action["automation_target_relationship_fields"] == [encounters_rel["id"]]


def test_modify_related_entities_target_relationship_fields_accepts_dotted_ref(
    patch_live_lookups,
):
    patients = load_fixture("objects/patients.json")
    encounters_rel = next(
        f for f in patients["fields"] if f["api_name"] == "encounters"
    )
    spec = {
        "api_name": "mre_dotted_list_test",
        "name": "MRE Dotted List Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "modify",
                "step_type": "modify_related_entities",
                "order": 0,
                "parent_key": None,
                "action_modify_related_entities": {
                    "object_to_modify": "encounters",
                    "automation_target_relationship_fields": ["patients.encounters"],
                    "fields_to_modify": [],
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_modify_related_entities"]
    assert action["automation_target_relationship_fields"] == [encounters_rel["id"]]


def test_modify_related_entities_context_entity_field_dotted_ref_resolves(
    patch_live_lookups,
):
    """Regression: context_entity_field only ran through `_unwrap_id`, so a
    dotted 'object.field' ref (which automation.md's general field_ref rule
    says should work) was sent to the API as a literal string and 400'd
    ("Must be a valid UUID"). Now resolved via `_resolve_field` like
    field_to_modify/related_object_field already were.
    """
    patients = load_fixture("objects/patients.json")
    encounters = load_fixture("objects/encounters.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    attending_provider = next(
        f for f in encounters["fields"] if f["api_name"] == "attending_provider"
    )
    spec = {
        "api_name": "mre_context_field_test",
        "name": "MRE Context Field Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "modify",
                "step_type": "modify_related_entities",
                "order": 0,
                "parent_key": None,
                "action_modify_related_entities": {
                    "object_to_modify": "encounters",
                    "automation_target_relationship_fields": ["patients.encounters"],
                    "fields_to_modify": [
                        {
                            "value_type": "context_entity_value",
                            "field_to_modify": "encounters.attending_provider",
                            "context_entity_field": "patients.mrn",
                        }
                    ],
                },
            }
        ],
    }
    payload = _build(spec)
    field_update = payload["steps"][0]["action_modify_related_entities"][
        "fields_to_modify"
    ][0]
    assert field_update["field_to_modify"] == attending_provider["id"]
    assert field_update["context_entity_field"] == mrn["id"]
    assert field_update["value_type"] == "context_entity_value"


def test_modify_related_entities_record_source_from_a_variable(patch_live_lookups):
    """Without `record_source`, the server defaults to `related_object_field`
    and 400s for want of a hop (confirmed live 2026-10-06)."""
    spec = {
        "api_name": "mre_record_source_test",
        "name": "MRE Record Source Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "modify",
                "step_type": "modify_related_entities",
                "order": 0,
                "parent_key": None,
                "action_modify_related_entities": {
                    "object_to_modify": "encounters",
                    "automation_target_relationship_fields": [],
                    "record_source": "automation_variable",
                    "record_source_variables": ["visit_record"],
                    "fields_to_modify": [],
                },
            }
        ],
    }
    action = _build(spec)["steps"][0]["action_modify_related_entities"]
    assert action["record_source"] == "automation_variable"
    assert action["record_source_variables"] == ["visit_record"]
    assert action["automation_target_relationship_fields"] == []


def test_call_llm_derives_html_prompt_from_plain_prompt(patch_live_lookups):
    """The Kizen builder UI keeps prompt/html_prompt in sync (same quirk as
    notify_member_via_text's content/html_content) — a plain-prompt-only
    call_llm step runs fine via the API but its rich-text prompt editor
    renders blank without html_prompt. `custom_objects.<field>` is call_llm's
    merge-field namespace token for the automation's own target_object."""
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "call_llm_html_prompt_test",
        "name": "Call LLM HTML Prompt Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "Summarize MRN {{ custom_objects.mrn }} for the chart.",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_call_llm"]
    assert action["html_prompt"] == (
        '<p>Summarize MRN <span class="kzn-merge-field" '
        f'data-merge-field-fallback-label="{mrn["display_name"]}" '
        'data-merge-field-relationship="custom_objects.mrn">'
        "{{ custom_objects.mrn }}</span> for the chart.</p>"
    )


def test_call_llm_preserves_explicit_html_prompt(patch_live_lookups):
    spec = {
        "api_name": "call_llm_explicit_html_test",
        "name": "Call LLM Explicit HTML Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "plain",
                    "html_prompt": "<p>custom <b>markup</b></p>",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_call_llm"]
    assert action["html_prompt"] == "<p>custom <b>markup</b></p>"


def test_call_llm_rejects_data_type(patch_live_lookups):
    """Kizen accepts `data_type` on call_llm and drops it (confirmed live
    2026-10-14), so the planner refuses it rather than forwarding a no-op."""
    spec = {
        "api_name": "call_llm_data_type_test",
        "name": "Call LLM Data Type Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "plain",
                    "data_type": "text",
                },
            }
        ],
    }
    with pytest.raises(PlanError, match="data_type"):
        _build(spec)


def test_call_llm_destinations_resolve_field_ref(patch_live_lookups):
    """call_llm previously ran destinations through plain _normalize_destinations
    (no field_ref resolution) while file_content_extraction used the
    field_ref-resolving path — an inconsistency with no documented reason.
    Both now share _resolve_llm_destinations."""
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "call_llm_dest_field_ref_test",
        "name": "Call LLM Dest Field Ref Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "Extract the MRN.",
                    "destinations": [{"field_ref": "patients.mrn"}],
                },
            }
        ],
    }
    payload = _build(spec)
    dest = payload["steps"][0]["action_call_llm"]["destinations"][0]
    assert dest["field"] == mrn["id"]


def test_call_llm_related_object_field_with_explicit_hop(patch_live_lookups):
    """Cross-object write: related_object_field names the destination on the
    related record; relationship_field_ref names the hop on target_object.
    Confirmed live 2026-07-27 that the wire repurposes `field` as the hop —
    not the destination — whenever related_object_field is present."""
    encounters = load_fixture("objects/encounters.json")
    patients = load_fixture("objects/patients.json")
    patient_rel = next(
        f for f in encounters["fields"] if f["api_name"] == "patient_rel"
    )
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "call_llm_related_dest_test",
        "name": "Call LLM Related Dest Test",
        "type": "record_based",
        "target_object": "encounters",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "Extract the patient's MRN from this encounter.",
                    "destinations": [
                        {
                            "related_object_field": "patients.mrn",
                            "relationship_field_ref": "encounters.patient_rel",
                        }
                    ],
                },
            }
        ],
    }
    payload = _build(spec)
    dest = payload["steps"][0]["action_call_llm"]["destinations"][0]
    assert dest["related_object_field"] == mrn["id"]
    assert dest["field"] == patient_rel["id"]


def test_call_llm_related_object_field_hop_via_field_ref(patch_live_lookups):
    """The relationship hop can also be given via the plain field_ref key
    (the wire dialect the builder UI itself produces) instead of the more
    readable relationship_field_ref alias."""
    encounters = load_fixture("objects/encounters.json")
    patients = load_fixture("objects/patients.json")
    patient_rel = next(
        f for f in encounters["fields"] if f["api_name"] == "patient_rel"
    )
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "call_llm_related_dest_field_ref_hop_test",
        "name": "Call LLM Related Dest Field Ref Hop Test",
        "type": "record_based",
        "target_object": "encounters",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "Extract the patient's MRN from this encounter.",
                    "destinations": [
                        {
                            "related_object_field": "patients.mrn",
                            "field_ref": "encounters.patient_rel",
                        }
                    ],
                },
            }
        ],
    }
    payload = _build(spec)
    dest = payload["steps"][0]["action_call_llm"]["destinations"][0]
    assert dest["related_object_field"] == mrn["id"]
    assert dest["field"] == patient_rel["id"]


def test_call_llm_related_object_field_without_hop_raises(patch_live_lookups):
    """No relationship_field_ref, no field_ref/field, and target_object
    (patients) has no relationship field pointing at the destination's
    object (encounters) — must fail clearly instead of sending a bogus
    `field` or letting the server 400 with a confusing message."""
    spec = {
        "api_name": "call_llm_related_dest_no_hop_test",
        "name": "Call LLM Related Dest No Hop Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "llm",
                "step_type": "call_llm",
                "order": 0,
                "parent_key": None,
                "action_call_llm": {
                    "model_name": "gemini/gemini-2.5-flash",
                    "prompt": "Summarize the most recent encounter.",
                    "destinations": [
                        {"related_object_field": "encounters.clinical_notes"}
                    ],
                },
            }
        ],
    }
    with pytest.raises(PlanError, match="relationship hop"):
        _build(spec)


# ---------------------------------------------------------------------------
# call_llm MCP server, run-as account, turn limit, tool failure policy
# (wire shapes confirmed live 2026-10-27)
# ---------------------------------------------------------------------------

_SERVER_ID = "bde29351-0000-4000-8000-000000000001"
_ACCOUNT_ID = "afa714ac-0000-4000-8000-000000000002"
_TWIN_ID = "afa714ac-0000-4000-8000-000000000003"


@pytest.fixture
def step_pickers(monkeypatch):
    """Serve the call_llm step's two pickers; count the GETs."""
    from kizen_builder.api import automations as api
    from kizen_builder.api import client

    class _Client:
        def __init__(self, cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = {"servers": 0, "accounts": 0}
    lists = {
        "servers": [
            {"id": _SERVER_ID, "name": "Example Tools", "api_name": "example_tools"}
        ],
        "accounts": [
            {"id": _ACCOUNT_ID, "name": "Automation Runner"},
            {"id": _TWIN_ID, "name": "Twin"},
            {"id": "afa714ac-0000-4000-8000-000000000004", "name": "Twin"},
        ],
    }

    def serve(which):
        def fetch(_client):
            calls[which] += 1
            return lists[which]

        return fetch

    monkeypatch.setattr(client, "KizenClient", _Client)
    monkeypatch.setattr(api, "list_mcp_step_options", serve("servers"))
    monkeypatch.setattr(api, "list_run_tools_as_options", serve("accounts"))
    return {"calls": calls, "lists": lists}


def _llm_spec(*blocks: dict) -> dict:
    return {
        "api_name": "call_llm_tools_test",
        "name": "Call LLM Tools Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": f"llm{i}",
                "step_type": "call_llm",
                "order": i,
                "parent_key": f"llm{i - 1}" if i else None,
                "action_call_llm": {"model_name": "kizen/pro", "prompt": "Hi", **b},
            }
            for i, b in enumerate(blocks)
        ],
    }


_TOOL_KEYS = (
    "mcp_server_id",
    "run_tools_as_id",
    "max_sequential_turns",
    "tool_failure_policy",
)


def _tool_keys(payload: dict, i: int = 0) -> tuple:
    block = payload["steps"][i]["action_call_llm"]
    return tuple(block[k] for k in _TOOL_KEYS)


def test_call_llm_omitted_tool_keys_build_to_explicit_defaults(patch_live_lookups):
    """Omitting both ids on a PUT keeps the server and clears the account,
    so the builder always sends the pair, null included."""
    assert _tool_keys(_build(_llm_spec({}))) == (None, None, 25, "never_fail")


def test_call_llm_names_resolve_through_the_step_pickers(
    patch_live_lookups, step_pickers
):
    payload = _build(
        _llm_spec(
            {"mcp_server": "example_tools", "run_tools_as": "Automation Runner"},
            {
                "mcp_server": "example_tools",
                "run_tools_as": "Automation Runner",
                "max_sequential_turns": 10,
                "tool_failure_policy": "never_fail",
            },
        )
    )
    assert _tool_keys(payload, 0) == (_SERVER_ID, _ACCOUNT_ID, 25, "never_fail")
    assert _tool_keys(payload, 1) == (_SERVER_ID, _ACCOUNT_ID, 10, "never_fail")
    assert step_pickers["calls"] == {"servers": 1, "accounts": 1}


def test_call_llm_ids_and_read_objects_need_no_lookup(patch_live_lookups):
    """A UUID or a read's expanded object never reaches a resolver."""
    from kizen_builder.translate import _SHIM_CTX

    for block in (
        {"mcp_server_id": _SERVER_ID, "run_tools_as_id": _ACCOUNT_ID},
        {"mcp_server": _SERVER_ID, "run_tools_as": _ACCOUNT_ID},
        {
            "mcp_server": {"id": _SERVER_ID, "api_name": "example_tools"},
            "run_tools_as": {"id": _ACCOUNT_ID, "name": "Automation Runner"},
        },
    ):
        out = _STEP_BUILDERS["call_llm"](
            {"model_name": "kizen/pro", "html_prompt": "<p>Hi</p>", **block},
            AutomationDef.model_validate(_llm_spec()),
            _SHIM_CTX,
        )
        assert (out["mcp_server_id"], out["run_tools_as_id"]) == (
            _SERVER_ID,
            _ACCOUNT_ID,
        )


@pytest.mark.parametrize(
    "block, message",
    [
        (
            {"mcp_server": "nope", "run_tools_as": "Automation Runner"},
            "MCP server 'nope' isn't one a Call LLM step can attach in "
            "'{env}'. Available: example_tools",
        ),
        (
            {"mcp_server": "example_tools", "run_tools_as": "Nobody"},
            "service account 'Nobody' can't run a Call LLM step's tools in "
            "'{env}'. Available: Automation Runner, Twin, Twin",
        ),
        (
            {"mcp_server": "example_tools", "run_tools_as": "Twin"},
            "service account name 'Twin' matches 2 accounts in '{env}': "
            f"{_TWIN_ID}, afa714ac-0000-4000-8000-000000000004. Use the id instead.",
        ),
        (
            {"mcp_server": "example_tools", "mcp_server_id": _TWIN_ID},
            "set mcp_server or mcp_server_id, not both",
        ),
        (
            {"run_tools_as": "Automation Runner", "run_tools_as_id": _TWIN_ID},
            "set run_tools_as or run_tools_as_id, not both",
        ),
        (
            {"mcp_server": "example_tools"},
            "step 'llm0': mcp_server is set without run_tools_as. Kizen runs an "
            "MCP server's tools as a service account, so set both or neither.",
        ),
        (
            {"run_tools_as_id": _ACCOUNT_ID},
            "step 'llm0': run_tools_as is set without mcp_server. Set both or neither.",
        ),
    ],
    ids=[
        "unknown-server",
        "unknown-account",
        "ambiguous-account",
        "server-twice",
        "account-twice",
        "server-alone",
        "account-alone",
    ],
)
def test_call_llm_tool_reference_errors(
    patch_live_lookups, step_pickers, block, message
):
    env = LiveContext().env
    with pytest.raises(PlanError) as e:
        _build(_llm_spec(block))
    assert message.format(env=env) in str(e.value)


def test_call_llm_both_spellings_agreeing_is_fine(patch_live_lookups, step_pickers):
    payload = _build(
        _llm_spec(
            {
                "mcp_server": "example_tools",
                "mcp_server_id": _SERVER_ID,
                "run_tools_as": {"id": _ACCOUNT_ID},
                "run_tools_as_id": _ACCOUNT_ID,
            }
        )
    )
    assert _tool_keys(payload)[:2] == (_SERVER_ID, _ACCOUNT_ID)


@pytest.mark.parametrize(
    "field, value",
    [
        ("max_sequential_turns", 0),
        ("max_sequential_turns", 32768),
        ("tool_failure_policy", "fail_step"),
    ],
)
def test_call_llm_tool_settings_are_bounded_in_the_spec(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=field):
        AutomationDef.model_validate(_llm_spec({field: value}))


@pytest.fixture
def live_mcp_automation(monkeypatch, patch_live_lookups):
    """The captured call_llm_mcp automation as the live one `update` reads:
    step 0 has a server and account, step 1 has neither."""
    import kizen_builder.tools.planners.automations as ma

    raw = load_fixture("automations/call_llm_mcp.raw.json")
    raw["custom_object"]["name"] = "patients"
    monkeypatch.setattr(
        ma, "list_automations", lambda: [{"api_name": raw["api_name"], "id": raw["id"]}]
    )
    monkeypatch.setattr(ma, "get_automation", lambda api_name: {"raw": raw})
    return raw


def _update_spec(raw: dict, *blocks: dict) -> dict:
    spec = _llm_spec(*blocks)
    spec.update(api_name=raw["api_name"], name=raw["name"])
    steps = sorted(raw["steps"], key=lambda s: s["order"])
    for step, live in zip(spec["steps"], steps, strict=True):
        step["id"] = live["id"]
    return spec


def test_update_dry_run_names_steps_whose_mcp_server_goes(live_mcp_automation):
    """A spec that omits the keys sends nulls, so the update detaches a server
    attached in the UI. The preview `--dry-run` prints says which steps."""
    plan = plan_update_automation(_update_spec(live_mcp_automation, {}, {}))
    (op,) = plan.operations
    assert op.preview["detaches_mcp_server"] == ["llm0"]
    assert op.preview["clears_run_tools_as"] == ["llm0"]


def test_update_dry_run_is_quiet_when_the_server_stays(
    live_mcp_automation, step_pickers
):
    step_pickers["lists"]["servers"][0]["id"] = live_mcp_automation["steps"][0][
        "action_call_llm"
    ]["mcp_server"]["id"]
    step_pickers["lists"]["accounts"][0]["id"] = live_mcp_automation["steps"][0][
        "action_call_llm"
    ]["run_tools_as"]["id"]
    tools = {"mcp_server": "example_tools", "run_tools_as": "Automation Runner"}
    (op,) = plan_update_automation(
        _update_spec(live_mcp_automation, tools, {})
    ).operations
    assert "detaches_mcp_server" not in op.preview
    assert "clears_run_tools_as" not in op.preview


def test_steps_edit_call_llm_names_the_server_by_api_name(
    patch_live_lookups, step_pickers
):
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {
        "action_call_llm": {
            "model_name": "kizen/pro",
            "html_prompt": "<p>Hi</p>",
            "mcp_server": "example_tools",
            "run_tools_as": "Automation Runner",
            "max_sequential_turns": 10,
        }
    }
    out = normalize_step_patch(patch, "llm", "call_llm", _LIVE_PAYLOAD, "patients")
    block = out["action_call_llm"]
    assert tuple(block[k] for k in _TOOL_KEYS) == (
        _SERVER_ID,
        _ACCOUNT_ID,
        10,
        "never_fail",
    )


def test_find_relationship_field_auto_detects_unambiguous_hop(monkeypatch):
    """LiveContext.find_relationship_field is the auto-detect fallback used
    when a related_object_field destination omits relationship_field_ref."""
    ctx = LiveContext()
    fake_object = {
        "fields": [
            {"api_name": "unrelated", "relation_target": None, "id": "aaaa"},
            {"api_name": "patient_rel", "relation_target": "patients", "id": "bbbb"},
        ]
    }
    monkeypatch.setattr(ctx, "object_data", lambda api_name: fake_object)
    assert ctx.find_relationship_field("encounters", "patients") == "bbbb"
    assert ctx.find_relationship_field("encounters", "nonexistent_object") is None


def test_find_relationship_field_ambiguous_returns_none(monkeypatch):
    """Two candidate relationship fields to the same object — refuse to
    guess; the caller must set relationship_field_ref explicitly."""
    ctx = LiveContext()
    fake_object = {
        "fields": [
            {
                "api_name": "primary_patient",
                "relation_target": "patients",
                "id": "aaaa",
            },
            {
                "api_name": "referring_patient",
                "relation_target": "patients",
                "id": "bbbb",
            },
        ]
    }
    monkeypatch.setattr(ctx, "object_data", lambda api_name: fake_object)
    assert ctx.find_relationship_field("encounters", "patients") is None


def test_create_related_entity_reduces_expanded_variable_refs(patch_live_lookups):
    """Regression: a live GET of create_related_entity expands
    new_entity_stage/new_entity_owner_variable/target_variable to full
    objects. The server 400s if those are echoed back unchanged on write --
    it wants a bare stage id and bare variable *names* respectively."""
    spec = {
        "api_name": "cre_test",
        "name": "CRE Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "create",
                "step_type": "create_related_entity",
                "order": 0,
                "parent_key": None,
                "action_create_related_entity": {
                    "target_object": "patients",
                    "target_custom_object": "patients",
                    "new_entity_owner_variable": {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "name": "owner",
                        "data_type": "employee",
                    },
                    "new_entity_stage": {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "name": "New",
                        "deleted": False,
                    },
                    "target_variable": {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "name": "new_record",
                        "data_type": "entity",
                    },
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_create_related_entity"]
    assert action["new_entity_owner_variable"] == "owner"
    assert action["new_entity_stage"] == "22222222-2222-2222-2222-222222222222"
    assert action["target_variable"] == "new_record"


def test_notify_member_via_email_normalizes_team_member_and_message_id(
    patch_live_lookups,
):
    spec = {
        "api_name": "notify_test",
        "name": "Notify Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_email",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_email": {
                    "team_member": {"type": "owner"},
                    "email_template_id": "11111111-1111-1111-1111-111111111111",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_email"]
    assert action["team_member"] == {"type": "owner"}
    assert action["id"] == "11111111-1111-1111-1111-111111111111"


def test_notify_member_via_text_normalizes_team_member(patch_live_lookups):
    spec = {
        "api_name": "notify_text_test",
        "name": "Notify Text Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {
                        "type": "last_active_role",
                        "role_id": "22222222-2222-2222-2222-222222222222",
                    },
                    "content": "Reminder: class starts soon.",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert action["team_member"] == {
        "type": "last_active_role",
        "role_id": "22222222-2222-2222-2222-222222222222",
    }
    assert action["content"] == "Reminder: class starts soon."
    assert action["html_content"] == "<p>Reminder: class starts soon.</p>"
    assert "base_message_id" not in action


def test_notify_member_via_text_preserves_explicit_html_content(patch_live_lookups):
    spec = {
        "api_name": "notify_text_html_test",
        "name": "Notify Text HTML Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "plain",
                    "html_content": "<p>custom <b>markup</b></p>",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert action["html_content"] == "<p>custom <b>markup</b></p>"


def test_notify_member_via_text_renders_merge_field_span(patch_live_lookups):
    spec = {
        "api_name": "notify_text_merge_test",
        "name": "Notify Text Merge Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "Hi {{ entity_record.owner }}, please review.",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert action["content"] == "Hi {{ entity_record.owner }}, please review."
    assert action["html_content"] == (
        '<p>Hi <span class="kzn-merge-field" '
        'data-merge-field-fallback-label="Owner" '
        'data-merge-field-relationship="entity_record.owner">'
        "{{ entity_record.owner }}</span>, please review.</p>"
    )


def test_notify_member_via_text_merge_field_pseudo_field_falls_back_to_title_case(
    patch_live_lookups,
):
    """entity_record pseudo-fields (link_url, created, estimated_close_date,
    ...) aren't real object fields — no field-metadata lookup succeeds, so
    the label falls back to a title-cased reading of the api_name."""
    spec = {
        "api_name": "notify_text_merge_unknown_test",
        "name": "Notify Text Merge Unknown Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "{{ entity_record.link_url }}",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert 'data-merge-field-fallback-label="Link Url"' in action["html_content"]


def test_notify_member_via_text_merge_field_reserved_namespaces(patch_live_lookups):
    """team_member.* (the notified team member's own fields) and business.*
    (tenant settings) are reserved merge-field namespaces, not custom object
    api_names, and neither carries `objectname` — confirmed from a live
    capture (2026-08-26). The specific label VALUES asserted below
    ("Team Member First Name", "Business Zip/Postal Code") are also from
    that same live capture, for these two exact tokens — not a claim that
    labels in these namespaces are generally derivable from the field
    api_name; an unrecognized token in either namespace still falls back to
    a title-cased guess (merge_fields._fallback_label)."""
    spec = {
        "api_name": "notify_text_merge_namespaces_test",
        "name": "Notify Text Merge Namespaces Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "{{ team_member.first_name }} {{ business.postal_code }}",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert (
        'data-merge-field-relationship="team_member.first_name"'
        in action["html_content"]
    )
    assert (
        'data-merge-field-fallback-label="Team Member First Name"'
        in action["html_content"]
    )
    assert (
        'data-merge-field-relationship="business.postal_code"' in action["html_content"]
    )
    assert (
        'data-merge-field-fallback-label="Business Zip/Postal Code"'
        in action["html_content"]
    )
    assert "data-merge-field-objectname" not in action["html_content"]


def test_notify_member_via_text_merge_field_custom_object_namespace_gains_objectname(
    patch_live_lookups,
):
    """A namespace that is a real custom-object api_name — not one of
    merge_fields.RESERVED_NAMESPACES — gets `data-merge-field-objectname`
    holding the object's DISPLAY name, confirmed from a live capture
    (2026-08-26): `object_with_workflow` -> "object with workflow", `county`
    -> "County". No committed fixture uses a real custom-object api_name as
    a merge-field namespace (every fixture uses `custom_objects`, the
    pseudo-token for the automation's own target_object — see
    test_call_llm_derives_html_prompt_from_plain_prompt), so this uses the
    `patients` object fixture directly as the namespace instead — a real,
    already-committed object record, just not one previously exercised in
    this position. This is the gap the repo's own fixtures don't cover
    (BCLI-027)."""
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "notify_text_merge_objectname_test",
        "name": "Notify Text Merge Objectname Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "{{ patients.mrn }}",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert action["html_content"] == (
        '<p><span class="kzn-merge-field" '
        f'data-merge-field-fallback-label="{mrn["display_name"]}" '
        'data-merge-field-relationship="patients.mrn" '
        f'data-merge-field-objectname="{patients["display_name"]}">'
        "{{ patients.mrn }}</span></p>"
    )


def test_notify_member_via_text_merge_field_automation_variable_is_literal(
    patch_live_lookups,
):
    """automation_variable.<name> tokens are NOT title-cased by Kizen at all
    — the fallback-label is the literal variable name as authored. Confirmed
    from committed fixtures, not a live capture done for this item:
    tests/fixtures/automations/llm_comparison.raw.json:296
    (`fallback-label="llm_extract_output"`) and
    on_or_around_date_goto.raw.json:1230
    (`fallback-label="classification_total"`). Reserved namespace, so no
    `objectname` either."""
    spec = {
        "api_name": "notify_text_merge_automation_variable_test",
        "name": "Notify Text Merge Automation Variable Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "{{ automation_variable.llm_extract_output }}",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    assert (
        'data-merge-field-fallback-label="llm_extract_output"' in action["html_content"]
    )
    assert "data-merge-field-objectname" not in action["html_content"]


def test_notify_member_via_text_merge_field_automation_history(patch_live_lookups):
    """automation_history.<field> is a reserved namespace (no objectname).
    Its label is NOT a fixed function of the field: the same token
    (`automation_history.execution_id`) carries "Automation Execution ID" in
    three committed fixtures but "Agentic Workflow Execution ID" in
    `kitchen_sink_triggers.raw.json` and in a live capture — see
    merge-field-markup-captured-live.md's "label is NOT a function of the
    token" finding. This is authoring-time context, not a canonical value,
    so this test does not pin a specific label string for any
    automation_history field — only that a span is produced (relationship
    and namespace classification are correct, no objectname) for both a
    field with a captured-but-contested label and one with none at all."""
    spec = {
        "api_name": "notify_text_merge_automation_history_test",
        "name": "Notify Text Merge Automation History Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": (
                        "{{ automation_history.execution_id }} "
                        "{{ automation_history.automation_id }} "
                        "{{ automation_history.unrecognized_field }}"
                    ),
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    html_content = action["html_content"]
    for field in ("execution_id", "automation_id", "unrecognized_field"):
        assert (
            f'data-merge-field-relationship="automation_history.{field}"'
            in html_content
        )
        assert f"{{{{ automation_history.{field} }}}}" in html_content
    assert "data-merge-field-objectname" not in html_content


def test_notify_member_via_text_merge_field_multi_segment_token(patch_live_lookups):
    """The token regex must match a namespace followed by ONE OR MORE
    dot-separated segments, not exactly two. A real multi-segment
    relationship-hop token — `custom_objects.primary_document_record.id` —
    is committed at
    tests/fixtures/automations/activity_logged_schedule_activity.raw.json:246.
    The pre-existing single-dot regex could not match this at all: the
    token fell through untouched to html.escape and rendered as literal
    `{{ ... }}` braces in the recipient's message. This test only proves
    the token is captured into a span instead of being silently skipped —
    it does not assert the exact live label ("Primary Document Record
    (ID)"), which requires resolving a relationship hop this item's
    resolvers don't attempt (see _merge_field_resolvers)."""
    spec = {
        "api_name": "notify_text_merge_multi_segment_test",
        "name": "Notify Text Merge Multi Segment Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "notify",
                "step_type": "notify_member_via_text",
                "order": 0,
                "parent_key": None,
                "action_notify_member_via_text": {
                    "team_member": {"type": "owner"},
                    "content": "{{ custom_objects.primary_document_record.id }}",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_notify_member_via_text"]
    # If the regex still silently skipped the token, this would instead be
    # `<p>{{ custom_objects.primary_document_record.id }}</p>` — plain
    # html-escaped text, no span at all.
    assert action["html_content"] == (
        '<p><span class="kzn-merge-field" '
        'data-merge-field-fallback-label="Primary Document Record Id" '
        'data-merge-field-relationship="custom_objects.primary_document_record.id">'
        "{{ custom_objects.primary_document_record.id }}</span></p>"
    )


def test_manual_trigger_auto_prepended(patch_live_lookups):
    payload = _build(BRANCHING_SPEC)
    types = [t["type"] for t in payload["triggers"]]
    assert types[0] == "manual"
    assert "new_entity_created" in types
    orders = [t["order"] for t in payload["triggers"]]
    assert orders == sorted(orders)


def test_target_object_resolved_to_custom_object_id(patch_live_lookups):
    payload = _build(BRANCHING_SPEC)
    patients = load_fixture("objects/patients.json")
    assert payload["custom_object_id"] == patients["id"]


def test_field_ref_resolved_to_uuid(patch_live_lookups):
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = {
        "api_name": "cfv_test",
        "name": "CFV Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "set_mrn",
                "step_type": "change_field_value",
                "order": 0,
                "parent_key": None,
                "action_change_field_value": {
                    "field_ref": "patients.mrn",
                    "specific_field_value": "A-1",
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_change_field_value"]["actions"][0]
    assert action["field_to_modify"] == mrn["id"]
    assert action["specific_field_value"] == "A-1"


def test_read_shape_specific_field_value_unwrapped(patch_live_lookups):
    """Live GET wraps values as {value: ...}; writes need the bare scalar."""
    spec = {
        "api_name": "cfv_test",
        "name": "CFV Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "set_mrn",
                "step_type": "change_field_value",
                "order": 0,
                "parent_key": None,
                "action_change_field_value": {
                    "field_ref": "mrn",  # unqualified -> target_object
                    "specific_field_value": {"value": "A-2"},
                },
            }
        ],
    }
    payload = _build(spec)
    action = payload["steps"][0]["action_change_field_value"]["actions"][0]
    assert action["specific_field_value"] == "A-2"


def _cfv_spec(field_to_modify):
    return {
        "api_name": "cfv_test",
        "name": "CFV Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "set",
                "step_type": "change_field_value",
                "order": 0,
                "parent_key": None,
                "action_change_field_value": {
                    "field_to_modify": field_to_modify,
                    "specific_field_value": "x",
                },
            }
        ],
    }


def test_bare_uuid_field_reference_passes_through(patch_live_lookups):
    uuid_val = "12345678-1234-4321-8765-123456789abc"
    payload = _build(_cfv_spec(uuid_val))
    action = payload["steps"][0]["action_change_field_value"]["actions"][0]
    assert action["field_to_modify"] == uuid_val


def test_long_api_name_not_mistaken_for_uuid(patch_live_lookups):
    """Regression: strings ≥30 chars used to be passed through as if they
    were UUIDs; now anything that doesn't parse as a UUID resolves as a
    field reference (and fails loudly if unknown)."""
    with pytest.raises(PlanError, match="not found"):
        _build(_cfv_spec("a_long_field_api_name_well_over_thirty_characters"))


def test_trigger_order_collision_raises_at_plan_time(patch_live_lookups):
    """Two triggers with no explicit `order` both default to 0 (see
    `_build_trigger_payload`); the server only catches this on a live PUT/POST
    with `HTTP 400: triggers: Trigger orders must be sequential from 0 to
    N-1` — this must be caught statically instead, at --dry-run time."""
    spec = {
        "api_name": "trigger_order_test",
        "name": "Trigger Order Test",
        "type": "record_based",
        "target_object": "patients",
        "triggers": [
            {"trigger_type": "manual"},
            {
                "trigger_type": "activity_logged",
                "trigger_activity_logged": {"activity_type": "call"},
            },
        ],
        "steps": [],
    }
    with pytest.raises(
        PlanError, match="trigger orders must be sequential from 0 to 1"
    ):
        _build(spec)


def test_trigger_order_explicit_and_sequential_is_fine(patch_live_lookups):
    spec = {
        "api_name": "trigger_order_test",
        "name": "Trigger Order Test",
        "type": "record_based",
        "target_object": "patients",
        "triggers": [
            {"trigger_type": "manual", "order": 0},
            {
                "trigger_type": "activity_logged",
                "order": 1,
                "trigger_activity_logged": {"activity_type": "call"},
            },
        ],
        "steps": [],
    }
    payload = _build(spec)
    assert [t["order"] for t in payload["triggers"]] == [0, 1]


def test_unsupported_trigger_type_raises_with_supported_list(patch_live_lookups):
    spec = {
        "api_name": "sched_test",
        "name": "Sched Test",
        "type": "global",
        "triggers": [
            {
                "trigger_type": "stage_updated",
                "order": 0,
                "trigger_stage_updated": {},
            }
        ],
        "steps": [],
    }
    with pytest.raises(
        PlanError, match="trigger type 'stage_updated' not yet supported"
    ):
        _build(spec)


def test_trigger_schedule(patch_live_lookups):
    """schedule trigger: wire shape is flat and matches the read shape
    exactly (rrule, is_advanced) — confirmed live against a real global
    automation's recurring trigger (2026-07-22)."""
    spec = {
        "api_name": "schedule_trigger_test",
        "name": "Schedule Trigger Test",
        "type": "global",
        "triggers": [
            {
                "trigger_type": "schedule",
                "order": 1,
                "trigger_schedule": {
                    "rrule": "DTSTART:20260801T120000Z\nRRULE:FREQ=DAILY;INTERVAL=1",
                    "is_advanced": False,
                },
            }
        ],
        "steps": [],
    }
    payload = _build(spec)
    schedule_trigger = next(t for t in payload["triggers"] if t["type"] == "schedule")
    assert schedule_trigger["trigger_schedule"] == {
        "rrule": "DTSTART:20260801T120000Z\nRRULE:FREQ=DAILY;INTERVAL=1",
        "is_advanced": False,
    }


def _email_received_spec(block: dict | None) -> dict:
    trigger: dict = {"trigger_type": "email_received", "order": 1}
    goal_trigger: dict = {"trigger_type": "email_received"}
    if block is not None:
        trigger["trigger_email_received_from_contact"] = block
        goal_trigger["trigger_email_received_from_contact"] = block
    return {
        "api_name": "email_received_test",
        "name": "Email Received Test",
        "type": "record_based",
        "target_object": "client_client",
        "triggers": [trigger],
        "steps": [
            {
                "key": "g0",
                "step_type": "goal",
                "order": 0,
                "parent_key": None,
                "step_goal": {"wait_type": "never", "triggers": [goal_trigger]},
            }
        ],
    }


def _email_received_blocks(payload: dict) -> tuple[dict, dict]:
    top = next(t for t in payload["triggers"] if t["type"] == "email_received")
    (goal,) = payload["steps"][0]["step_goal"]["triggers"]
    assert "trigger_email_received" not in top
    assert "trigger_email_received" not in goal
    return (
        top["trigger_email_received_from_contact"],
        goal["trigger_email_received_from_contact"],
    )


@pytest.mark.parametrize(
    "block, wire",
    [
        # Omitted entirely: the server requires receiving_inbox, and `any` is
        # the UI default.
        (None, {"receiving_inbox": "any"}),
        ({}, {"receiving_inbox": "any"}),
        ({"receiving_inbox": "any"}, {"receiving_inbox": "any"}),
        (
            {
                "receiving_inbox": "selected_integrated_inboxes",
                "selected_integrated_inboxes": ["1cf296ff-d456-413b-80bd-dda8576ead55"],
            },
            {
                "receiving_inbox": "selected_integrated_inboxes",
                "selected_integrated_inboxes": ["1cf296ff-d456-413b-80bd-dda8576ead55"],
            },
        ),
    ],
)
def test_trigger_email_received(patch_live_lookups, block, wire):
    """email_received: the config rides under
    `trigger_email_received_from_contact`, at the top level and in a goal.
    The wire matches what saved live (2026-10-14)."""
    assert _email_received_blocks(_build(_email_received_spec(block))) == (wire, wire)


@pytest.mark.parametrize("where", ["trigger", "goal"])
def test_trigger_email_received_selected_needs_an_inbox(patch_live_lookups, where):
    empty = {
        "receiving_inbox": "selected_integrated_inboxes",
        "selected_integrated_inboxes": [],
    }
    spec = _email_received_spec(None)
    if where == "trigger":
        spec["triggers"][0]["trigger_email_received_from_contact"] = empty
    else:
        spec["steps"][0]["step_goal"]["triggers"][0][
            "trigger_email_received_from_contact"
        ] = empty
    with pytest.raises(PlanError, match="needs at least one inbox UUID"):
        _build(spec)


def test_goal_trigger_without_a_type_is_a_plan_error(patch_live_lookups):
    spec = _email_received_spec(None)
    del spec["steps"][0]["step_goal"]["triggers"][0]["trigger_type"]
    with pytest.raises(PlanError, match="goal trigger 0 has no trigger_type"):
        _build(spec)


def test_trigger_email_received_from_contact_is_not_a_type():
    """The config key's spelling is not a trigger type; the server says
    "No such trigger_type" (confirmed live 2026-10-14)."""
    spec = _email_received_spec(None)
    spec["triggers"][0]["trigger_type"] = "email_received_from_contact"
    with pytest.raises(ValueError, match="unknown trigger_type.*'email_received'"):
        AutomationDef.model_validate(spec)


def test_unsupported_step_type_raises_with_supported_list(patch_live_lookups):
    spec = {
        "api_name": "dsa_test",
        "name": "DSA Test",
        "type": "global",
        "steps": [
            {
                "key": "s1",
                "step_type": "delete_scheduled_activity",
                "order": 0,
                "parent_key": None,
            }
        ],
    }
    with pytest.raises(PlanError, match="step type 'delete_scheduled_activity'"):
        _build(spec)


# ---------------------------------------------------------------------------
# code_step output translation
# ---------------------------------------------------------------------------


def _code_step_related_output_spec(**output_overrides: object) -> dict:
    output = {
        "name": "initial_index",
        "output_type": "related_field",
        "field": "13392cb9-25f5-4d7c-beb3-0ee01fe4b9b6",
        "related_field": "12d46c25-6a6b-47fb-851f-8b3e24c7fee1",
        "conflict_resolution": "overwrite",
    }
    output.update(output_overrides)
    return {
        "api_name": "code_step_related_output_test",
        "name": "Code Step Related Output Test",
        "type": "global",
        "steps": [
            {
                "key": "s1",
                "step_type": "code_step",
                "order": 0,
                "parent_key": None,
                "action_code_step": {
                    "script": "outputs.initial_index = '1'",
                    "outputs": [output],
                },
            }
        ],
    }


def test_code_step_related_field_output_translates_ids(patch_live_lookups):
    """A code_step output_type of 'related_field' round-trips: `kizen
    automations steps get` already reads this shape back for live steps, so
    the write side must accept it too (was previously unimplemented — every
    output_type but 'field'/'variable' silently dropped `field`/`related_field`,
    and the server 400s on the resulting empty output)."""
    payload = _build(_code_step_related_output_spec())
    (step,) = payload["steps"]
    (output,) = step["action_code_step"]["outputs"]
    assert output["field"] == {"id": "13392cb9-25f5-4d7c-beb3-0ee01fe4b9b6"}
    assert output["related_field"] == {"id": "12d46c25-6a6b-47fb-851f-8b3e24c7fee1"}
    assert output["conflict_resolution"] == "overwrite"


def test_code_step_related_field_output_requires_field(patch_live_lookups):
    spec = _code_step_related_output_spec()
    spec["steps"][0]["action_code_step"]["outputs"][0].pop("field")
    with pytest.raises(PlanError, match="has no `field`"):
        _build(spec)


def test_code_step_related_field_output_requires_related_field(patch_live_lookups):
    spec = _code_step_related_output_spec()
    spec["steps"][0]["action_code_step"]["outputs"][0].pop("related_field")
    with pytest.raises(PlanError, match="has no `related_field`"):
        _build(spec)


def test_code_step_errors_keep_their_prefix(patch_live_lookups):
    spec = _code_step_related_output_spec()
    output = spec["steps"][0]["action_code_step"]["outputs"][0]
    output.pop("field")
    with pytest.raises(
        PlanError,
        match=rf"^step 's1': code_step output '{output['name']}' is type 'related_field'",
    ):
        _build(spec)


# ---------------------------------------------------------------------------
# plugin_code_step
# ---------------------------------------------------------------------------

PLUGIN_SPEC = {
    "api_name": "plugin_code_steps",
    "name": "Plugin Code Steps",
    "type": "record_based",
    "target_object": "client_client",
    "steps": [
        {
            "key": "summarize",
            "step_type": "plugin_code_step",
            "order": 0,
            "parent_key": None,
            "action_plugin_code_step": {
                "plugin_app_api_name": "example_plugin",
                "action_step_api_name": "summarize_records",
                "inputs": [
                    {
                        "name": "heading",
                        "input_type": "field",
                        "field_ref": "client_client.first_name",
                        "data_type": "string",
                        "is_list": False,
                    },
                    {
                        "name": "prior",
                        "input_type": "variable",
                        "variable_name": "context_text",
                    },
                ],
                "outputs": [
                    {
                        "name": "context",
                        "output_type": "field",
                        "field_ref": "client_client.last_name",
                        "conflict_resolution": "update_if_blank",
                    },
                    {
                        "name": "records_json",
                        "output_type": "variable",
                        "variable_name": "context_text",
                    },
                ],
                "secrets": ["business_id"],
            },
        },
        {
            "key": "convert",
            "step_type": "plugin_code_step",
            "order": 1,
            "parent_key": "summarize",
            "action_plugin_code_step": {
                "plugin_app_api_name": "example_plugin_preview_some_branch",
                "action_step_api_name": "convert_to_pdf",
                "notify_plugin_developer_on_failure": True,
            },
        },
    ],
}


def _plugin_spec(**block_overrides: object) -> dict:
    spec = json.loads(json.dumps(PLUGIN_SPEC))
    spec["steps"][0]["action_plugin_code_step"].update(block_overrides)
    return spec


def test_plugin_code_step_builds_the_wire_block(patch_live_lookups):
    """Wire shape confirmed live 2026-10-05 and 2026-10-14: both api names
    verbatim, no plugin-app id, code_step-shaped inputs/outputs, secrets
    as `[{name}]`."""
    first, second = _build(PLUGIN_SPEC)["steps"]
    assert first["action_plugin_code_step"] == {
        "plugin_app_api_name": "example_plugin",
        "action_step_api_name": "summarize_records",
        "notify_plugin_developer_on_failure": False,
        "secrets": [{"name": "business_id"}],
        "inputs": [
            {
                "name": "heading",
                "input_type": "field",
                "field": {"id": "321de730-826e-46fc-b507-533fb5386701"},
                "data_type": "string",
                "is_list": False,
            },
            {
                "name": "prior",
                "input_type": "variable",
                "variable": {"name": "context_text"},
            },
        ],
        "outputs": [
            {
                "name": "context",
                "output_type": "field",
                "field": {"id": "27d03bf6-ac68-4e68-a0d1-9b0858dc19f9"},
                "conflict_resolution": "update_if_blank",
            },
            {
                "name": "records_json",
                "output_type": "variable",
                "variable": {"name": "context_text"},
            },
        ],
    }
    assert second["action_plugin_code_step"] == {
        "plugin_app_api_name": "example_plugin_preview_some_branch",
        "action_step_api_name": "convert_to_pdf",
        "notify_plugin_developer_on_failure": True,
        "secrets": [],
        "inputs": [],
        "outputs": [],
    }


def test_plugin_code_step_install_config_input_carries_no_source(patch_live_lookups):
    """`business_plugin_config` has no key of its own on the wire, and the
    UI-built read of one carries only nulls for the other sources."""
    spec = _plugin_spec(
        inputs=[
            {
                "name": "settings",
                "input_type": "business_plugin_config",
                "data_type": "string",
                "is_list": False,
            }
        ]
    )
    (inp,) = _build(spec)["steps"][0]["action_plugin_code_step"]["inputs"]
    assert inp == {
        "name": "settings",
        "input_type": "business_plugin_config",
        "data_type": "string",
        "is_list": False,
    }


def test_code_step_still_rejects_install_config_input():
    spec = {
        "api_name": "code_only",
        "name": "Code Only",
        "type": "global",
        "steps": [
            {
                "key": "s1",
                "step_type": "code_step",
                "order": 0,
                "parent_key": None,
                "action_code_step": {
                    "script": "pass",
                    "inputs": [
                        {"name": "settings", "input_type": "business_plugin_config"}
                    ],
                },
            }
        ],
    }
    with pytest.raises(ValueError) as exc:
        AutomationDef.model_validate(spec)
    message = str(exc.value)
    for valid in ("'field'", "'variable'", "'related_field'", "'static_value'"):
        assert valid in message


def test_plugin_code_step_rejects_a_plugin_app_id(patch_live_lookups):
    """LLM steps take `business_plugin_app_id`; plugin steps don't, and the
    server would drop it."""
    spec = _plugin_spec(business_plugin_app_id="11111111-1111-1111-1111-111111111111")
    with pytest.raises(
        PlanError, match="business_plugin_app_id would be silently dropped"
    ):
        _build(spec)


def test_plugin_code_step_requires_the_step_api_name():
    spec = _plugin_spec()
    del spec["steps"][0]["action_plugin_code_step"]["action_step_api_name"]
    with pytest.raises(ValueError, match="action_step_api_name"):
        AutomationDef.model_validate(spec)


def test_plugin_code_step_errors_name_the_step_type(patch_live_lookups):
    spec = _plugin_spec(inputs=[{"name": "file", "input_type": "field"}])
    with pytest.raises(
        PlanError,
        match="plugin_code_step input 'file' is type 'field' but has no "
        "field/field_ref/field_id",
    ):
        _build(spec)


def test_plugin_code_step_id_forwarded(patch_live_lookups):
    spec = json.loads(json.dumps(PLUGIN_SPEC))
    spec["steps"][0]["id"] = "plugin-step-uuid-123"
    first, second = _build(spec)["steps"]
    assert first["id"] == "plugin-step-uuid-123"
    assert "id" not in second


def test_diff_automation_reproduced_plugin_spec_has_no_plugin_step_lines(
    patch_live_lookups,
):
    """The diff isn't empty: the stub object's id, error_notification_email
    and initialize_variable read keys show up, as they do without plugin
    steps (00-inbox/automation-diff-shows-read-only-trigger-keys.md)."""
    raw = load_fixture("automations/plugin_code_steps.raw.json")
    steps = sorted(raw["steps"], key=lambda s: s["order"])
    keys = ["init", "with_fields", "with_variable"]
    spec = {
        "api_name": raw["api_name"],
        "name": raw["name"],
        "type": "record_based",
        "target_object": "client_client",
        "triggers": [
            {
                "trigger_type": "manual",
                "order": 0,
                "id": raw["triggers"][0]["id"],
                "description": raw["triggers"][0]["description"],
            }
        ],
        "steps": [
            {
                "key": key,
                "id": step["id"],
                "step_type": step["step_type"],
                "order": step["order"],
                "parent_key": keys[i - 1] if i else None,
                "description": step["description"],
                "action_on_failure": step["action_on_failure"],
                _block_field_for(step["step_type"]): step[
                    _block_field_for(step["step_type"])
                ],
            }
            for i, (key, step) in enumerate(zip(keys, steps, strict=True))
        ],
    }
    plugin_ids = {s["id"][:8] for s in steps if s["step_type"] == "plugin_code_step"}
    assert len(plugin_ids) == 2
    step_paths = [
        d["path"].split(".")[1]
        for d in diff_automation(spec)["diff"]
        if d["path"].startswith("steps.")
    ]
    assert not [p for p in step_paths if p in plugin_ids or p.startswith("new:")]


@pytest.mark.parametrize("verb", ["activate", "edit_other_step"])
def test_put_rebuilds_plugin_steps_through_the_builder(monkeypatch, verb):
    """`activate`/`deactivate` and every `steps` verb re-PUT the plugin steps
    they don't touch. Until 2026-10-14 those went through `_generic_block`,
    which kept expanded read objects; the server tolerated it then (checked
    live), but the PUT now carries the same blocks a spec would build."""
    from kizen_builder.api import automations as auto_api
    from kizen_builder.tools import automations as auto_tools
    from kizen_builder.tools.planners.automations import _step_plugin_code_step

    raw = load_fixture("automations/plugin_code_steps.raw.json")
    captured: dict = {}
    monkeypatch.setattr(
        auto_api,
        "list_automations",
        lambda client: [{"api_name": raw["api_name"], "id": raw["id"]}],
    )
    monkeypatch.setattr(auto_api, "get_automation", lambda client, automation_id: raw)

    def fake_update_automation(client, automation_id, payload, last_revision=None):
        captured.update(payload)
        return {}

    monkeypatch.setattr(auto_api, "update_automation", fake_update_automation)
    if verb == "activate":
        auto_tools.set_active(raw["api_name"], True, execute=True)
    else:

        def mutate(payload: dict, _raw: dict) -> dict:
            payload["steps"][0]["user_description"] = "edited"
            return {}

        auto_tools.patch_steps(raw["api_name"], mutate, execute=True)

    sent = {s["id"]: s for s in captured["steps"]}
    plugin_steps = [s for s in raw["steps"] if s["step_type"] == "plugin_code_step"]
    assert len(plugin_steps) == 2
    for step in plugin_steps:
        expected = _step_plugin_code_step(
            dict(step["action_plugin_code_step"]), None, None
        )
        assert sent[step["id"]]["action_plugin_code_step"] == expected


# ---------------------------------------------------------------------------
# graph validation (spec-level, no live state needed)
# ---------------------------------------------------------------------------


def test_duplicate_step_keys_rejected():
    spec = {
        **BRANCHING_SPEC,
        "triggers": [],
        "steps": [
            {"key": "a", "step_type": "stop_execution", "order": 0, "parent_key": None},
            {"key": "a", "step_type": "stop_execution", "order": 1, "parent_key": "a"},
        ],
    }
    with pytest.raises(ValueError, match="duplicate step keys"):
        AutomationDef.model_validate(spec)


def test_record_based_requires_target_object():
    with pytest.raises(ValueError, match="target_object"):
        AutomationDef.model_validate(
            {"api_name": "x", "name": "X", "type": "record_based"}
        )


# ---------------------------------------------------------------------------
# planner collision checks
# ---------------------------------------------------------------------------


def test_plan_create_rejects_existing_api_name(patch_live_lookups):
    spec = {
        "api_name": "form_submission",  # exists in automations/list.json
        "name": "Dupe",
        "type": "global",
        "steps": [],
    }
    with pytest.raises(PlanError, match="already exists"):
        plan_create_automation(spec)


def test_plan_update_rejects_unknown_api_name(patch_live_lookups):
    spec = {"api_name": "no_such_auto", "name": "Nope", "type": "global", "steps": []}
    with pytest.raises(PlanError, match="no automation with api_name"):
        plan_update_automation(spec)


def test_plan_update_carries_existing_uuid_and_revision(patch_live_lookups):
    autos = load_fixture("automations/list.json")
    existing = next(a for a in autos if a["api_name"] == "test_two_code_steps")
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.action == "update"
    assert op.existing_uuid == existing["id"]
    assert op.preview["current_revision"] == existing["revision"]


def test_plan_update_payload_is_the_full_put_body(patch_live_lookups):
    """Server-managed fields and last_revision are baked in at plan time, so
    the previewed payload is byte-for-byte what apply PUTs."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.payload["last_revision"] == raw["revision"]
    assert op.payload["folder"] == raw["folder"]
    assert op.payload["priority_rank"] == raw["priority_rank"]


def test_plan_update_strips_variable_ids(patch_live_lookups):
    """Variable ids are stripped everywhere: the server recreates the
    variable set on every PUT regardless, and an id inside an
    initialize_variable step's variable definition crashes the PUT with
    HTTP 500 (observed live). Variables match by name."""
    raw = load_fixture("automations/form_submission.raw.json")
    org_match = next(v for v in raw["variables"] if v["name"] == "org_match")
    spec = {
        "api_name": "form_submission",
        "name": "Form Submission",
        "type": "global",
        "steps": [
            {
                "key": "init",
                "step_type": "initialize_variable",
                "order": 0,
                "parent_key": None,
                "action_initialize_variable": {
                    "variable": {"name": "org_match", "data_type": "string"},
                    "sources": [],
                },
            }
        ],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    step = op.payload["steps"][0]
    assert "id" not in step["action_initialize_variable"]["variable"]
    assert step["action_initialize_variable"]["variable"]["name"] == org_match["name"]
    # the live variable set rides along, normalized to wire form
    # (ids and server-managed timestamps stripped)
    sent = op.payload["variables"]
    assert [v["name"] for v in sent] == [v["name"] for v in raw["variables"]]
    assert all("id" not in v and "created" not in v for v in sent)


# ---------------------------------------------------------------------------
# Webhook trigger auth: kept on update, typed, mirrored as the UI writes it
# (confirmed live 2026-10-27)
# ---------------------------------------------------------------------------

_WEBHOOK_RAW = "automations/webhook_auth_triggers.raw.json"
_SECRET_DESCRIPTIONS = {
    "partner_token": "partner token (api)",
    "partner_basic": "partner basic (api)",
}


@pytest.fixture
def webhook_live(patch_live_lookups, monkeypatch):
    """Serve the four-auth-type fixture as a live automation, with the
    integration secret descriptions the header label resolves against."""
    import kizen_builder.tools.planners.automations as ma

    raw = load_fixture(_WEBHOOK_RAW)
    listing = [*ma.list_automations(), {"id": raw["id"], "api_name": raw["api_name"]}]
    monkeypatch.setattr(ma, "list_automations", lambda: listing)
    monkeypatch.setattr(
        ma.LiveContext,
        "integration_secret_description",
        lambda self, api_name: _SECRET_DESCRIPTIONS.get(api_name),
    )
    return raw


def _webhook_spec(raw: dict, *, auth: bool = False, ids: bool = True) -> dict:
    """A spec reproducing the fixture, with or without the webhook auth keys.
    With auth, headers carry no `html_value` and `secret` is the read shape."""
    unspec = {"id", "webhook_url"} | (
        set() if auth else {"auth_type", "headers", "secret"}
    )
    triggers = []
    for t in raw["triggers"]:
        spec_t = {"trigger_type": t["trigger_type"], "order": t.get("order", 0)}
        if ids:
            spec_t["id"] = t["id"]
        block = t.get("trigger_webhook")
        if block is not None:
            block = {k: v for k, v in block.items() if k not in unspec}
            if auth and block["headers"]:
                block["headers"] = [
                    {k: v for k, v in h.items() if k != "html_value"}
                    for h in block["headers"]
                ]
            spec_t["trigger_webhook"] = block
        triggers.append(spec_t)
    stop = raw["steps"][0]
    return {
        "api_name": raw["api_name"],
        "name": raw["name"],
        "type": "global",
        "triggers": triggers,
        "steps": [
            {
                "key": "stop",
                "id": stop["id"],
                "step_type": "stop_execution",
                "order": 0,
                "parent_key": None,
                "description": stop["description"],
                "action_on_failure": stop["action_on_failure"],
                "action_stop_execution": stop["action_stop_execution"],
            }
        ],
    }


def _webhook_blocks(payload: dict) -> dict[str, dict]:
    return {
        t["trigger_webhook"]["name"]: t["trigger_webhook"]
        for t in payload["triggers"]
        if t["type"] == "webhook"
    }


def _auth(block: dict) -> dict:
    return {k: block[k] for k in ("auth_type", "headers", "secret") if k in block}


_LIVE_HTTP_HEADER = {
    "auth_type": "http_header",
    "headers": [
        {
            "name": "token-name",
            "value": "{{ integration_secret.partner_token }} -merge field",
            "html_value": '<p><span class="kzn-merge-field" '
            'data-merge-field-fallback-label="partner token (api)" '
            'data-merge-field-relationship="integration_secret.partner_token">'
            "{{ integration_secret.partner_token }}</span> -merge field</p>",
        }
    ],
}


def test_plan_update_omitted_webhook_auth_keeps_live_auth(webhook_live):
    """Before 2026-10-27 the PUT went out without auth and Kizen reset every
    webhook to Kizen API auth, changing its URL. The live auth now rides
    along, with the read-shape secret reduced to its api_name."""
    (op,) = plan_update_automation(_webhook_spec(webhook_live)).operations
    blocks = _webhook_blocks(op.payload)
    assert _auth(blocks["webhook_auth_triggers"]) == {"auth_type": "kizen_api"}
    assert _auth(blocks["webhook_auth_triggers_1"]) == _LIVE_HTTP_HEADER
    assert _auth(blocks["webhook_auth_triggers_2"]) == {
        "auth_type": "basic_auth",
        "secret": {"api_name": "partner_basic"},
    }
    assert _auth(blocks["webhook_auth_triggers_3"]) == {"auth_type": "none"}


def test_plan_update_matches_live_webhook_by_name_without_id(webhook_live):
    (op,) = plan_update_automation(_webhook_spec(webhook_live, ids=False)).operations
    blocks = _webhook_blocks(op.payload)
    assert _auth(blocks["webhook_auth_triggers_1"]) == _LIVE_HTTP_HEADER
    assert blocks["webhook_auth_triggers_2"]["auth_type"] == "basic_auth"
    assert blocks["webhook_auth_triggers_3"]["auth_type"] == "none"


def test_plan_update_matches_renamed_webhook_by_id(webhook_live):
    """A renamed webhook that keeps its trigger id still keeps the live auth;
    no live trigger carries the new name, so only the id can match."""
    spec = _webhook_spec(webhook_live)
    for t in spec["triggers"]:
        block = t.get("trigger_webhook")
        if block:
            block["name"] = f"renamed_{block['name']}"
    (op,) = plan_update_automation(spec).operations
    blocks = _webhook_blocks(op.payload)
    assert _auth(blocks["renamed_webhook_auth_triggers_1"]) == _LIVE_HTTP_HEADER
    assert blocks["renamed_webhook_auth_triggers_2"]["auth_type"] == "basic_auth"
    assert blocks["renamed_webhook_auth_triggers_3"]["auth_type"] == "none"


def test_plan_update_explicit_webhook_auth_is_sent_as_written(webhook_live):
    spec = _webhook_spec(webhook_live)
    for t in spec["triggers"]:
        if "trigger_webhook" in t:
            t["trigger_webhook"]["auth_type"] = "kizen_api"
    (op,) = plan_update_automation(spec).operations
    for block in _webhook_blocks(op.payload).values():
        assert _auth(block) == {"auth_type": "kizen_api"}


def test_plan_update_new_webhook_trigger_copies_no_auth(webhook_live):
    spec = _webhook_spec(webhook_live)
    spec["triggers"].append(
        {
            "trigger_type": "webhook",
            "order": 5,
            "trigger_webhook": {"name": "brand_new"},
        }
    )
    (op,) = plan_update_automation(spec).operations
    assert _auth(_webhook_blocks(op.payload)["brand_new"]) == {}


@pytest.mark.parametrize("auth", [False, True], ids=["omitted", "restated"])
def test_diff_automation_shows_no_webhook_auth_lines(webhook_live, auth):
    """A spec that leaves auth out, or restates live's auth (headers without
    `html_value`, `secret` in its read shape), diffs clean on auth."""
    diff = diff_automation(_webhook_spec(webhook_live, auth=auth))["diff"]
    auth_lines = [
        d
        for d in diff
        if any(f".{k}" in d["path"] for k in ("auth_type", "headers", "secret"))
    ]
    assert auth_lines == []


@pytest.mark.parametrize(
    "block, message",
    [
        (
            {"auth_type": "basic_auth", "headers": [{"name": "X-A", "value": "v"}]},
            "trigger_webhook.headers only applies when auth_type is 'http_header'; "
            "Kizen drops it for any other auth type.",
        ),
        (
            {"headers": [{"name": "X-A", "value": "v"}]},
            "trigger_webhook.headers only applies when auth_type is 'http_header'; "
            "Kizen drops it for any other auth type.",
        ),
        (
            {"auth_type": "http_header", "secret": {"api_name": "s"}},
            "trigger_webhook.secret only applies when auth_type is 'basic_auth'; "
            "Kizen drops it for any other auth type.",
        ),
        (
            {"auth_header_name": "X-A", "auth_header_value": "v"},
            "trigger_webhook.auth_header_name, auth_header_value and "
            "html_auth_header_value are no longer read by Kizen. Use auth_type "
            """'http_header' with headers: [{"name": ..., "value": ...}].""",
        ),
    ],
    ids=["headers", "headers-no-auth-type", "secret", "retired-keys"],
)
def test_webhook_auth_validator_messages(block, message):
    import re

    from pydantic import ValidationError

    from kizen_builder.models.spec import TriggerWebhookConfig

    with pytest.raises(ValidationError, match=re.escape(message)):
        TriggerWebhookConfig.model_validate({"name": "hook", **block})


def test_webhook_auth_models_accept_the_read_shape():
    from kizen_builder.models.spec import TriggerWebhookConfig

    raw = load_fixture(_WEBHOOK_RAW)
    for t in raw["triggers"]:
        if "trigger_webhook" in t:
            TriggerWebhookConfig.model_validate(t["trigger_webhook"])


def test_webhook_header_html_value_matches_the_ui_byte_for_byte():
    from kizen_builder.tools.planners.automations import _webhook_auth

    live = _LIVE_HTTP_HEADER["headers"][0]
    block = {
        "auth_type": "http_header",
        "headers": [{"name": live["name"], "value": live["value"]}],
    }
    out = _webhook_auth(
        block,
        lambda ns, path: (
            _SECRET_DESCRIPTIONS[path] if ns == "integration_secret" else None
        ),
    )
    assert out["headers"][0]["html_value"] == live["html_value"]


@pytest.mark.parametrize("html_value", ["<p>kept</p>", "", None])
def test_webhook_header_present_html_value_is_kept(html_value):
    from kizen_builder.tools.planners.automations import _webhook_auth

    header = {
        "name": "X-A",
        "value": "{{ integration_secret.x }}",
        "html_value": html_value,
    }

    def no_lookup(ns, path):
        raise AssertionError("a present html_value must not be derived")

    out = _webhook_auth({"auth_type": "http_header", "headers": [header]}, no_lookup)
    assert out["headers"] == [header]


def test_live_to_payload_reduces_webhook_auth_without_lookups():
    """The read path (`translate`, and every `steps`/`activate`/`move` PUT)
    runs under a context that raises on any lookup; live headers always carry
    `html_value`, so nothing is derived."""
    from kizen_builder.translate import live_to_payload

    blocks = _webhook_blocks(live_to_payload(load_fixture(_WEBHOOK_RAW)))
    assert _auth(blocks["webhook_auth_triggers_1"]) == _LIVE_HTTP_HEADER
    assert blocks["webhook_auth_triggers_2"]["secret"] == {"api_name": "partner_basic"}
    assert "headers" not in blocks["webhook_auth_triggers_2"]
    assert "secret" not in blocks["webhook_auth_triggers_3"]


def test_live_to_payload_drops_retired_webhook_auth_keys():
    from kizen_builder.translate import live_to_payload

    payload = live_to_payload(
        load_fixture("automations/condition_variable_rhs_pipeline.raw.json")
    )
    for block in _webhook_blocks(payload).values():
        assert not [k for k in block if k.startswith(("auth_header", "html_auth"))]


def test_integration_secret_description_is_fetched_once(env_config):
    import respx

    from kizen_builder.tools.planners.automations import LiveContext

    with respx.mock(base_url=env_config.base_url) as router:
        route = router.get("/api/integration-secrets").respond(
            json={
                "results": [
                    {
                        "api_name": "partner_token",
                        "description": "partner token (api)",
                        "obfuscated_value": "****",
                    }
                ],
                "next": None,
            }
        )
        ctx = LiveContext()
        assert (
            ctx.integration_secret_description("partner_token") == "partner token (api)"
        )
        assert ctx.integration_secret_description("missing") is None
    assert route.call_count == 1
    assert "****" not in repr(vars(ctx))


# ---------------------------------------------------------------------------
# `active`: preserve-on-omission (the regression the reporter hit)
# ---------------------------------------------------------------------------


def test_plan_update_omitted_active_preserves_live_active(patch_live_lookups):
    """An update spec that says nothing about `active` must not deactivate a
    running automation. `form_submission` is live `active: true`; the spec
    below omits the key entirely — this is the exact shape the reporter
    hit without realising it (First-Use Feedback §7/§9 row #12)."""
    spec = {
        "api_name": "form_submission",
        "name": "Form Submission",
        "type": "global",
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.payload["active"] is True
    # no live state was disturbed, so the preview shows a plain value
    assert op.preview["active"] is True


def test_plan_update_explicit_false_deactivates_and_preview_names_transition(
    patch_live_lookups,
):
    """A spec that explicitly asks to turn a live-active automation off is a
    legitimate request and still does it — but the preview must name it as a
    transition, not a bare value, so `--dry-run` output is legible."""
    spec = {
        "api_name": "form_submission",
        "name": "Form Submission",
        "type": "global",
        "active": False,
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.payload["active"] is False
    assert op.preview["active"] == "True → False (DEACTIVATES a live automation)"


def test_plan_update_explicit_true_activates_and_preview_names_transition(
    patch_live_lookups,
):
    """The mirror case: explicitly activating a live-inactive automation is
    likewise shown as a transition. `test_two_code_steps` is live
    `active: false`."""
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "active": True,
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.payload["active"] is True
    assert op.preview["active"] == "False → True (ACTIVATES an inactive automation)"


def test_plan_update_active_matches_live_shows_plain_value(patch_live_lookups):
    """When the resolved `active` equals the live value, the preview must not
    raise a false alarm — a plain bool, not a transition string."""
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "active": False,
        "steps": [],
    }
    plan = plan_update_automation(spec)
    (op,) = plan.operations
    assert op.preview["active"] is False


def test_plan_create_omitted_active_defaults_false(patch_live_lookups):
    """A create spec has no live state to preserve — an omitted `active`
    keeps today's documented default of False."""
    spec = {
        "api_name": "brand_new_automation",
        "name": "Brand New Automation",
        "type": "global",
        "steps": [],
    }
    plan = plan_create_automation(spec)
    (op,) = plan.operations
    assert op.payload["active"] is False
    assert op.preview["active"] is False


# ---------------------------------------------------------------------------
# diff_automation: live vs. spec-as-applied, no write
# ---------------------------------------------------------------------------


def test_diff_automation_rejects_unknown_api_name(patch_live_lookups):
    """Same lookup, same error text as `plan_update_automation` — reusing
    that check rather than inventing a new error message."""
    spec = {"api_name": "no_such_auto", "name": "Nope", "type": "global", "steps": []}
    with pytest.raises(PlanError, match="no automation with api_name"):
        diff_automation(spec)


def test_diff_automation_reproduced_spec_is_empty(patch_live_lookups):
    """The golden case: a spec that reproduces `test_two_code_steps`'s live
    shape — hand-authored keys, real ids echoed back, `active` omitted —
    diffs to nothing, even though the fixture's live steps use
    `live_to_payload`-synthesized keys and this spec uses different ones."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    steps = sorted(raw["steps"], key=lambda s: s["order"])
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        # `active` deliberately omitted — resolves to live's `false` via
        # BCLI-016, so this also proves that resolution reaches `diff`.
        "triggers": [
            {
                "trigger_type": "manual",
                "order": 0,
                "id": raw["triggers"][0]["id"],
                "description": raw["triggers"][0]["description"],
            }
        ],
        "steps": [
            {
                "key": "step0",
                "id": steps[0]["id"],
                "step_type": "code_step",
                "order": 0,
                "parent_key": None,
                "description": steps[0]["description"],
                "user_description": steps[0]["user_description"],
                "action_on_failure": "notify_pause",
                "action_code_step": steps[0]["action_code_step"],
            },
            {
                "key": "step1",
                "id": steps[1]["id"],
                "step_type": "code_step",
                "order": 1,
                "parent_key": "step0",
                "description": steps[1]["description"],
                "user_description": steps[1]["user_description"],
                "action_on_failure": "notify_pause",
                "action_code_step": steps[1]["action_code_step"],
            },
        ],
    }
    result = diff_automation(spec)
    assert result["api_name"] == "test_two_code_steps"
    assert result["revision"] == raw["revision"]
    assert result["diff"] == []


def test_diff_automation_surfaces_active_flip(patch_live_lookups):
    """`active` is diffed like any other top-level field — no special-casing
    for the BCLI-016 bug beyond reusing `plan_update_automation`'s own
    resolution: an *explicit* flip in the spec still shows."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    steps = sorted(raw["steps"], key=lambda s: s["order"])
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "active": True,  # live is false
        "triggers": [
            {
                "trigger_type": "manual",
                "order": 0,
                "id": raw["triggers"][0]["id"],
                "description": raw["triggers"][0]["description"],
            }
        ],
        "steps": [
            {
                "key": "step0",
                "id": steps[0]["id"],
                "step_type": "code_step",
                "order": 0,
                "parent_key": None,
                "description": steps[0]["description"],
                "user_description": steps[0]["user_description"],
                "action_on_failure": "notify_pause",
                "action_code_step": steps[0]["action_code_step"],
            },
            {
                "key": "step1",
                "id": steps[1]["id"],
                "step_type": "code_step",
                "order": 1,
                "parent_key": "step0",
                "description": steps[1]["description"],
                "user_description": steps[1]["user_description"],
                "action_on_failure": "notify_pause",
                "action_code_step": steps[1]["action_code_step"],
            },
        ],
    }
    result = diff_automation(spec)
    assert result["diff"] == [{"path": "active", "before": False, "after": True}]


def test_diff_automation_never_writes(patch_live_lookups, monkeypatch):
    """Constraint check: `diff_automation` must not go anywhere near
    `update_automation` (the PUT). Fails loudly if it ever does."""
    from kizen_builder.api import automations as auto_api

    def _boom(*args, **kwargs):
        raise AssertionError("diff_automation must never call update_automation")

    monkeypatch.setattr(auto_api, "update_automation", _boom)
    spec = {
        "api_name": "test_two_code_steps",
        "name": "Test Two Code Steps",
        "type": "global",
        "steps": [],
    }
    diff_automation(spec)  # steps=[] means every live step reports as removed


# ---------------------------------------------------------------------------
# condition filter_config: JSON spec rendering + raw normalization
# ---------------------------------------------------------------------------


@pytest.fixture
def patients_filter_schema():
    """Serve filtering-DSL schema lookups from the patients fixture."""
    from kizen_builder import filtering

    obj = load_fixture("objects/patients.json")

    class _Schema:
        def custom_object(self, api_name):
            return {"id": obj["id"], "name": api_name}

        def get_field(self, obj_id, name):
            for f in obj["fields"]:
                if name in (f["api_name"], f["id"]):
                    return {
                        "name": f["api_name"],
                        "id": f["id"],
                        "field_type": f["field_type"],
                        "is_default": False,
                        "options": f["options"] or [],
                    }
            return None

    filtering.set_default_client(_Schema())
    yield
    filtering.set_default_client(None)


def _condition_spec(filter_config, target_object="patients"):
    spec = {
        "api_name": "cond_fc_test",
        "name": "Cond FC Test",
        "type": "record_based" if target_object else "global",
        "steps": [
            {
                "key": "check",
                "step_type": "condition",
                "order": 0,
                "parent_key": None,
                "step_condition": {
                    "type": "custom_filter",
                    "filter_config": filter_config,
                },
            }
        ],
    }
    if target_object:
        spec["target_object"] = target_object
    return spec


def test_condition_filter_spec_rendered_via_dsl(
    patch_live_lookups, patients_filter_schema
):
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    payload = _build(
        _condition_spec({"all": [{"field": "mrn", "op": "=", "value": "A-1"}]})
    )
    fc = payload["steps"][0]["step_condition"]["filter_config"]
    assert fc["invalid"] is False
    assert fc["query"][0]["id"] == "query-0"
    (clause,) = fc["query"][0]["filters"]
    assert clause["field"] == f'"custom"::{mrn["id"]}'
    assert clause["value"] == "A-1"


def test_condition_filter_spec_requires_target_object(
    patch_live_lookups, patients_filter_schema
):
    with pytest.raises(PlanError, match="resolvable object"):
        _build(
            _condition_spec(
                {"all": [{"field": "mrn", "op": "=", "value": "A-1"}]},
                target_object=None,
            )
        )


def test_condition_raw_filter_config_normalized(patch_live_lookups):
    raw = {
        "and": False,
        "query": [
            {
                "and": True,
                "filters": [
                    {
                        "type": "fields",
                        "field": "name",
                        "subtype": "non_custom",
                        "condition": "=",
                        "value": "x",
                    },
                ],
            }
        ],
    }
    payload = _build(_condition_spec(raw))
    fc = payload["steps"][0]["step_condition"]["filter_config"]
    assert fc["query"][0]["id"] == "query-0"
    assert fc["invalid"] is False


def test_condition_raw_filter_config_null_value_rejected(patch_live_lookups):
    raw = {"query": [{"filters": [{"condition": "is_blank", "value": None}]}]}
    with pytest.raises(PlanError, match="null"):
        _build(_condition_spec(raw))


def test_compiled_rules_carry_the_editor_layout(
    patch_live_lookups, patients_filter_schema
):
    mrn_is = {"all": [{"field": "mrn", "op": "=", "value": "A-1"}]}
    condition = _build(_condition_spec(mrn_is))["steps"][0]["step_condition"]
    search = _build(
        _search_records_spec(
            {
                "custom_object": "patients",
                "filter_type": "custom_filter",
                "filter_config": mrn_is,
                "destination_variable": "found",
            }
        )
    )["steps"][0]["action_search_records"]
    for block in (condition, search):
        (clause,) = block["filter_config"]["query"][0]["filters"]
        assert clause["view_model"][2:] == [
            ["condition", "equals"],
            ["value_type", "static"],
            ["value", "A-1"],
        ]


def test_preview_warns_about_rules_saved_without_the_editor_layout(
    patch_live_lookups, patients_filter_schema
):
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    spec = _condition_spec(
        {
            "all": [
                {"field": "mrn", "op": "=", "value": "A-1"},
                {"field": "mrn", "op": "contains", "value": "A"},
            ]
        }
    )
    raw_rule = {
        "type": "fields_v2",
        "subtype": "custom",
        "field": f'"custom"::{mrn["id"]}',
        "condition": "=",
        "value": "A-1",
    }
    spec["steps"].append(
        {
            "key": "find",
            "step_type": "search_records",
            "order": 1,
            "parent_key": "check",
            "parent_branch": "yes",
            "action_search_records": {
                "custom_object": "patients",
                "filter_type": "custom_filter",
                "filter_config": {"query": [{"filters": [raw_rule]}]},
                "destination_variable": "found",
            },
        }
    )
    assert plan_create_automation(spec).operations[0].preview["warnings"] == [
        "step 'check': the rule on 'mrn' (text, contains) is saved without the "
        "Kizen editor's layout, so the editor may not show it. It still runs.",
        "step 'check': the Kizen editor uses a step's rule layouts only when "
        "every rule has one, so it ignores the layout on this step's other 1 "
        "rule(s) too; a rule on a default field such as 'created' or 'owner' "
        "then shows no field.",
        f"step 'find': the rule on '\"custom\"::{mrn['id']}' (text, =) is saved "
        "without the Kizen editor's layout, so the editor may not show it. It "
        "still runs.",
    ]


@pytest.mark.parametrize("rhs_value", [True, False], ids=["unchanged", "changed"])
def test_update_keeps_the_ui_description_of_an_unchanged_rule(
    patch_live_lookups, rhs_value
):
    """A spec never writes a rule's card text. `update` keeps the live one
    while the rule is as it was live, and `diff` agrees with what it sends."""
    raw = load_fixture("automations/form_submission.raw.json")
    live = next(s for s in raw["steps"] if s["step_type"] == "condition")
    (live_rule,) = live["step_condition"]["filter_config"]["query"][0]["filters"]
    rule = {k: v for k, v in live_rule.items() if k != "description"}
    spec = {
        "api_name": "form_submission",
        "name": "Form Submission",
        "type": "global",
        "steps": [
            {
                "key": "check",
                "id": live["id"],
                "step_type": "condition",
                "order": 0,
                "parent_key": None,
                "step_condition": {
                    "type": "custom_filter",
                    "filter_config": {
                        "query": [{"filters": [{**rule, "rhs_value": rhs_value}]}]
                    },
                },
            }
        ],
    }
    (op,) = plan_update_automation(spec).operations
    (sent,) = op.payload["steps"][0]["step_condition"]["filter_config"]["query"][0][
        "filters"
    ]
    dropped = [
        d
        for d in diff_automation(spec)["diff"]
        if d["path"].endswith(".filters[0].description")
    ]
    if rhs_value is True:
        assert sent["description"] == live_rule["description"]
        assert dropped == []
    else:
        assert "description" not in sent
        assert [d["after"] for d in dropped] == ["<absent>"]


def _variable_spec(rule, *declarations):
    """A condition step comparing to a variable, after root-chain steps that
    declare variables."""
    spec = _condition_spec({"all": [rule]})
    (check,) = spec["steps"]
    steps = [
        {
            "key": f"declare{i}",
            "order": i,
            "parent_key": f"declare{i - 1}" if i else None,
        }
        | step
        for i, step in enumerate(declarations)
    ]
    check.update(order=len(steps), parent_key=steps[-1]["key"] if steps else None)
    spec["steps"] = [*steps, check]
    return spec


def _init_variable(name, data_type, is_array=True):
    return {
        "step_type": "initialize_variable",
        "action_initialize_variable": {
            "sources": [],
            "variable": {"name": name, "data_type": data_type, "is_array": is_array},
        },
    }


def _search_into(name, obj="patients", filter_config=None):
    return {
        "step_type": "search_records",
        "action_search_records": {
            "custom_object": {"name": obj},
            "filter_type": "custom_filter",
            "filter_config": filter_config or NAME_IS_X,
            "destination_variable": name,
        },
    }


def test_condition_compares_a_field_to_a_declared_array_variable(
    patch_live_lookups, patients_filter_schema
):
    payload = _build(
        _variable_spec(
            {"field": "gender", "op": "is_any_of", "variable": "genders"},
            _init_variable("genders", "string"),
        )
    )
    (clause,) = payload["steps"][-1]["step_condition"]["filter_config"]["query"][0][
        "filters"
    ]
    assert clause["value"] == "genders"
    assert clause["value_type"] == "variable"
    assert clause["condition"] == "is_any_of"
    assert [
        "variable",
        {"name": "genders", "is_array": True, "data_type": "string"},
    ] in (clause["view_model"])


@pytest.mark.parametrize(
    ("declarations", "message"),
    [
        (
            [],
            "step 'check': variable 'genders' isn't declared in this "
            "automation (declared arrays: none)",
        ),
        (
            [_init_variable("genders", "string", is_array=False)],
            "step 'check': variable 'genders' isn't an array — comparing a "
            "field to a variable needs an array variable",
        ),
        (
            # Declared by the search, so the error is about its kind.
            [_init_variable("other", "string"), _search_into("genders")],
            "step 'check': field 'gender' (dropdown) can't be compared to "
            "an entity (patients) array variable",
        ),
    ],
)
def test_condition_variable_checked_against_the_spec_declarations(
    patch_live_lookups, patients_filter_schema, declarations, message
):
    spec = _variable_spec(
        {"field": "gender", "op": "is_any_of", "variable": "genders"}, *declarations
    )
    with pytest.raises(PlanError) as e:
        _build(spec)
    assert str(e.value) == message


def test_search_records_filter_rejects_a_variable(
    patch_live_lookups, patients_filter_schema
):
    rule = {"field": "name", "op": "is_any_of", "variable": "found"}
    spec = _variable_spec(
        {"field": "gender", "op": "is_blank"},
        _search_into("found", filter_config={"all": [rule]}),
    )
    with pytest.raises(PlanError, match="only works in a condition step"):
        _build(spec)


# A condition with no rules is accepted by the API, but the Kizen UI shows an
# error on the step (reported 2026-09-29), so the spec and patch paths reject it.


def _with_condition(spec, step_condition, **step_fields):
    (step,) = spec["steps"]
    step["step_condition"] = step_condition
    step.update(step_fields)
    return spec


@pytest.mark.parametrize(
    ("step_condition", "message"),
    [
        (
            {"type": "custom_filter", "filter_config": {"and": False, "query": []}},
            "'check' has no filter rules",
        ),
        (
            {"type": "custom_filter", "filter_config": {"query": [{"filters": []}]}},
            r"'check' has an empty rule group \(query\[0\]\)",
        ),
        ({}, "'check' has no filter rules"),
        ({"type": "in_group"}, r"'check' \(in_group\) has no group_ids"),
        (
            {"type": "not_in_group", "group_ids": []},
            r"'check' \(not_in_group\) has no group_ids",
        ),
    ],
    ids=["empty_query", "empty_group", "empty_block", "in_group", "not_in_group"],
)
def test_condition_without_rules_rejected(patch_live_lookups, step_condition, message):
    spec = _with_condition(_condition_spec(None), step_condition)
    with pytest.raises(PlanError, match=message):
        _build(spec)


def test_skipped_condition_without_rules_rejected(patch_live_lookups):
    spec = _with_condition(
        _condition_spec(None),
        {"type": "custom_filter", "filter_config": {"query": []}},
        should_skip_execution=True,
        continue_with_branch="yes",
    )
    with pytest.raises(PlanError, match="'check' has no filter rules"):
        _build(spec)


def test_in_group_condition_with_group_ids_builds(patch_live_lookups):
    spec = _with_condition(
        _condition_spec(None), {"type": "in_group", "group_ids": ["g-1"]}
    )
    block = _build(spec)["steps"][0]["step_condition"]
    assert block["group_ids"] == ["g-1"]


_LIVE_PAYLOAD = {
    "name": "Cond",
    "api_name": "cond",
    "type": "record_based",
    "steps": [],
}


def test_steps_add_condition_without_rules_rejected(patch_live_lookups):
    from kizen_builder.tools.automations import build_wire_step

    step = {
        "key": "new_check",
        "step_type": "condition",
        "step_condition": {"type": "custom_filter", "filter_config": {"query": []}},
    }
    with pytest.raises(PlanError, match="'new_check' has no filter rules"):
        build_wire_step(step, _LIVE_PAYLOAD, "patients")


def test_steps_edit_condition_without_rules_rejected(patch_live_lookups):
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {
        "step_condition": {"type": "custom_filter", "filter_config": {"query": []}}
    }
    with pytest.raises(PlanError, match="'check' has no filter rules"):
        normalize_step_patch(patch, "check", "condition", _LIVE_PAYLOAD, "patients")


_GENDERS_DECLARED = {
    **_LIVE_PAYLOAD,
    "steps": [
        {
            "key": "declare",
            "type": "initialize_variable",
            "action_initialize_variable": {
                "sources": [],
                "variable": {
                    "name": "genders",
                    "data_type": "string",
                    "is_array": True,
                },
            },
        }
    ],
}
_GENDERS_RULE = {
    "type": "custom_filter",
    "filter_config": {
        "all": [{"field": "gender", "op": "is_any_of", "variable": "genders"}]
    },
}


def _steps_add(payload):
    from kizen_builder.tools.automations import build_wire_step

    step = {"key": "check", "step_type": "condition", "step_condition": _GENDERS_RULE}
    return build_wire_step(step, payload, "patients")["step_condition"]


def _steps_edit(payload):
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {"step_condition": _GENDERS_RULE}
    return normalize_step_patch(patch, "check", "condition", payload, "patients")[
        "step_condition"
    ]


@pytest.mark.parametrize("patch_step", [_steps_add, _steps_edit])
def test_steps_add_and_edit_see_the_live_variable_declarations(
    patch_live_lookups, patients_filter_schema, patch_step
):
    (clause,) = patch_step(_GENDERS_DECLARED)["filter_config"]["query"][0]["filters"]
    assert clause["value"] == "genders"
    with pytest.raises(PlanError) as e:
        patch_step(_LIVE_PAYLOAD)
    assert str(e.value) == (
        "step 'check': variable 'genders' isn't declared in this automation "
        "(declared arrays: none)"
    )


@pytest.mark.parametrize(
    "step_type, block, key",
    [
        (
            "create_related_entity",
            {"new_entity_name": "x", "new_setting": 1},
            "new_setting",
        ),
        # Exempt on a live read, but a patch that sets it would change nothing.
        (
            "modify_related_entities",
            {"automation_variables": ["v"]},
            "automation_variables",
        ),
    ],
)
def test_steps_edit_refuses_a_key_the_builder_does_not_read(step_type, block, key):
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {_block_field_for(step_type): block}
    with pytest.raises(
        PlanError, match=rf"^step 'edited' \S+: {key} would be silently dropped"
    ):
        normalize_step_patch(patch, "edited", step_type, _LIVE_PAYLOAD, "patients")


def test_steps_edit_without_condition_block_not_checked(patch_live_lookups):
    """A live automation that already carries an empty condition can still be
    edited, e.g. to rename the step, without the patch tripping the check."""
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {"user_description": "renamed"}
    assert (
        normalize_step_patch(patch, "check", "condition", _LIVE_PAYLOAD, "patients")
        == patch
    )


# ---------------------------------------------------------------------------
# search_records step — confirmed live (2026-07-22): custom_object/
# filter_groups/destination_variable are each {"id"|"name": ...} objects on
# write, per the public /api/docs/schema SearchRecordsRequest — NOT the bare
# scalars most other action steps in this file use for analogous fields.
# ---------------------------------------------------------------------------


def _search_records_spec(action_config, target_object=None):
    spec = {
        "api_name": "search_records_test",
        "name": "Search Records Test",
        "type": "record_based" if target_object else "global",
        "steps": [
            {
                "key": "s1",
                "step_type": "search_records",
                "order": 0,
                "parent_key": None,
                "action_search_records": action_config,
            }
        ],
    }
    if target_object:
        spec["target_object"] = target_object
    return spec


def test_step_search_records_all_records(patch_live_lookups):
    patients = load_fixture("objects/patients.json")
    payload = _build(
        _search_records_spec(
            {
                "custom_object": "patients",
                "filter_type": "all_records",
                "destination_variable": "found_patients",
                "destination_variable_resolution": "overwrite",
            }
        )
    )
    action = payload["steps"][0]["action_search_records"]
    assert action["custom_object"] == {"id": patients["id"]}
    assert action["destination_variable"] == {"name": "found_patients"}
    assert action["filter_type"] == "all_records"
    assert action["destination_variable_resolution"] == "overwrite"
    assert action["filter_config"] is None
    assert action["filter_groups"] == []


def test_step_search_records_filter_groups_by_id(patch_live_lookups):
    """filter_groups entries already carrying an id (a live read's expanded
    shape, or an author-supplied UUID) pass through without a saved-filter-
    group lookup."""
    group_id = "18fea0cc-7ecd-4085-a7ad-dc8337782abd"
    payload = _build(
        _search_records_spec(
            {
                "custom_object": "patients",
                "filter_type": "in_group",
                "filter_groups": [
                    group_id,
                    {"id": "22222222-2222-4222-8222-222222222222", "name": "Other"},
                ],
                "destination_variable": "found_patients",
            }
        )
    )
    action = payload["steps"][0]["action_search_records"]
    assert action["filter_groups"] == [
        {"id": group_id},
        {"id": "22222222-2222-4222-8222-222222222222"},
    ]


def test_step_search_records_custom_filter_uses_own_object_not_target_object(
    patch_live_lookups, patients_filter_schema
):
    """A global automation has no target_object, but search_records' own
    custom_object still resolves filter_config field names correctly."""
    patients = load_fixture("objects/patients.json")
    mrn = next(f for f in patients["fields"] if f["api_name"] == "mrn")
    payload = _build(
        _search_records_spec(
            {
                "custom_object": "patients",
                "filter_type": "custom_filter",
                "filter_config": {"all": [{"field": "mrn", "op": "=", "value": "A-1"}]},
                "destination_variable": "found_patients",
            },
            target_object=None,
        )
    )
    fc = payload["steps"][0]["action_search_records"]["filter_config"]
    (clause,) = fc["query"][0]["filters"]
    assert clause["field"] == f'"custom"::{mrn["id"]}'


def test_step_search_records_unknown_object_errors(patch_live_lookups):
    with pytest.raises(PlanError, match="not found"):
        _build(
            _search_records_spec(
                {
                    "custom_object": "no_such_object",
                    "filter_type": "all_records",
                    "destination_variable": "found_patients",
                }
            )
        )


# ---------------------------------------------------------------------------
# schedule_activity — association_configs (ticket 20260720-173206) and
# assigned_to (ticket 20260722-153607) write-dialect fixes.
# ---------------------------------------------------------------------------


def _schedule_activity_spec(action_config, target_object="patients"):
    return {
        "api_name": "schedule_activity_test",
        "name": "Schedule Activity Test",
        "type": "record_based",
        "target_object": target_object,
        "steps": [
            {
                "key": "s1",
                "step_type": "schedule_activity",
                "order": 0,
                "parent_key": None,
                "action_schedule_activity": action_config,
            }
        ],
    }


def test_step_schedule_activity_sends_note_and_html_note(patch_live_lookups):
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "note": "Plain note",
                "html_note": "<p><strong>Rich</strong> note</p>",
            }
        )
    )
    action = payload["steps"][0]["action_schedule_activity"]
    assert action["note"] == "Plain note"
    assert action["html_note"] == "<p><strong>Rich</strong> note</p>"


def test_step_schedule_activity_association_configs_author_shape(patch_live_lookups):
    patients = load_fixture("objects/patients.json")
    encounters_field = next(
        f for f in patients["fields"] if f["api_name"] == "encounters"
    )
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "association_configs": [
                    {
                        "object": "patients",
                        "source": "related_field",
                        "relationship_field_ref": "encounters",
                    },
                    {"object": "patients", "source": "none"},
                ],
            }
        )
    )
    configs = payload["steps"][0]["action_schedule_activity"]["association_configs"]
    assert configs[0] == {
        "custom_object_id": patients["id"],
        "association_source": "related_field",
        "relationship_field_id": encounters_field["id"],
    }
    assert configs[1] == {
        "custom_object_id": patients["id"],
        "association_source": "none",
    }


def test_step_schedule_activity_association_configs_read_shape_passthrough(
    patch_live_lookups,
):
    """A live read's expanded association_configs dict (custom_object/
    association_source/relationship_field/automation_variable) is accepted
    as-is, not just the friendlier author shape."""
    patients = load_fixture("objects/patients.json")
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "association_configs": [
                    {
                        "custom_object": {
                            "id": patients["id"],
                            "object_name": "Patients",
                        },
                        "association_source": "none",
                        "relationship_field": None,
                        "automation_variable": None,
                    }
                ],
            }
        )
    )
    configs = payload["steps"][0]["action_schedule_activity"]["association_configs"]
    assert configs == [
        {"custom_object_id": patients["id"], "association_source": "none"}
    ]


def test_steps_edit_schedule_activity_block_from_steps_get_is_unchanged(
    patch_live_lookups,
):
    """The block `steps get` prints carries `custom_object_id`; re-applying it
    unchanged must not null it (the server 400s on a null)."""
    import copy

    from kizen_builder.tools.automations import normalize_step_patch
    from kizen_builder.translate import live_to_payload

    raw = load_fixture("automations/activity_logged_schedule_activity.raw.json")
    payload = live_to_payload(raw)
    step = next(s for s in payload["steps"] if s["type"] == "schedule_activity")
    block = step["action_schedule_activity"]
    patch = {"action_schedule_activity": copy.deepcopy(block)}
    out = normalize_step_patch(
        patch, step["key"], "schedule_activity", payload, raw["custom_object"]["name"]
    )
    assert out["action_schedule_activity"] == block


@pytest.mark.parametrize(
    "entry",
    [
        {
            "custom_object_id": "8fa0a1cb-1111-4111-8111-111111111111",
            "association_source": "related_field",
            "relationship_field_id": "8fa0a1cb-2222-4222-8222-222222222222",
        },
        {
            "custom_object_id": "8fa0a1cb-1111-4111-8111-111111111111",
            "association_source": "record_variable",
            "automation_variable_name": "x",
        },
    ],
    ids=["related_field", "record_variable"],
)
def test_step_schedule_activity_association_configs_write_shape_passthrough(
    patch_live_lookups, entry
):
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "association_configs": [entry],
            }
        )
    )
    configs = payload["steps"][0]["action_schedule_activity"]["association_configs"]
    assert configs == [entry]


def test_step_schedule_activity_association_config_without_object_rejected(
    patch_live_lookups,
):
    spec = _schedule_activity_spec(
        {
            "activity_type_id": "11111111-1111-4111-8111-111111111111",
            "association_configs": [{"object": "patients"}, {"source": "none"}],
        }
    )
    with pytest.raises(PlanError) as e:
        _build(spec)
    assert str(e.value) == "step 's1': " + (
        "schedule_activity association_configs entry 2 names no object: set "
        "'object' to the associated object's api_name, or 'custom_object_id' "
        "to its UUID."
    )


def test_step_schedule_activity_assigned_to_specific_role_uses_role_id(
    patch_live_lookups,
):
    """Regression for ticket 20260722-153607: the builder used to emit a
    bare `role` key, which the server silently ignores — `role_id` is the
    real wire key (confirmed against the public /api/docs/schema
    ScheduleActivityAssignmentRequest and a live 400)."""
    role_id = "33333333-3333-4333-8333-333333333333"
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "assigned_to": {"assignment_type": "specific_role", "role": role_id},
            }
        )
    )
    assigned_to = payload["steps"][0]["action_schedule_activity"]["assigned_to"]
    assert assigned_to == {"assignment_type": "specific_role", "role_id": role_id}


def test_step_schedule_activity_assigned_to_variable_is_wrapped(patch_live_lookups):
    """`variable` must be an {"name"|"id": ...} object per the public schema's
    VariableRequest — not the bare name string this codebase's other
    variable references use elsewhere."""
    payload = _build(
        _schedule_activity_spec(
            {
                "activity_type_id": "11111111-1111-4111-8111-111111111111",
                "assigned_to": {
                    "assignment_type": "team_member_from_variable",
                    "variable": "assignee_var",
                },
            }
        )
    )
    assigned_to = payload["steps"][0]["action_schedule_activity"]["assigned_to"]
    assert assigned_to == {
        "assignment_type": "team_member_from_variable",
        "variable": {"name": "assignee_var"},
    }


# ---------------------------------------------------------------------------
# move_to_folder — regression for ticket 20260720-174419: the write dialect
# is a bare `folder_id`, not the `folder: {id, name}` shape a live read
# returns (that shape PUTs fine but the server silently ignores it — the
# folder never actually changes; confirmed live 2026-07-22).
# ---------------------------------------------------------------------------


def test_move_to_folder_sends_folder_id_not_folder(monkeypatch):
    from kizen_builder.api import automations as auto_api
    from kizen_builder.tools import automations as auto_tools

    raw = dict(load_fixture("automations/two_code_steps.raw.json"))
    captured: dict = {}

    monkeypatch.setattr(
        auto_api,
        "list_automations",
        lambda client: [{"api_name": raw["api_name"], "id": raw["id"]}],
    )
    monkeypatch.setattr(auto_api, "get_automation", lambda client, automation_id: raw)

    def fake_update_automation(client, automation_id, payload, last_revision=None):
        captured.update(payload)
        return {}

    monkeypatch.setattr(auto_api, "update_automation", fake_update_automation)

    result = auto_tools.move_to_folder(
        raw["api_name"],
        "22222222-2222-4222-8222-222222222222",
        "Target Folder",
        execute=True,
    )

    assert captured["folder_id"] == "22222222-2222-4222-8222-222222222222"
    assert "folder" not in captured
    assert result["after"] == {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "Target Folder",
    }


# ---------------------------------------------------------------------------
# Branch groups, parallel branches, skipped conditions
# ---------------------------------------------------------------------------


def _s(key: str, parent: str | None, step_type: str, order: int, **kw: object) -> dict:
    return {
        "key": key,
        "parent_key": parent,
        "step_type": step_type,
        "order": order,
        "action_on_failure": "notify_pause",
        **kw,
    }


def _merged_condition_spec(**condition: object) -> dict:
    """The shape of fixture `merged_condition`: yes/no lanes rejoin at a
    merge step, and the tail hangs off the merge."""
    return {
        "api_name": "merged_condition",
        "name": "Merged Condition",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            _s(
                "check",
                None,
                "condition",
                0,
                is_branch_group_initiator=True,
                step_condition={"type": "custom_filter", "filter_config": NAME_IS_X},
                **condition,
            ),
            _s("wait_yes", "check", "delay", 1, parent_branch="yes"),
            _s("wait_no", "check", "delay", 2, parent_branch="no"),
            _s("merge", "check", "merge_branches", 3),
            _s("tail", "merge", "delay", 4),
        ],
    }


def test_merged_condition_payload(patch_live_lookups):
    by_key = {s["key"]: s for s in _build(_merged_condition_spec())["steps"]}
    assert by_key["check"]["is_branch_group_initiator"] is True
    assert by_key["merge"] == {
        "key": "merge",
        "parent_key": "check",
        "parent_yes_no": "",
        "parent_condition": "",
        "type": "merge_branches",
        "prefix": "step",
        "order": 3,
        "user_description": "",
        "action_on_failure": "notify_pause",
        "should_skip_execution": False,
        "goal_type": False,
    }
    assert by_key["tail"]["parent_key"] == "merge"
    for key in ("wait_yes", "merge", "tail"):
        assert "is_branch_group_initiator" not in by_key[key]


def test_merged_goal_payload(patch_live_lookups):
    spec = _merged_condition_spec()
    spec["steps"][0] = _s(
        "check",
        None,
        "goal",
        0,
        is_branch_group_initiator=True,
        step_goal={"wait_type": "delay", "delay_type": "minutes", "delay_amount": 5},
    )
    by_key = {s["key"]: s for s in _build(spec)["steps"]}
    assert by_key["check"]["type"] == "goal"
    assert by_key["check"]["is_branch_group_initiator"] is True
    assert by_key["merge"]["parent_key"] == "check"


@pytest.mark.parametrize("path", ["yes", "no"])
def test_skipped_condition_payload(patch_live_lookups, path):
    spec = _merged_condition_spec(
        should_skip_execution=True,
        continue_with_branch=path,
        error_notification_severity_level="warning",
    )
    check = _build(spec)["steps"][0]
    assert check["should_skip_execution"] is True
    assert check["continue_with_branch"] == path
    assert check["error_notification_severity_level"] == "warning"


def test_skipped_condition_without_branch_rejected():
    spec = _merged_condition_spec(should_skip_execution=True)
    with pytest.raises(ValueError, match="skipped condition 'check' needs"):
        AutomationDef.model_validate(spec)


@pytest.mark.parametrize(
    "step_type", ["branch", "merge_branches", "next_or_finish", "exit_loop"]
)
def test_configless_step_with_config_block_rejected(step_type):
    spec = _merged_condition_spec()
    merge = next(s for s in spec["steps"] if s["step_type"] == "merge_branches")
    merge.update(
        step_type=step_type, step_delay={"minutes": 5, "value_origin": "static"}
    )
    with pytest.raises(ValueError, match=f"'{step_type}' takes no config block"):
        AutomationDef.model_validate(spec)


def test_merged_condition_without_merge_step_fails_plan(patch_live_lookups):
    spec = _merged_condition_spec()
    spec["steps"] = spec["steps"][:3]
    with pytest.raises(PlanError, match="initiator 'check' has 0 merge_branches"):
        plan_create_automation(spec)


def test_merge_under_non_initiator_fails_plan(patch_live_lookups):
    spec = _merged_condition_spec()
    spec["steps"][0]["is_branch_group_initiator"] = False
    spec["steps"].append(_s("merge_2", "check", "merge_branches", 5))
    with pytest.raises(PlanError) as e:
        plan_create_automation(spec)
    for key in ("merge", "merge_2"):
        assert f"merge_branches step '{key}' has parent 'check'" in str(e.value)


def _loop_spec() -> dict:
    """The shape of fixture `loops`, built from a spec."""

    def var(key: str, parent: str | None, name: str, is_array: bool, order: int):
        variable = {"name": name, "data_type": "string", "is_array": is_array}
        block = {"sources": [], "variable": variable}
        if is_array:
            block["array_aggregation_mode"] = "add_all"
        return _s(
            key, parent, "initialize_variable", order, action_initialize_variable=block
        )

    def loop(key: str, parent: str, array: str, order: int):
        block = {"array_variable": array, "item_variable": f"{array}_item"}
        return _s(
            key, parent, "loop", order, is_branch_group_initiator=True, step_loop=block
        )

    return {
        "api_name": "loops",
        "name": "Loops",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            var("arr0", None, "arr0", True, 0),
            var("arr0_item", "arr0", "arr0_item", False, 1),
            var("arr1", "arr0_item", "arr1", True, 2),
            var("arr1_item", "arr1", "arr1_item", False, 3),
            loop("each", "arr1_item", "arr0", 4),
            _s(
                "check",
                "each",
                "condition",
                5,
                is_branch_group_initiator=True,
                step_condition={"type": "custom_filter", "filter_config": NAME_IS_X},
            ),
            _s("wait", "check", "delay", 6, parent_branch="yes"),
            _s("leave", "check", "exit_loop", 7, parent_branch="no"),
            _s("merge", "check", "merge_branches", 8),
            _s("next", "each", "next_or_finish", 9),
            loop("each_again", "next", "arr1", 10),
            _s("body", "each_again", "delay", 11),
            _s("next_again", "each_again", "next_or_finish", 12),
            _s("tail", "next_again", "delay", 13),
        ],
    }


def test_loop_spec_payload(patch_live_lookups):
    by_key = {s["key"]: s for s in _build(_loop_spec())["steps"]}
    assert by_key["each"]["step_loop"] == {
        "loop_type": "for_each_item",
        "array_variable": "arr0",
        "item_variable": "arr0_item",
    }
    assert by_key["each"]["is_branch_group_initiator"] is True
    for key in ("leave", "next"):
        assert not [k for k in by_key[key] if k.startswith("step_")]
        assert [k for k in by_key[key] if k.startswith("action_")] == [
            "action_on_failure"
        ]


def test_loop_spec_needs_its_step_loop():
    spec = _loop_spec()
    del spec["steps"][4]["step_loop"]
    with pytest.raises(ValueError, match="'loop' requires an 'step_loop' block"):
        AutomationDef.model_validate(spec)


def test_broken_loop_spec_fails_plan(patch_live_lookups):
    spec = _loop_spec()
    spec["steps"][9]["parent_key"] = "check"
    with pytest.raises(PlanError) as e:
        plan_create_automation(spec)
    assert "loop 'each' has 0 next_or_finish children" in str(e.value)
    assert "next_or_finish step 'next' has parent 'check'" in str(e.value)


def _var(key, parent, name, order, data_type="string", is_array=False, sources=()):
    variable = {"name": name, "data_type": data_type, "is_array": is_array}
    if data_type == "entity":
        variable["data_subtype"] = load_fixture("objects/patients.json")["id"]
    block = {"sources": list(sources), "variable": variable}
    if is_array:
        block["array_aggregation_mode"] = "add_all"
    return _s(
        key, parent, "initialize_variable", order, action_initialize_variable=block
    )


def _loop(key, parent, array, item, order, **kw):
    block = {"array_variable": array, "item_variable": item}
    return _s(key, parent, "loop", order, step_loop=block, **kw)


_STRINGS = [{"source_type": "static", "source_subtype": "string", "value": ["a", "b"]}]


def _authored_loop_spec(steps: list[dict], record_based: bool = True) -> dict:
    for i, s in enumerate(steps):
        s["order"] = i
    spec = {
        "api_name": "authored_loop",
        "name": "Authored Loop",
        "type": "record_based" if record_based else "global",
        "steps": steps,
    }
    if record_based:
        spec["target_object"] = "patients"
    return spec


def _string_loop_steps() -> list[dict]:
    return [
        _var("arr", None, "arr", 0, is_array=True, sources=_STRINGS),
        _var("item", "arr", "arr_item", 0),
        _loop("each", "item", "arr", "arr_item", 0),
        _s("body", "each", "delay", 0),
        _s("next", "each", "next_or_finish", 0),
        _s("after", "next", "delay", 0),
    ]


def _nested_loops(
    depth: int, root_parent: str | None = None, arrays: list[str] | None = None
) -> list[dict]:
    """`depth` loops, each the body of the one before, and an `exit_loop` in
    the innermost body. Each loop iterates its entry in `arrays` (by default
    a different array per level) through its own item variable."""
    arrays = arrays or [f"arr{n}" for n in range(depth)]
    steps: list[dict] = []
    parent = root_parent
    for name in dict.fromkeys(arrays):
        steps.append(_var(name, parent, name, 0, is_array=True, sources=_STRINGS))
        parent = name
    for n in range(depth):
        steps.append(_var(f"item{n}", parent, f"item{n}", 0))
        parent = f"item{n}"
    closers = []
    for n, name in enumerate(arrays):
        steps.append(_loop(f"l{n}", parent, name, f"item{n}", 0))
        closers.append(_s(f"n{n}", f"l{n}", "next_or_finish", 0))
        parent = f"l{n}"
    steps.append(_s("leave", parent, "exit_loop", 0))
    return steps + closers[::-1]


def _built_steps(spec: dict) -> dict[str, dict]:
    return {s["key"]: s for s in _build(spec)["steps"]}


def _preview(spec: dict) -> dict:
    return plan_create_automation(spec).operations[0].preview


def _fan_out_lines(preview: dict) -> list[str]:
    """The preview's warnings minus the ones about raw condition rules."""
    return [w for w in preview.get("warnings", []) if "parallel children" in w]


@pytest.mark.parametrize("flag", [None, False, True])
def test_loop_is_always_sent_as_an_initiator(patch_live_lookups, flag):
    """The server stores true on a loop whatever is sent (confirmed live
    2026-10-27), so the builder sends true and `diff` shows no flip."""
    steps = _string_loop_steps()
    if flag is not None:
        steps[2]["is_branch_group_initiator"] = flag
    by_key = _built_steps(_authored_loop_spec(steps, record_based=False))
    assert by_key["each"]["is_branch_group_initiator"] is True
    assert [k for k, s in by_key.items() if "is_branch_group_initiator" in s] == [
        "each"
    ]


def test_string_array_loop_spec(patch_live_lookups):
    spec = _authored_loop_spec(_string_loop_steps(), record_based=False)
    by_key = _built_steps(spec)
    assert by_key["each"]["step_loop"] == {
        "loop_type": "for_each_item",
        "array_variable": "arr",
        "item_variable": "arr_item",
    }
    assert by_key["item"]["action_initialize_variable"]["sources"] == []
    assert by_key["after"]["parent_key"] == "next"
    assert "warnings" not in _preview(spec)


def test_record_array_loop_over_search_records(patch_live_lookups):
    steps = [
        _var("recs", None, "recs", 0, data_type="entity", is_array=True),
        _var("rec", "recs", "recs_item", 0, data_type="entity"),
        _s(
            "find",
            "rec",
            "search_records",
            0,
            action_search_records={
                "custom_object": "patients",
                "filter_type": "all_records",
                "destination_variable": "recs",
            },
        ),
        _loop("each", "find", "recs", "recs_item", 0),
        _s("body", "each", "delay", 0),
        _s("next", "each", "next_or_finish", 0),
    ]
    spec = _authored_loop_spec(steps)
    by_key = _built_steps(spec)
    assert by_key["find"]["action_search_records"]["destination_variable"] == {
        "name": "recs"
    }
    assert by_key["each"]["step_loop"]["array_variable"] == "recs"
    assert "warnings" not in _preview(spec)


def test_loop_on_a_condition_yes_branch(patch_live_lookups):
    steps = _string_loop_steps()[:5]
    check = _s(
        "check",
        "item",
        "condition",
        0,
        step_condition={"type": "custom_filter", "filter_config": NAME_IS_X},
    )
    steps[2].update(parent_key="check", parent_branch="yes")
    other = _s("other", "check", "delay", 0, parent_branch="no")
    spec = _authored_loop_spec([*steps[:2], check, *steps[2:], other])
    by_key = _built_steps(spec)
    assert by_key["each"]["parent_yes_no"] == "yes"
    assert by_key["each"]["is_branch_group_initiator"] is True
    assert _fan_out_lines(_preview(spec)) == []


def test_nested_loops_with_an_exit_loop(patch_live_lookups):
    spec = _authored_loop_spec(_nested_loops(2), record_based=False)
    by_key = _built_steps(spec)
    assert by_key["l1"]["parent_key"] == "l0"
    assert by_key["leave"]["parent_key"] == "l1"
    assert (by_key["n0"]["parent_key"], by_key["n1"]["parent_key"]) == ("l0", "l1")
    assert "warnings" not in _preview(spec)


def test_loop_with_two_body_lanes_still_warns(patch_live_lookups):
    steps = [*_string_loop_steps(), _s("other_lane", "each", "delay", 0)]
    assert _preview(_authored_loop_spec(steps))["warnings"] == [
        "step 'each' has 2 parallel children but is a 'loop', not a 'branch'; "
        "put a 'branch' step in front of the fan-out"
    ]


def test_loop_nested_past_three_is_refused(patch_live_lookups):
    """Kizen's builder holds three levels. The API accepts more, so this is
    the CLI's own rule, checked at plan time."""
    assert "warnings" not in _preview(_authored_loop_spec(_nested_loops(3), False))
    with pytest.raises(PlanError) as e:
        _build(_authored_loop_spec(_nested_loops(5), False))
    problems = str(e.value).split("\n  - ")[1:]
    assert problems == [
        f"loop 'l{n}' is nested {n + 1} levels deep; Kizen's builder allows at "
        "most three. The API accepts deeper loops, but the CLI refuses them so "
        "the automation stays editable in the UI."
        for n in (3, 4)
    ]


def test_loop_after_a_next_or_finish_is_not_nested_in_that_loop(patch_live_lookups):
    """`again` follows the innermost loop of three, so it is three deep, not four."""
    steps = [
        _var("again_arr", None, "again_arr", 0, is_array=True, sources=_STRINGS),
        _var("again_item", "again_arr", "again_item", 0),
        *_nested_loops(3, root_parent="again_item"),
        _loop("again", "n2", "again_arr", "again_item", 0),
        _s("again_next", "again", "next_or_finish", 0),
    ]
    assert "warnings" not in _preview(_authored_loop_spec(steps, False))


def _same_array_problem(key: str, array: str, outer: str) -> str:
    return (
        f"loop '{key}' iterates '{array}', which loop '{outer}' around it already "
        "iterates; Kizen's builder needs a different array variable at each level. "
        "The API accepts it, but the CLI refuses it so the automation stays "
        "editable in the UI."
    )


@pytest.mark.parametrize(
    ("arrays", "problem"),
    [
        (["a", "a"], ("l1", "a", "l0")),
        (["a", "b", "a"], ("l2", "a", "l0")),
    ],
    ids=["outer_and_inner", "two_levels_down"],
)
def test_nested_loop_over_an_array_already_iterated_is_refused(
    patch_live_lookups, arrays, problem
):
    spec = _authored_loop_spec(_nested_loops(len(arrays), arrays=arrays), False)
    with pytest.raises(PlanError) as e:
        _build(spec)
    assert str(e.value).split("\n  - ")[1:] == [_same_array_problem(*problem)]


def test_loops_over_one_array_in_sequence_build(patch_live_lookups):
    """The second loop hangs off the first's next_or_finish: not nested."""
    variables, loop = _string_loop_steps()[:2], _string_loop_steps()[2:]
    loop[0]["parent_key"] = "again_item"
    steps = [
        *variables,
        _var("again_item", "item", "again_item", 0),
        *loop,
        _loop("again", "after", "arr", "again_item", 0),
        _s("again_body", "again", "delay", 0),
        _s("again_next", "again", "next_or_finish", 0),
    ]
    by_key = _built_steps(_authored_loop_spec(steps, False))
    assert by_key["again"]["step_loop"]["array_variable"] == "arr"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("continue_with_branch", "maybe"),
        ("error_notification_severity_level", "fatal"),
        ("is_initiator", True),
    ],
)
def test_new_step_fields_rejected_when_malformed(field, value):
    spec = _merged_condition_spec(**{field: value})
    with pytest.raises(ValueError, match=field):
        AutomationDef.model_validate(spec)


def _branching_test_spec() -> dict:
    """Step for step, the UI-built fixture `branching_test`: an unmerged
    branch, a merged one with a branch after its merge, and merged and
    unmerged branches nested inside that. References are the fixture's own
    UUIDs, so the spec diffs against the fixture itself."""
    raw = load_fixture("automations/branching_test.raw.json")
    live = sorted(raw["steps"], key=lambda s: s["order"])
    automation_id = live[3]["action_start_automation"]["automations"][0]["id"]
    string_obj = live[12]["action_search_records"]["custom_object"]["id"]
    uuid_obj = live[17]["action_search_records"]["custom_object"]["id"]
    create_obj = live[18]["action_create_related_entity"]["target_custom_object"]
    script = live[11]["action_code_step"]["script"]

    def array_var(name: str, data_type: str, mode: str) -> dict:
        return {
            "variable": {"name": name, "data_type": data_type, "is_array": True},
            "sources": [],
            "array_aggregation_mode": mode,
            "deduplicate_array": False,
        }

    def search(obj: str, var: str) -> dict:
        return {
            "custom_object": obj,
            "filter_type": "all_records",
            "destination_variable": var,
        }

    code = {"runtime": "python-3-13", "script": script, "inputs": [], "outputs": []}
    steps = [
        _s("init_strings", None, "initialize_variable", 0,
           action_initialize_variable=array_var("string_arr", "string", "add_all")),
        _s("init_uuids", "init_strings", "initialize_variable", 1,
           action_initialize_variable=array_var("uuid_arr", "uuid", "add_first_non_blank")),
        _s("parallel", "init_uuids", "branch", 2, user_description="Not Merging Branches"),
        _s("start_other", "parallel", "start_automation", 3,
           action_start_automation={"record_source": "global", "automation_ids": [automation_id]}),
        _s("set_strings", "parallel", "update_variable", 4, action_update_variable={
            "variable": {"name": "string_arr", "data_type": "string"},
            "sources": [{"source_type": "static", "source_subtype": "string",
                         "value": ["Stuff", "Things"], "array_separator": "comma"}],
            "array_aggregation_mode": "add_all",
            "deduplicate_array": False,
        }),
        _s("group", "set_strings", "branch", 5, is_branch_group_initiator=True,
           user_description="Branches that merge"),
        _s("group_merge", "group", "merge_branches", 6),
        _s("after_merge", "group_merge", "branch", 7, is_branch_group_initiator=True),
        _s("after_merge_merge", "after_merge", "merge_branches", 8),
        _s("done", "after_merge_merge", "stop_execution", 9,
           action_stop_execution={"action": "stop_and_complete", "notify": False}),
        _s("nested_unmerged", "after_merge", "branch", 10),
        _s("code_a", "nested_unmerged", "code_step", 11, action_code_step=code),
        _s("search_strings", "nested_unmerged", "search_records", 12,
           action_search_records=search(string_obj, "string_arr")),
        _s("nested_merged", "after_merge", "branch", 13, is_branch_group_initiator=True),
        _s("nested_merged_merge", "nested_merged", "merge_branches", 14),
        _s("code_b", "nested_merged", "code_step", 15, action_code_step=code),
        _s("llm", "nested_merged", "call_llm", 16, user_description="things",
           action_call_llm={
               "model_name": "kizen/pro",
               "prompt": "stuff",
               "merge_field_validation": "default_to_unknown",
               "is_advanced": False,
               "destinations": [{"variable": "string_arr",
                                 "conflict_resolution": "overwrite_except_null"}],
           }),
        _s("search_uuids", "group", "search_records", 17,
           action_search_records=search(uuid_obj, "uuid_arr")),
        _s("create", "group", "create_related_entity", 18,
           action_create_related_entity={
               "new_entity_name": "thing 1",
               "new_entity_name_html": "<p>thing 1</p>",
               "new_entity_owner_type": "newly_assigned_owner",
               "new_entity_owner_sub_type": "round_robin_any_role",
               "target_custom_object": create_obj["id"],
           }),
    ]  # fmt: skip
    return {
        "api_name": "branching_test",
        "name": "Branching Test",
        "type": "global",
        "steps": steps,
    }


def test_branching_test_transcription_diffs_clean(patch_live_lookups, monkeypatch):
    """The spec reproduces the UI-built graph. What's left is noise any spec
    shows: server-written descriptions, and the plugin app the server fills
    in for a `kizen/*` model."""
    import kizen_builder.tools.planners.automations as ma

    raw = load_fixture("automations/branching_test.raw.json")
    monkeypatch.setattr(
        ma, "list_automations", lambda: [{"api_name": raw["api_name"], "id": raw["id"]}]
    )
    structural = [
        d
        for d in diff_automation(_branching_test_spec())["diff"]
        if d["path"].rsplit(".", 1)[-1] not in ("description", "business_plugin_app_id")
    ]
    assert structural == []


def test_branching_test_payload_shape(patch_live_lookups):
    steps = {s["key"]: s for s in _build(_branching_test_spec())["steps"]}
    initiators = {k for k, s in steps.items() if s.get("is_branch_group_initiator")}
    assert initiators == {"group", "after_merge", "nested_merged"}
    for key in ("parallel", "group", "group_merge", "nested_unmerged"):
        blocks = set(steps[key]) - {"action_on_failure"}
        assert not any(k.startswith(("action_", "step_")) for k in blocks)
    assert steps["after_merge"]["parent_key"] == "group_merge"


def test_fan_out_without_branch_step_warns(patch_live_lookups):
    spec = _branching_test_spec()
    for s in spec["steps"]:
        if s["key"] == "parallel":
            s.update(step_type="delay", step_delay={})
    preview = plan_create_automation(spec).operations[0].preview
    assert preview["warnings"] == [
        "step 'parallel' has 2 parallel children but is a 'delay', not a "
        "'branch'; put a 'branch' step in front of the fan-out"
    ]


@pytest.mark.parametrize(
    "spec", [_merged_condition_spec(), BRANCHING_SPEC], ids=["merged", "yes_no"]
)
def test_no_fan_out_warning_for_branch_or_condition_children(patch_live_lookups, spec):
    preview = plan_create_automation(spec).operations[0].preview
    assert _fan_out_lines(preview) == []


# ---------------------------------------------------------------------------
# Honoured keys: a spec key the builder doesn't read is a plan-time error
# ---------------------------------------------------------------------------


def _one_step_spec(step_type: str, block: dict) -> dict:
    return {
        "api_name": "honoured_test",
        "name": "Honoured Test",
        "type": "record_based",
        "target_object": "patients",
        "steps": [
            {
                "key": "only",
                "step_type": step_type,
                "order": 0,
                "parent_key": None,
                _block_field_for(step_type): block,
            }
        ],
    }


_CRE_WITH_FIELDS_TO_SET = {
    "target_object": "patients",
    "new_entity_name": "Follow-up",
    "fields_to_set": {"status": "open"},
}


def test_unread_step_key_is_a_plan_error(patch_live_lookups):
    """`fields_to_set` exists nowhere in the planner and on no Kizen
    environment: it used to plan, apply and report success without reaching
    the wire."""
    spec = _one_step_spec("create_related_entity", _CRE_WITH_FIELDS_TO_SET)
    with pytest.raises(PlanError) as e:
        _build(spec)
    msg = str(e.value)
    assert "step 'only' action_create_related_entity" in msg
    assert "fields_to_set" in msg
    assert "target_custom_object" in msg  # the honoured set is listed


def test_unread_trigger_key_is_a_plan_error(patch_live_lookups):
    spec = {
        **_one_step_spec("delay", {"days": 1}),
        "triggers": [
            {
                "trigger_type": "activity_logged",
                "order": 1,
                "trigger_activity_logged": {"activity_type_name": "Call"},
            }
        ],
    }
    with pytest.raises(PlanError, match="trigger_activity_logged: activity_type_name"):
        _build(spec)


def test_pass_through_builders_accept_any_key(patch_live_lookups):
    """`delay` forwards its whole block, so the CLI drops nothing."""
    payload = _build(_one_step_spec("delay", {"days": 1, "not_a_delay_key": 2}))
    assert payload["steps"][0]["step_delay"]["not_a_delay_key"] == 2


@pytest.mark.parametrize("flags", [["--dry-run"], ["--dry-run", "--json"]])
def test_unread_key_fails_before_the_plan_renders(patch_live_lookups, flags):
    from typer.testing import CliRunner

    import kizen_builder.cli as cli

    spec = _one_step_spec("create_related_entity", _CRE_WITH_FIELDS_TO_SET)
    result = CliRunner().invoke(
        cli.app, ["automations", "create", *flags], input=json.dumps(spec)
    )
    assert result.exit_code == 1
    assert "plan error:" in result.stderr
    assert "fields_to_set" in result.stderr
    assert result.stdout == ""


def test_create_related_entity_reads_target_object_as_target_custom_object(
    patch_live_lookups,
):
    patients = load_fixture("objects/patients.json")
    spec = _one_step_spec(
        "create_related_entity",
        {"target_object": "patients", "new_entity_name": "Follow-up"},
    )
    action = _build(spec)["steps"][0]["action_create_related_entity"]
    assert action["target_custom_object"] == patients["id"]


def test_create_related_entity_field_values_is_a_plan_error(patch_live_lookups):
    """Kizen accepts `field_values` and stores nothing (confirmed live
    2026-10-05), so it is an unread key like any other."""
    from kizen_builder.tools.automations import build_wire_step

    block = {
        "target_custom_object": "patients",
        "new_entity_name": "Follow-up",
        "field_values": [{"field": "status", "value": "open"}],
    }
    spec = _one_step_spec("create_related_entity", block)
    with pytest.raises(PlanError, match="field_values would be silently dropped"):
        _build(spec)
    with pytest.raises(PlanError, match="field_values would be silently dropped"):
        build_wire_step(spec["steps"][0], _LIVE_PAYLOAD, "patients")


def test_create_related_entity_needs_only_target_custom_object(patch_live_lookups):
    from kizen_builder.tools.automations import build_wire_step

    patients = load_fixture("objects/patients.json")
    spec = _one_step_spec(
        "create_related_entity",
        {"target_custom_object": "patients", "new_entity_name": "Follow-up"},
    )
    action = _build(spec)["steps"][0]["action_create_related_entity"]
    assert action["target_custom_object"] == patients["id"]
    step = build_wire_step(spec["steps"][0], _LIVE_PAYLOAD, "patients")
    action = step["action_create_related_entity"]
    assert action["target_custom_object"] == patients["id"]


def test_create_related_entity_without_a_target_names_both_keys():
    spec = _one_step_spec("create_related_entity", {"new_entity_name": "Follow-up"})
    with pytest.raises(ValueError, match="'target_custom_object'.*'target_object'"):
        AutomationDef.model_validate(spec)


def test_every_wired_builder_declares_its_keys():
    undeclared = [
        f"{kind} {name}"
        for kind, registry in (("step", _STEP_BUILDERS), ("trigger", _TRIGGER_BUILDERS))
        for name, builder in registry.items()
        if not hasattr(builder, "honoured_keys")
    ]
    assert undeclared == [], (
        f"declare each builder's keys with @honours(...) or @honours_all: {undeclared}"
    )


@pytest.mark.parametrize(
    "builder",
    sorted(
        {
            b
            for b in [*_STEP_BUILDERS.values(), *_TRIGGER_BUILDERS.values()]
            if b.honoured_keys is not None
        },
        key=lambda b: b.__name__,
    ),
    ids=lambda b: b.__name__,
)
def test_declared_keys_match_what_the_builder_reads(builder):
    """A key read but not declared is rejected although it works; a key
    declared but no longer read slips through and is dropped."""
    assert set(builder.honoured_keys) == builder_keys.declarable(builder.__name__)


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "automations").glob("*.raw.json")),
    ids=lambda p: p.name.removesuffix(".raw.json"),
)
def test_every_captured_read_key_is_classified(path):
    """A read key that is null in every capture today would make `translate`
    refuse the automation the day it is set. Declare it: honoured if the
    builder should carry it, `unsent_on_read` if it must not be sent."""
    assert builder_keys.unclassified_read_keys(json.loads(path.read_text())) == set()


# No captured fixture has these; test_uncaptured_builder_output_replans_unchanged
# re-plans their output instead.
_UNCAPTURED = {"notify_member_via_email", "notify_member_via_text"}

# Wired triggers no captured fixture has: their wire shape is unverified.
_UNCAPTURED_TRIGGERS = {"form_submitted", "survey_submitted"}


def _wire_blocks() -> list:
    from kizen_builder.translate import live_to_payload

    params = []
    for path in sorted((FIXTURES / "automations").glob("*.raw.json")):
        raw = json.loads(path.read_text())
        payload = live_to_payload(raw)
        target = (raw.get("custom_object") or {}).get("name")
        name = path.name.removesuffix(".raw.json")
        for kind, entries, builders, cfg in (
            ("step", payload["steps"], _STEP_BUILDERS, _block_field_for),
            ("trigger", payload["triggers"], _TRIGGER_BUILDERS, trigger_config_field),
        ):
            for e in entries:
                if e["type"] in builders:
                    block = e.get(cfg(e["type"])) or {}
                    id_ = f"{name}-{e['key']}"
                    params.append(
                        pytest.param(kind, e["type"], block, payload, target, id=id_)
                    )
    return params


_WIRE_BLOCKS = _wire_blocks()


def test_wire_blocks_cover_every_wired_type():
    steps = {p.values[1] for p in _WIRE_BLOCKS if p.values[0] == "step"}
    triggers = {p.values[1] for p in _WIRE_BLOCKS if p.values[0] == "trigger"}
    assert set(_STEP_BUILDERS) - steps == _UNCAPTURED
    assert set(_TRIGGER_BUILDERS) - triggers == _UNCAPTURED_TRIGGERS


def test_builder_output_passes_the_check():
    """Idempotence: every wire block the translator emits from a captured
    automation — what `steps get` and `automations show` print — re-plans
    without a rejected key, so a copied block can be re-applied."""
    for p in _WIRE_BLOCKS:
        kind, block_type, block, _, _ = p.values
        builders = _STEP_BUILDERS if kind == "step" else _TRIGGER_BUILDERS
        _reject_unhonoured_keys(builders[block_type], block, p.id)


@pytest.mark.parametrize(
    "step_type, block",
    [
        (
            "notify_member_via_email",
            {
                "team_member": {"type": "owner"},
                "cc_team_member": {"type": "employee", "employee": {"id": "e-1"}},
                "email_template_id": "11111111-1111-1111-1111-111111111111",
            },
        ),
        (
            "notify_member_via_text",
            {
                "team_member": {"type": "employee", "employee_id": "e-1"},
                "content": "Reminder",
                "message_template_id": "22222222-2222-2222-2222-222222222222",
            },
        ),
    ],
)
def test_uncaptured_builder_output_replans_unchanged(
    patch_live_lookups, step_type, block
):
    cfg = _block_field_for(step_type)
    wire = _build(_one_step_spec(step_type, block))["steps"][0][cfg]
    assert _build(_one_step_spec(step_type, wire))["steps"][0][cfg] == wire


# Intended differences between a captured wire block and its rebuild, as
# {(step_or_trigger_type, dotted_path): reason}. List indices read as `[]`.
_REBUILD_ALLOWED_DIFFS: dict[tuple[str, str], str] = {}


def _diff_paths(a: object, b: object, path: str = "") -> set[str]:
    """Dotted paths where `a` and `b` differ; an absent key counts."""
    if isinstance(a, dict) and isinstance(b, dict):
        return {
            p
            for k in a.keys() | b.keys()
            for p in (
                _diff_paths(a[k], b[k], f"{path}.{k}" if path else k)
                if k in a and k in b
                else {f"{path}.{k}" if path else k}
            )
        }
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return {
            p for x, y in zip(a, b, strict=True) for p in _diff_paths(x, y, f"{path}[]")
        }
    return set() if a == b else {path}


def _rebuild(kind, block_type, wire, payload, target):
    """The builder call `normalize_step_patch` makes, with no live lookups."""
    import copy

    from kizen_builder.tools.automations import _payload_auto
    from kizen_builder.translate import _SHIM_CTX

    if kind == "trigger":
        return _TRIGGER_BUILDERS[block_type](copy.deepcopy(wire), _SHIM_CTX)
    auto = _payload_auto(payload, target)
    return _STEP_BUILDERS[block_type](copy.deepcopy(wire), auto, _SHIM_CTX)


@pytest.mark.parametrize("kind, block_type, wire, payload, target", _WIRE_BLOCKS)
def test_builders_rebuild_wire_blocks_unchanged(
    kind, block_type, wire, payload, target
):
    """What `steps get` prints, re-applied through `steps edit`'s builder
    call, rebuilds value for value — so a copied block is a no-op."""
    allowed = {p for (t, p) in _REBUILD_ALLOWED_DIFFS if t == block_type}
    diffs = _diff_paths(wire, _rebuild(kind, block_type, wire, payload, target))
    unexpected = sorted(diffs - allowed)
    assert not unexpected, f"rebuild differs at {unexpected}"


def test_rebuild_allowed_diffs_all_occur():
    seen = {
        (p.values[1], d)
        for p in _WIRE_BLOCKS
        for d in _diff_paths(p.values[2], _rebuild(*p.values))
    }
    stale = sorted(set(_REBUILD_ALLOWED_DIFFS) - seen)
    assert not stale, f"_REBUILD_ALLOWED_DIFFS entries match no difference: {stale}"


# ---------------------------------------------------------------------------
# initialize_variable variable: data_type, is_array, unknown keys
# (confirmed live 2026-10-14: an unknown data_type is a bare HTTP 500)
# ---------------------------------------------------------------------------


def _variable_type_message() -> str:
    from typing import get_args

    from kizen_builder.models.spec.automations_shared import (
        AutomationVariableDataType,
    )

    *rest, last = (f"'{t}'" for t in get_args(AutomationVariableDataType))
    return f"Input should be {', '.join(rest)} or {last}"


def test_variable_data_type_matches_the_schema_snapshot():
    from typing import get_args

    from kizen_builder.models.spec.automations_shared import (
        AutomationVariableDataType,
    )
    from tests.drift.contracts import load_snapshot

    entry = load_snapshot()["contracts"]["schema:AutomationVariableRequest"]
    enum = entry["properties"]["data_type"]
    assert enum.startswith("ExecutionVariableDataTypeEnum{")
    assert set(get_args(AutomationVariableDataType)) == set(
        enum[enum.index("{") + 1 : -1].split(",")
    )


def _fixture_variable() -> dict:
    """An entity array variable as a live read carries it: all 11 keys."""
    raw = load_fixture("automations/condition_variable_rhs_pipeline.raw.json")
    return next(
        s["action_initialize_variable"]["variable"]
        for s in raw["steps"]
        if s["step_type"] == "initialize_variable"
        and "is_array_item_variable" in s["action_initialize_variable"]["variable"]
    )


def _edit_variable(variable: dict) -> dict:
    from kizen_builder.tools.automations import normalize_step_patch

    patch = {"action_initialize_variable": {"variable": variable, "sources": []}}
    return normalize_step_patch(
        patch, "init", "initialize_variable", _LIVE_PAYLOAD, "patients"
    )


_BAD_VARIABLES = [
    pytest.param({"name": "tags", "data_type": t}, "data_type", id=t)
    for t in ("text", "list", "object")
] + [
    pytest.param(
        {"name": "tags", "data_type": "string", "is_array": "maybe"},
        "is_array",
        id="is_array-maybe",
    ),
    pytest.param(
        {"name": "tags", "data_type": "string", "isArray": True},
        "isArray",
        id="isArray-typo",
    ),
]

_GOOD_VARIABLES = [
    pytest.param({"name": "owner", "data_type": "employee"}, id="employee"),
    pytest.param(
        {"name": "branch", "data_type": "entity", "data_subtype": "obj-uuid"},
        id="entity",
    ),
    pytest.param({"name": "inbox", "data_type": "email"}, id="email"),
]


@pytest.mark.parametrize("path", ["create", "steps edit"])
@pytest.mark.parametrize("variable, field", _BAD_VARIABLES)
def test_bad_variable_is_a_spec_error(patch_live_lookups, path, variable, field):
    from pydantic import ValidationError

    spec = _one_step_spec("initialize_variable", {"variable": variable})
    with pytest.raises(ValidationError) as e:
        if path == "create":
            AutomationDef.model_validate(spec)
        else:
            _edit_variable(variable)
    (err,) = e.value.errors()
    assert err["loc"][-3:] == ("action_initialize_variable", "variable", field)
    if field == "data_type":
        assert err["msg"] == _variable_type_message()


@pytest.mark.parametrize("variable", [*_GOOD_VARIABLES, pytest.param(None, id="read")])
def test_good_variable_plans_on_both_paths(patch_live_lookups, variable):
    variable = variable or _fixture_variable()
    spec = _one_step_spec("initialize_variable", {"variable": dict(variable)})
    built = _build(spec)["steps"][0]["action_initialize_variable"]["variable"]
    edited = _edit_variable(dict(variable))["action_initialize_variable"]["variable"]
    assert built["data_type"] == edited["data_type"] == variable["data_type"]


# ---------------------------------------------------------------------------
# update_variable value_resolution (confirmed live 2026-10-14)
# ---------------------------------------------------------------------------


def _array_update_block(**extra: object) -> dict:
    return {
        "variable": {"name": "tags", "data_type": "string"},
        "sources": [
            {
                "source_type": "static",
                "source_subtype": "string",
                "value": ["b"],
                "array_separator": "comma",
            }
        ],
        "array_aggregation_mode": "add_all",
        "deduplicate_array": False,
        **extra,
    }


@pytest.mark.parametrize("value", ["add_only", "overwrite", "overwrite_except_null"])
def test_update_variable_forwards_value_resolution(patch_live_lookups, value):
    spec = _one_step_spec(
        "update_variable", _array_update_block(value_resolution=value)
    )
    block = _build(spec)["steps"][0]["action_update_variable"]
    assert block["value_resolution"] == value
    assert block["array_aggregation_mode"] == "add_all"
    assert block["deduplicate_array"] is False


@pytest.mark.parametrize("extra", [{}, {"value_resolution": None}])
def test_update_variable_omits_null_value_resolution(patch_live_lookups, extra):
    spec = _one_step_spec("update_variable", _array_update_block(**extra))
    assert "value_resolution" not in _build(spec)["steps"][0]["action_update_variable"]


@pytest.mark.parametrize(
    "key, value, allowed",
    [
        (
            "value_resolution",
            "append",
            "'add_only', 'overwrite' or 'overwrite_except_null'",
        ),
        ("array_aggregation_mode", "all", "'add_first_non_blank' or 'add_all'"),
    ],
)
def test_update_variable_rejects_unknown_option(key, value, allowed):
    spec = _one_step_spec("update_variable", _array_update_block(**{key: value}))
    with pytest.raises(ValueError, match=allowed):
        AutomationDef.model_validate(spec)


def test_initialize_variable_rejects_value_resolution(patch_live_lookups):
    block = _array_update_block(value_resolution="add_only")
    block["variable"]["is_array"] = True
    with pytest.raises(PlanError, match="only update_variable does"):
        _build(_one_step_spec("initialize_variable", block))


def test_live_update_variable_with_null_value_resolution_replans_unchanged(
    patch_live_lookups,
):
    """Post-2026-10-13 reads carry `value_resolution: null` on every
    update_variable. A spec copied from that read plans to the same wire
    block the translator produces, with no value_resolution key."""
    from kizen_builder.translate import live_to_payload

    raw = load_fixture("automations/update_variable_goto_branching.raw.json")
    step = next(s for s in raw["steps"] if s["step_type"] == "update_variable")
    step["action_update_variable"]["value_resolution"] = None
    live = next(s for s in live_to_payload(raw)["steps"] if s["id"] == step["id"])

    spec = _one_step_spec("update_variable", step["action_update_variable"])
    planned = _build(spec)["steps"][0]["action_update_variable"]
    assert "value_resolution" not in planned
    assert planned == live["action_update_variable"]


def _init_then_update_spec(*, is_array: bool | None, defined_as: str = "tags") -> dict:
    variable: dict = {"name": defined_as, "data_type": "string"}
    if is_array is not None:
        variable["is_array"] = is_array
    spec = _one_step_spec("initialize_variable", {"variable": variable, "sources": []})
    spec["steps"][0]["key"] = "init"
    spec["steps"].append(
        {
            "key": "bump",
            "step_type": "update_variable",
            "order": 1,
            "parent_key": "init",
            "action_update_variable": _array_update_block(value_resolution="add_only"),
        }
    )
    return spec


@pytest.mark.parametrize("is_array", [False, None])
def test_update_variable_rejects_value_resolution_on_scalar(
    patch_live_lookups, is_array
):
    with pytest.raises(PlanError, match="'bump' sets value_resolution on 'tags'"):
        _build(_init_then_update_spec(is_array=is_array))


def test_update_variable_value_resolution_on_array_plans(patch_live_lookups):
    steps = _build(_init_then_update_spec(is_array=True))["steps"]
    assert steps[1]["action_update_variable"]["value_resolution"] == "add_only"


def test_update_variable_value_resolution_on_undefined_variable_passes(
    patch_live_lookups,
):
    """No definition to check against: the server decides."""
    steps = _build(_init_then_update_spec(is_array=False, defined_as="other"))["steps"]
    assert steps[1]["action_update_variable"]["value_resolution"] == "add_only"


def test_value_resolution_check_reads_raw_wire_steps():
    """``steps add`` can hand the check a live payload's raw step dicts."""
    from types import SimpleNamespace

    from kizen_builder.tools.planners.automations import (
        _scalar_value_resolution_problem,
    )

    def init(is_array):
        return {
            "step_type": "initialize_variable",
            "action_initialize_variable": {
                "variable": {
                    "name": "tags",
                    "data_type": "string",
                    "is_array": is_array,
                }
            },
        }

    block = {"variable": "tags", "value_resolution": "add_only"}
    scalar = SimpleNamespace(steps=[init(False), {"step_type": "delay"}])
    assert "isn't an array variable" in _scalar_value_resolution_problem(
        "bump", block, scalar
    )
    array = SimpleNamespace(steps=[init(True)])
    assert _scalar_value_resolution_problem("bump", block, array) is None


def test_translate_keeps_value_resolution_on_scalar_variable():
    from kizen_builder.translate import live_to_payload

    raw = load_fixture("automations/update_variable_goto_branching.raw.json")
    step = next(s for s in raw["steps"] if s["step_type"] == "update_variable")
    step["action_update_variable"]["value_resolution"] = "add_only"
    live = next(s for s in live_to_payload(raw)["steps"] if s["id"] == step["id"])
    assert live["action_update_variable"]["value_resolution"] == "add_only"


def _patient_field_id(api_name: str) -> str:
    patients = load_fixture("objects/patients.json")
    return next(f["id"] for f in patients["fields"] if f["api_name"] == api_name)


@pytest.mark.parametrize(
    "ref", ["mrn", "patients.mrn", "db489b3e-a880-41f7-90b9-312b8cfdd02b"]
)
def test_create_related_entity_context_entity_field_resolves(patch_live_lookups, ref):
    spec = _one_step_spec(
        "create_related_entity",
        {
            "target_object": "encounters",
            "new_entity_name": "Follow-up",
            "context_entity_field": ref,
        },
    )
    action = _build(spec)["steps"][0]["action_create_related_entity"]
    assert action["context_entity_field"] == _patient_field_id("mrn")


def _field_ref_trigger_spec(
    trigger_type: str, block: dict, *, goal: bool, target: str | None = "patients"
) -> dict:
    trigger = {"trigger_type": trigger_type, f"trigger_{trigger_type}": block}
    spec: dict = {
        "api_name": "field_ref_trigger_test",
        "name": "Field Ref Trigger Test",
        "type": "record_based" if target else "global",
        "target_object": target,
        "steps": [],
    }
    if goal:
        spec["steps"] = [
            {
                "key": "g0",
                "step_type": "goal",
                "order": 0,
                "parent_key": None,
                "step_goal": {"wait_type": "never", "triggers": [trigger]},
            }
        ]
    else:
        spec["triggers"] = [{**trigger, "order": 1}]
    return spec


def _field_ref_trigger_block(payload: dict, trigger_type: str, goal: bool) -> dict:
    if goal:
        (t,) = payload["steps"][0]["step_goal"]["triggers"]
    else:
        t = next(t for t in payload["triggers"] if t["type"] == trigger_type)
    return t[f"trigger_{trigger_type}"]


_FIELD_REF_TRIGGER_CASES = [
    ("field_updated", "mrn", {"fire_on_create": True}),
    ("on_or_around_date", "date_of_birth", {"date_offset": "on_day_and_time"}),
]


@pytest.mark.parametrize("goal", [False, True], ids=["trigger", "goal"])
@pytest.mark.parametrize("trigger_type, field, extra", _FIELD_REF_TRIGGER_CASES)
@pytest.mark.parametrize("qualified", [False, True], ids=["bare", "dotted"])
def test_trigger_field_ref_resolves(
    patch_live_lookups, trigger_type, field, extra, goal, qualified
):
    ref = f"patients.{field}" if qualified else field
    spec = _field_ref_trigger_spec(trigger_type, {"field_ref": ref, **extra}, goal=goal)
    block = _field_ref_trigger_block(_build(spec), trigger_type, goal)
    assert block["field_id"] == _patient_field_id(field)
    assert "field_ref" not in block


@pytest.mark.parametrize("goal", [False, True], ids=["trigger", "goal"])
@pytest.mark.parametrize("trigger_type, field, extra", _FIELD_REF_TRIGGER_CASES)
def test_trigger_field_ref_uuid_passes_through(
    patch_live_lookups, trigger_type, field, extra, goal
):
    uuid = _patient_field_id(field)
    spec = _field_ref_trigger_spec(
        trigger_type, {"field_ref": uuid, **extra}, goal=goal
    )
    assert (
        _field_ref_trigger_block(_build(spec), trigger_type, goal)["field_id"] == uuid
    )


@pytest.mark.parametrize("goal", [False, True], ids=["trigger", "goal"])
@pytest.mark.parametrize("trigger_type, field, extra", _FIELD_REF_TRIGGER_CASES)
def test_trigger_bare_field_ref_needs_a_target_object(
    patch_live_lookups, trigger_type, field, extra, goal
):
    spec = _field_ref_trigger_spec(
        trigger_type, {"field_ref": field, **extra}, goal=goal, target=None
    )
    with pytest.raises(PlanError, match="unqualified and the automation has no"):
        _build(spec)


@pytest.mark.parametrize(
    "ref",
    [
        "encounters",
        "patients.encounters",
        {"id": "b69f1a9c-3f3b-4c4d-9989-60a83373c0e8"},
    ],
)
def test_start_automation_relationship_field_ids_resolve(patch_live_lookups, ref):
    spec = _one_step_spec(
        "start_automation",
        {
            "record_source": "related_record",
            "relationship_field_ids": [ref],
            "automation_ids": ["00000000-0000-4000-8000-000000000001"],
        },
    )
    action = _build(spec)["steps"][0]["action_start_automation"]
    assert action["relationship_field_ids"] == [_patient_field_id("encounters")]


def test_stop_execution_maps_to_its_config_block():
    from kizen_builder.models.spec.automations import _STEP_TYPE_TO_CONFIG_FIELD

    assert _STEP_TYPE_TO_CONFIG_FIELD["stop_execution"] == "action_stop_execution"
    spec = _one_step_spec("stop_execution", {})
    del spec["steps"][0]["action_stop_execution"]
    AutomationDef.model_validate(spec)


@pytest.mark.parametrize(
    "step_type", ["branch", "merge_branches", "next_or_finish", "exit_loop"]
)
def test_configless_step_with_stop_execution_block_rejected(step_type):
    spec = _merged_condition_spec()
    merge = next(s for s in spec["steps"] if s["step_type"] == "merge_branches")
    merge.update(
        step_type=step_type, action_stop_execution={"action": "stop_and_complete"}
    )
    with pytest.raises(ValueError, match=f"'{step_type}' takes no config block"):
        AutomationDef.model_validate(spec)


# ---------------------------------------------------------------------------
# assign_team_member — each `type` needs its own keys. The server 400s a
# selector without them, but its message for a wrong hop names the wrong field.
# ---------------------------------------------------------------------------

_EMPLOYEE = "33333333-3333-3333-3333-333333333333"
_HOP = "889f65e2-dd31-42f0-a088-c3c1dd8ea484"  # encounters.patient_rel, many_to_one
_FAR_SELECTOR = "35b02ad4-0d62-4cfb-90d4-dbe4b9dc5904"  # patients.owner


def _assign_spec(block: dict, target_object: str | None = "encounters") -> dict:
    return {
        "api_name": "assign_test",
        "name": "Assign Test",
        "type": "record_based" if target_object else "global",
        "target_object": target_object,
        "steps": [
            {
                "key": "assign",
                "step_type": "assign_team_member",
                "order": 0,
                "parent_key": None,
                "action_assign_team_member": block,
            }
        ],
    }


@pytest.mark.parametrize(
    ("block", "missing"),
    [
        ({"type": "team_member"}, "employee_id: a team member's UUID"),
        ({"type": "round_robin_role"}, "role_id: a role's UUID"),
        ({"type": "round_robin_team_members"}, "employee_ids: team member UUIDs"),
        (
            {"type": "team_selector_field"},
            "field_id: a team-selector field on the automation's own object",
        ),
        (
            {"type": "related_team_selector_field", "field_id": _FAR_SELECTOR},
            "related_field_id: a single-value relationship field on the "
            "automation's own object",
        ),
        (
            {"type": "related_team_selector_field", "related_field_id": _HOP},
            "field_id: a team-selector field on the object related_field_id points to",
        ),
    ],
)
def test_assign_team_member_missing_key_rejected(patch_live_lookups, block, missing):
    with pytest.raises(PlanError) as e:
        _build(_assign_spec(block))
    msg = str(e.value)
    assert f"'assign' (type {block['type']}) needs {missing}." in msg
    assert "not that a run assigns anyone" in msg


def test_assign_team_member_round_robin_all_needs_nothing(patch_live_lookups):
    block = _build(_assign_spec({"type": "round_robin_all"}))["steps"][0]
    assert block["action_assign_team_member"] == {"type": "round_robin_all"}


def test_assign_team_member_read_dialect_satisfies_the_check(patch_live_lookups):
    block = {
        "type": "related_team_selector_field",
        "related_field": {"id": _HOP, "name": "patient_rel"},
        "field": {"id": _FAR_SELECTOR, "name": "owner"},
    }
    built = _build(_assign_spec(block))["steps"][0]["action_assign_team_member"]
    assert built == {
        "type": "related_team_selector_field",
        "field_id": _FAR_SELECTOR,
        "related_field_id": _HOP,
    }
    employee = {"type": "team_member", "employee": {"id": _EMPLOYEE}}
    built = _build(_assign_spec(employee))["steps"][0]["action_assign_team_member"]
    assert built == {"type": "team_member", "employee_id": _EMPLOYEE}


def test_assign_team_member_related_selector_builds_both_keys(patch_live_lookups):
    block = {
        "type": "related_team_selector_field",
        "related_field_id": _HOP,
        "field_id": _FAR_SELECTOR,
    }
    built = _build(_assign_spec(block))["steps"][0]["action_assign_team_member"]
    assert built == {
        "type": "related_team_selector_field",
        "related_field_id": _HOP,
        "field_id": _FAR_SELECTOR,
    }


def test_assign_team_member_hop_must_be_single_value(patch_live_lookups):
    block = {
        "type": "related_team_selector_field",
        "related_field_id": "ae78e103-ab10-47d5-9dbc-730c26ae4882",  # many_to_many
        "field_id": _FAR_SELECTOR,
    }
    with pytest.raises(PlanError) as e:
        _build(_assign_spec(block))
    assert str(e.value) == (
        "assign_team_member step 'assign': related_field_id is 'conditions' on "
        "'encounters', which isn't a single-value relationship field "
        "(field_type relationship, cardinality many_to_many). It must be the hop "
        "from this object; field_id names the selector on the far object."
    )


def test_assign_team_member_hop_must_be_on_target_object(patch_live_lookups):
    # The reported shape: the far object's selector passed as the hop.
    block = {
        "type": "related_team_selector_field",
        "related_field_id": _FAR_SELECTOR,
        "field_id": _FAR_SELECTOR,
    }
    with pytest.raises(PlanError) as e:
        _build(_assign_spec(block))
    assert str(e.value) == (
        f"assign_team_member step 'assign': related_field_id {_FAR_SELECTOR} "
        "isn't a field on 'encounters'. It must be the hop from this object; "
        "field_id names the selector on the far object."
    )


def test_assign_team_member_hop_check_skipped_without_an_object():
    class Unreadable:
        def object_data(self, api_name):
            raise PlanError(f"object '{api_name}' not found")

    block = {
        "type": "related_team_selector_field",
        "related_field_id": _FAR_SELECTOR,
        "field_id": _FAR_SELECTOR,
    }
    for target_object in ("encounters", None):
        auto = AutomationDef.model_validate(_assign_spec(block, target_object))
        assert _assign_team_member_problem("assign", block, auto, Unreadable()) is None


def test_steps_add_and_edit_check_assign_team_member(patch_live_lookups):
    from kizen_builder.tools.automations import build_wire_step, normalize_step_patch

    block = {"type": "team_selector_field"}
    step = {
        "key": "assign",
        "step_type": "assign_team_member",
        "action_assign_team_member": block,
    }
    with pytest.raises(
        PlanError, match="'assign' \\(type team_selector_field\\) needs"
    ):
        build_wire_step(step, _LIVE_PAYLOAD, "encounters")
    patch = {"action_assign_team_member": dict(block)}
    with pytest.raises(
        PlanError, match="'assign' \\(type team_selector_field\\) needs"
    ):
        normalize_step_patch(
            patch, "assign", "assign_team_member", _LIVE_PAYLOAD, "encounters"
        )


def test_notify_team_member_selector_not_checked(patch_live_lookups):
    """`_team_member_selector` is shared; the assign check stays on assign."""
    spec = _assign_spec({"type": "round_robin_all"})
    spec["steps"][0] = {
        "key": "notify",
        "step_type": "notify_member_via_text",
        "order": 0,
        "parent_key": None,
        "action_notify_member_via_text": {
            "team_member": {"type": "team_member"},
            "content": "hi",
        },
    }
    action = _build(spec)["steps"][0]["action_notify_member_via_text"]
    assert action["team_member"] == {"type": "team_member"}


def test_translate_keeps_an_incomplete_assign_team_member():
    """Live reads skip the check, so an existing automation still roundtrips."""
    from kizen_builder.translate import live_to_payload

    raw = load_fixture("automations/kitchen_sink_triggers.raw.json")
    step = next(s for s in raw["steps"] if s["step_type"] == "assign_team_member")
    step["action_assign_team_member"] = {"type": "team_selector_field", "field": None}
    live = next(s for s in live_to_payload(raw)["steps"] if s["id"] == step["id"])
    assert live["action_assign_team_member"] == {"type": "team_selector_field"}


def test_steps_edit_checks_value_resolution_on_a_scalar_variable(patch_live_lookups):
    """`steps edit` shares the spec path's built-block checks."""
    from kizen_builder.tools.automations import normalize_step_patch

    payload = {
        **_GENDERS_DECLARED,
        "steps": [
            {
                **_GENDERS_DECLARED["steps"][0],
                "action_initialize_variable": {
                    "sources": [],
                    "variable": {"name": "genders", "data_type": "string"},
                },
            }
        ],
    }
    patch = {
        "action_update_variable": {
            "variable": "genders",
            "value_resolution": "add_only",
        }
    }
    with pytest.raises(PlanError, match="isn't an array variable"):
        normalize_step_patch(patch, "bump", "update_variable", payload, "patients")

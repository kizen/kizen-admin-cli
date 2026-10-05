"""Golden tests for the automation plan builder: spec in -> wire payload out.

Encodes the branching model (parent_key + parent_yes_no), the
condition-step action_on_failure rule, manual-trigger auto-injection,
field_ref resolution, and the unsupported-type error contract.
"""

from __future__ import annotations

import json

import pytest

from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools.planners.automations import (
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    LiveContext,
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
                "filter_config": {"and": False, "query": [], "invalid": False},
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
                    "variable": {"name": "org_match", "data_type": "text"},
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
                step_condition={
                    "type": "custom_filter",
                    "filter_config": {"and": False, "query": [], "invalid": False},
                },
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


@pytest.mark.parametrize("step_type", ["branch", "merge_branches"])
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
               "target_object": create_obj["name"],
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
    shows: server-written descriptions, the plugin app the server fills in
    for a `kizen/*` model, and a variable definition's null read defaults."""
    import kizen_builder.tools.planners.automations as ma

    raw = load_fixture("automations/branching_test.raw.json")
    monkeypatch.setattr(
        ma, "list_automations", lambda: [{"api_name": raw["api_name"], "id": raw["id"]}]
    )
    structural = [
        d
        for d in diff_automation(_branching_test_spec())["diff"]
        if d["path"].rsplit(".", 1)[-1] not in ("description", "business_plugin_app_id")
        and not (
            ".action_initialize_variable.variable." in d["path"]
            and d["before"] in (None, False)
            and d["after"] == "<absent>"
        )
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
    assert "warnings" not in plan_create_automation(spec).operations[0].preview


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


# No captured fixture has these; the test below re-plans their output instead.
_UNCAPTURED = {"notify_member_via_email", "notify_member_via_text"}


def test_builder_output_passes_the_check():
    """Idempotence: every wire block the translator emits from a captured
    automation — what `steps get` and `automations show` print — re-plans
    without a rejected key, so a copied block can be re-applied."""
    from kizen_builder.translate import live_to_payload

    checked: set[str] = set()
    for path in sorted((FIXTURES / "automations").glob("*.raw.json")):
        payload = live_to_payload(json.loads(path.read_text()))
        for s in payload["steps"]:
            builder = _STEP_BUILDERS.get(s["type"])
            if builder is not None:
                block = s.get(_block_field_for(s["type"])) or {}
                _reject_unhonoured_keys(builder, block, f"{path.name} {s['key']}")
                checked.add(s["type"])
        for t in payload["triggers"]:
            builder = _TRIGGER_BUILDERS.get(t["type"])
            if builder is not None:
                block = t.get(f"trigger_{t['type']}") or {}
                _reject_unhonoured_keys(builder, block, f"{path.name} {t['key']}")
    declared = {t for t, b in _STEP_BUILDERS.items() if b.honoured_keys is not None}
    assert declared - checked == _UNCAPTURED


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

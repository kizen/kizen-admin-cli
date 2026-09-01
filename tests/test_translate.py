"""Golden tests for the GET→PUT translator: live automation JSON in, wire
payload out.

Every assertion here encodes a transform that was discovered against the
real API (a 400, a 500, or — worst — a silently-dropped value). The
kitchen-sink fixture is a sanitized capture of an automation built in the
Kizen UI to cover every trigger type and the long tail of step types; the
whole file round-tripped live (PUT → re-GET → semantic_diff == []) at the
revision it was captured from.
"""

from __future__ import annotations

import copy
import json

import pytest

from kizen_builder.translate import (
    diff_wire_payloads,
    live_to_payload,
    semantic_diff,
    synthesize_step_keys,
    synthesize_trigger_keys,
    validate_payload,
)
from tests.conftest import load_fixture

ALL_FIXTURES = [
    "automations/activity_logged_schedule_activity.raw.json",
    "automations/archive_record.raw.json",
    "automations/condition_code_step.raw.json",
    "automations/create_and_modify_related.raw.json",
    "automations/form_submission.raw.json",
    "automations/kitchen_sink_triggers.raw.json",
    "automations/llm_comparison.raw.json",
    "automations/on_or_around_date_goto.raw.json",
    "automations/schedule_trigger.raw.json",
    "automations/two_code_steps.raw.json",
    "automations/update_variable_goto_branching.raw.json",
    "automations/webhook_delete_scheduled_activity.raw.json",
]


@pytest.fixture(scope="module")
def kitchen() -> dict:
    raw = load_fixture("automations/kitchen_sink_triggers.raw.json")
    return live_to_payload(raw)


def _steps_by_type(payload: dict, step_type: str) -> list[dict]:
    return [s for s in payload["steps"] if s["type"] == step_type]


def _step_block(payload: dict, step_type: str, idx: int = 0) -> dict:
    step = _steps_by_type(payload, step_type)[idx]
    return step[f"action_{step_type}"]


# ---------------------------------------------------------------------------
# Whole-payload invariants, over every fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_translated_payload_validates(path: str) -> None:
    payload = live_to_payload(load_fixture(path))
    assert validate_payload(payload) == []


def test_validator_flags_disconnected_graph() -> None:
    """The server silently accepts multi-root step graphs; this fixture is a
    capture of one such corrupt automation. validate_payload is the only
    thing standing between a bad edit and silently orphaned steps."""
    payload = live_to_payload(load_fixture("automations/condition_roundtrip.raw.json"))
    problems = validate_payload(payload)
    assert any("root step" in p for p in problems)


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_no_secrets_survive_translation(path: str) -> None:
    """Reads expand business_plugin_app with obfuscated secret values.

    Writes take business_plugin_app_id; echoing the expanded object back
    would ship secret material in every PUT.
    """
    text = json.dumps(live_to_payload(load_fixture(path)))
    assert "obfuscated_value" not in text
    assert '"business_plugin_app"' not in text


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_no_variable_ids_survive_translation(path: str) -> None:
    """The server recreates the variable set wholesale on every PUT; a
    variable `id` inside action_initialize_variable is an HTTP 500."""
    payload = live_to_payload(load_fixture(path))
    for v in payload.get("variables", []):
        assert "id" not in v
    for s in payload["steps"]:
        iv = s.get("action_initialize_variable")
        if iv and isinstance(iv.get("variable"), dict):
            assert "id" not in iv["variable"]


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_step_and_trigger_ids_are_echoed_back(path: str) -> None:
    """Every step/trigger `live_to_payload` builds already exists (it came
    from a GET), so its real server `id` must always be echoed back on the
    PUT — that's what keeps its identity, and its execution history, across
    a `kizen automations steps add/edit/remove` / `roundtrip` write instead
    of the server assigning a fresh id."""
    raw = load_fixture(path)
    payload = live_to_payload(raw)
    raw_step_ids = {s["id"] for s in raw.get("steps") or []}
    raw_trigger_ids = {t["id"] for t in raw.get("triggers") or []}
    assert {s["id"] for s in payload["steps"]} == raw_step_ids
    assert {t["id"] for t in payload["triggers"]} == raw_trigger_ids

    # Per-id check, not just set membership — a permutation (step A's id
    # landing on step B) would pass the set comparisons above while still
    # corrupting every affected step's execution history.
    step_keys = synthesize_step_keys(raw)
    trigger_keys = synthesize_trigger_keys(raw)
    for s in payload["steps"]:
        assert s["key"] == step_keys[s["id"]], (
            f"step id {s['id']} landed on key '{s['key']}', expected "
            f"'{step_keys[s['id']]}'"
        )
    for t in payload["triggers"]:
        assert t["key"] == trigger_keys[t["id"]], (
            f"trigger id {t['id']} landed on key '{t['key']}', expected "
            f"'{trigger_keys[t['id']]}'"
        )

    # Goal steps embed their own nested triggers (wait-until conditions),
    # which carry ids in the same way top-level triggers do.
    for raw_step in raw.get("steps") or []:
        if raw_step["step_type"] != "goal":
            continue
        raw_nested_ids = {
            t["id"] for t in (raw_step.get("step_goal") or {}).get("triggers") or []
        }
        wire_step = next(s for s in payload["steps"] if s["id"] == raw_step["id"])
        wire_nested_ids = {
            t["id"] for t in wire_step["step_goal"]["triggers"] if t.get("id")
        }
        assert wire_nested_ids == raw_nested_ids, (
            f"goal step {raw_step['id']}'s nested trigger ids were not echoed "
            "back — this orphans that trigger's execution history"
        )


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_orders_are_sequential(path: str) -> None:
    payload = live_to_payload(load_fixture(path))
    assert sorted(s["order"] for s in payload["steps"]) == list(
        range(len(payload["steps"]))
    )
    assert sorted(t["order"] for t in payload["triggers"]) == list(
        range(len(payload["triggers"]))
    )


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_self_diff_is_empty(path: str) -> None:
    raw = load_fixture(path)
    assert semantic_diff(raw, raw) == []


# ---------------------------------------------------------------------------
# Kitchen sink: per-type wire dialects (each one a live 400/500/data-loss)
# ---------------------------------------------------------------------------


def test_kitchen_covers_the_long_tail(kitchen: dict) -> None:
    types = {s["type"] for s in kitchen["steps"]}
    assert {
        "archive_record",
        "assign_team_member",
        "audio_transcription",
        "modify_related_entities",
        "send_related_contact_email",
        "send_related_contact_text",
        "http_request",
        "math_operator",
        "goal",
        "delay",
    } <= types
    assert len(kitchen["triggers"]) >= 16


def test_archive_record_uses_id_dialect(kitchen: dict) -> None:
    blk = _step_block(kitchen, "archive_record")
    assert blk["record_source"] == "related_object_field"
    assert all(isinstance(r, str) for r in blk["relationship_field_ids"])
    assert "relationship_fields" not in blk  # read key: silently ignored


def test_assign_team_member_uses_id_dialect(kitchen: dict) -> None:
    blk = _step_block(kitchen, "assign_team_member")
    assert blk["type"] == "round_robin_role"
    assert isinstance(blk["role_id"], str)
    assert "role" not in blk


def test_audio_transcription_shares_extraction_shape(kitchen: dict) -> None:
    blk = _step_block(kitchen, "audio_transcription")
    assert isinstance(blk["business_plugin_app_id"], str)
    assert isinstance(blk["input_field"], str)
    assert blk["data_type"] == "audio"
    for dest in blk["destinations"]:
        assert not isinstance(dest.get("field"), dict)


def test_extraction_destination_related_object_field_is_bare_uuid(
    kitchen: dict,
) -> None:
    blk = _step_block(kitchen, "file_content_extraction")
    rel = [d for d in blk["destinations"] if "related_object_field" in d]
    assert rel and all(isinstance(d["related_object_field"], str) for d in rel)


def test_modify_related_entities_tags_are_bare_uuids(kitchen: dict) -> None:
    """Both tag lists take bare UUID strings; even the {id, name} shape the
    OpenAPI spec documents is an HTTP 500."""
    blk = _step_block(kitchen, "modify_related_entities")
    assert isinstance(blk["object_to_modify"], str)
    sfv = blk["fields_to_modify"][0]["specific_field_value"]
    assert all(isinstance(t, str) for t in sfv["tags_to_add"])
    assert all(isinstance(t, str) for t in sfv["tags_to_remove"])


def test_cc_team_member_normalized_to_id_dialect(kitchen: dict) -> None:
    blocks = [
        s["action_send_related_contact_email"]
        for s in _steps_by_type(kitchen, "send_related_contact_email")
    ]
    ccs = [b["cc_team_member"] for b in blocks if "cc_team_member" in b]
    assert ccs
    by_type = {cc["type"]: cc for cc in ccs}
    assert all(isinstance(e, str) for e in by_type["employees"]["employee_ids"])
    assert "employees" not in by_type["employees"]  # read key


def test_message_steps_reference_message_by_id(kitchen: dict) -> None:
    email = _step_block(kitchen, "send_related_contact_email")
    assert set(email["email"]) == {"id"}
    text = _step_block(kitchen, "send_related_contact_text")
    assert set(text["text"]) == {"id"}


# ---------------------------------------------------------------------------
# diff_wire_payloads: live vs. spec-as-applied, wire (PUT) dialect
# ---------------------------------------------------------------------------


def _rekey(payload: dict, prefix: str) -> dict:
    """Return a deep copy with every step/trigger `key` replaced by a
    differently-named one, `parent_key` and `go_to_automation_step`
    references remapped to match — the shape a hand-authored spec takes
    (author picks their own keys; identity rides on `id`, not `key`).
    Simulates the exact churn `key`/`parent_key` exclusion exists to absorb,
    including a `go_to` pointed at the same target under its new key."""
    out = copy.deepcopy(payload)
    step_map = {s["key"]: f"{prefix}step{i}" for i, s in enumerate(out["steps"])}
    trigger_map = {
        t["key"]: f"{prefix}trigger{i}" for i, t in enumerate(out["triggers"])
    }
    for s in out["steps"]:
        s["key"] = step_map[s["key"]]
        if s["parent_key"]:
            s["parent_key"] = step_map[s["parent_key"]]
        go_to = s.get("action_go_to_automation_step")
        if isinstance(go_to, dict):
            if go_to.get("step_key"):
                go_to["step_key"] = step_map[go_to["step_key"]]
            if go_to.get("trigger_key"):
                go_to["trigger_key"] = trigger_map[go_to["trigger_key"]]
    for t in out["triggers"]:
        t["key"] = trigger_map[t["key"]]
    return out


def test_diff_wire_payloads_self_diff_is_empty() -> None:
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = live_to_payload(raw)
    assert diff_wire_payloads(live, spec) == []


def test_diff_wire_payloads_ignores_key_resynthesis() -> None:
    """The golden case this item exists for: a spec that reproduces a live
    automation, authored with its own keys, must show zero diff even though
    every `key`/`parent_key` differs textually from the live side's
    synthesized ones."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = _rekey(live_to_payload(raw), "authored_")
    assert diff_wire_payloads(live, spec) == []


def test_diff_wire_payloads_ignores_go_to_key_resynthesis() -> None:
    """A `go_to_automation_step` reference must resolve by matched identity,
    same as `parent_key` — re-keying it to point at the *same* target under
    its new key must not register as a change."""
    raw = load_fixture("automations/on_or_around_date_goto.raw.json")
    live = live_to_payload(raw)
    spec = _rekey(live_to_payload(raw), "authored_")
    assert diff_wire_payloads(live, spec) == []


def test_diff_wire_payloads_reports_go_to_retarget() -> None:
    """A go_to genuinely pointed at a different step must still surface —
    matched-identity resolution must not suppress a real retarget."""
    raw = load_fixture("automations/on_or_around_date_goto.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    go_to_step = next(s for s in spec["steps"] if s["type"] == "go_to_automation_step")
    other_step = next(
        s
        for s in spec["steps"]
        if s["key"] != go_to_step["action_go_to_automation_step"]["step_key"]
        and s["type"] != "go_to_automation_step"
    )
    go_to_step["action_go_to_automation_step"]["step_key"] = other_step["key"]
    entries = diff_wire_payloads(live, spec)
    assert len(entries) == 1
    (entry,) = entries
    assert entry["path"].endswith(".action_go_to_automation_step.step_key")
    assert entry["before"] != entry["after"]
    assert entry["after"] == other_step["id"][:8]


def test_diff_wire_payloads_reports_added_step() -> None:
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    spec["steps"].append(
        {
            "key": "s02_stop_execution",
            "parent_key": spec["steps"][-1]["key"],
            "parent_yes_no": "",
            "parent_condition": "",
            "type": "stop_execution",
            "prefix": "step",
            "order": 2,
            "user_description": "",
            "action_on_failure": "notify_continue",
            "should_skip_execution": False,
            "goal_type": False,
            "action_stop_execution": {},
        }
    )
    entries = diff_wire_payloads(live, spec)
    assert len(entries) == 1
    (entry,) = entries
    assert entry["before"] == "<absent>"
    assert entry["after"]["type"] == "stop_execution"
    assert entry["path"].startswith("steps.new:")


def test_diff_wire_payloads_reports_removed_step() -> None:
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    removed = spec["steps"].pop()
    entries = diff_wire_payloads(live, spec)
    assert len(entries) == 1
    (entry,) = entries
    assert entry["after"] == "<absent>"
    assert entry["before"]["id"] == removed["id"]
    assert entry["path"] == f"steps.{removed['id'][:8]}"


def test_diff_wire_payloads_dangling_spec_id_is_addition_not_edit() -> None:
    """A spec step carrying an `id` that matches no live step must be an
    addition, and the live step it displaces a removal — not merged into a
    single "step edited" entry, which would misreport a delete+add as an
    in-place change and hide the spec's bogus id entirely."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    removed = spec["steps"].pop()
    spec["steps"].append(
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "key": "s01_stop_execution",
            "parent_key": spec["steps"][-1]["key"],
            "parent_yes_no": "",
            "parent_condition": "",
            "type": "stop_execution",
            "prefix": "step",
            "order": 1,
            "user_description": "",
            "action_on_failure": "notify_continue",
            "should_skip_execution": False,
            "goal_type": False,
            "action_stop_execution": {},
        }
    )
    entries = diff_wire_payloads(live, spec)
    by_path = {e["path"]: e for e in entries}
    assert by_path[f"steps.{removed['id'][:8]}"]["after"] == "<absent>"
    assert by_path["steps.99999999"]["before"] == "<absent>"
    assert (
        by_path["steps.99999999"]["after"]["id"]
        == "99999999-9999-9999-9999-999999999999"
    )
    assert len(entries) == 2


def test_diff_wire_payloads_reports_one_changed_field() -> None:
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    spec["steps"][0]["action_code_step"]["script"] = 'outputs.log("changed")'
    entries = diff_wire_payloads(live, spec)
    assert len(entries) == 1
    (entry,) = entries
    step_octet = live["steps"][0]["id"][:8]
    assert entry["path"] == f"steps.{step_octet}.action_code_step.script"
    assert entry["before"] == 'outputs.log("step 1")'
    assert entry["after"] == 'outputs.log("changed")'


def test_diff_wire_payloads_reports_reparenting() -> None:
    """Excluding `parent_key` from the literal comparison must not also hide
    a genuine reparenting — the parent is compared by matched identity."""
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    spec["steps"][1]["parent_key"] = None
    entries = diff_wire_payloads(live, spec)
    assert len(entries) == 1
    (entry,) = entries
    child_octet = live["steps"][1]["id"][:8]
    parent_octet = live["steps"][0]["id"][:8]
    assert entry["path"] == f"steps.{child_octet}.parent"
    assert entry["before"] == parent_octet
    assert entry["after"] is None


def test_diff_wire_payloads_active_is_diffed_like_any_top_level_field() -> None:
    raw = load_fixture("automations/two_code_steps.raw.json")
    live = live_to_payload(raw)
    spec = copy.deepcopy(live)
    spec["active"] = not live["active"]
    entries = diff_wire_payloads(live, spec)
    assert entries == [
        {"path": "active", "before": live["active"], "after": spec["active"]}
    ]

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

from kizen_builder.tools.planners.automations import _collapse_variable_overrides
from kizen_builder.tools.plans import PlanError
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
    "automations/branching_test.raw.json",
    "automations/call_llm_mcp.raw.json",
    "automations/condition_code_step.raw.json",
    "automations/condition_variable_rhs.raw.json",
    "automations/condition_variable_rhs_pipeline.raw.json",
    "automations/create_and_modify_related.raw.json",
    "automations/email_received.raw.json",
    "automations/extraction_input_sources.raw.json",
    "automations/form_submission.raw.json",
    "automations/kitchen_sink_triggers.raw.json",
    "automations/llm_comparison.raw.json",
    "automations/loops.raw.json",
    "automations/merged_condition.raw.json",
    "automations/merged_condition_skipped.raw.json",
    "automations/new_features_loops.raw.json",
    "automations/on_or_around_date_goto.raw.json",
    "automations/plugin_code_steps.raw.json",
    "automations/schedule_trigger.raw.json",
    "automations/sets_variable_overrides.raw.json",
    "automations/two_code_steps.raw.json",
    "automations/update_variable_goto_branching.raw.json",
    "automations/webhook_delete_scheduled_activity.raw.json",
]


@pytest.fixture(scope="module")
def kitchen() -> dict:
    raw = load_fixture("automations/kitchen_sink_triggers.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def archive() -> dict:
    """`fields_to_clear` is a real capture; its `automation_variable_overrides`
    are synthetic, transcribed from a previously captured live dialect
    rather than taken from a raw GET — see sets_variable_overrides.raw.json
    for the equivalent real capture."""
    raw = load_fixture("automations/archive_record.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def form() -> dict:
    raw = load_fixture("automations/form_submission.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def sets_overrides() -> dict:
    raw = load_fixture("automations/sets_variable_overrides.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def branching() -> dict:
    """UI-built: unmerged, merged, nested and after-merge Branch cards."""
    raw = load_fixture("automations/branching_test.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def merged_condition() -> dict:
    raw = load_fixture("automations/merged_condition.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def skipped_condition() -> dict:
    """Synthetic edit of merged_condition.raw.json: the condition has
    `should_skip_execution: true` and `continue_with_branch: "no"`, and the
    yes-path delay has `error_notification_severity_level: "warning"`. The
    same edit was made live and re-read in this shape, but that GET was not
    captured in full."""
    raw = load_fixture("automations/merged_condition_skipped.raw.json")
    return live_to_payload(raw)


@pytest.fixture(scope="module")
def loops() -> dict:
    """Spec-built, then read back: a loop whose body is a merged condition
    with an `exit_loop` on its no branch, then a second loop after the
    first's `next_or_finish`, then a step after that. Its fixed translation
    round-tripped live with `semantic_diff == []` (2026-10-27)."""
    return live_to_payload(load_fixture("automations/loops.raw.json"))


@pytest.fixture(scope="module")
def ui_loops() -> dict:
    """UI-built: a loop over a record array, then a loop over a string array
    whose body is a merged condition. Only the manual trigger is kept. The
    call_llm step's `mcp_server`, `run_tools_as`, `max_sequential_turns` and
    `tool_failure_policy` were popped because the CLI doesn't model them yet;
    the change that models them restores them here."""
    return live_to_payload(load_fixture("automations/new_features_loops.raw.json"))


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
    thing standing between a bad edit and silently orphaned steps.

    The fixture also carries a condition with no rules (``query: []``). The
    spec path rejects that, but the live path has to tolerate it so the
    automation can still be read and fixed."""
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


def test_email_received_rides_under_its_from_contact_key() -> None:
    """The one trigger type whose config key isn't `trigger_<type>`. Sending
    `trigger_email_received` is a 400 on every re-PUT, at the top level and
    in a goal (confirmed live 2026-10-14)."""
    payload = live_to_payload(load_fixture("automations/email_received.raw.json"))
    (trigger,) = [t for t in payload["triggers"] if t["type"] == "email_received"]
    assert trigger["trigger_email_received_from_contact"] == {"receiving_inbox": "any"}

    (goal,) = _steps_by_type(payload, "goal")
    (nested,) = goal["step_goal"]["triggers"]
    # Reads expand each selected inbox to {id, email}; the wire takes the id.
    assert nested["trigger_email_received_from_contact"] == {
        "receiving_inbox": "selected_integrated_inboxes",
        "selected_integrated_inboxes": ["1cf296ff-d456-413b-80bd-dda8576ead55"],
    }
    assert "trigger_email_received" not in json.dumps(payload).replace(
        "trigger_email_received_from_contact", ""
    )


def test_email_received_empty_selection_still_translates() -> None:
    """The server saves "selected inboxes" with an empty list (200,
    2026-10-14), so a read of one must re-PUT as is. Specs still reject it."""
    raw = load_fixture("automations/email_received.raw.json")
    empty = {
        "receiving_inbox": "selected_integrated_inboxes",
        "selected_integrated_inboxes": [],
    }
    (trigger,) = [t for t in raw["triggers"] if t["trigger_type"] == "email_received"]
    trigger["trigger_email_received_from_contact"] = empty
    (goal,) = [s for s in raw["steps"] if s["step_type"] == "goal"]
    goal["step_goal"]["triggers"][0]["trigger_email_received_from_contact"] = empty

    payload = live_to_payload(raw)
    (trigger,) = [t for t in payload["triggers"] if t["type"] == "email_received"]
    (goal,) = _steps_by_type(payload, "goal")
    assert trigger["trigger_email_received_from_contact"] == empty
    assert (
        goal["step_goal"]["triggers"][0]["trigger_email_received_from_contact"] == empty
    )


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


# extraction_input_sources.raw.json: one step per probed input_value_source
# type, plus an input_field-only step whose read carries the mirrored
# context_entity_field source. Captured live 2026-10-14.
_POLICY_HOP = "62bb1252-acf9-4bca-914e-91e9f4ab952b"
_POLICY_DOCUMENT = "ee4cecb6-10a9-435f-b82c-4798a64808de"
_CLAIM_ATTACHMENT = "a32c92f6-60d2-4dfe-83c3-66a85b7b7107"


@pytest.fixture(scope="module")
def extraction_sources() -> dict:
    raw = load_fixture("automations/extraction_input_sources.raw.json")
    return live_to_payload(raw)


def test_related_record_source_is_written_as_input_value_source(
    extraction_sources: dict,
) -> None:
    related = {
        "type": "related_object_field",
        "field": _POLICY_DOCUMENT,
        "relationship_field": _POLICY_HOP,
    }
    extract = _step_block(extraction_sources, "file_content_extraction", 0)
    transcribe = _step_block(extraction_sources, "audio_transcription")
    for blk in (extract, transcribe):
        assert blk["input_value_source"] == related
        assert "input_field" not in blk


def test_variable_source_is_written_by_name(extraction_sources: dict) -> None:
    blk = _step_block(extraction_sources, "file_content_extraction", 1)
    assert blk["input_value_source"] == {
        "type": "automation_variable",
        "variable": "claim_file",
    }
    assert "input_field" not in blk


def test_mirrored_context_source_collapses_to_input_field(
    extraction_sources: dict,
) -> None:
    """Every read mirrors input_field into a context_entity_field source, and
    Kizen 400s when a write carries both."""
    raw = load_fixture("automations/extraction_input_sources.raw.json")
    read = raw["steps"][4]["action_file_content_extraction"]
    assert read["input_value_source"]["type"] == "context_entity_field"
    blk = _step_block(extraction_sources, "file_content_extraction", 2)
    assert blk["input_field"] == _CLAIM_ATTACHMENT
    assert "input_value_source" not in blk


def _extraction_step(block: dict):
    from kizen_builder.models.spec import AutomationDef, AutomationStepDef

    step = AutomationStepDef.model_validate(
        {
            "key": "extract",
            "step_type": "file_content_extraction",
            "order": 0,
            "action_file_content_extraction": {"model_name": "kizen/lite", **block},
        }
    )
    auto = AutomationDef(
        name="t", api_name="t", type="record_based", target_object="claims"
    )
    return step, auto


class _RefContext:
    def field_uuid(self, obj: str, fld: str) -> str:
        return {
            ("claims", "policy"): _POLICY_HOP,
            ("policies", "policy_document"): _POLICY_DOCUMENT,
        }[(obj, fld)]


def test_source_dotted_refs_resolve_to_bare_uuids() -> None:
    from kizen_builder.tools.planners.automations import _build_step_payload

    step, auto = _extraction_step(
        {
            "input_value_source": {
                "type": "related_object_field",
                "relationship_field": "claims.policy",
                "field": "policies.policy_document",
            }
        }
    )
    blk = _build_step_payload(step, auto, _RefContext())[
        "action_file_content_extraction"
    ]
    assert blk["input_value_source"] == {
        "type": "related_object_field",
        "relationship_field": _POLICY_HOP,
        "field": _POLICY_DOCUMENT,
    }
    assert "input_field" not in blk


def test_input_field_and_a_different_source_is_refused() -> None:
    from kizen_builder.tools.planners.automations import _build_step_payload

    step, auto = _extraction_step(
        {
            "input_field_id": _CLAIM_ATTACHMENT,
            "input_value_source": {
                "type": "automation_variable",
                "variable": "claim_file",
            },
        }
    )
    with pytest.raises(PlanError) as e:
        _build_step_payload(step, auto, _RefContext())
    assert str(e.value) == (
        "step 'extract': set input_field_ref or input_value_source, not both "
        "— Kizen accepts only one."
    )


def test_extraction_without_an_input_is_refused() -> None:
    from kizen_builder.tools.planners.automations import _build_step_payload

    step, auto = _extraction_step({"prompt": "Summarize."})
    with pytest.raises(PlanError) as e:
        _build_step_payload(step, auto, _RefContext())
    assert str(e.value) == (
        "step 'extract': needs an input — set input_field_ref (a file field "
        "on the target record) or input_value_source (a related record's "
        "field, or a variable)."
    )


def test_modify_related_entities_tags_are_bare_uuids(kitchen: dict) -> None:
    """Both tag lists take bare UUID strings; even the {id, name} shape the
    OpenAPI spec documents is an HTTP 500."""
    blk = _step_block(kitchen, "modify_related_entities")
    assert isinstance(blk["object_to_modify"], str)
    sfv = blk["fields_to_modify"][0]["specific_field_value"]
    assert all(isinstance(t, str) for t in sfv["tags_to_add"])
    assert all(isinstance(t, str) for t in sfv["tags_to_remove"])


def _modify_from_variable_read() -> dict:
    """create_and_modify_related with its modify step reading its records
    from a variable, in the shape a live read returns (2026-10-06)."""
    raw = load_fixture("automations/create_and_modify_related.raw.json")
    (step,) = [s for s in raw["steps"] if s["step_type"] == "modify_related_entities"]
    step["action_modify_related_entities"].update(
        record_source="automation_variable",
        record_source_variables=["visit_record"],
        automation_variables=[
            {
                "name": "visit_record",
                "deleted": False,
                "data_type": "entity",
                "data_subtype": "custom_object",
            }
        ],
        automation_target_relationship_fields=[],
    )
    return raw


def test_modify_related_entities_keeps_its_record_source() -> None:
    """Dropping `record_source` made the server default to
    `related_object_field` and 400 for want of a hop, so the automation could
    not be re-saved at all (confirmed live 2026-10-06)."""
    blk = _step_block(
        live_to_payload(_modify_from_variable_read()), "modify_related_entities"
    )
    assert blk["record_source"] == "automation_variable"
    assert blk["record_source_variables"] == ["visit_record"]
    assert blk["automation_target_relationship_fields"] == []
    # The expanded mirror and the config-row id are read-only.
    assert "automation_variables" not in blk
    assert "id" not in blk


def test_modify_related_entities_record_source_variables_accept_definitions() -> None:
    raw = _modify_from_variable_read()
    (step,) = [s for s in raw["steps"] if s["step_type"] == "modify_related_entities"]
    step["action_modify_related_entities"]["record_source_variables"] = [
        {"name": "visit_record"}
    ]
    blk = _step_block(live_to_payload(raw), "modify_related_entities")
    assert blk["record_source_variables"] == ["visit_record"]


def test_schedule_activity_keeps_its_html_note() -> None:
    """A PUT without `html_note` clears it, while `note` survives (confirmed
    live 2026-10-06)."""
    raw = load_fixture("automations/activity_logged_schedule_activity.raw.json")
    (step,) = [s for s in raw["steps"] if s["step_type"] == "schedule_activity"]
    step["action_schedule_activity"]["html_note"] = "<p><strong>Rich</strong> note</p>"
    blk = _step_block(live_to_payload(raw), "schedule_activity")
    assert blk["html_note"] == "<p><strong>Rich</strong> note</p>"
    assert blk["note"] == step["action_schedule_activity"]["note"]


# ---------------------------------------------------------------------------
# Read keys no builder models: refused rather than dropped on PUT
# ---------------------------------------------------------------------------


def _with_block_key(
    path: str, step_type: str, key: str, value: object
) -> tuple[dict, dict]:
    """The fixture, with `key` set on the first `step_type` step's block."""
    raw = load_fixture(path)
    step = next(s for s in raw["steps"] if s["step_type"] == step_type)
    step[_block_field(step_type)][key] = value
    return raw, step


def _block_field(step_type: str) -> str:
    from kizen_builder.tools.planners.automations import _block_field_for

    return _block_field_for(step_type)


def test_unmodelled_step_key_is_refused() -> None:
    raw, step = _with_block_key(
        "automations/two_code_steps.raw.json", "code_step", "new_setting", "on"
    )
    with pytest.raises(PlanError) as e:
        live_to_payload(raw)
    msg = str(e.value)
    key = synthesize_step_keys(raw)[step["id"]]
    assert (
        f"automation '{raw['name']}' step '{key}' action_code_step: new_setting" in msg
    )
    assert "would be lost on PUT" in msg


def test_unmodelled_trigger_key_is_refused() -> None:
    raw = load_fixture("automations/activity_logged_schedule_activity.raw.json")
    trigger = next(t for t in raw["triggers"] if t["trigger_type"] == "activity_logged")
    trigger["trigger_activity_logged"]["new_setting"] = ["x"]
    key = synthesize_trigger_keys(raw)[trigger["id"]]
    with pytest.raises(
        PlanError, match=f"trigger '{key}' trigger_activity_logged: new_setting"
    ):
        live_to_payload(raw)


@pytest.mark.parametrize("value", [None, [], {}, ""])
def test_empty_unmodelled_key_is_not_refused(value: object) -> None:
    raw, _ = _with_block_key(
        "automations/two_code_steps.raw.json", "code_step", "new_setting", value
    )
    assert "new_setting" not in json.dumps(live_to_payload(raw))


@pytest.mark.parametrize(
    "path, step_type",
    [
        # `@honours_all`: forwards its whole block.
        ("automations/merged_condition.raw.json", "delay"),
        # No builder: `_generic_block` strips read-only keys and forwards the rest.
        (
            "automations/webhook_delete_scheduled_activity.raw.json",
            "delete_scheduled_activity",
        ),
    ],
)
def test_forwarding_types_carry_any_key(path: str, step_type: str) -> None:
    raw, step = _with_block_key(path, step_type, "new_setting", "on")
    (out,) = [s for s in live_to_payload(raw)["steps"] if s["id"] == step["id"]]
    assert out[_block_field(step_type)]["new_setting"] == "on"


def test_refused_read_sends_no_put(env_config) -> None:
    import httpx
    import respx

    from kizen_builder.tools import automations as auto_tools
    from tests.conftest import FAKE_BASE_URL

    raw, _ = _with_block_key(
        "automations/two_code_steps.raw.json", "code_step", "new_setting", "on"
    )
    base = f"{FAKE_BASE_URL}/api/automation2/automations"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(base).mock(
            return_value=httpx.Response(
                200, json={"results": [{"id": raw["id"], "api_name": raw["api_name"]}]}
            )
        )
        mock.get(f"{base}/{raw['id']}").mock(return_value=httpx.Response(200, json=raw))
        put = mock.put(f"{base}/{raw['id']}").mock(
            return_value=httpx.Response(200, json={})
        )
        with pytest.raises(PlanError, match="new_setting"):
            auto_tools.set_active(raw["api_name"], True, execute=True)
    assert not put.called


@pytest.mark.parametrize("verb", ["activate", "show"])
def test_refused_read_is_a_cli_error(monkeypatch, verb: str) -> None:
    from typer.testing import CliRunner

    import kizen_builder.cli as cli
    from kizen_builder.api import automations as auto_api

    raw, _ = _with_block_key(
        "automations/two_code_steps.raw.json", "code_step", "new_setting", "on"
    )
    monkeypatch.setattr(
        auto_api,
        "list_automations",
        lambda c: [{"api_name": raw["api_name"], "id": raw["id"]}],
    )
    monkeypatch.setattr(auto_api, "get_automation", lambda c, i: raw)
    args = ["automations", verb, raw["api_name"], *(["--yes"] * (verb == "activate"))]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1
    assert result.stderr.startswith("error: automation ")
    assert "new_setting would be lost on PUT" in result.stderr


def test_go_to_trigger_becomes_its_trigger_key() -> None:
    raw = load_fixture("automations/update_variable_goto_branching.raw.json")
    go_to = next(s for s in raw["steps"] if s["step_type"] == "go_to_automation_step")
    trigger_id = raw["triggers"][0]["id"]
    go_to["action_go_to_automation_step"]["trigger"] = {"id": trigger_id}
    (step,) = [s for s in live_to_payload(raw)["steps"] if s["id"] == go_to["id"]]
    block = step["action_go_to_automation_step"]
    assert block["trigger_key"] == synthesize_trigger_keys(raw)[trigger_id]
    assert "trigger" not in block


def test_go_to_trigger_outside_the_automation_is_refused() -> None:
    raw = load_fixture("automations/update_variable_goto_branching.raw.json")
    go_to = next(s for s in raw["steps"] if s["step_type"] == "go_to_automation_step")
    go_to["action_go_to_automation_step"]["trigger"] = {
        "id": "00000000-0000-4000-8000-000000000999"
    }
    with pytest.raises(
        PlanError,
        match=r"step '\S+' action_go_to_automation_step: points at trigger id "
        "00000000-0000-4000-8000-000000000999",
    ):
        live_to_payload(raw)


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


@pytest.mark.parametrize(
    "path",
    [
        "automations/condition_variable_rhs.raw.json",
        "automations/condition_variable_rhs_pipeline.raw.json",
    ],
)
def test_ui_built_variable_comparison_survives_translation(path: str) -> None:
    """A field compared to a variable carries `value_type: "variable"` and the
    variable's name as `value`; the raw filter_config passes through intact."""
    raw = load_fixture(path)
    (live_cond,) = [s for s in raw["steps"] if s["step_type"] == "condition"]
    live = live_to_payload(raw)
    (cond,) = _steps_by_type(live, "condition")
    assert (
        cond["step_condition"]["filter_config"]
        == live_cond["step_condition"]["filter_config"]
    )
    assert diff_wire_payloads(live, _rekey(live_to_payload(raw), "authored_")) == []


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


def test_fields_to_clear_collapses_to_bare_uuids(archive: dict) -> None:
    """Read expands each fields_to_clear entry to a full field object; write
    wants bare UUIDs, same as the tag-list handling in the same function
    (`_change_field_value_action`). Affects every read-modify-write path
    that touches this action."""
    blk = _step_block(archive, "change_field_value")
    action = blk["actions"][0]
    assert action["fields_to_clear"] == [
        "b3f6a3c1-2f8e-4a9d-9d0a-5c1f6e2a7b41",
        "c7c696bd-4f21-4e2a-8b3e-1a9d6f0c8e52",
    ]


def test_automation_variable_overrides_groups_by_target_automation(
    archive: dict,
) -> None:
    """Read is a flat list, one entry per override, every reference expanded
    to a full object. Write groups entries by target automation id, and the
    unwrapping is non-uniform: `context_entity_field` -> bare UUID,
    `variable_to_override`/`automation_variable` -> bare name. Two
    override-bearing entries targeting different automations is the shape
    that actually exercises the grouping — a single-target step would pass
    even a naive un-grouped implementation."""
    blk = _step_block(archive, "start_automation")
    overrides = blk["automation_variable_overrides"]
    assert len(overrides) == 2
    by_automation = {o["automation_id"]: o["variable_overrides"] for o in overrides}
    assert by_automation["3e1d490a-9358-4c7d-ada3-a1f9c9cb9606"] == [
        {
            "variable_to_override": "contacts_file_id",
            "value_source": "context_entity_field",
            "context_entity_field": "22585898-3f4c-4a1b-9e2d-6c8a1f5b3d70",
        }
    ]
    assert by_automation["ff3bd36b-9a2e-4c1d-8b7a-2e4f6a9c1d3e"] == [
        {
            "variable_to_override": "file_upload_record",
            "value_source": "automation_variable",
            "automation_variable": "file_upload_record",
        }
    ]


def test_five_overrides_on_one_target_automation_group_into_one_entry(
    sets_overrides: dict,
) -> None:
    """Real capture (`sets_variable_overrides`, business ff433e1f…, 2026-09-01):
    one start_automation step sends five overrides all targeting the same
    automation. Confirms grouping accumulates within a target instead of only
    across targets (`tools/planners/automations.py:1296-1300`), and pins the
    `specific_value` fix — the live bug this item was reopened for: the value
    was silently dropped, collapsing to `{"variable_to_override": "bool",
    "value_source": "specific_value"}` with no `specific_value` key at all."""
    blk = _step_block(sets_overrides, "start_automation", idx=1)
    overrides = blk["automation_variable_overrides"]
    assert len(overrides) == 1
    group = overrides[0]
    assert group["automation_id"] == "c18c9d5a-fa95-4dd6-b3b2-05df03975677"
    assert group["variable_overrides"] == [
        {
            "variable_to_override": "array_var",
            "value_source": "automation_variable",
            "automation_variable": "array_of_objects",
        },
        {
            "variable_to_override": "bool",
            "value_source": "specific_value",
            "specific_value": "True",
        },
        {
            "variable_to_override": "team_member",
            "value_source": "automation_variable",
            "automation_variable": "team_member",
        },
        {
            "variable_to_override": "other_uuid",
            "value_source": "automation_variable",
            "automation_variable": "team_member",
        },
        {
            "variable_to_override": "datetime",
            "value_source": "blank",
        },
    ]


def test_collapse_variable_overrides_carries_unseen_value_source() -> None:
    """An override whose `value_source` no capture has exercised still has to
    survive the trip: `activate`/`deactivate`/`steps` PUT the whole automation
    back, so refusing would strand an automation Kizen itself accepted, and
    dropping the key is the bug this function exists to fix. The value is
    carried across and narrowed by its key — a `_field` reference to a bare
    UUID, per this module's field convention."""
    entry = {
        "target_automation": {"id": "auto-1", "name": "Target"},
        "variable_to_override": {"id": "v1", "name": "some_var"},
        "value_source": "relationship_field",
        "relationship_field": {"id": "field-1", "name": "Some Field"},
    }
    assert _collapse_variable_overrides([entry]) == [
        {
            "automation_id": "auto-1",
            "variable_overrides": [
                {
                    "variable_to_override": "some_var",
                    "value_source": "relationship_field",
                    "relationship_field": "field-1",
                }
            ],
        }
    ]


def test_collapse_variable_overrides_rejects_non_object_entry() -> None:
    """A hand-authored spec can put anything in this list; a scalar must be a
    named PlanError rather than an AttributeError traceback."""
    with pytest.raises(PlanError, match="not an object"):
        _collapse_variable_overrides(["oops"])


def test_collapse_variable_overrides_rejects_mixed_grouped_and_flat() -> None:
    """A list that is neither uniformly grouped (write dialect) nor uniformly
    flat (read dialect) must raise instead of silently reinterpreting every
    entry as one or the other — the shape this review flagged as a live
    landmine."""
    mixed = [
        {"automation_id": "abc-123", "variable_overrides": []},
        {
            "target_automation": {"id": "def-456", "name": "Other"},
            "variable_to_override": {"id": "x", "name": "other_var"},
            "value_source": "automation_variable",
            "automation_variable": {"id": "x", "name": "other_var"},
        },
    ]
    with pytest.raises(PlanError, match="mixes grouped"):
        _collapse_variable_overrides(mixed)


def test_field_value_mappings_stay_bare_uuids(form: dict) -> None:
    """Unlike fields_to_clear, source_values/target_values are bare option
    UUID strings on read already — never expanded to objects. Pins the shape
    so a future server-side change to this read dialect is caught rather
    than assumed — checked deliberately, and not reproducing here."""
    blocks = _step_block(form, "modify_related_entities")
    mappings = [
        m
        for f in blocks["fields_to_modify"]
        for m in f.get("field_value_mappings") or []
    ]
    assert mappings
    for m in mappings:
        assert all(isinstance(v, str) for v in m["source_values"])
        assert all(isinstance(v, str) for v in m["target_values"])


# ---------------------------------------------------------------------------
# Plugin steps
# ---------------------------------------------------------------------------


def test_plugin_steps_translate_to_the_write_dialect() -> None:
    """Reads expand fields to {id, name, display_name, deleted}, variables
    to {name, deleted} and secrets to {id, name, deleted}; the write
    carries bare {id} fields and {name} variables and secrets."""
    payload = live_to_payload(load_fixture("automations/plugin_code_steps.raw.json"))
    with_fields = _step_block(payload, "plugin_code_step", 0)
    with_variable = _step_block(payload, "plugin_code_step", 1)
    assert with_fields == {
        "plugin_app_api_name": "example_plugin",
        "action_step_api_name": "summarize_records",
        "notify_plugin_developer_on_failure": False,
        "secrets": [],
        "inputs": [
            {
                "name": "heading",
                "input_type": "field",
                "field": {"id": "321de730-826e-46fc-b507-533fb5386701"},
                "data_type": "string",
                "is_list": False,
            },
            {
                "name": "object",
                "input_type": "static_value",
                "static_value": {
                    "value": "client_client",
                    "entity_record": None,
                    "employee": None,
                },
                "data_type": "string",
                "is_list": False,
            },
        ],
        "outputs": [
            {
                "name": "context",
                "output_type": "field",
                "field": {"id": "27d03bf6-ac68-4e68-a0d1-9b0858dc19f9"},
                "conflict_resolution": "update_if_blank",
                "create_missing_option": False,
                "data_type": "string",
                "is_list": False,
            }
        ],
    }
    assert with_variable["secrets"] == [{"name": "business_id"}]
    assert with_variable["inputs"][1]["variable"] == {"name": "context_text"}
    assert with_variable["outputs"][0]["variable"] == {"name": "context_text"}


def test_plugin_step_install_config_input_reads_back_bare() -> None:
    """A UI-built `business_plugin_config` input reads with every other
    source key null (seen live 2026-10-14); none of them is written back."""
    raw = copy.deepcopy(load_fixture("automations/plugin_code_steps.raw.json"))
    step = next(s for s in raw["steps"] if s["step_type"] == "plugin_code_step")
    step["action_plugin_code_step"]["inputs"] = [
        {
            "input_type": "business_plugin_config",
            "name": "settings",
            "variable": None,
            "field": None,
            "related_field": None,
            "static_value": None,
            "data_type": "string",
            "is_list": False,
        }
    ]
    (inp,) = _step_block(live_to_payload(raw), "plugin_code_step")["inputs"]
    assert inp == {
        "name": "settings",
        "input_type": "business_plugin_config",
        "data_type": "string",
        "is_list": False,
    }


# ---------------------------------------------------------------------------
# Branch groups, skipped conditions, step severity
# ---------------------------------------------------------------------------


def _step(payload: dict, key: str) -> dict:
    return next(s for s in payload["steps"] if s["key"] == key)


def test_branch_group_initiators_are_echoed(branching: dict) -> None:
    """Without the flag the server rejects every merge step: "Merge step's
    parent (s05_branch) must be a branch group initiator step." """
    initiators = {
        s["key"] for s in branching["steps"] if s["is_branch_group_initiator"]
    }
    assert initiators == {"s05_branch", "s07_branch", "s13_branch"}


def test_merged_condition_initiator_is_echoed(merged_condition: dict) -> None:
    assert _step(merged_condition, "s00_condition")["is_branch_group_initiator"]
    merge = _step(merged_condition, "s03_merge_branches")
    assert merge["parent_key"] == "s00_condition"
    assert merge["parent_yes_no"] == ""


def test_branch_and_merge_steps_carry_no_config_block(branching: dict) -> None:
    """Both types are read with no config block, so none is written back."""
    group_steps = [
        s for s in branching["steps"] if s["type"] in ("branch", "merge_branches")
    ]
    assert len(group_steps) == 8
    for s in group_steps:
        assert not [k for k in s if k.startswith("step_")]
        assert [k for k in s if k.startswith("action_")] == ["action_on_failure"]


def test_skipped_condition_keeps_continue_with_branch(
    skipped_condition: dict,
) -> None:
    """Dropping it is a 400: "This field is required when
    should_skip_execution is True for condition and goal steps." """
    cond = _step(skipped_condition, "s00_condition")
    assert cond["should_skip_execution"] is True
    assert cond["continue_with_branch"] == "no"
    others = [s for s in skipped_condition["steps"] if s is not cond]
    assert all("continue_with_branch" not in s for s in others)


def test_step_severity_is_echoed(skipped_condition: dict) -> None:
    """Omitting it silently resets the step to `inherit`."""
    levels = {
        s["key"]: s["error_notification_severity_level"]
        for s in skipped_condition["steps"]
    }
    assert levels.pop("s01_delay") == "warning"
    assert set(levels.values()) == {"inherit"}


def test_validator_flags_initiator_without_merge_step(branching: dict) -> None:
    payload = copy.deepcopy(branching)
    payload["steps"] = [s for s in payload["steps"] if s["key"] != "s14_merge_branches"]
    problems = validate_payload(payload)
    assert problems == [
        "branch group initiator 's13_branch' has 0 merge_branches children; "
        "the server requires exactly one"
    ]


def test_validator_flags_initiator_with_two_merge_steps(
    merged_condition: dict,
) -> None:
    payload = copy.deepcopy(merged_condition)
    extra = copy.deepcopy(_step(payload, "s03_merge_branches"))
    extra.update(id=None, key="s05_merge_branches", order=5)
    payload["steps"].append(extra)
    problems = validate_payload(payload)
    assert problems == [
        "branch group initiator 's00_condition' has 2 merge_branches children; "
        "the server requires exactly one"
    ]


def test_validator_flags_merge_step_under_non_initiator(branching: dict) -> None:
    """The exact graph today's CLI sent: merge steps kept, flags dropped."""
    payload = copy.deepcopy(branching)
    _step(payload, "s05_branch")["is_branch_group_initiator"] = False
    problems = validate_payload(payload)
    assert problems == [
        "merge_branches step 's06_merge_branches' has parent 's05_branch', "
        "which is not a branch group initiator (server 400s otherwise)"
    ]


@pytest.mark.parametrize("value", ["yes", "no"])
def test_validator_accepts_skipped_condition_with_branch(
    skipped_condition: dict, value: str
) -> None:
    payload = copy.deepcopy(skipped_condition)
    _step(payload, "s00_condition")["continue_with_branch"] = value
    assert validate_payload(payload) == []


@pytest.mark.parametrize("value", [None, "", "maybe"])
def test_validator_flags_skipped_condition_without_branch(
    skipped_condition: dict, value: str | None
) -> None:
    payload = copy.deepcopy(skipped_condition)
    _step(payload, "s00_condition")["continue_with_branch"] = value
    assert validate_payload(payload) == [
        "skipped condition 's00_condition' needs continue_with_branch 'yes' or "
        "'no' (server 400s otherwise)"
    ]


def test_validator_flags_skipped_goal_without_branch(kitchen: dict) -> None:
    payload = copy.deepcopy(kitchen)
    goal = _steps_by_type(payload, "goal")[0]
    goal["should_skip_execution"] = True
    assert validate_payload(payload) == [
        f"skipped goal '{goal['key']}' needs continue_with_branch 'yes' or "
        "'no' (server 400s otherwise)"
    ]


def test_call_llm_tool_settings_translate_to_ids() -> None:
    """Reads expand mcp_server/run_tools_as; the wire takes bare UUIDs. A
    step with no server sends an explicit null pair: a PUT that omits both
    keeps the server and clears the account (confirmed live 2026-10-27).
    live_to_payload runs the builder with a context that fails on any lookup."""
    raw = load_fixture("automations/call_llm_mcp.raw.json")
    steps = sorted(raw["steps"], key=lambda s: s["order"])
    with_tools, bare = (s["action_call_llm"] for s in steps)
    payload = live_to_payload(raw)
    keys = (
        "mcp_server_id",
        "run_tools_as_id",
        "max_sequential_turns",
        "tool_failure_policy",
    )
    assert [_step_block(payload, "call_llm", i)[k] for i in (0, 1) for k in keys] == [
        with_tools["mcp_server"]["id"],
        with_tools["run_tools_as"]["id"],
        with_tools["max_sequential_turns"],
        "never_fail",
        None,
        None,
        25,
        "never_fail",
    ]
    assert bare["mcp_server"] is None and with_tools["max_sequential_turns"] == 10


def test_validator_accepts_plain_fan_out() -> None:
    """A step with several plain children and no Branch card between them:
    the server still accepts it, so the group rules must not reject it."""
    payload = live_to_payload(load_fixture("automations/llm_comparison.raw.json"))
    children: dict[str, int] = {}
    for s in payload["steps"]:
        if s["parent_key"] and not s["parent_yes_no"]:
            children[s["parent_key"]] = children.get(s["parent_key"], 0) + 1
    fan_out = [k for k, n in children.items() if n > 1]
    assert fan_out
    assert all(_step(payload, k)["type"] != "branch" for k in fan_out)
    assert validate_payload(payload) == []


def test_diff_wire_payloads_reads_omitted_step_level_keys_as_defaults(
    skipped_condition: dict,
) -> None:
    """A spec-built payload omits the step-level keys the live side always
    carries. Omitted means the server default, so only real differences show."""
    spec = copy.deepcopy(skipped_condition)
    for s in spec["steps"]:
        for k in (
            "is_branch_group_initiator",
            "error_notification_severity_level",
            "continue_with_branch",
        ):
            s.pop(k, None)
    by_leaf = {
        e["path"].rsplit(".", 1)[1]: (e["before"], e["after"])
        for e in diff_wire_payloads(skipped_condition, spec)
    }
    assert by_leaf == {
        "is_branch_group_initiator": (True, False),
        "error_notification_severity_level": ("warning", "inherit"),
        "continue_with_branch": ("no", "<absent>"),
    }


def _step_variables(payload: dict) -> list[dict]:
    steps = _steps_by_type(payload, "initialize_variable")
    return [s["action_initialize_variable"]["variable"] for s in steps]


def _variable_definitions(payload: dict) -> list[dict]:
    """Top-level `variables` and each initialize_variable step's definition."""
    return [*payload["variables"], *_step_variables(payload)]


def test_diff_ignores_what_the_server_derives_on_an_item_variable(
    loops: dict,
) -> None:
    """A spec never names `is_array_item_variable`/`source_array_variable`:
    the server sets both from the loop (confirmed live 2026-10-27)."""
    assert any(v["is_array_item_variable"] for v in _variable_definitions(loops))
    spec = copy.deepcopy(loops)
    for v in _variable_definitions(spec):
        del v["is_array_item_variable"], v["source_array_variable"]
    assert diff_wire_payloads(loops, spec) == []
    assert diff_wire_payloads(spec, loops) == []


def test_diff_reads_a_null_variable_key_as_absent(loops: dict) -> None:
    """A spec can't send null, and an omitted key reads back null."""
    spec = copy.deepcopy(loops)
    for v in _variable_definitions(spec):
        for k in [k for k, x in v.items() if x is None]:
            del v[k]
    assert diff_wire_payloads(loops, spec) == []


def _top(payload: dict) -> dict:
    return payload


def _init_block(payload: dict) -> dict:
    return _steps_by_type(payload, "initialize_variable")[0][
        "action_initialize_variable"
    ]


def _static_list_source(payload: dict) -> dict:
    return _init_block(payload)["sources"][0]


def _update_source(payload: dict) -> dict:
    (step,) = _steps_by_type(payload, "update_variable")
    return step["action_update_variable"]["sources"][0]


def _step_zero(payload: dict) -> dict:
    return payload["steps"][0]


def _trigger_zero(payload: dict) -> dict:
    return payload["triggers"][0]


# (fixture, where the key lives, key, server default, a non-default value)
_SERVER_DEFAULTS = {
    "error_notification_email": ("loops", _top, "", "ops@example.com"),
    "deduplicate_array": ("loops", _init_block, False, True),
    "array_separator": ("loops", _static_list_source, "comma", "semicolon"),
    "value_html": ("branching_test", _update_source, "", "<p>x</p>"),
    "step description": (
        "loops",
        _step_zero,
        "Initialize Variable",
        "arr0 = [2 items]",
    ),
    "trigger description": ("loops", _trigger_zero, "Manual", "Run by hand"),
}


def _defaults_case(name: str) -> tuple[dict, dict, str, object, object]:
    fixture, where, default, other = _SERVER_DEFAULTS[name]
    key = name.split()[-1]
    live = live_to_payload(load_fixture(f"automations/{fixture}.raw.json"))
    where(live)[key] = default
    return live, copy.deepcopy(live), key, default, other


@pytest.mark.parametrize("name", sorted(_SERVER_DEFAULTS))
def test_diff_reads_an_omitted_key_as_its_server_default(name: str) -> None:
    """Omitted on a PUT, each reads back as its default (confirmed live
    2026-10-27), so a spec that leaves it out changes nothing."""
    live, spec, key, _, _ = _defaults_case(name)
    del _SERVER_DEFAULTS[name][1](spec)[key]
    assert diff_wire_payloads(live, spec) == []
    assert diff_wire_payloads(spec, live) == []


@pytest.mark.parametrize("name", sorted(_SERVER_DEFAULTS))
@pytest.mark.parametrize("non_default_side", ["live", "spec"])
def test_diff_still_shows_a_non_default_value(name: str, non_default_side: str) -> None:
    """The key is compared as its default, not dropped: a non-default value on
    either side diffs against an omitted one."""
    live, spec, key, default, other = _defaults_case(name)
    where = _SERVER_DEFAULTS[name][1]
    del where(spec)[key]
    if non_default_side == "live":
        where(live)[key] = other
        expected = [(other, default)]
    else:
        where(spec)[key] = other
        expected = [(default, other)]
    assert [(e["before"], e["after"]) for e in diff_wire_payloads(live, spec)] == (
        expected
    )


@pytest.mark.parametrize("top_level", [False, True])
def test_diff_still_shows_an_item_variable_change(loops: dict, top_level: bool) -> None:
    spec = copy.deepcopy(loops)
    item = next(
        v
        for v in (spec["variables"] if top_level else _step_variables(spec))
        if v["is_array_item_variable"]
    )
    item["data_type"] = "uuid"
    assert [(e["before"], e["after"]) for e in diff_wire_payloads(loops, spec)] == [
        ("string", "uuid")
    ]


# ---------------------------------------------------------------------------
# Loops: `step_loop` by name, configless closers, and the server's loop rules
# (each rule a live 400, each accepted shape a live 200, 2026-10-27)
# ---------------------------------------------------------------------------


def test_loop_reads_its_step_loop_block() -> None:
    from kizen_builder.tools.planners.automations import _block_field_for

    assert _block_field_for("loop") == "step_loop"


def test_loop_variables_are_sent_by_name(loops: dict, ui_loops: dict) -> None:
    """Reads expand both variables; ids are refused on write."""
    blocks = [s["step_loop"] for s in _steps_by_type(loops, "loop")]
    assert blocks == [
        {
            "loop_type": "for_each_item",
            "array_variable": a,
            "item_variable": f"{a}_item",
        }
        for a in ("arr0", "arr1")
    ]
    (record_loop, _) = _steps_by_type(ui_loops, "loop")
    assert record_loop["step_loop"]["array_variable"] == "array_var_obj"


def test_loop_builder_takes_names_or_read_dicts() -> None:
    from kizen_builder.tools.planners.automations import _step_loop

    names = {"array_variable": "arr", "item_variable": "arr_item"}
    read = {k: {"id": "x", "name": v, "is_array": False} for k, v in names.items()}
    expected = {"loop_type": "for_each_item", **names}
    assert _step_loop(names, None, None) == expected
    assert _step_loop(read, None, None) == expected


def test_loop_keeps_its_initiator_flag(loops: dict) -> None:
    assert all(s["is_branch_group_initiator"] for s in _steps_by_type(loops, "loop"))


@pytest.mark.parametrize("step_type", ["next_or_finish", "exit_loop"])
def test_loop_closers_carry_no_config_block(loops: dict, step_type: str) -> None:
    for s in _steps_by_type(loops, step_type):
        assert not [k for k in s if k.startswith("step_")]
        assert [k for k in s if k.startswith("action_")] == ["action_on_failure"]


def test_misread_config_block_is_refused() -> None:
    """A block the translator doesn't read for the step's type would be
    dropped from the PUT, as `step_loop` once was."""
    raw = load_fixture("automations/loops.raw.json")
    step = next(s for s in raw["steps"] if s["step_type"] == "delay")
    step["step_foo"] = {"x": 1}
    key = synthesize_step_keys(raw)[step["id"]]
    with pytest.raises(PlanError) as e:
        live_to_payload(raw)
    assert str(e.value) == (
        f"automation '{raw['name']}' step '{key}': step_foo would be lost on PUT "
        "— the CLI doesn't read that block for a 'delay' step, so it stopped "
        "before writing anything. Change this step in the Kizen UI until the CLI "
        "supports it."
    )


def test_block_on_a_configless_step_is_refused() -> None:
    raw = load_fixture("automations/loops.raw.json")
    step = next(s for s in raw["steps"] if s["step_type"] == "exit_loop")
    step["action_exit_loop"] = {"x": 1}
    with pytest.raises(PlanError, match="action_exit_loop would be lost on PUT"):
        live_to_payload(raw)


@pytest.mark.parametrize("value", [True, "on", 3, ["x"]])
def test_scalar_step_level_key_is_not_refused(value: object) -> None:
    """Only a config block (a dict) can be lost; a new step-level setting
    must not lock every automation out of CLI writes."""
    raw = load_fixture("automations/loops.raw.json")
    next(s for s in raw["steps"] if s["step_type"] == "delay")["step_new"] = value
    live_to_payload(raw)


@pytest.mark.parametrize("value", [None, {}])
def test_empty_misread_block_is_not_refused(value: object) -> None:
    raw = load_fixture("automations/loops.raw.json")
    next(s for s in raw["steps"] if s["step_type"] == "loop")["action_loop"] = value
    assert "action_loop" not in json.dumps(live_to_payload(raw))


def _ws(key: str, step_type: str, parent: str | None = None, branch: str = "", **kw):
    return {
        "key": key,
        "type": step_type,
        "parent_key": parent,
        "parent_yes_no": branch,
        **kw,
    }


def _var(key: str, name: str, parent: str | None, data_type: str = "string", **kw):
    variable = {"name": name, "data_type": data_type, "is_array": False, **kw}
    return _ws(
        key,
        "initialize_variable",
        parent,
        action_initialize_variable={
            "sources": [],
            "variable": variable,
        },
    )


def _loop(key: str, parent: str, array: str, item: str, branch: str = "", **kw):
    block = {
        "loop_type": "for_each_item",
        "array_variable": array,
        "item_variable": item,
    }
    return _ws(key, "loop", parent, branch, step_loop=block, **kw)


def _head(n: int = 1, data_type: str = "string", subtype: str | None = None):
    """`arr<i>` arrays and their `arr<i>_item` variables, chained."""
    steps, prev = [], None
    for i in range(n):
        steps.append(
            _var(
                f"va{i}",
                f"arr{i}",
                prev,
                data_type,
                is_array=True,
                data_subtype=subtype,
            )
        )
        steps.append(
            _var(f"vi{i}", f"arr{i}_item", f"va{i}", data_type, data_subtype=subtype)
        )
        prev = f"vi{i}"
    return steps, prev


def _graph(steps: list[dict]) -> dict:
    for order, s in enumerate(steps):
        s["order"] = order
    return {"steps": steps, "triggers": []}


def _delay(key: str, parent: str, branch: str = "") -> dict:
    return _ws(key, "delay", parent, branch, step_delay={"minutes": 1})


def _base(**kw) -> list[dict]:
    """head → loop `l` → body `b1`, closed by `n`, then `after`."""
    steps, p = _head()
    return steps + [
        _loop("l", p, "arr0", "arr0_item", is_branch_group_initiator=True),
        _delay("b1", "l"),
        _ws("n", "next_or_finish", "l"),
        _delay("after", "n"),
    ]


def _nest(depth: int) -> list[dict]:
    steps, parent = _head(depth)
    for d in range(depth):
        steps.append(_loop(f"l{d}", parent, f"arr{d}", f"arr{d}_item"))
        parent = f"l{d}"
    steps.append(_delay("inner", parent))
    steps += [_ws(f"n{d}", "next_or_finish", f"l{d}") for d in reversed(range(depth))]
    return steps


def _accepted_shapes() -> dict[str, list[dict]]:
    shapes: dict[str, list[dict]] = {"A2-plain": _base()}
    s = _base()
    s[1]["action_initialize_variable"]["variable"].update(
        is_array_item_variable=True, source_array_variable="arr0"
    )
    shapes["A3-item-with-read-flags"] = s
    s = _base()
    s[2].pop("is_branch_group_initiator")
    shapes["A4-loop-not-flagged"] = s
    s = _base()
    s[-1]["parent_key"] = "l"
    shapes["A5-after-hangs-off-loop"] = s
    shapes["A7-empty-body"] = [x for x in _base() if x["key"] != "b1"]
    head, p = _head(data_type="entity", subtype="obj-1")
    shapes["A8-record-array"] = head + [
        _loop("l", p, "arr0", "arr0_item"),
        _delay("b1", "l"),
        _ws("n", "next_or_finish", "l"),
    ]
    shapes["A9-exit-after-body-step"] = _base() + [_ws("x", "exit_loop", "b1")]
    shapes["B5-nest-3"] = _nest(3)
    head, p = _head()
    shapes["B7-loop-on-yes-branch"] = head + [
        _ws("c", "condition", p),
        _loop("l", "c", "arr0", "arr0_item", branch="yes"),
        _delay("b1", "l"),
        _ws("n", "next_or_finish", "l"),
        _delay("no1", "c", "no"),
    ]
    shapes["B9-step-after-exit"] = _base() + [
        _ws("x", "exit_loop", "b1"),
        _delay("after_x", "x"),
    ]
    shapes["B10-exit-direct-child"] = _base() + [_ws("x", "exit_loop", "l")]
    shapes["B13-two-body-lanes"] = _base() + [_delay("b2", "l")]
    shapes["B14-branch-group-in-body"] = _base() + [
        _ws("br", "branch", "l", is_branch_group_initiator=True),
        _delay("lane1", "br"),
        _delay("lane2", "br"),
        _ws("m", "merge_branches", "br"),
    ]
    head, p = _head(2)
    shapes["D2-exit-after-inner-loop"] = head + [
        _loop("l0", p, "arr0", "arr0_item"),
        _loop("l1", "l0", "arr1", "arr1_item"),
        _delay("ib", "l1"),
        _ws("n1", "next_or_finish", "l1"),
        _ws("x", "exit_loop", "n1"),
        _ws("n0", "next_or_finish", "l0"),
    ]
    head, p = _head(data_type="uuid")
    shapes["D3-uuid-array"] = head + [
        _loop("l", p, "arr0", "arr0_item"),
        _ws("n", "next_or_finish", "l"),
    ]
    return shapes


_ACCEPTED = _accepted_shapes()


@pytest.mark.parametrize("name", sorted(_ACCEPTED))
def test_loop_shapes_the_server_accepts_validate(name: str) -> None:
    assert validate_payload(_graph(copy.deepcopy(_ACCEPTED[name]))) == []


@pytest.mark.parametrize("depth", [4, 6])
def test_deep_nesting_the_server_accepts_fails_only_the_cli_rule(depth: int) -> None:
    """B6 and C1 saved live; Kizen's builder can't hold them, so the CLI refuses."""
    assert _problems(_nest(depth)) == [
        f"loop 'l{d}' is nested {d + 1} levels deep; Kizen's builder allows at "
        "most three. The API accepts deeper loops, but the CLI refuses them so "
        "the automation stays editable in the UI."
        for d in range(3, depth)
    ]


def _nest_through_conditions(depth: int) -> list[dict]:
    """Like `_nest`, but each inner loop sits on the yes lane of a condition
    in the outer loop's body."""
    steps, parent = _head(depth)
    for d in range(depth):
        branch = ""
        if d:
            steps.append(_ws(f"c{d}", "condition", parent))
            parent, branch = f"c{d}", "yes"
        steps.append(_loop(f"l{d}", parent, f"arr{d}", f"arr{d}_item", branch))
        parent = f"l{d}"
    steps += [_ws(f"n{d}", "next_or_finish", f"l{d}") for d in reversed(range(depth))]
    return steps


def test_depth_counts_loops_through_a_condition_lane() -> None:
    """A condition between levels is not a level, and doesn't end the walk."""
    assert _problems(_nest_through_conditions(3)) == []
    assert _problems(_nest_through_conditions(4)) == [
        "loop 'l3' is nested 4 levels deep; Kizen's builder allows at most three. "
        "The API accepts deeper loops, but the CLI refuses them so the automation "
        "stays editable in the UI."
    ]


def test_one_array_at_two_levels_the_server_accepts_fails_only_the_cli_rule() -> None:
    """The API saved one array in an outer and an inner loop, each with its own
    item variable (confirmed live 2026-10-27). Kizen's builder refuses it, so
    the CLI does too, and with nothing else wrong."""
    steps, p = _head()
    steps += [
        _var("vb", "inner_item", p),
        _loop("outer", "vb", "arr0", "arr0_item"),
        _loop("inner", "outer", "arr0", "inner_item"),
        _delay("b1", "inner"),
        _ws("n_in", "next_or_finish", "inner"),
        _ws("n_out", "next_or_finish", "outer"),
    ]
    assert _problems(steps) == [
        "loop 'inner' iterates 'arr0', which loop 'outer' around it already "
        "iterates; Kizen's builder needs a different array variable at each "
        "level. The API accepts it, but the CLI refuses it so the automation "
        "stays editable in the UI."
    ]


@pytest.mark.parametrize("fixture", ["loops", "ui_loops"])
def test_captured_loop_graphs_validate(request, fixture: str) -> None:
    assert validate_payload(request.getfixturevalue(fixture)) == []


def _problems(steps: list[dict]) -> list[str]:
    return validate_payload(_graph(steps))


_NOF_COUNT = (
    "loop 'l' has {n} next_or_finish children; the server requires exactly "
    "one, which closes the loop body (server 400s otherwise)"
)


def test_loop_without_next_or_finish_is_flagged() -> None:
    steps = [s for s in _base() if s["key"] not in ("n", "after")]
    assert _problems(steps) == [_NOF_COUNT.format(n=0)]


def test_loop_with_two_next_or_finish_is_flagged() -> None:
    assert _problems(_base() + [_ws("n2", "next_or_finish", "l")]) == [
        _NOF_COUNT.format(n=2)
    ]


def test_merge_step_closing_a_loop_is_flagged() -> None:
    steps = _base()
    steps[-2] = _ws("n", "merge_branches", "l")
    assert _problems(steps) == [
        _NOF_COUNT.format(n=0),
        "loop 'l' has a merge_branches child; a loop is closed by next_or_finish, "
        "not merge_branches (server 400s otherwise)",
    ]


_NOT_UNDER_LOOP = (
    "next_or_finish step '{k}' has parent '{p}', which is not a loop; it must "
    "hang directly off the loop it closes (server 400s otherwise)"
)


def test_next_or_finish_chained_after_the_body_is_flagged() -> None:
    steps = _base()
    steps[-2]["parent_key"] = "b1"
    assert _problems(steps) == [
        _NOF_COUNT.format(n=0),
        _NOT_UNDER_LOOP.format(k="n", p="b1"),
    ]


def test_next_or_finish_closing_a_condition_is_flagged() -> None:
    steps = _base() + [
        _ws("c", "condition", "b1", is_branch_group_initiator=True),
        _ws("cn", "next_or_finish", "c"),
    ]
    assert _NOT_UNDER_LOOP.format(k="cn", p="c") in _problems(steps)


_EXIT = "exit_loop step 'x' is not inside a loop body (server 400s otherwise)"


def test_exit_loop_outside_any_loop_is_flagged() -> None:
    head, p = _head()
    assert _problems(head + [_delay("d", p), _ws("x", "exit_loop", "d")]) == [_EXIT]


def test_exit_loop_after_the_next_or_finish_is_flagged() -> None:
    assert _problems(_base() + [_ws("x", "exit_loop", "n")]) == [_EXIT]


def test_loop_over_a_scalar_is_flagged() -> None:
    steps = _base()
    steps[0]["action_initialize_variable"]["variable"]["is_array"] = False
    assert _problems(steps) == [
        "loop 'l' iterates 'arr0', which is not an array variable of records, "
        "strings or UUIDs (server 400s otherwise)"
    ]


def test_loop_over_an_undeclared_array_is_flagged() -> None:
    steps = _base()
    steps[2]["step_loop"]["array_variable"] = "nope"
    assert _problems(steps) == [
        "loop 'l' iterates 'nope', which is not an array variable of records, "
        "strings or UUIDs (server 400s otherwise)"
    ]


_ITEM = (
    "loop 'l' item variable 'arr0_item' must be declared by an "
    "initialize_variable step as a single {t} variable, the type of 'arr0' "
    "(server 400s otherwise)"
)


def test_undeclared_item_variable_is_flagged() -> None:
    steps = [s for s in _base() if s["key"] != "vi0"]
    steps[1]["parent_key"] = "va0"
    assert _problems(steps) == [_ITEM.format(t="string")]


@pytest.mark.parametrize(
    "change",
    [{"data_type": "uuid"}, {"is_array": True}],
    ids=["other-type", "array"],
)
def test_mismatched_item_variable_is_flagged(change: dict) -> None:
    steps = _base()
    steps[1]["action_initialize_variable"]["variable"].update(change)
    assert _problems(steps) == [_ITEM.format(t="string")]


def test_item_variable_of_another_object_is_flagged() -> None:
    steps = _ACCEPTED["A8-record-array"]
    steps = copy.deepcopy(steps)
    steps[1]["action_initialize_variable"]["variable"]["data_subtype"] = "obj-2"
    assert _problems(steps) == [_ITEM.format(t="entity")]


def test_loops_sharing_an_item_variable_are_flagged() -> None:
    steps = _base()
    steps[-1:] = [
        _loop("l2", "n", "arr0", "arr0_item"),
        _ws("n2", "next_or_finish", "l2"),
    ]
    assert _problems(steps) == [
        "loops 'l' and 'l2' share item variable 'arr0_item'; each loop needs its "
        "own (server 400s otherwise)"
    ]


def _show(monkeypatch, fixture: str, *args: str) -> str:
    from typer.testing import CliRunner

    import kizen_builder.cli as cli
    from kizen_builder.api import automations as auto_api

    raw = load_fixture(fixture)
    monkeypatch.setattr(
        auto_api,
        "list_automations",
        lambda c: [{"api_name": raw["api_name"], "id": raw["id"]}],
    )
    monkeypatch.setattr(auto_api, "get_automation", lambda c, i: raw)
    result = CliRunner().invoke(
        cli.app, ["automations", "show", raw["api_name"], *args]
    )
    assert result.exit_code == 0, result.output
    return result.stdout


def test_show_labels_a_loop_with_its_variables(monkeypatch) -> None:
    out = _show(monkeypatch, "automations/loops.raw.json")
    assert "s04_loop [for each arr0 as arr0_item]  Loop" in out
    assert "s10_loop [for each arr1 as arr1_item]  Loop" in out


def test_show_puts_the_body_under_the_loop_and_the_rest_after_it(
    monkeypatch,
) -> None:
    out = _show(monkeypatch, "automations/loops.raw.json")
    tree = out[out.index("└── s04_loop") :]
    assert [line.rstrip() for line in tree.splitlines()] == [
        "└── s04_loop [for each arr0 as arr0_item]  Loop",
        "    ├── body",
        "    │   └── s05_condition  Condition",
        "    │       ├── yes",
        "    │       │   └── s06_delay  Delay",
        "    │       ├── no",
        "    │       │   └── s07_exit_loop  Exit Loop",
        "    │       └── s08_merge_branches  Merge Branches",
        "    └── s09_next_or_finish  Next/Finish",
        "        └── s10_loop [for each arr1 as arr1_item]  Loop",
        "            ├── body",
        "            │   └── s11_delay  Delay",
        "            └── s12_next_or_finish  Next/Finish",
        "                └── s13_delay  Delay",
    ]


def test_show_json_is_the_translated_payload(monkeypatch) -> None:
    out = _show(monkeypatch, "automations/loops.raw.json", "--json")
    raw = load_fixture("automations/loops.raw.json")
    assert json.loads(out)["payload"] == live_to_payload(raw)

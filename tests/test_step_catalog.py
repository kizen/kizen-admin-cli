"""The catalog behind `kizen automations step-types` stays tied to the
planner registries and the spec models."""

from __future__ import annotations

import json
import uuid
from typing import get_args

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.models.spec import AutomationDef, AutomationStepDef
from kizen_builder.models.spec.automations_actions_messaging import (
    ActivityAssignmentType,
)
from kizen_builder.models.spec.automations_shared import SOURCE_SUBTYPES
from kizen_builder.models.spec.automations_triggers import AutomationTriggerDef
from kizen_builder.tools import automation_check, step_catalog
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    STEP_PURPOSES,
    TRIGGER_PURPOSES,
    LiveContext,
    _build_automation_payload,
)
from tests.conftest import plain

runner = CliRunner()


ALL_TYPES = (*step_catalog.STEP_TYPES, *step_catalog.TRIGGER_TYPES)


def test_catalog_lists_exactly_the_wired_types():
    assert set(step_catalog.STEP_TYPES) == {*_STEP_BUILDERS, *_CONFIGLESS_STEP_TYPES}
    assert set(step_catalog.TRIGGER_TYPES) == set(_TRIGGER_BUILDERS)
    assert [r["type"] for r in step_catalog.list_types()] == list(ALL_TYPES)


def test_no_step_and_trigger_share_a_name():
    """`show <type>` finds the kind from the name alone. A shared name would
    need a flag to pick one."""
    assert not set(step_catalog.STEP_TYPES) & set(step_catalog.TRIGGER_TYPES)


def test_every_wired_type_has_a_purpose():
    assert set(STEP_PURPOSES) == set(step_catalog.STEP_TYPES)
    assert set(TRIGGER_PURPOSES) == set(step_catalog.TRIGGER_TYPES)
    assert all(r["purpose"] for r in step_catalog.list_types())


@pytest.mark.parametrize("name", ALL_TYPES)
def test_every_type_describes(name):
    info = step_catalog.describe(name)
    assert info["variants"], name
    json.dumps(info)  # --json must serialize
    for v in info["variants"]:
        spec = v["spec"]
        if info["kind"] == "step":
            assert spec["step_type"] == name and spec["parent_key"] is None
        else:
            assert spec["trigger_type"] == name
        assert "order" not in spec


def _variants():
    return [
        (name, v["name"])
        for name in ALL_TYPES
        for v in step_catalog.describe(name)["variants"]
    ]


def _with_orders(spec):
    return automation_check.with_defaults(spec)[0]


def test_skeleton_and_variants_check_with_no_order_warning():
    """Entries carry no `order`; the planner numbers them by position, so
    pasting several in a row doesn't trip the order check."""
    auto = step_catalog.skeleton("deals")
    auto["name"] = "Probe"
    auto["steps"][0]["action_change_field_value"] = {
        "field_ref": "deals.name",
        "specific_field_value": "x",
    }
    wait = step_catalog.variant_spec("delay", "minimal")
    activity = step_catalog.variant_spec("schedule_activity", "immediately")
    activity["parent_key"] = "delay"
    activity["action_schedule_activity"]["activity_type"] = "follow_up"
    auto["steps"] += [wait, activity]
    changed = step_catalog.variant_spec("field_updated", "any_change")
    changed["trigger_field_updated"]["field_ref"] = "deals.name"
    auto["triggers"].append(changed)
    checked = automation_check.check_automation(auto)
    assert [s.order for s in checked.auto.steps] == [0, 1, 2]
    assert [t.order for t in checked.auto.triggers] == [0, 1]
    assert not [w for w in checked.warnings if "order" in w.message]


@pytest.mark.parametrize(("name", "variant"), _variants())
def test_every_variant_is_a_valid_entry(name, variant):
    spec = step_catalog.variant_spec(name, variant)
    model = AutomationStepDef if "step_type" in spec else AutomationTriggerDef
    model.model_validate({"order": 0, **spec})  # the planner numbers steps


@pytest.mark.parametrize("target", [None, "deals"])
def test_skeleton_is_a_valid_automation(target):
    auto = AutomationDef.model_validate(_with_orders(step_catalog.skeleton(target)))
    assert auto.target_object == (target or "my_object")
    assert [t.trigger_type for t in auto.triggers] == ["manual"]
    assert len(auto.steps) == 1


def test_aliases_name_wired_types_only():
    wired = set(ALL_TYPES)
    assert not set(step_catalog.ALIASES) & wired
    for alias, targets in step_catalog.ALIASES.items():
        assert targets and set(targets) <= wired, alias


@pytest.mark.parametrize(
    ("given", "meant"),
    [("wait", "delay"), ("field_changed", "field_updated"), ("delay", "delay")],
)
def test_resolve_takes_an_alias_with_one_target(given, meant):
    assert step_catalog.resolve(given) == meant


def test_alias_with_several_targets_lists_them():
    with pytest.raises(step_catalog.UnknownType) as e:
        step_catalog.resolve("send_email")
    assert str(e.value) == (
        "'send_email' isn't a type name. It could be:\n"
        f"  notify_member_via_email: {STEP_PURPOSES['notify_member_via_email']}\n"
        f"  send_related_contact_email: {STEP_PURPOSES['send_related_contact_email']}"
    )


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        ("create_activity", "'schedule_activity'"),
        ("shedule_activity", "'schedule_activity'"),
        ("trigger_manual", "'manual'"),
        ("update_records", "'change_field_value'"),
        ("send_emails", "'notify_member_via_email' or 'send_related_contact_email'"),
    ],
)
def test_unknown_type_suggests_what_was_meant(given, meant):
    with pytest.raises(step_catalog.UnknownType, match=f"Did you mean {meant}"):
        step_catalog.describe(given)


def test_unknown_type_with_no_near_name_points_at_list():
    with pytest.raises(step_catalog.UnknownType, match="step-types list"):
        step_catalog.describe("zzz")


def test_unknown_variant_names_the_variants():
    with pytest.raises(step_catalog.UnknownType, match="Variants: minimal"):
        step_catalog.variant_spec("delay", "nope")


@pytest.mark.parametrize(
    ("type_name", "guide"),
    list(step_catalog._STEP_GUIDES.items()),
    ids=list(step_catalog._STEP_GUIDES),
)
def test_guide_blocks_raise_no_selector_findings(type_name, guide):
    for variant in guide.variants:
        assert step_catalog.selector_findings(type_name, variant.block) == []


def test_selector_findings_accept_the_live_read_spelling():
    block = {
        "assigned_to": {"assignment_type": "team_member", "employee": {"id": "x"}},
        "schedule": {"type": "immediately"},
    }
    assert step_catalog.selector_findings("schedule_activity", block) == []
    block["assigned_to"] = {"type": "team_member"}
    (finding,) = step_catalog.selector_findings("schedule_activity", block)
    assert finding["path"] == "assigned_to.employee_id"


def test_an_id_key_without_its_selector_names_the_options_it_fits():
    block = {"assigned_to": {"role_id": "x"}, "schedule": {"type": "immediately"}}
    (finding,) = step_catalog.selector_findings("schedule_activity", block)
    assert finding["path"] == "assigned_to.assignment_type"
    assert finding["message"] == (
        "'role_id' is given but assigned_to.assignment_type isn't; set it to "
        "'specific_role' or 'round_robin_role' or 'last_active_role'"
    )


def test_guide_selectors_match_the_model_choices():
    """A guide can't offer an option the model refuses, or leave one out."""
    fields = {
        f["path"]: f for f in step_catalog.describe("schedule_activity")["fields"]
    }
    for selector in step_catalog._STEP_GUIDES["schedule_activity"].selectors:
        offered = {o.value for o in selector.options}
        assert offered == set(fields[selector.path]["choices"]), selector.path
        for option in selector.options:
            parent = selector.path.rsplit(".", 1)[0]
            for need in option.needs:
                assert f"{parent}.{need}" in fields, (selector.path, need)
    assert set(get_args(ActivityAssignmentType)) == set(
        fields["assigned_to.assignment_type"]["choices"]
    )


_GUIDES = {**step_catalog._STEP_GUIDES, **step_catalog._TRIGGER_GUIDES}


@pytest.mark.parametrize("type_name", sorted(_GUIDES))
def test_every_guide_selector_matches_the_model(type_name):
    """Where the model types a selector, the guide offers exactly its
    choices, and every key an option needs is a key of the block."""
    fields = {f["path"]: f for f in step_catalog.describe(type_name)["fields"]}
    for selector in _GUIDES[type_name].selectors:
        choices = fields[selector.path].get("choices")
        if choices:
            assert {o.value for o in selector.options} == set(choices), selector.path
        parent = selector.path.rpartition(".")[0]
        for option in selector.options:
            for need in option.needs:
                key = f"{parent}.{need}" if parent else need
                assert key in fields, (selector.path, need)


def test_condition_lists_the_filter_keys_and_ops():
    from kizen_builder import filtering

    fields = {f["path"]: f for f in step_catalog.describe("condition")["fields"]}
    assert fields["filter_config.all[].op"]["choices"] == filtering.VALID_SPEC_OPS
    assert fields["filter_config.any"]["same_as"] == "filter_config.all"
    assert "filter_config.query[].filters" in fields
    assert "filter_config.all[].variable" in fields


@pytest.mark.parametrize(
    "type_name, path, choices",
    [
        ("initialize_variable", "sources[].array_separator", ["comma", "newline"]),
        (
            "change_field_value",
            "change_type",
            [
                "specific_value",
                "related_object_field",
                "this_record_field",
                "variable",
                "entity_variable_field",
                "blank",
            ],
        ),
        ("goal", "delay_type", ["minutes", "hours", "days", ""]),
        (
            "modify_related_entities",
            "fields_to_modify[].value_type",
            [
                "specific_value",
                "context_entity_value",
                "variable",
                "entity_variable_field",
                "blank",
            ],
        ),
    ],
)
def test_typed_enum_fields_list_their_choices(type_name, path, choices):
    fields = {f["path"]: f for f in step_catalog.describe(type_name)["fields"]}
    assert fields[path]["choices"] == choices


@pytest.mark.parametrize("type_name", ["initialize_variable", "update_variable"])
def test_source_subtypes_per_source_type_are_on_the_card(type_name):
    notes = "\n".join(step_catalog.describe(type_name)["notes"])
    for source_type, subtypes in SOURCE_SUBTYPES.items():
        assert f"`{source_type}`: {', '.join(subtypes)}." in notes
    assert "`primary_related_object_field`" in notes


def test_schedule_activity_variants_build_a_payload(patch_live_lookups):
    """The blocks the CLI hands out go through the real builder once their
    placeholders name things the fixture env has."""
    fills = {
        "<activity api_name>": "11111111-1111-4111-8111-111111111111",
        "<object>.<date field>": "patients.date_of_birth",
        "<team member uuid>": "22222222-2222-4222-8222-222222222222",
    }
    for variant in step_catalog._SCHEDULE_ACTIVITY.variants:
        text = json.dumps(step_catalog.variant_spec("schedule_activity", variant.name))
        for placeholder, value in fills.items():
            text = text.replace(placeholder, value)
        auto = AutomationDef.model_validate(
            _with_orders(
                {
                    "name": "t",
                    "api_name": "t",
                    "target_object": "patients",
                    "steps": [json.loads(text)],
                }
            )
        )
        block = _build_automation_payload(auto, LiveContext())["steps"][0][
            "action_schedule_activity"
        ]
        assert block["schedule"]["type"] == variant.block["schedule"]["type"]
        assert "field_ref" not in json.dumps(block), variant.name


_FILLS = {
    "<object api_name>": "encounters",
    "<object>.<relationship field>": "patients.encounters",
    "<employee UUID>": "22222222-2222-4222-8222-222222222222",
    "<role UUID>": "33333333-3333-4333-8333-333333333333",
    "<stage UUID>": "44444444-4444-4444-8444-444444444444",
    "<variable name>": "made",
    "<number field UUID>": "55555555-5555-4555-8555-555555555555",
}


def _built_variants(type_name, target="patients", **fills):
    """Each variant of ``type_name`` built by the real builder on a
    ``target`` automation, once its placeholders name things the fixture
    env has."""
    block_key = step_catalog.describe(type_name)["block"]
    for variant in step_catalog._STEP_GUIDES[type_name].variants:
        text = json.dumps(step_catalog.variant_spec(type_name, variant.name))
        for placeholder, value in {**_FILLS, **fills}.items():
            text = text.replace(placeholder, value)
        auto = AutomationDef.model_validate(
            _with_orders(
                {
                    "name": "t",
                    "api_name": "t",
                    "target_object": target,
                    "steps": [json.loads(text)],
                }
            )
        )
        payload = _build_automation_payload(auto, LiveContext())
        yield variant, payload["steps"][0][block_key]


def test_create_related_entity_variants_build_a_payload(patch_live_lookups):
    for variant, block in _built_variants("create_related_entity"):
        assert set(variant.block) <= set(block), variant.name
        for key in ("new_entity_owner_type", "new_entity_owner_sub_type"):
            assert block.get(key) == variant.block.get(key), variant.name
        uuid.UUID(block["target_custom_object"])


def test_math_operator_variants_build_a_payload(patch_live_lookups):
    for variant, block in _built_variants("math_operator"):
        assert block["subtype"] == variant.block["subtype"]
        if "variable" in variant.block:
            assert block["variable"] == {"name": "made"}
        for arg in block["simple_builder_arguments"]:
            assert arg["argument_type"] in arg or (
                arg["argument_type"] == "field" and "field_id" in arg
            )
            if arg["argument_type"] == "variable":
                assert arg["variable"] == {"name": "made"}


def test_code_step_variants_build_a_payload(patch_live_lookups):
    fills = {
        "<object>.<field>": "encounters.encounter_priority",
        "<object>.<relationship field>": "encounters.patient_rel",
        "<related object>.<field>": "patients.mrn",
    }
    for variant, block in _built_variants("code_step", "encounters", **fills):
        spec = variant.block
        assert [i["name"] for i in block["inputs"]] == [
            i["name"] for i in spec["inputs"]
        ]
        for built, given in zip(block["inputs"], spec["inputs"], strict=True):
            assert built["input_type"] == given.get("input_type", "field")
            source = {
                "field": "field",
                "variable": "variable",
                "related_field": "related_field",
                "static_value": "static_value",
            }[built["input_type"]]
            assert built[source], (variant.name, built)
        for built in block["outputs"]:
            assert built.get("field") or built.get("variable"), variant.name


def test_assign_team_member_variants_build_a_payload(patch_live_lookups):
    fills = {
        "<object>.<team selector field>": "encounters.owner",
        "<related object>.<team selector field>": "patients.owner",
        "<relationship field UUID>": "889f65e2-dd31-42f0-a088-c3c1dd8ea484",
    }
    for variant, block in _built_variants("assign_team_member", "encounters", **fills):
        assert block["type"] == variant.block["type"]
        assert "field_ref" not in block


def test_modify_related_entities_variant_builds_a_payload(patch_live_lookups):
    for variant, block in _built_variants("modify_related_entities"):
        assert block["fields_to_modify"], variant.name


# Each per-type section the automation doc used to carry now lives on that
# type's card. One key sentence per section proves it moved, not vanished.
_MOVED_NOTES = [
    ("webhook", "?business_id=<uuid>"),
    ("on_or_around_date", "9 pm"),
    ("schedule", "no time-zone field"),
    ("email_received", "only client custom object"),
    ("activity_logged", "Branching on what someone logged"),
    ("scheduled_activity_overdue", "Branching on what someone logged"),
    ("form_submitted", "does not exist"),
    ("survey_submitted", "does not exist"),
    ("stage_updated", "made by another automation"),
    ("goal", "while the run waits"),
    ("change_field_value", "no move-stage step"),
    ("plugin_code_step", "preview install"),
    ("create_related_entity", "context_entity_field_resolution"),
    ("modify_related_entities", "hop chain"),
    ("file_content_extraction", "single-valued"),
    ("audio_transcription", "single-valued"),
    ("call_llm", "is_advanced: true"),
    ("call_llm", "MCP tools"),
    ("notify_member_via_email", "A template"),
    ("stop_execution", "pause_and_error"),
    ("schedule_activity", "19:48"),
    ("schedule_activity", "target_variable"),
    ("schedule_activity", "context_record"),
    ("assign_team_member", "writes only this record's `owner`"),
    ("code_step", "secret"),
]


@pytest.mark.parametrize(("type_name", "sentence"), _MOVED_NOTES)
def test_moved_section_is_on_its_card(type_name, sentence):
    notes = "\n".join(step_catalog.describe(type_name).get("notes", []))
    assert sentence in notes


@pytest.mark.parametrize(
    "type_name",
    [
        "call_llm",
        "notify_member_via_email",
        "notify_member_via_text",
        "send_related_contact_email",
        "send_related_contact_text",
    ],
)
def test_text_steps_point_at_the_merge_field_table(type_name):
    assert step_catalog._MERGE_FIELDS_NOTE in step_catalog.describe(type_name)["notes"]


def test_merge_field_table_is_a_concept():
    assert "entity_record.<field>" in step_catalog.CONCEPTS["merge-fields"]


# --- CLI ------------------------------------------------------------------


def test_cli_show_prints_steps_choices_and_lookups():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "show", "schedule_activity"]
    )
    assert r.exit_code == 0, r.output
    assert "config block: action_schedule_activity" in r.output
    assert "# with_delay:" in r.output
    assert '"parent_key": null' in r.output
    assert "schedule.with_delay.time_offset: pick one" in r.output
    assert "specific_time    with time" in r.output
    assert "kizen activities list" in r.output


def test_cli_show_prints_a_blank_choice_as_empty_quotes():
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "goal"])
    assert r.exit_code == 0, r.output
    assert 'minutes | hours | days | ""' in r.output


def test_cli_variant_is_pipeable_json():
    r = runner.invoke(
        cli.app,
        [
            "automations",
            "step-types",
            "show",
            "schedule_activity",
            "--variant",
            "on_date",
        ],
    )
    assert r.exit_code == 0, r.output
    spec = json.loads(r.output)
    assert spec["key"] == "schedule_activity" and spec["parent_key"] is None
    assert spec["action_schedule_activity"]["schedule"]["type"] == "on_or_around_date"


def test_cli_json_carries_the_schema():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "show", "schedule_activity", "--json"]
    )
    info = json.loads(r.output)
    assert "ActivityScheduleConfig" in info["schema"]["$defs"]
    assert info["selectors"][0]["path"] == "schedule.type"


def test_cli_unknown_type_and_variant_exit_2():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "show", "shedule_activity"]
    )
    assert r.exit_code == 2
    assert "Did you mean 'schedule_activity'" in r.output
    r = runner.invoke(
        cli.app,
        ["automations", "step-types", "show", "delay", "--variant", "nope"],
    )
    assert r.exit_code == 2


def test_cli_list_json_rows():
    r = runner.invoke(cli.app, ["automations", "step-types", "list", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.output)
    assert [set(row) for row in rows] == [
        {"type", "kind", "block", "purpose", "variants"}
    ] * len(ALL_TYPES)
    assert {r["kind"] for r in rows} == {"step", "trigger"}
    r = runner.invoke(cli.app, ["automations", "step-types", "list"])
    assert r.exit_code == 0 and "schedule_activity" in r.output
    assert "action_schedule_activity" not in r.output
    r = runner.invoke(cli.app, ["automations", "step-types", "list", "-o", "csv"])
    assert r.output.splitlines()[0] == "type,kind,purpose,variants"


def test_cli_show_alias_shows_its_type():
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "wait"])
    assert r.exit_code == 0, r.output
    first, second = r.stdout.splitlines()[:2]
    assert first == "'wait' isn't a type name; showing delay."
    assert second.startswith("delay (step):")


@pytest.mark.parametrize("extra", [["--json"], ["--variant", "minimal"]])
def test_cli_show_alias_note_goes_to_stderr_with_json(extra):
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "ai_step", *extra])
    assert r.exit_code == 0, r.output
    assert r.stderr.strip() == "'ai_step' isn't a type name; showing call_llm."
    out = json.loads(r.stdout)
    assert out.get("type", out.get("step_type")) == "call_llm"


def test_cli_show_alias_with_several_targets_exits_2():
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "send_text"])
    assert r.exit_code == 2
    lines = r.stderr.splitlines()
    assert lines[0] == "error: 'send_text' isn't a type name. It could be:"
    assert [line.split(":")[0] for line in lines[1:]] == [
        "  notify_member_via_text",
        "  send_related_contact_text",
    ]


def test_trigger_cards_say_a_trigger_has_no_key():
    note = "A trigger has no key; steps refer to triggers by position."
    for name in step_catalog.TRIGGER_TYPES:
        assert note in step_catalog.describe(name)["notes"], name
    assert "notes" not in step_catalog.describe("delay")


def test_condition_card_leads_with_llm_decision():
    variants = step_catalog.describe("condition")["variants"]
    assert variants[0]["name"] == "llm_decision"
    block = variants[0]["spec"]["step_condition"]
    assert block["type"] == "llm_decision"
    assert set(block["llm_decision"]) == {"model_name", "prompt"}


def test_schedule_activity_has_a_round_robin_variant():
    spec = step_catalog.variant_spec("schedule_activity", "round_robin")
    assigned = spec["action_schedule_activity"]["assigned_to"]
    assert assigned == {"assignment_type": "round_robin_all"}


def test_cli_show_finds_a_trigger():
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "schedule"])
    assert r.exit_code == 0, r.output
    assert "schedule (trigger)" in r.output and "trigger_schedule" in r.output


def test_cli_skeleton_is_json():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "skeleton", "--object", "deals"]
    )
    assert r.exit_code == 0, r.output
    AutomationDef.model_validate(_with_orders(json.loads(r.output)))


def test_there_is_no_trigger_types_command():
    r = runner.invoke(cli.app, ["automations", "trigger-types", "list"])
    assert r.exit_code == 2


def test_cli_concepts():
    r = runner.invoke(cli.app, ["automations", "step-types", "concepts", "--json"])
    assert set(json.loads(r.output)) == set(step_catalog.CONCEPTS)


def test_automations_help_points_at_step_types():
    r = runner.invoke(cli.app, ["automations", "--help"])
    assert "step-types show <type>" in plain(r.output)
    for args in (["create"], ["update"], ["steps", "add"]):
        r = runner.invoke(cli.app, ["automations", *args, "--help"])
        flat = plain(r.output.replace("│", " "))
        assert "kizen automations step-types show" in flat, args

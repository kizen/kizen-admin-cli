"""The catalog behind `kizen automations step-types` stays tied to the
planner registries and the spec models."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.models.spec import AutomationDef, AutomationStepDef
from kizen_builder.models.spec.automations_actions_messaging import (
    ActivityAssignmentType,
)
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
    return automation_check._with_defaults(spec)[0]


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
    checked = automation_check.check_automation(auto)
    assert [s.order for s in checked.auto.steps] == [0, 1, 2]
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
    assert [v["name"] for v in variants] == ["llm_decision", "minimal"]
    block = variants[0]["spec"]["step_condition"]
    assert block["type"] == "llm_decision"
    assert set(block["llm_decision"]) == {"model_name", "prompt"}
    assert variants[1]["spec"]["step_condition"] == {}


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

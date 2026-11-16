"""The step catalog behind `kizen automations step-types` stays tied to the
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
from kizen_builder.tools import step_catalog
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    STEP_PURPOSES,
    LiveContext,
    _build_automation_payload,
)

runner = CliRunner()


def test_catalog_lists_exactly_the_wired_types():
    assert set(step_catalog.STEP_TYPES) == {*_STEP_BUILDERS, *_CONFIGLESS_STEP_TYPES}


def test_every_wired_step_type_has_a_purpose():
    assert set(STEP_PURPOSES) == set(step_catalog.STEP_TYPES)
    assert all(STEP_PURPOSES.values())


def test_every_type_describes():
    for name in step_catalog.STEP_TYPES:
        info = step_catalog.describe(name)
        assert info["variants"], name
        json.dumps(info)  # --json must serialize
        for v in info["variants"]:
            assert v["spec"]["step_type"] == name
            assert v["spec"]["parent_key"] is None and v["spec"]["order"] == 0


def _variants():
    return [
        (name, v["name"])
        for name in step_catalog.STEP_TYPES
        for v in step_catalog.describe(name)["variants"]
    ]


@pytest.mark.parametrize(("name", "variant"), _variants())
def test_every_variant_is_a_valid_step(name, variant):
    AutomationStepDef.model_validate(step_catalog.variant_spec(name, variant))


def test_unknown_type_names_the_wired_ones():
    with pytest.raises(step_catalog.UnknownType, match="schedule_activity"):
        step_catalog.describe("send_email")


def test_unknown_variant_names_the_variants():
    with pytest.raises(step_catalog.UnknownType, match="Variants: minimal"):
        step_catalog.variant_spec("delay", "nope")


def test_guide_selectors_match_the_model_choices():
    """A guide can't offer an option the model refuses, or leave one out."""
    fields = {
        f["path"]: f for f in step_catalog.describe("schedule_activity")["fields"]
    }
    for selector in step_catalog._GUIDES["schedule_activity"].selectors:
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
            {
                "name": "t",
                "api_name": "t",
                "target_object": "patients",
                "steps": [json.loads(text)],
            }
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
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "nope"])
    assert r.exit_code == 2
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
        {"type", "block", "purpose", "variants"}
    ] * len(step_catalog.STEP_TYPES)
    r = runner.invoke(cli.app, ["automations", "step-types", "list"])
    assert r.exit_code == 0 and "schedule_activity" in r.output


def test_cli_concepts():
    r = runner.invoke(cli.app, ["automations", "step-types", "concepts", "--json"])
    assert set(json.loads(r.output)) == set(step_catalog.CONCEPTS)


def test_automations_help_points_at_step_types():
    r = runner.invoke(cli.app, ["automations", "--help"])
    assert "step-types show <type>" in " ".join(r.output.split())
    for args in (["create"], ["update"], ["steps", "add"]):
        r = runner.invoke(cli.app, ["automations", *args, "--help"])
        flat = " ".join(r.output.replace("│", " ").split())
        assert "kizen automations step-types show" in flat, args

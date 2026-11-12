"""`kizen automations step-types`: the catalog stays tied to the registries,
the models and the shipped doc it reads from."""

from __future__ import annotations

import json
import re
from typing import get_args

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder import docs
from kizen_builder.models.spec import AutomationDef, AutomationStepDef
from kizen_builder.models.spec.automations_actions_messaging import (
    ActivityAssignmentType,
)
from kizen_builder.tools import step_catalog
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    LiveContext,
    _build_automation_payload,
)

runner = CliRunner()


def test_catalog_lists_exactly_the_wired_types():
    assert set(step_catalog.STEP_TYPES) == {*_STEP_BUILDERS, *_CONFIGLESS_STEP_TYPES}
    assert set(step_catalog.TRIGGER_TYPES) == set(_TRIGGER_BUILDERS)


def test_every_step_type_reads_a_purpose_from_the_automation_doc():
    rows = step_catalog.list_types("step")
    assert [r["type"] for r in rows if not r.get("purpose")] == []
    assert not any("](" in r["purpose"] for r in rows), "a markdown link leaked"


def test_the_docs_wired_table_names_no_type_the_catalog_lacks():
    text = docs.topic_path("automation").read_text()
    table = text.split("## Wired step types", 1)[1].split("\n\n", 2)[1]
    named = set(re.findall(r"^\| `([a-z_]+)`", table, flags=re.M))
    assert named <= set(step_catalog.STEP_TYPES)


@pytest.mark.parametrize("kind", ["step", "trigger"])
def test_every_type_describes(kind):
    types = step_catalog.STEP_TYPES if kind == "step" else step_catalog.TRIGGER_TYPES
    for name in types:
        info = step_catalog.describe(name, kind)
        assert info["variants"], name
        json.dumps(info)  # --json must serialize
        key = "step_type" if kind == "step" else "trigger_type"
        assert all(v["spec"][key] == name for v in info["variants"])


def test_unknown_type_names_the_wired_ones():
    with pytest.raises(step_catalog.UnknownType, match="schedule_activity"):
        step_catalog.describe("send_email")


def _guided():
    return [
        (name, v)
        for name, guide in step_catalog._STEP_GUIDES.items()
        for v in guide.variants
    ]


@pytest.mark.parametrize(
    ("name", "variant"), _guided(), ids=lambda x: getattr(x, "name", x)
)
def test_guide_blocks_pass_the_step_model(name, variant):
    spec = step_catalog.variant_spec(name, variant.name)
    AutomationStepDef.model_validate({"key": "s", "order": 0, **spec})
    assert step_catalog.selector_findings(name, variant.block) == []


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
            {
                "name": "t",
                "api_name": "t",
                "target_object": "patients",
                "steps": [
                    {"key": "s", "parent_key": None, "order": 0, **json.loads(text)}
                ],
            }
        )
        block = _build_automation_payload(auto, LiveContext())["steps"][0][
            "action_schedule_activity"
        ]
        assert block["schedule"]["type"] == variant.block["schedule"]["type"]
        assert "field_ref" not in json.dumps(block), variant.name


def test_selector_findings_accept_the_live_read_spelling():
    block = {
        "assigned_to": {"assignment_type": "team_member", "employee": {"id": "x"}},
        "schedule": {"type": "immediately"},
    }
    assert step_catalog.selector_findings("schedule_activity", block) == []
    block["assigned_to"] = {"type": "team_member"}
    (finding,) = step_catalog.selector_findings("schedule_activity", block)
    assert finding["path"] == "assigned_to.employee_id"


# --- CLI ------------------------------------------------------------------


def test_cli_show_prints_blocks_choices_and_lookups():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "show", "schedule_activity"]
    )
    assert r.exit_code == 0, r.output
    assert "config block: action_schedule_activity" in r.output
    assert "# with_delay:" in r.output
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
    assert spec["action_schedule_activity"]["schedule"]["type"] == "on_or_around_date"


def test_cli_json_carries_the_schema():
    r = runner.invoke(
        cli.app, ["automations", "step-types", "show", "schedule_activity", "--json"]
    )
    info = json.loads(r.output)
    assert "ActivityScheduleConfig" in info["schema"]["$defs"]
    assert info["selectors"][0]["path"] == "schedule.type"


def test_cli_unknown_type_and_variant_exit_2():
    r = runner.invoke(cli.app, ["automations", "step-types", "show", "send_email"])
    assert r.exit_code == 2
    r = runner.invoke(
        cli.app,
        ["automations", "step-types", "show", "delay", "--variant", "nope"],
    )
    assert r.exit_code == 2


def test_cli_list_and_trigger_types():
    r = runner.invoke(cli.app, ["automations", "step-types", "list"])
    assert r.exit_code == 0 and "schedule_activity" in r.output
    assert "variants: immediately" in r.output
    r = runner.invoke(cli.app, ["automations", "trigger-types", "show", "schedule"])
    assert r.exit_code == 0 and "trigger_schedule" in r.output
    r = runner.invoke(cli.app, ["automations", "step-types", "concepts", "--json"])
    assert set(json.loads(r.output)) == set(step_catalog.CONCEPTS)


def test_automations_help_points_at_the_authoring_commands():
    r = runner.invoke(cli.app, ["automations", "--help"])
    flat = " ".join(r.output.split())
    assert "step-types show <type>" in flat
    assert "lint" in flat
    for verb in ("create", "update"):
        r = runner.invoke(cli.app, ["automations", verb, "--help"])
        flat = " ".join(r.output.replace("│", " ").split())
        assert "kizen automations step-types show" in flat, verb
        assert "kizen automations lint" in flat, verb

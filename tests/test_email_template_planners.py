"""Tests for `tools.planners.messages`'s spec-driven email-template planners.

`plan_create_template_from_spec`/`plan_update_template(..., spec=...)` take
already-resolved sections (image uploads happened earlier, in the CLI layer
— see `tools.email_craft.resolve_spec_images`'s docstring for why that split
exists) and must build a Plan with no live calls of their own beyond what
`plan_update_template` already needs to resolve the target template. Per
`CLAUDE.md`, nothing under `tools/planners/` performs a POST/PUT/PATCH/DELETE
— asserted here indirectly: these tests mock only GET/list endpoints, never
a write, and the planners still succeed.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenClient
from kizen_builder.models.spec.email_templates import EmailTemplateDef
from kizen_builder.tools.messages import craft_summary
from kizen_builder.tools.planners import messages as message_planners
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

TEMPLATE_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"


def _resolved_sections(*, layout: str = "1 Column", n_cells: int = 1) -> list[dict]:
    cells = [
        {"blocks": [{"kind": "text", "html": f"<p>cell {i}</p>"}]}
        for i in range(n_cells)
    ]
    return [
        {"background_color": "#FFFFFF", "rows": [{"layout": layout, "cells": cells}]}
    ]


def _spec(**overrides) -> EmailTemplateDef:
    base = {"name": "Newsletter", "subject": "Hi", "sections": []}
    base.update(overrides)
    return EmailTemplateDef.model_validate(base)


# ---------------------------------------------------------------------------
# plan_create_template_from_spec
# ---------------------------------------------------------------------------


def test_plan_create_builds_one_create_op_with_coupled_content():
    spec = _spec()
    plan = message_planners.plan_create_template_from_spec(spec, _resolved_sections())
    assert len(plan.operations) == 1
    op = plan.operations[0]
    assert op.action == "create"
    assert op.kind == "email_template"
    assert op.key == "Newsletter"
    assert op.payload["name"] == "Newsletter"
    assert op.payload["subject"] == "Hi"
    assert op.payload["type"] == "email"
    assert op.payload["sender_type"] == "business"
    assert op.payload["from_name_type"] == "default"
    # No raw craft_json/content ever entered this function — both are
    # derived from resolved_sections by the same one-pass emitter.
    summary = craft_summary(
        {"craft_json": op.payload["craft_json"], "content": op.payload["content"]}
    )
    assert summary["coupled"] is True


@respx.mock
def test_plan_create_makes_no_live_calls():
    """A planner performs no POST/PUT/PATCH/DELETE (CLAUDE.md). No route is
    registered here, so respx's default `assert_all_mocked` would raise on
    any httpx call this function tries to make — its absence is the proof."""
    message_planners.plan_create_template_from_spec(_spec(), _resolved_sections())


def test_plan_create_row_cell_count_mismatch_raises_plan_error_not_silent_reshape():
    resolved = [
        {
            "background_color": "#FFFFFF",
            "rows": [
                {
                    "layout": "2 Columns",
                    "cells": [{"blocks": []}],  # needs 2, got 1
                }
            ],
        }
    ]
    try:
        message_planners.plan_create_template_from_spec(_spec(), resolved)
        raise AssertionError("expected PlanError")
    except PlanError as e:
        assert "2 cell" in str(e)


def test_plan_create_unsupported_block_kind_raises_plan_error():
    resolved = [
        {
            "background_color": "#FFFFFF",
            "rows": [
                {"layout": "1 Column", "cells": [{"blocks": [{"kind": "attachments"}]}]}
            ],
        }
    ]
    try:
        message_planners.plan_create_template_from_spec(_spec(), resolved)
        raise AssertionError("expected PlanError")
    except PlanError as e:
        assert "unsupported block kind" in str(e)


# ---------------------------------------------------------------------------
# plan_update_template(..., spec=..., resolved_sections=...)
# ---------------------------------------------------------------------------


def _client() -> KizenClient:
    from kizen_builder.config import load_env_config

    return KizenClient(load_env_config())


@respx.mock
def test_plan_update_from_spec_rebuilds_both_fields_together():
    existing = {
        "id": TEMPLATE_ID,
        "name": "Old Name",
        "subject": "Old subject",
        "craft_json": {"ROOT": {"type": {"resolvedName": "Root"}}},
        "content": "<p>old</p>",
    }
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
        return_value=httpx.Response(200, json=existing)
    )
    spec = _spec(name="New Name", subject="New subject")
    plan = message_planners.plan_update_template(
        TEMPLATE_ID, spec=spec, resolved_sections=_resolved_sections()
    )
    op = plan.operations[0]
    assert op.action == "update"
    assert op.existing_uuid == TEMPLATE_ID
    assert op.payload["name"] == "New Name"
    assert op.payload["subject"] == "New subject"
    summary = craft_summary(
        {"craft_json": op.payload["craft_json"], "content": op.payload["content"]}
    )
    assert summary["coupled"] is True


def test_plan_update_raw_patch_path_is_unchanged():
    """The existing --craft-json-file/--content-file path (patch dict, no
    spec) must keep working exactly as before this item."""
    with respx.mock:
        respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
            return_value=httpx.Response(
                200,
                json={"id": TEMPLATE_ID, "name": "t", "craft_json": {}, "content": ""},
            )
        )
        plan = message_planners.plan_update_template(TEMPLATE_ID, {"name": "renamed"})
        assert plan.operations[0].payload == {"name": "renamed"}


def test_plan_update_with_neither_patch_nor_spec_raises_plan_error():
    with respx.mock:
        respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
            return_value=httpx.Response(
                200,
                json={"id": TEMPLATE_ID, "name": "t", "craft_json": {}, "content": ""},
            )
        )
        try:
            message_planners.plan_update_template(TEMPLATE_ID, {})
            raise AssertionError("expected PlanError")
        except PlanError as e:
            assert "nothing to update" in str(e)


# ---------------------------------------------------------------------------
# A template with blank compiled `content` is refused where it is an input
# ---------------------------------------------------------------------------

AUTOMATION_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"
CRAFT = {"ROOT": {"type": {"resolvedName": "Root"}}}


def _template(**overrides) -> dict:
    base = {
        "id": TEMPLATE_ID,
        "name": "Placeholder Template",
        "subject": "Hello",
        "craft_json": CRAFT,
        "content": "<p>hi</p>",
    }
    base.update(overrides)
    return base


def _mock_template(template: dict) -> None:
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
        return_value=httpx.Response(200, json=template)
    )


@pytest.fixture
def automation(monkeypatch):
    monkeypatch.setattr(
        message_planners, "get_automation", lambda api_name: {"id": AUTOMATION_ID}
    )


@respx.mock
def test_create_message_refuses_blank_content_with_a_builder_tree(automation):
    _mock_template(_template(content=""))
    with pytest.raises(PlanError) as e:
        message_planners.plan_create_automation_message("some_automation", TEMPLATE_ID)
    msg = str(e.value)
    assert "Placeholder Template" in msg
    assert TEMPLATE_ID in msg
    assert "has a builder tree but no compiled `content`" in msg
    assert "Kizen email builder" in msg
    assert "--spec-file" in msg


@respx.mock
def test_create_message_refuses_whitespace_only_content(automation):
    _mock_template(_template(content="  \n\t "))
    with pytest.raises(PlanError):
        message_planners.plan_create_automation_message("some_automation", TEMPLATE_ID)


@respx.mock
def test_blank_template_without_craft_json_does_not_claim_a_builder_tree(automation):
    _mock_template(_template(content=None, craft_json=None))
    with pytest.raises(PlanError) as e:
        message_planners.plan_create_automation_message("some_automation", TEMPLATE_ID)
    assert "builder tree" not in str(e.value)


@respx.mock
def test_create_message_from_a_normal_template_still_plans(automation):
    template = _template()
    _mock_template(template)
    plan = message_planners.plan_create_automation_message(
        "some_automation", TEMPLATE_ID
    )
    assert len(plan.operations) == 1
    op = plan.operations[0]
    assert op.action == "create"
    assert op.payload["template"]["content"] == "<p>hi</p>"
    assert op.payload == {"automation_id": AUTOMATION_ID, "template": template}


@respx.mock
def test_clone_refuses_a_blank_source():
    _mock_template(_template(content=""))
    with pytest.raises(PlanError) as e:
        message_planners.plan_clone_template(TEMPLATE_ID, "Copy")
    assert "Placeholder Template" in str(e.value)


@respx.mock
def test_clone_from_a_normal_source_still_plans():
    _mock_template(_template())
    plan = message_planners.plan_clone_template(TEMPLATE_ID, "Copy")
    op = plan.operations[0]
    assert op.payload["name"] == "Copy"
    assert op.payload["content"] == "<p>hi</p>"
    assert op.payload["craft_json"] == CRAFT


@respx.mock
def test_update_and_delete_still_plan_against_a_blank_template():
    _mock_template(_template(content=""))
    update = message_planners.plan_update_template(TEMPLATE_ID, {"name": "renamed"})
    assert update.operations[0].payload == {"name": "renamed"}
    spec_update = message_planners.plan_update_template(
        TEMPLATE_ID, spec=_spec(), resolved_sections=_resolved_sections()
    )
    assert spec_update.operations[0].payload["content"]
    delete = message_planners.plan_delete_template(TEMPLATE_ID)
    assert delete.operations[0].action == "delete"

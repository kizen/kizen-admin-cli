"""Golden tests for pipeline-stage plan builders and the fields-options guardrail.

Stages are a sub-resource of a pipeline object (`/api/pipelines/{id}/stages`)
distinct from the object's mirrored `stage` field's options — see
`kizen docs show reference` ("Pipeline stages"). These tests lock the wire payloads
for create/update/remove and the record-move op, plus the guardrail that
stops `fields options add/remove` from silently no-op'ing against a
stage-backed field.
"""

from __future__ import annotations

import pytest

from kizen_builder.tools.planners.fields import (
    plan_add_field_options,
    plan_remove_field_option,
)
from kizen_builder.tools.planners.pipeline_stages import (
    plan_create_stage,
    plan_move_record,
    plan_remove_stage,
    plan_update_stage,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import load_fixture

DEALS = "deals"
PROSPECTING_ID = "aaaaaaaa-0000-4000-8000-000000000001"
NEGOTIATION_ID = "aaaaaaaa-0000-4000-8000-000000000002"
CLOSED_WON_ID = "aaaaaaaa-0000-4000-8000-000000000003"


def _deals_id() -> str:
    return load_fixture("objects/deals.json")["id"]


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_stage_defaults_status_open_and_appends_order(patch_live_lookups):
    plan = plan_create_stage(DEALS, "Closed Lost")
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "stage"
    assert op.key == "deals.stage:Closed Lost"
    assert op.payload == {"name": "Closed Lost", "status": "open", "order": 3}
    assert op.parent_object_uuid == _deals_id()


def test_create_stage_with_explicit_status_pct_and_order(patch_live_lookups):
    plan = plan_create_stage(
        DEALS, "Closed Lost", status="lost", percentage_chance_to_close=0, order=5
    )
    (op,) = plan.operations
    assert op.payload == {
        "name": "Closed Lost",
        "status": "lost",
        "order": 5,
        "percentage_chance_to_close": 0,
    }


def test_create_stage_rejects_bad_status(patch_live_lookups):
    with pytest.raises(PlanError, match="status must be one of"):
        plan_create_stage(DEALS, "New Stage", status="bogus")


def test_create_stage_rejects_duplicate_name(patch_live_lookups):
    with pytest.raises(PlanError, match="already exists"):
        plan_create_stage(DEALS, "Prospecting")


def test_create_stage_rejects_non_pipeline_object(patch_live_lookups):
    with pytest.raises(PlanError, match="not a pipeline object"):
        plan_create_stage("patients", "New Stage")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_stage_diffs_only_changed_keys(patch_live_lookups):
    plan = plan_update_stage(DEALS, "Prospecting", {"percentage_chance_to_close": 15})
    (op,) = plan.operations
    assert op.action == "update"
    assert op.kind == "stage"
    assert op.payload == {"percentage_chance_to_close": 15}
    assert op.existing_uuid == PROSPECTING_ID
    assert op.parent_object_uuid == _deals_id()


def test_update_stage_no_changes_is_skip(patch_live_lookups):
    plan = plan_update_stage(DEALS, "Prospecting", {"status": "open"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}


def test_update_stage_by_id(patch_live_lookups):
    plan = plan_update_stage(DEALS, NEGOTIATION_ID, {"order": 4})
    (op,) = plan.operations
    assert op.existing_uuid == NEGOTIATION_ID
    assert op.payload == {"order": 4}


def test_update_stage_rejects_bad_status(patch_live_lookups):
    with pytest.raises(PlanError, match="status must be one of"):
        plan_update_stage(DEALS, "Prospecting", {"status": "bogus"})


def test_update_stage_unknown_stage(patch_live_lookups):
    with pytest.raises(PlanError, match="not found"):
        plan_update_stage(DEALS, "Nope", {"status": "won"})


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_stage_requires_move_to(patch_live_lookups):
    with pytest.raises(PlanError, match="--move-to is required"):
        plan_remove_stage(DEALS, "Prospecting", "")


def test_remove_stage_rejects_move_to_self(patch_live_lookups):
    with pytest.raises(PlanError, match="different stage"):
        plan_remove_stage(DEALS, "Prospecting", "Prospecting")


def test_remove_stage_payload(patch_live_lookups):
    plan = plan_remove_stage(DEALS, "Prospecting", "Negotiation")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "stage"
    assert op.existing_uuid == PROSPECTING_ID
    assert op.payload == {"new_stage_id": NEGOTIATION_ID}
    assert op.parent_object_uuid == _deals_id()


# ---------------------------------------------------------------------------
# record move
# ---------------------------------------------------------------------------


def test_move_record_payload(patch_live_lookups):
    plan = plan_move_record(DEALS, "record-uuid-1", "Closed Won")
    (op,) = plan.operations
    assert op.action == "update"
    assert op.kind == "record_move"
    assert op.key == "deals#record-uuid-1.move"
    assert op.payload == {"stage_id": CLOSED_WON_ID}
    assert op.existing_uuid == "record-uuid-1"
    # The move route answers 500 on the api_name; it takes the object UUID.
    assert op.parent_object_uuid == _deals_id()


def test_move_record_unknown_stage(patch_live_lookups):
    with pytest.raises(PlanError, match="not found"):
        plan_move_record(DEALS, "record-uuid-1", "Nope")


def test_move_record_rejects_non_pipeline_object(patch_live_lookups):
    with pytest.raises(PlanError, match="not a pipeline object"):
        plan_move_record("patients", "record-uuid-1", "Anything")


# ---------------------------------------------------------------------------
# guardrail: fields options add/remove refuse a stage-backed field
# ---------------------------------------------------------------------------


def test_field_options_add_rejects_stage_backed_field(patch_live_lookups):
    with pytest.raises(PlanError, match="mirrors the pipeline's stages"):
        plan_add_field_options(DEALS, "stage", ["Extra Stage"])


def test_field_options_remove_rejects_stage_backed_field(patch_live_lookups):
    with pytest.raises(PlanError, match="mirrors the pipeline's stages"):
        plan_remove_field_option(DEALS, "stage", "Prospecting")


def test_field_options_add_still_works_on_non_stage_field(patch_live_lookups):
    # deal_value is a money field with no options at all — sanity check the
    # guardrail doesn't fire for unrelated fields on a pipeline object. Use a
    # field type that supports options instead: none on this fixture, so this
    # asserts against the "wrong field type" error rather than the guardrail.
    with pytest.raises(PlanError, match="which has no options to add"):
        plan_add_field_options(DEALS, "deal_value", ["X"])

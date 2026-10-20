"""The eight activity planner entry points: activity-type and activity-field
create/update/delete, plus field-option add/remove.

None of these build a `KizenClient` directly — they call `get_activity` /
`list_activities` from `tools/activities.py`, which do — so the seam is the
same `respx` HTTP mocking `test_activities_tool.py` uses, one layer down.
`_build_activity_payload` / `_build_activity_field_payload` are already
locked down in `tests/test_activity_payloads.py`; this file only asserts on
the ops the planners build around them (action/kind/key/payload/preview/
existing_uuid/deferred refs), not the payload shape itself.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError
from kizen_builder.tools.planners.activities import (
    plan_add_activity_field_options,
    plan_create_activity,
    plan_create_activity_fields,
    plan_delete_activity,
    plan_delete_activity_field,
    plan_remove_activity_field_option,
    plan_update_activity,
    plan_update_activity_field,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

ACTIVITY_ID = "00000000-0000-4000-8000-000000000a01"
FIELD_ID = "00000000-0000-4000-8000-000000000a02"
OPTION_ID = "00000000-0000-4000-8000-000000000a03"
OTHER_OPTION_ID = "00000000-0000-4000-8000-000000000a04"

ACTIVITY_DETAIL = {
    "id": ACTIVITY_ID,
    "name": "Site Visit",
    "api_name": "site_visit",
    "description": "desc",
    "is_editable": True,
    "association_mode": "all_objects_associated",
    "n_submissions": 3,
    "custom_objects": [],
    "selected_objects": [],
}

DROPDOWN_FIELD = {
    "id": FIELD_ID,
    "name": "outcome",
    "display_name": "Outcome",
    "field_type": "dropdown",
    "custom_object_field": None,
    "is_required": False,
    "is_read_only": False,
    "is_hidden": False,
    "order": 0,
    "options": [
        {"id": OPTION_ID, "name": "Yes", "code": "yes"},
        {"id": OTHER_OPTION_ID, "name": "No", "code": "no"},
    ],
}

TEXT_FIELD = {
    "id": "00000000-0000-4000-8000-000000000a09",
    "name": "notes",
    "display_name": "Notes",
    "field_type": "text",
    "custom_object_field": None,
    "is_required": False,
    "is_read_only": False,
    "is_hidden": False,
    "order": 1,
    "options": None,
}


def _mock_activity_list(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(
            200, json=body or {"results": [ACTIVITY_DETAIL], "next": None}
        )
    )


def _mock_activity_detail(identifier: str = "site_visit", status: int = 200, body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities/{identifier}").mock(
        return_value=httpx.Response(
            status,
            json=body
            if body is not None
            else (ACTIVITY_DETAIL if status == 200 else {"detail": "not found"}),
        )
    )


def _mock_activity_fields(identifier: str = ACTIVITY_ID, body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities/{identifier}/fields").mock(
        return_value=httpx.Response(
            200, json=body if body is not None else [DROPDOWN_FIELD]
        )
    )


# ---------------------------------------------------------------------------
# plan_create_activity
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_create_activity_with_inline_fields_defers_parent_ref():
    """Fields declared inline on the create spec are follow-on ops keyed to
    resolve their parent activity id from the create op's result — the
    activity doesn't exist yet at plan time."""
    _mock_activity_list(body={"results": [], "next": None})

    plan = plan_create_activity(
        {
            "name": "Site Visit",
            "api_name": "site_visit",
            "fields": [
                {"name": "Outcome", "api_name": "outcome", "field_type": "text"},
            ],
        }
    )

    create_op, field_op = plan.operations
    assert create_op.action == "create"
    assert create_op.kind == "activity"
    assert create_op.key == "activity:site_visit"
    assert create_op.payload["name"] == "Site Visit"

    assert field_op.action == "create"
    assert field_op.kind == "activity_field"
    assert field_op.key == "activity:site_visit.field:outcome"
    assert field_op.deferred_parent_object_key == "activity:site_visit"
    assert field_op.parent_object_uuid is None
    assert "with 1 field(s)" in plan.summary


@respx.mock
def test_plan_create_activity_raises_when_api_name_already_exists():
    _mock_activity_list(
        body={"results": [{"id": ACTIVITY_ID, "api_name": "site_visit"}], "next": None}
    )
    try:
        plan_create_activity({"name": "Site Visit", "api_name": "site_visit"})
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert ACTIVITY_ID in str(exc)


@respx.mock
def test_plan_create_activity_raises_on_duplicate_field_label_in_batch():
    _mock_activity_list(body={"results": [], "next": None})
    try:
        plan_create_activity(
            {
                "name": "Site Visit",
                "fields": [
                    {"name": "Outcome", "field_type": "text"},
                    {"name": "Outcome", "field_type": "text"},
                ],
            }
        )
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "duplicate activity field" in str(exc)


# ---------------------------------------------------------------------------
# plan_update_activity
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_update_activity_emits_skip_when_nothing_changed():
    _mock_activity_detail()
    plan = plan_update_activity("site_visit", {"name": "Site Visit"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}
    assert "No changes" in plan.summary


@respx.mock
def test_plan_update_activity_builds_diff_for_scalar_changes_only():
    _mock_activity_detail()
    plan = plan_update_activity(
        "site_visit", {"name": "Renamed", "is_editable": ACTIVITY_DETAIL["is_editable"]}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"name": "Renamed"}
    assert op.existing_uuid == ACTIVITY_ID


@respx.mock
def test_plan_update_activity_always_includes_structural_keys_when_passed():
    """`visibility_rules` (and the other structural keys) are always sent when
    explicitly present in `changes` — unlike scalar keys, there's no
    old-vs-new comparison against live state for them."""
    _mock_activity_detail()
    plan = plan_update_activity("site_visit", {"visibility_rules": []})
    (op,) = plan.operations
    assert op.payload == {"visibility_rules": []}


@respx.mock
def test_plan_update_activity_raises_when_not_found():
    _mock_activity_detail(identifier="nope", status=404)
    try:
        plan_update_activity("nope", {"name": "X"})
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "nope" in str(exc)


# ---------------------------------------------------------------------------
# plan_delete_activity
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_delete_activity():
    _mock_activity_detail()
    plan = plan_delete_activity("site_visit")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "activity"
    assert op.existing_uuid == ACTIVITY_ID
    assert op.preview["n_submissions"] == 3


@respx.mock
def test_plan_delete_activity_raises_when_not_found():
    _mock_activity_detail(identifier="nope", status=404)
    try:
        plan_delete_activity("nope")
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "nope" in str(exc)


# ---------------------------------------------------------------------------
# plan_create_activity_fields
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_create_activity_fields_happy_path_sets_parent_uuid_directly():
    """Unlike the inline-create case, the activity already exists here, so
    the parent id is known at plan time — `parent_object_uuid`, not a
    deferred ref."""
    _mock_activity_detail()
    _mock_activity_fields()

    plan = plan_create_activity_fields(
        "site_visit", [{"name": "Notes", "api_name": "notes", "field_type": "text"}]
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.parent_object_uuid == ACTIVITY_ID
    assert op.key == "activity:site_visit.field:notes"
    assert "1 field(s)" in plan.summary


@respx.mock
def test_plan_create_activity_fields_raises_when_no_fields_given():
    try:
        plan_create_activity_fields("site_visit", [])
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "no fields" in str(exc)


@respx.mock
def test_plan_create_activity_fields_raises_when_field_already_exists():
    _mock_activity_detail()
    _mock_activity_fields()
    try:
        plan_create_activity_fields(
            "site_visit",
            [{"name": "Outcome", "api_name": "outcome", "field_type": "text"}],
        )
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "already exists" in str(exc)


@respx.mock
def test_plan_create_activity_fields_raises_on_duplicate_label_in_batch():
    _mock_activity_detail()
    _mock_activity_fields(body=[])
    try:
        plan_create_activity_fields(
            "site_visit",
            [
                {"name": "Notes", "field_type": "text"},
                {"name": "Notes", "field_type": "text"},
            ],
        )
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "duplicate activity field" in str(exc)


# ---------------------------------------------------------------------------
# plan_update_activity_field
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_update_activity_field_builds_diff_across_all_supported_keys():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])

    plan = plan_update_activity_field(
        "site_visit",
        "notes",
        {
            "name": "New Label",
            "description": "new desc",
            "required": True,
            "read_only": True,
            "hidden": True,
            "order": 5,
        },
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {
        "display_name": "New Label",
        "description": "new desc",
        "is_required": True,
        "is_read_only": True,
        "is_hidden": True,
        "order": 5,
    }
    assert op.existing_uuid == TEXT_FIELD["id"]
    assert op.parent_object_uuid == ACTIVITY_ID


@respx.mock
def test_plan_update_activity_field_skips_when_nothing_changed():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])
    plan = plan_update_activity_field(
        "site_visit", "notes", {"name": TEXT_FIELD["display_name"]}
    )
    (op,) = plan.operations
    assert op.action == "skip"


@respx.mock
def test_plan_update_activity_field_raises_when_field_unknown():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])
    try:
        plan_update_activity_field("site_visit", "bogus", {"required": True})
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "not found" in str(exc)
        assert "notes" in str(exc)


# ---------------------------------------------------------------------------
# plan_delete_activity_field
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_delete_activity_field():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])
    plan = plan_delete_activity_field("site_visit", "notes")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "activity_field"
    assert op.existing_uuid == TEXT_FIELD["id"]
    assert op.parent_object_uuid == ACTIVITY_ID


@respx.mock
def test_plan_delete_activity_field_raises_when_field_unknown():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])
    try:
        plan_delete_activity_field("site_visit", "bogus")
        raise AssertionError("expected PlanError")
    except PlanError:
        pass


# ---------------------------------------------------------------------------
# plan_add_activity_field_options
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_add_activity_field_options_skips_existing_and_creates_new():
    _mock_activity_detail()
    _mock_activity_fields(body=[DROPDOWN_FIELD])

    plan = plan_add_activity_field_options("site_visit", "outcome", ["yes", "Maybe"])

    skip_op, create_op = plan.operations
    assert skip_op.action == "skip"
    assert skip_op.preview["note"] == "already exists"
    assert create_op.action == "create"
    assert create_op.payload == {"field_id": FIELD_ID, "name": "Maybe"}
    assert create_op.parent_object_uuid == ACTIVITY_ID
    assert "Add 1 option(s)" in plan.summary


@respx.mock
def test_plan_add_activity_field_options_raises_when_no_options_given():
    try:
        plan_add_activity_field_options("site_visit", "outcome", [])
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "no options" in str(exc)


@respx.mock
def test_plan_add_activity_field_options_raises_for_non_option_field_type():
    _mock_activity_detail()
    _mock_activity_fields(body=[TEXT_FIELD])
    try:
        plan_add_activity_field_options("site_visit", "notes", ["A"])
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "has no options" in str(exc)


# ---------------------------------------------------------------------------
# plan_remove_activity_field_option
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_remove_activity_field_option_by_name_case_insensitive_drops_without_remap():
    _mock_activity_detail()
    _mock_activity_fields(body=[DROPDOWN_FIELD])

    plan = plan_remove_activity_field_option("site_visit", "outcome", "yes")

    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "activity_field_option"
    assert op.existing_uuid == OPTION_ID
    assert op.payload == {"field_id": FIELD_ID}
    assert op.preview["on_delete"] == "dropped (records lose this value)"


@respx.mock
def test_plan_remove_activity_field_option_by_id_with_remap():
    _mock_activity_detail()
    _mock_activity_fields(body=[DROPDOWN_FIELD])

    plan = plan_remove_activity_field_option(
        "site_visit", "outcome", OPTION_ID, remap_to="No"
    )

    (op,) = plan.operations
    assert op.payload == {"field_id": FIELD_ID, "remap_to": OTHER_OPTION_ID}
    assert op.preview["on_delete"] == "records remapped to 'No'"


@respx.mock
def test_plan_remove_activity_field_option_raises_when_option_unknown():
    _mock_activity_detail()
    _mock_activity_fields(body=[DROPDOWN_FIELD])
    try:
        plan_remove_activity_field_option("site_visit", "outcome", "bogus")
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "not found" in str(exc)


@respx.mock
def test_plan_remove_activity_field_option_raises_when_remap_target_unknown():
    _mock_activity_detail()
    _mock_activity_fields(body=[DROPDOWN_FIELD])
    try:
        plan_remove_activity_field_option(
            "site_visit", "outcome", "yes", remap_to="bogus"
        )
        raise AssertionError("expected PlanError")
    except PlanError as exc:
        assert "bogus" in str(exc)


@respx.mock
@pytest.mark.parametrize("existing, given", [("same", "same"), (None, "")])
def test_plan_update_activity_field_unchanged_description_is_skip(existing, given):
    _mock_activity_detail()
    _mock_activity_fields(body=[dict(TEXT_FIELD, description=existing)])
    plan = plan_update_activity_field("site_visit", "notes", {"description": given})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}


_ACTIVITY_PLANS = [
    pytest.param(
        lambda: plan_update_activity("site_visit", {"name": "X"}), id="update"
    ),
    pytest.param(lambda: plan_delete_activity("site_visit"), id="delete"),
    pytest.param(
        lambda: plan_update_activity_field("site_visit", "notes", {"required": True}),
        id="update_field",
    ),
]


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("plan", _ACTIVITY_PLANS)
def test_activity_plans_do_not_report_other_errors_as_not_found(plan, status):
    _mock_activity_detail(status=status, body={"detail": "boom"})
    with pytest.raises(KizenAPIError) as exc:
        plan()
    assert exc.value.status_code == status

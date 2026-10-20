"""The six custom-object planner entry points: object and field-category
create/update/delete.

These reach live state through `get_object` / `list_objects`, which the
planner imports from `tools.objects`. Those are monkeypatched on the planner
module directly — the same live-lookup seam `patch_live_lookups` (conftest.py)
uses, and the layer whose HTTP chain is already covered by
`tests/test_objects_tool.py`. Tests that only need realistic shape use the
`patch_live_lookups` fixture and its `tests/fixtures/objects/` data; tests
asserting a *diff* patch in their own `raw` block, since the CLI-derived
fixtures carry `raw: None`.

`tests/test_object_payloads.py` already covers `_build_object_payload` and
`_build_pipeline_stages`; nothing here re-asserts those.
"""

from __future__ import annotations

import pytest

from kizen_builder.models.spec import FieldCategory, ObjectDef
from kizen_builder.tools.planners import objects as object_planners
from kizen_builder.tools.plans import PlanError

OBJ_ID = "00000000-0000-4000-8000-0000000002a0"
CAT_ID = "00000000-0000-4000-8000-0000000002a1"
CAT_ID_2 = "00000000-0000-4000-8000-0000000002a2"

OBJECT_DETAIL = {
    "id": OBJ_ID,
    "api_name": "policies",
    "display_name": "Policies",
    "entity_name": "Policy",
    "object_type": "standard",
    "categories": [
        {"id": CAT_ID, "name": "Details", "order": 0},
        {"id": CAT_ID_2, "name": "Billing", "order": 1},
    ],
    "fields": [],
    "raw": {
        "object_name": "Policies",
        "entity_name": "Policy",
        "description": "",
        "default_on_activities": True,
    },
}


@pytest.fixture
def live(monkeypatch):
    """Serve get_object/list_objects from a controlled in-test object."""

    def _install(detail=None, listing=None):
        obj = OBJECT_DETAIL if detail is None else detail

        def fake_get_object(api_name):
            if api_name != obj["api_name"]:
                raise LookupError(f"object '{api_name}' not found (no fixture)")
            return obj

        monkeypatch.setattr(object_planners, "get_object", fake_get_object)
        monkeypatch.setattr(
            object_planners,
            "list_objects",
            lambda: listing if listing is not None else [],
        )
        return obj

    return _install


# ---------------------------------------------------------------------------
# plan_create_object
# ---------------------------------------------------------------------------


def test_plan_create_object_emits_one_create_op(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Policies", "api_name": "policies", "entity_name": "Policy"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "object"
    assert op.key == "policies"
    assert op.preview["object_name"] == "Policies"
    assert op.preview["entity_name"] == "Policy"
    assert op.payload["object_name"] == "Policies"
    assert op.payload["object_type"] == "standard"


def test_plan_create_object_accepts_a_model_as_well_as_a_dict(live):
    live()
    plan = object_planners.plan_create_object(
        ObjectDef.model_validate({"name": "Policies", "api_name": "policies"})
    )
    (op,) = plan.operations
    assert op.payload["object_name"] == "Policies"


def test_plan_create_object_entity_name_defaults_to_the_display_name(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Policies", "api_name": "policies"}
    )
    (op,) = plan.operations
    assert op.preview["entity_name"] == "Policies"


def test_plan_create_object_raises_when_the_display_name_is_taken(live):
    # Kizen 400s a duplicate object_name (confirmed live 2026-10-14).
    live(listing=[{"id": OBJ_ID, "api_name": "policies", "display_name": "Policies"}])
    with pytest.raises(PlanError, match="already exists"):
        object_planners.plan_create_object({"name": "Policies"})


def test_plan_create_object_keys_on_the_derived_api_name(live):
    live()
    plan = object_planners.plan_create_object({"name": "Service Tickets"})
    (op,) = plan.operations
    assert op.key == "service_tickets"
    assert op.preview["api_name (derived)"] == "service_tickets"
    assert "warning" not in op.preview


def test_plan_create_object_refuses_an_api_name_kizen_wont_derive(live):
    live()
    with pytest.raises(PlanError, match=r"becomes 'policies', not 'policy'") as e:
        object_planners.plan_create_object({"name": "Policies", "api_name": "policy"})
    assert '--name "Policy"' in str(e.value)


@pytest.mark.parametrize(
    "api_name, name",
    [
        ("employee_m7SZCzg3", "Employee"),  # suffixed, no name derives it
        ("client_client", "Contacts"),  # "Client Client" would be suffixed
    ],
)
def test_plan_create_object_reports_a_live_api_name_as_existing(live, api_name, name):
    live(listing=[{"id": OBJ_ID, "api_name": api_name, "display_name": name}])
    with pytest.raises(PlanError, match=f"'{api_name}' already exists") as e:
        object_planners.plan_create_object({"name": name, "api_name": api_name})
    assert "becomes" not in str(e.value)
    assert "--name" not in str(e.value)


def test_plan_create_object_mismatch_with_no_possible_name_says_so(live):
    live()
    with pytest.raises(PlanError, match="No display name produces 'Policy_X'"):
        object_planners.plan_create_object({"name": "Policies", "api_name": "Policy_X"})


def test_plan_create_object_warns_when_the_derived_slug_is_taken(live):
    # Another object's display name slugs the same way: Kizen suffixes ours.
    live(listing=[{"id": OBJ_ID, "api_name": "policies", "display_name": "Policies!"}])
    plan = object_planners.plan_create_object({"name": "Policies"})
    (op,) = plan.operations
    assert op.key == "policies"
    assert "random suffix" in op.preview["warning"]


def test_plan_create_object_ignores_deleted_objects(live):
    live(
        listing=[
            {
                "id": OBJ_ID,
                "api_name": "policies",
                "display_name": "Policies",
                "deleted": True,
            }
        ]
    )
    (op,) = object_planners.plan_create_object({"name": "Policies"}).operations
    assert "warning" not in op.preview


def test_plan_create_object_pipeline_type_carries_stages(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Deals", "api_name": "deals", "object_type": "pipeline"}
    )
    (op,) = plan.operations
    assert op.payload["pipeline"]["stages"][0]["name"] == "Open"


# ---------------------------------------------------------------------------
# plan_update_object
# ---------------------------------------------------------------------------


def test_plan_update_object_sends_only_changed_keys(live):
    live()
    plan = object_planners.plan_update_object(
        "policies", {"object_name": "Renamed", "entity_name": "Policy"}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.existing_uuid == OBJ_ID
    # entity_name already matches live state, so it is not resent
    assert op.payload == {"object_name": "Renamed"}
    assert op.preview["diff"] == {"object_name": "Policies → Renamed"}


def test_plan_update_object_can_change_every_supported_key(live):
    live()
    plan = object_planners.plan_update_object(
        "policies",
        {
            "object_name": "Renamed",
            "entity_name": "Contract",
            "description": "now set",
            "default_on_activities": False,
        },
    )
    (op,) = plan.operations
    assert op.payload == {
        "object_name": "Renamed",
        "entity_name": "Contract",
        "description": "now set",
        "default_on_activities": False,
    }


def test_plan_update_object_treats_a_null_description_as_empty_string(live):
    """The API returns `description: null` for an unset description but the
    spec carries `""`, so setting it to `""` must not register as a change."""
    live(detail=dict(OBJECT_DETAIL, raw=dict(OBJECT_DETAIL["raw"], description=None)))
    plan = object_planners.plan_update_object("policies", {"description": ""})
    (op,) = plan.operations
    assert op.action == "skip"


def test_plan_update_object_no_changes_skips(live):
    live()
    plan = object_planners.plan_update_object("policies", {"object_name": "Policies"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}
    assert op.preview["diff"] == "no changes"
    assert "No changes" in plan.summary


def test_plan_update_object_ignores_unknown_keys(live):
    live()
    plan = object_planners.plan_update_object("policies", {"not_a_key": "x"})
    (op,) = plan.operations
    assert op.action == "skip"


def test_plan_update_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_update_object("nope", {"object_name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_object
# ---------------------------------------------------------------------------


def test_plan_delete_object_warns_about_data_loss(live):
    live()
    plan = object_planners.plan_delete_object("policies")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "object"
    assert op.existing_uuid == OBJ_ID
    assert "archives the object and its data" in op.preview["warning"]


def test_plan_delete_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_delete_object("nope")


# ---------------------------------------------------------------------------
# plan_create_category
# ---------------------------------------------------------------------------


def test_plan_create_category_is_scoped_to_its_parent_object(live):
    live()
    plan = object_planners.plan_create_category(
        "policies", {"name": "Claims", "api_name": "claims"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "category"
    assert op.parent_object_uuid == OBJ_ID
    assert op.payload == {"name": "Claims"}
    assert op.preview["object"] == "policies"


def test_plan_create_category_accepts_a_model(live):
    live()
    plan = object_planners.plan_create_category(
        "policies",
        FieldCategory.model_validate({"name": "Claims", "api_name": "claims"}),
    )
    (op,) = plan.operations
    assert op.payload == {"name": "Claims"}


def test_plan_create_category_raises_when_the_name_is_taken(live):
    live()
    with pytest.raises(PlanError, match="category 'Details' already exists"):
        object_planners.plan_create_category(
            "policies", {"name": "Details", "api_name": "details"}
        )


def test_plan_create_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_create_category(
            "nope", {"name": "Claims", "api_name": "claims"}
        )


# ---------------------------------------------------------------------------
# plan_update_category
# ---------------------------------------------------------------------------


def test_plan_update_category_renames(live):
    live()
    plan = object_planners.plan_update_category(
        "policies", "Details", {"name": "Overview"}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"name": "Overview"}
    assert op.existing_uuid == CAT_ID
    assert op.parent_object_uuid == OBJ_ID
    assert op.preview["diff"] == {"name": "Details → Overview"}


def test_plan_update_category_same_name_skips(live):
    live()
    plan = object_planners.plan_update_category(
        "policies", "Details", {"name": "Details"}
    )
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.preview["diff"] == "no changes"
    assert "No changes" in plan.summary


def test_plan_update_category_unknown_category_lists_available(live):
    live()
    with pytest.raises(PlanError, match=r"not found on 'policies'"):
        object_planners.plan_update_category("policies", "Nope", {"name": "x"})


def test_plan_update_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_update_category("nope", "Details", {"name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_category
# ---------------------------------------------------------------------------


def test_plan_delete_category(live):
    live()
    plan = object_planners.plan_delete_category("policies", "Billing")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "category"
    assert op.existing_uuid == CAT_ID_2
    assert op.parent_object_uuid == OBJ_ID
    assert op.key == "policies.Billing"


def test_plan_delete_category_unknown_category_lists_available(live):
    live()
    with pytest.raises(PlanError, match=r"not found on 'policies'"):
        object_planners.plan_delete_category("policies", "Nope")


def test_plan_delete_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_delete_category("nope", "Details")


# ---------------------------------------------------------------------------
# against the real captured object fixtures
# ---------------------------------------------------------------------------


def test_plan_create_category_against_a_captured_object_fixture(patch_live_lookups):
    """Same path, but driven by real captured API output rather than an
    in-test dict — guards against the hand-written fixtures above drifting
    from the shape the API actually returns."""
    plan = object_planners.plan_create_category(
        "patients", {"name": "New Group", "api_name": "new_group"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.parent_object_uuid == "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"
    assert op.payload == {"name": "New Group"}


def test_plan_delete_category_against_a_captured_object_fixture(patch_live_lookups):
    plan = object_planners.plan_delete_category("patients", "Identifiers")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.existing_uuid == "e523181d-9331-4328-b09f-829d8f2a367f"

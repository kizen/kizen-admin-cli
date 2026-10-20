"""The nine forms/surveys planner entry points: form create/update/delete/
duplicate, and field create/update/delete plus option add/remove.

Every planner reaches live state through `tools.forms.get_form` /
`list_forms`, which build their own `KizenClient` from config — so these go
through `respx` against `FAKE_BASE_URL`, the seam the autouse `fake_env`
fixture (conftest.py) already wires up, rather than injecting a client.

Forms and surveys are the same code with a different `base_path`/`kind`, so
the survey path is parameterized rather than duplicated: `kind` feeds the
plan-op `Kind` literals (`survey`, `survey_field`, `survey_field_option`)
that `tools/plans.py` dispatches on, and a wrong one would apply against the
wrong endpoint. `tests/test_form_payloads.py` already covers the pure payload
builders; nothing here re-asserts those.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError
from kizen_builder.models.spec import FormDef, FormFieldDef
from kizen_builder.tools.planners.forms import (
    plan_add_form_field_options,
    plan_create_form,
    plan_create_form_fields,
    plan_delete_form,
    plan_delete_form_field,
    plan_duplicate_form,
    plan_remove_form_field_option,
    plan_update_form,
    plan_update_form_field,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

FORM_ID = "00000000-0000-4000-8000-000000000f01"
FIELD_ID = "00000000-0000-4000-8000-000000000f02"
OPT_A_ID = "00000000-0000-4000-8000-000000000f03"
OPT_B_ID = "00000000-0000-4000-8000-000000000f04"
OBJ_ID = "00000000-0000-4000-8000-000000000f05"

# (base_path, kind) — every test that touches the shared code path runs both.
BOTH_KINDS = [("/api/forms", "form"), ("/api/surveys", "survey")]

FORM_DETAIL = {
    "id": FORM_ID,
    "name": "Contact Us",
    "api_name": "contact_us",
    "description": "original",
    "template_type": "modern",
    "number_submissions": 4,
}

DROPDOWN_FIELD = {
    "id": FIELD_ID,
    "name": "outcome",
    "display_name": "Outcome",
    "field_type": "dropdown",
    "is_required": False,
    "is_read_only": False,
    "is_hidden": False,
    "order": 0,
    "options": [
        {"id": OPT_A_ID, "name": "Yes", "code": "yes"},
        {"id": OPT_B_ID, "name": "No", "code": "no"},
    ],
}


def _mock_detail(base_path: str, identifier: str = "contact_us", body=None):
    return respx.get(f"{FAKE_BASE_URL}{base_path}/{identifier}").mock(
        return_value=httpx.Response(200, json=body if body is not None else FORM_DETAIL)
    )


def _mock_fields(base_path: str, body=None):
    return respx.get(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}/fields").mock(
        return_value=httpx.Response(
            200, json=body if body is not None else [DROPDOWN_FIELD]
        )
    )


def _mock_list(base_path: str, body=None):
    return respx.get(f"{FAKE_BASE_URL}{base_path}").mock(
        return_value=httpx.Response(200, json=body or {"results": [], "next": None})
    )


def _mock_missing(base_path: str, identifier: str):
    respx.get(f"{FAKE_BASE_URL}{base_path}/{identifier}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    respx.get(f"{FAKE_BASE_URL}{base_path}").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )


# ---------------------------------------------------------------------------
# plan_create_form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("base_path", "kind"), BOTH_KINDS)
@respx.mock
def test_plan_create_form_emits_one_op_with_kind_specific_key(base_path, kind):
    _mock_list(base_path)
    plan = plan_create_form(
        {"name": "Contact Us", "api_name": "contact_us", "related_object_id": OBJ_ID},
        base_path=base_path,
        kind=kind,
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == kind
    assert op.key == f"{kind}:contact_us"
    assert op.payload["related_object_id"] == OBJ_ID
    assert op.preview["fields"] == 0


@respx.mock
def test_plan_create_form_accepts_a_model_as_well_as_a_dict():
    _mock_list("/api/forms")
    plan = plan_create_form(
        FormDef.model_validate({"name": "Contact Us", "related_object_id": OBJ_ID})
    )
    (op,) = plan.operations
    assert op.payload["name"] == "Contact Us"
    # No api_name in the spec -> the server derives one, and the preview says so.
    assert op.preview["api_name"] == "(server-derived)"


@respx.mock
def test_plan_create_form_inline_fields_become_deferred_follow_on_ops():
    """Inline fields are created after the form itself and resolve their
    parent id from the create result, so the whole thing applies in one
    confirm rather than needing the form's UUID up front."""
    _mock_list("/api/forms")
    plan = plan_create_form(
        {
            "name": "Contact Us",
            "api_name": "contact_us",
            "related_object_id": OBJ_ID,
            "fields": [
                {"name": "Summary", "api_name": "summary", "field_type": "text"},
                {"name": "Notes", "api_name": "notes", "field_type": "longtext"},
            ],
        }
    )
    form_op, first, second = plan.operations
    assert form_op.key == "form:contact_us"
    assert form_op.preview["fields"] == 2
    for op, label in ((first, "summary"), (second, "notes")):
        assert op.kind == "form_field"
        assert op.key == f"form:contact_us.field:{label}"
        assert op.deferred_parent_object_key == "form:contact_us"
    # order is assigned by position in the batch
    assert first.payload["order"] == 0
    assert second.payload["order"] == 1


@respx.mock
def test_plan_create_form_resolves_related_object_by_api_name():
    _mock_list("/api/forms")
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": OBJ_ID, "name": "patients", "object_name": "Patients"}
                ],
                "next": None,
            },
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )
    plan = plan_create_form({"name": "Contact Us", "related_object": "patients"})
    (op,) = plan.operations
    assert op.payload["related_object_id"] == OBJ_ID


@respx.mock
def test_plan_create_form_raises_when_api_name_already_exists():
    _mock_list(
        "/api/forms",
        body={"results": [{"id": FORM_ID, "api_name": "contact_us"}], "next": None},
    )
    with pytest.raises(PlanError, match="already exists"):
        plan_create_form(
            {
                "name": "Contact Us",
                "api_name": "contact_us",
                "related_object_id": OBJ_ID,
            }
        )


@respx.mock
def test_plan_create_form_requires_a_related_object():
    """Submissions have to attach records somewhere, so the planner refuses a
    spec with neither `related_object` nor `related_object_id` rather than
    letting the API reject it."""
    _mock_list("/api/forms")
    with pytest.raises(PlanError, match="requires 'related_object'"):
        plan_create_form({"name": "Contact Us"})


@respx.mock
def test_plan_create_form_rejects_duplicate_inline_field_labels():
    _mock_list("/api/forms")
    with pytest.raises(PlanError, match="duplicate form field 'summary'"):
        plan_create_form(
            {
                "name": "Contact Us",
                "related_object_id": OBJ_ID,
                "fields": [
                    {"name": "A", "api_name": "summary", "field_type": "text"},
                    {"name": "B", "api_name": "summary", "field_type": "text"},
                ],
            }
        )


# ---------------------------------------------------------------------------
# plan_update_form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("base_path", "kind"), BOTH_KINDS)
@respx.mock
def test_plan_update_form_sends_only_changed_keys(base_path, kind):
    _mock_detail(base_path)
    plan = plan_update_form(
        "contact_us",
        {"name": "Renamed", "description": "original"},
        base_path=base_path,
        kind=kind,
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.kind == kind
    assert op.existing_uuid == FORM_ID
    # description matches what's live, so it is not resent
    assert op.payload == {"name": "Renamed"}


@respx.mock
def test_plan_update_form_ignores_unknown_keys():
    _mock_detail("/api/forms")
    plan = plan_update_form("contact_us", {"not_a_form_key": "x"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}


@respx.mock
def test_plan_update_form_no_changes_skips():
    _mock_detail("/api/forms")
    plan = plan_update_form("contact_us", {"name": "Contact Us"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.preview["diff"] == "no changes"
    assert "No changes" in plan.summary


@respx.mock
def test_plan_update_form_summarizes_form_ui_instead_of_dumping_it():
    """A raw form_ui diff would be an unreadable wall of layout JSON, so the
    preview reports page counts instead."""
    _mock_detail(
        "/api/forms",
        body=dict(FORM_DETAIL, form_ui={"pages": [{"a": 1}, {"b": 2}]}),
    )
    plan = plan_update_form("contact_us", {"form_ui": {"pages": [{"c": 3}]}})
    (op,) = plan.operations
    assert op.preview["diff"]["form_ui"] == "(2 page(s)) → (1 page(s))"


@respx.mock
def test_plan_update_form_not_found_raises_plan_error():
    _mock_missing("/api/forms", "nope")
    with pytest.raises(PlanError, match="form 'nope' not found"):
        plan_update_form("nope", {"name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_form / plan_duplicate_form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("base_path", "kind"), BOTH_KINDS)
@respx.mock
def test_plan_delete_form(base_path, kind):
    _mock_detail(base_path)
    plan = plan_delete_form("contact_us", base_path=base_path, kind=kind)
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == kind
    assert op.existing_uuid == FORM_ID
    assert op.preview["n_submissions"] == 4


@respx.mock
def test_plan_delete_form_not_found_raises_plan_error():
    _mock_missing("/api/surveys", "nope")
    with pytest.raises(PlanError, match="survey 'nope' not found"):
        plan_delete_form("nope", base_path="/api/surveys", kind="survey")


@respx.mock
def test_plan_duplicate_form_defaults_new_name_to_copy_of_source():
    _mock_detail("/api/forms")
    plan = plan_duplicate_form("contact_us")
    (op,) = plan.operations
    assert op.action == "duplicate"
    assert op.key == "form:contact_us:duplicate"
    assert op.payload == {}
    assert op.preview["new_name"] == "Copy of Contact Us"


@respx.mock
def test_plan_duplicate_form_explicit_name_goes_in_the_payload():
    _mock_detail("/api/forms")
    plan = plan_duplicate_form("contact_us", name="Second Copy")
    (op,) = plan.operations
    assert op.payload == {"name": "Second Copy"}
    assert op.preview["new_name"] == "Second Copy"


@respx.mock
def test_plan_duplicate_form_not_found_raises_plan_error():
    _mock_missing("/api/forms", "nope")
    with pytest.raises(PlanError, match="form 'nope' not found"):
        plan_duplicate_form("nope")


# ---------------------------------------------------------------------------
# plan_create_form_fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("base_path", "kind"), BOTH_KINDS)
@respx.mock
def test_plan_create_form_fields_orders_after_existing_fields(base_path, kind):
    """New fields append, so their order starts at the current field count
    rather than at 0 — otherwise they'd collide with existing fields."""
    _mock_detail(base_path)
    _mock_fields(base_path)
    plan = plan_create_form_fields(
        "contact_us",
        [
            {"name": "Notes", "api_name": "notes", "field_type": "text"},
            {"name": "Extra", "api_name": "extra", "field_type": "text"},
        ],
        base_path=base_path,
        kind=kind,
    )
    first, second = plan.operations
    assert first.kind == f"{kind}_field"
    assert first.parent_object_uuid == FORM_ID
    assert first.payload["order"] == 1  # one field already exists
    assert second.payload["order"] == 2


@respx.mock
def test_plan_create_form_fields_accepts_models():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_create_form_fields(
        "contact_us",
        [FormFieldDef.model_validate({"name": "Notes", "field_type": "text"})],
    )
    (op,) = plan.operations
    assert op.key == "form:contact_us.field:Notes"


@respx.mock
def test_plan_create_form_fields_empty_list_raises():
    with pytest.raises(PlanError, match="no fields provided"):
        plan_create_form_fields("contact_us", [])


@respx.mock
def test_plan_create_form_fields_rejects_existing_api_name():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="already exists"):
        plan_create_form_fields(
            "contact_us",
            [{"name": "Outcome", "api_name": "outcome", "field_type": "text"}],
        )


@respx.mock
def test_plan_create_form_fields_rejects_duplicates_within_the_batch():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="duplicate form field 'notes'"):
        plan_create_form_fields(
            "contact_us",
            [
                {"name": "A", "api_name": "notes", "field_type": "text"},
                {"name": "B", "api_name": "notes", "field_type": "text"},
            ],
        )


# ---------------------------------------------------------------------------
# plan_update_form_field
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_update_form_field_maps_spec_names_to_wire_names():
    """The spec says `name`/`required`/`hidden`; the wire wants
    `display_name`/`is_required`/`is_hidden`."""
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_update_form_field(
        "contact_us",
        "outcome",
        {"name": "Result", "required": True, "read_only": True, "hidden": True},
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.existing_uuid == FIELD_ID
    assert op.parent_object_uuid == FORM_ID
    assert op.payload == {
        "display_name": "Result",
        "is_required": True,
        "is_read_only": True,
        "is_hidden": True,
    }


@respx.mock
def test_plan_update_form_field_order_change_is_sent():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_update_form_field("contact_us", "outcome", {"order": 5})
    (op,) = plan.operations
    assert op.payload == {"order": 5}


@respx.mock
def test_plan_update_form_field_unchanged_values_skip():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_update_form_field(
        "contact_us", "outcome", {"name": "Outcome", "required": False}
    )
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}
    assert op.preview["diff"] == "no changes"


@respx.mock
def test_plan_update_form_field_unknown_field_lists_available():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="not found on form"):
        plan_update_form_field("contact_us", "nope", {"name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_form_field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("base_path", "kind"), BOTH_KINDS)
@respx.mock
def test_plan_delete_form_field(base_path, kind):
    _mock_detail(base_path)
    _mock_fields(base_path)
    plan = plan_delete_form_field(
        "contact_us", "outcome", base_path=base_path, kind=kind
    )
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == f"{kind}_field"
    assert op.existing_uuid == FIELD_ID
    assert op.parent_object_uuid == FORM_ID
    assert op.preview["field_type"] == "dropdown"


@respx.mock
def test_plan_delete_form_field_unknown_field_raises():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="not found on form"):
        plan_delete_form_field("contact_us", "nope")


# ---------------------------------------------------------------------------
# plan_add_form_field_options
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_add_form_field_options_skips_ones_that_already_exist():
    """Existing-option matching is case-insensitive, so re-running an add is
    a no-op rather than creating a near-duplicate."""
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_add_form_field_options("contact_us", "outcome", ["Maybe", "yes"])
    add, skip = plan.operations
    assert add.action == "create"
    assert add.payload == {"field_id": FIELD_ID, "name": "Maybe"}
    assert skip.action == "skip"
    assert skip.preview["note"] == "already exists"
    assert "Add 1 option(s)" in plan.summary


@respx.mock
def test_plan_add_form_field_options_empty_list_raises():
    with pytest.raises(PlanError, match="no options provided"):
        plan_add_form_field_options("contact_us", "outcome", [])


@respx.mock
def test_plan_add_form_field_options_rejects_a_non_option_field_type():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms", body=[dict(DROPDOWN_FIELD, field_type="text")])
    with pytest.raises(PlanError, match="which has no options"):
        plan_add_form_field_options("contact_us", "outcome", ["Maybe"])


# ---------------------------------------------------------------------------
# plan_remove_form_field_option
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_remove_form_field_option_without_remap_warns_data_loss():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_remove_form_field_option("contact_us", "outcome", "Yes")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.existing_uuid == OPT_A_ID
    assert op.payload == {"field_id": FIELD_ID}
    assert op.preview["on_delete"] == "dropped (records lose this value)"


@respx.mock
def test_plan_remove_form_field_option_with_remap_carries_target_id():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_remove_form_field_option("contact_us", "outcome", "Yes", remap_to="No")
    (op,) = plan.operations
    assert op.payload == {"field_id": FIELD_ID, "remap_to": OPT_B_ID}
    assert op.preview["on_delete"] == "records remapped to 'No'"


@pytest.mark.parametrize("token", [OPT_A_ID, "Yes", "yes", "YES"])
@respx.mock
def test_plan_remove_form_field_option_matches_by_id_name_code_or_case(token):
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_remove_form_field_option("contact_us", "outcome", token)
    (op,) = plan.operations
    assert op.existing_uuid == OPT_A_ID


@respx.mock
def test_plan_remove_form_field_option_unknown_option_lists_available():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="option 'Nope' not found"):
        plan_remove_form_field_option("contact_us", "outcome", "Nope")


@respx.mock
def test_plan_remove_form_field_option_unknown_remap_target_raises():
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    with pytest.raises(PlanError, match="option 'Nope' not found"):
        plan_remove_form_field_option("contact_us", "outcome", "Yes", remap_to="Nope")


_FORM_PLANS = [
    pytest.param(lambda: plan_update_form("contact_us", {"name": "x"}), id="update"),
    pytest.param(lambda: plan_delete_form("contact_us"), id="delete"),
    pytest.param(lambda: plan_duplicate_form("contact_us"), id="duplicate"),
    pytest.param(
        lambda: plan_update_form_field("contact_us", "outcome", {"required": True}),
        id="update_field",
    ),
]


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("plan", _FORM_PLANS)
def test_form_plans_do_not_report_other_errors_as_not_found(plan, status):
    respx.get(f"{FAKE_BASE_URL}/api/forms/contact_us").mock(
        return_value=httpx.Response(status, json={"detail": "boom"})
    )
    with pytest.raises(KizenAPIError) as exc:
        plan()
    assert exc.value.status_code == status


@respx.mock
def test_plan_update_form_field_always_sends_description():
    """Form field reads carry no description, so there is nothing to diff."""
    _mock_detail("/api/forms")
    _mock_fields("/api/forms")
    plan = plan_update_form_field("contact_us", "outcome", {"description": ""})
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"description": ""}

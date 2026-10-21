"""Golden tests for field plan builders: spec in -> exact wire payload out.

These encode the wire-format rules from `kizen docs show reference` (wysiwyg
translation, yesnomaybe defaults, option shapes, relationship resolution) so
refactors can't silently break them.
"""

from __future__ import annotations

import pytest
import respx

from kizen_builder.models.spec import FieldDef
from kizen_builder.tools.planners.fields import (
    plan_create_field,
    plan_create_fields,
    plan_update_field,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL, load_fixture

PATIENTS = "patients"
PATIENT_INFO_CAT = "Patient Info"


def _cat_id(obj_fixture: str, name: str) -> str:
    obj = load_fixture(f"objects/{obj_fixture}.json")
    return next(c["id"] for c in obj["categories"] if c["name"] == name)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_text_field_payload(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Insurance Notes",
            "api_name": "insurance_notes",
            "field_type": "text",
        },
        category=PATIENT_INFO_CAT,
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "field"
    assert op.key == "patients.insurance_notes"
    assert op.payload == {
        "name": "insurance_notes",
        "display_name": "Insurance Notes",
        "field_type": "text",
        "category": _cat_id(PATIENTS, PATIENT_INFO_CAT),
        "is_required": False,
        "is_read_only": False,
        "is_hidden": False,
    }
    obj = load_fixture("objects/patients.json")
    assert op.parent_object_uuid == obj["id"]


def test_wysiwyg_translates_to_longtext_with_markdown_meta(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {"name": "Care Summary", "api_name": "care_summary", "field_type": "wysiwyg"},
        category=PATIENT_INFO_CAT,
    )
    payload = plan.operations[0].payload
    assert payload["field_type"] == "longtext"
    assert payload["meta"] == {"is_markdown": True}


def test_dropdown_options_become_name_code_pairs(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Risk Level",
            "api_name": "risk_level",
            "field_type": "dropdown",
            "options": ["Low", "High"],
        },
        category=PATIENT_INFO_CAT,
    )
    assert plan.operations[0].payload["options"] == [
        {"name": "Low", "code": "Low"},
        {"name": "High", "code": "High"},
    ]


def test_yesnomaybe_defaults_options_with_lowercase_codes(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {"name": "Ambulatory", "api_name": "ambulatory", "field_type": "yesnomaybe"},
        category=PATIENT_INFO_CAT,
    )
    assert plan.operations[0].payload["options"] == [
        {"name": "Yes", "code": "yes"},
        {"name": "No", "code": "no"},
        {"name": "Maybe", "code": "maybe"},
    ]


def test_status_options_use_options_wire_key(patch_live_lookups):
    """`status` fields use the `options` key, NOT `status_options`."""
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Stage",
            "api_name": "stage_field",
            "field_type": "status",
            "status_options": [{"name": "Complete", "status": "won"}],
        },
        category=PATIENT_INFO_CAT,
    )
    payload = plan.operations[0].payload
    assert "status_options" not in payload
    assert payload["options"] == [{"name": "Complete", "code": "complete"}]


def test_relationship_resolves_target_object_and_category(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Tax Lots",
            "api_name": "tax_lots",
            "field_type": "relationship",
            "relation": {"target_object": "tax_lot", "relation_type": "primary"},
        },
        category=PATIENT_INFO_CAT,
    )
    tax_lot = load_fixture("objects/tax_lot.json")
    relation = plan.operations[0].payload["relation"]
    assert relation["related_object"] == tax_lot["id"]
    assert relation["related_category"] == tax_lot["categories"][0]["id"]
    assert relation["relation_type"] == "primary"
    # The API 500s if related_name is omitted (undocumented requirement —
    # OpenAPI marks it nullable). Default to this object's entity_name.
    assert relation["related_name"] == "Patient"


def test_relationship_related_name_is_overridable(patch_live_lookups):
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Tax Lots",
            "api_name": "tax_lots",
            "field_type": "relationship",
            "relation": {
                "target_object": "tax_lot",
                "relation_type": "primary",
                "related_name": "Custom Label",
            },
        },
        category=PATIENT_INFO_CAT,
    )
    assert plan.operations[0].payload["relation"]["related_name"] == "Custom Label"


@pytest.mark.parametrize(
    "cardinality,wire_relation_type",
    [
        ("one_to_one", "one_to_one"),
        ("many_to_one", "primary"),
        ("one_to_many", "primary_for"),
        ("many_to_many", "additional"),
        # Raw wire values still pass straight through for specs authored
        # from live API output.
        ("additional_for", "additional_for"),
        ("primary_for", "primary_for"),
    ],
)
def test_relationship_cardinality_resolves_to_wire_relation_type(
    patch_live_lookups, cardinality, wire_relation_type
):
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Tax Lots",
            "api_name": "tax_lots",
            "field_type": "relationship",
            "relation": {"target_object": "tax_lot", "relation_type": cardinality},
        },
        category=PATIENT_INFO_CAT,
    )
    assert plan.operations[0].payload["relation"]["relation_type"] == wire_relation_type


def test_relationship_defaults_to_many_to_one(patch_live_lookups):
    """No relation_type given -> defaults to many_to_one (wire: primary)."""
    plan = plan_create_field(
        PATIENTS,
        {
            "name": "Tax Lots",
            "api_name": "tax_lots",
            "field_type": "relationship",
            "relation": {"target_object": "tax_lot"},
        },
        category=PATIENT_INFO_CAT,
    )
    assert plan.operations[0].payload["relation"]["relation_type"] == "primary"


def test_create_rejects_existing_field(patch_live_lookups):
    with pytest.raises(PlanError, match="already exists"):
        plan_create_field(
            PATIENTS,
            {"name": "MRN", "api_name": "mrn", "field_type": "text"},
            category=PATIENT_INFO_CAT,
        )


def test_create_rejects_unknown_category(patch_live_lookups):
    with pytest.raises(PlanError, match="not found") as exc:
        plan_create_field(
            PATIENTS,
            {"name": "X", "api_name": "x_field", "field_type": "text"},
            category="No Such Category",
        )
    assert "Available:" in str(exc.value)  # error must list valid choices


def test_create_requires_category(patch_live_lookups):
    with pytest.raises(PlanError, match="category is required"):
        plan_create_field(
            PATIENTS,
            {"name": "X", "api_name": "x_field", "field_type": "text"},
            category=None,
        )


def test_create_rejects_unknown_object(patch_live_lookups):
    with pytest.raises(PlanError, match="not found"):
        plan_create_field(
            "no_such_object",
            {"name": "X", "api_name": "x_field", "field_type": "text"},
            category="Whatever",
        )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_only_sends_changed_keys(patch_live_lookups):
    plan = plan_update_field(PATIENTS, "mrn", {"name": "Medical Record #"})
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"display_name": "Medical Record #"}
    assert op.existing_uuid  # resolved from live state


def test_update_with_no_effective_change_is_skip(patch_live_lookups):
    obj = load_fixture("objects/patients.json")
    mrn = next(f for f in obj["fields"] if f["api_name"] == "mrn")
    plan = plan_update_field(PATIENTS, "mrn", {"name": mrn["display_name"]})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}


def test_update_unknown_field_lists_available(patch_live_lookups):
    with pytest.raises(PlanError, match="Available:"):
        plan_update_field(PATIENTS, "nope", {"name": "X"})


def _patients_with(monkeypatch, patch_live_lookups, *extra_fields, **mrn_changes):
    import kizen_builder.tools.planners.fields as mf

    obj = patch_live_lookups(PATIENTS)
    obj["fields"] = [
        {**f, **mrn_changes} if f["api_name"] == "mrn" else f for f in obj["fields"]
    ]
    obj["fields"] = [*extra_fields, *obj["fields"]]
    monkeypatch.setattr(mf, "get_object", lambda _api_name: obj)
    return obj


def test_update_ignores_a_deleted_field_with_the_same_api_name(
    monkeypatch, patch_live_lookups
):
    deleted_mrn = {"id": "deleted-mrn", "api_name": "mrn", "deleted": True}
    deleted_other = {"id": "deleted-x", "api_name": "old_notes", "deleted": True}
    obj = _patients_with(monkeypatch, patch_live_lookups, deleted_mrn, deleted_other)
    live_mrn = next(
        f for f in obj["fields"] if f["api_name"] == "mrn" and not f.get("deleted")
    )

    (op,) = plan_update_field(PATIENTS, "mrn", {"name": "Medical Record #"}).operations
    assert op.existing_uuid == live_mrn["id"]

    with pytest.raises(PlanError, match="Available:") as exc:
        plan_update_field(PATIENTS, "old_notes", {"name": "X"})
    assert "old_notes" not in str(exc.value).split("Available:")[1]


@pytest.mark.parametrize("existing, given", [("same", "same"), (None, "")])
def test_update_unchanged_description_is_skip(
    monkeypatch, patch_live_lookups, existing, given
):
    _patients_with(monkeypatch, patch_live_lookups, description=existing)
    (op,) = plan_update_field(PATIENTS, "mrn", {"description": given}).operations
    assert op.action == "skip"
    assert op.payload == {}


# One row per input key; each value differs from the `mrn` fixture's.
@pytest.mark.parametrize(
    "changes, category, expected",
    [
        ({"name": "MRN #"}, None, {"display_name": "MRN #"}),
        ({"description": "Chart id"}, None, {"description": "Chart id"}),
        ({"required": False}, None, {"is_required": False}),
        ({"read_only": True}, None, {"is_read_only": True}),
        ({"hidden": True}, None, {"is_hidden": True}),
        (
            {"options": ["A", "B"]},
            None,
            {"options": [{"name": "A", "code": "A"}, {"name": "B", "code": "B"}]},
        ),
        ({"rating": {"max_value": 5}}, None, {"rating": {"max_value": 5}}),
        (
            {"money_options": {"currency": "USD"}},
            None,
            {"money_options": {"currency": "USD"}},
        ),
        (
            {"decimal_options": {"decimal_places": 2}},
            None,
            {"decimal_options": {"decimal_places": 2}},
        ),
        (
            {"phone_options": {"default_country": "US"}},
            None,
            {"phonenumber_options": {"default_country": "US"}},
        ),
        (
            {"status_options": {"color": "#000000"}},
            None,
            {"status_options": {"color": "#000000"}},
        ),
        ({}, PATIENT_INFO_CAT, {"category": _cat_id(PATIENTS, PATIENT_INFO_CAT)}),
    ],
    ids=lambda v: next(iter(v), "") if isinstance(v, dict) else None,
)
def test_update_maps_each_change_to_its_wire_key(
    patch_live_lookups, changes, category, expected
):
    (op,) = plan_update_field(PATIENTS, "mrn", changes, category=category).operations
    assert op.action == "update"
    assert op.payload == expected


@pytest.mark.xfail(
    strict=True,
    reason=(
        "get_object drops is_read_only from the /fields rows, so False != None "
        "plans an update; carrying the key through flips this to XPASS"
    ),
)
def test_update_read_only_false_on_a_writable_field_is_skip():
    # Runs the real get_object: patch_live_lookups would serve the fixture and
    # never see a fix to get_object.
    obj_id = "00000000-0000-4000-8000-0000000000a1"
    base = f"{FAKE_BASE_URL}/api/custom-objects"
    mrn = {
        "id": "00000000-0000-4000-8000-0000000000f1",
        "name": "mrn",
        "field_type": "text",
        "is_required": True,
        "is_read_only": False,
        "is_hidden": False,
    }
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(base).respond(
            json={"results": [{"id": obj_id, "name": PATIENTS}], "next": None}
        )
        mock.get(f"{base}/{obj_id}/categories").respond(json=[])
        mock.get(f"{base}/{obj_id}/fields").respond(json=[mrn])
        (op,) = plan_update_field(PATIENTS, "mrn", {"read_only": False}).operations
    assert op.action == "skip"


# ---------------------------------------------------------------------------
# bulk create
# ---------------------------------------------------------------------------


def test_plan_create_fields_builds_one_op_per_field(patch_live_lookups):
    plan = plan_create_fields(
        PATIENTS,
        [
            (
                {"name": "A", "api_name": "field_a", "field_type": "text"},
                PATIENT_INFO_CAT,
            ),
            (
                {"name": "B", "api_name": "field_b", "field_type": "integer"},
                PATIENT_INFO_CAT,
            ),
        ],
    )
    assert len(plan.operations) == 2
    assert [op.key for op in plan.operations] == [
        "patients.field_a",
        "patients.field_b",
    ]
    assert all(op.action == "create" for op in plan.operations)
    # Same object → same parent uuid on every op (fetched once).
    assert len({op.parent_object_uuid for op in plan.operations}) == 1


def test_plan_create_fields_supports_per_field_categories(patch_live_lookups):
    plan = plan_create_fields(
        PATIENTS,
        [
            (
                {"name": "A", "api_name": "field_a", "field_type": "text"},
                "Patient Info",
            ),
            (
                {"name": "B", "api_name": "field_b", "field_type": "text"},
                "Demographics",
            ),
        ],
    )
    assert plan.operations[0].preview["category"] == "Patient Info"
    assert plan.operations[1].preview["category"] == "Demographics"


def test_plan_create_fields_rejects_duplicate_api_names(patch_live_lookups):
    with pytest.raises(PlanError, match="duplicate field api_name"):
        plan_create_fields(
            PATIENTS,
            [
                (
                    {"name": "A", "api_name": "dup", "field_type": "text"},
                    PATIENT_INFO_CAT,
                ),
                (
                    {"name": "B", "api_name": "dup", "field_type": "text"},
                    PATIENT_INFO_CAT,
                ),
            ],
        )


def test_plan_create_fields_rejects_collision_with_existing(patch_live_lookups):
    with pytest.raises(PlanError, match="already exists"):
        plan_create_fields(
            PATIENTS,
            [
                (
                    {"name": "MRN", "api_name": "mrn", "field_type": "text"},
                    PATIENT_INFO_CAT,
                )
            ],
        )


def test_plan_create_fields_empty_batch_errors(patch_live_lookups):
    with pytest.raises(PlanError, match="no fields"):
        plan_create_fields(PATIENTS, [])


# ---------------------------------------------------------------------------
# reserved field api_names (client-side guard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reserved", ["business_phone", "mobile_phone", "birthday"])
def test_contact_default_field_names_are_reserved(reserved):
    with pytest.raises(ValueError, match="Kizen-reserved"):
        FieldDef(name="X", api_name=reserved, field_type="text")

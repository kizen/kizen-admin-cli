"""Golden tests for the activity plan builders: spec in -> exact wire payload out.

These lock the wire-format rules for activity types and their fields
(wysiwyg -> longtext, dropdown option shape, rating defaults, order handling)
so refactors can't silently break them. The payload builders are pure, so no
live-API stubbing is needed.
"""

from __future__ import annotations

import pytest

from kizen_builder.models.spec import ActivityDef, ActivityFieldDef
from kizen_builder.tools.planners.activities import (
    _build_activity_field_payload,
    _build_activity_payload,
)

# ---------------------------------------------------------------------------
# Activity-type payload
# ---------------------------------------------------------------------------


def test_activity_payload_minimal_is_just_name():
    act = ActivityDef.model_validate({"name": "Site Visit"})
    assert _build_activity_payload(act) == {"name": "Site Visit"}


def test_activity_payload_includes_only_set_keys():
    act = ActivityDef.model_validate(
        {
            "name": "Site Visit",
            "api_name": "site_visit",
            "description": "desc",
            "is_editable": True,
            "association_mode": "selected_objects_associated",
        }
    )
    assert _build_activity_payload(act) == {
        "name": "Site Visit",
        "api_name": "site_visit",
        "description": "desc",
        "is_editable": True,
        "association_mode": "selected_objects_associated",
    }


OBJ_A = "00000000-0000-4000-8000-0000000000a1"
OBJ_B = "00000000-0000-4000-8000-0000000000b2"


def test_activity_payload_sends_selected_object_ids_as_associated_objects():
    """The shape POST accepts, confirmed live 2026-10-06: each entry needs all
    three flags, and without a mode the server picks all_objects_associated."""
    act = ActivityDef.model_validate(
        {"name": "Site Visit", "selected_object_ids": [OBJ_A, OBJ_B]}
    )
    assert _build_activity_payload(act) == {
        "name": "Site Visit",
        "association_mode": "selected_objects_associated",
        "associated_objects": [
            {
                "custom_object": {"id": OBJ_A},
                "is_required": False,
                "is_readonly": False,
                "order": 0,
            },
            {
                "custom_object": {"id": OBJ_B},
                "is_required": False,
                "is_readonly": False,
                "order": 1,
            },
        ],
    }


def test_activity_payload_keeps_an_explicit_association_mode():
    act = ActivityDef.model_validate(
        {
            "name": "Site Visit",
            "association_mode": "all_objects_associated",
            "selected_object_ids": [OBJ_A],
        }
    )
    payload = _build_activity_payload(act)
    assert payload["association_mode"] == "all_objects_associated"
    assert "selected_object_ids" not in payload


def test_activity_payload_passes_associated_objects_through_verbatim():
    """The read shape pastes back unchanged (POST accepted it, probe J)."""
    read_shape = [
        {
            "custom_object": {
                "id": OBJ_A,
                "name": "patients",
                "object_name": "Patients",
            },
            "is_required": True,
            "is_readonly": False,
            "order": 0,
        }
    ]
    act = ActivityDef.model_validate(
        {
            "name": "Site Visit",
            "association_mode": "selected_objects_associated",
            "associated_objects": read_shape,
        }
    )
    assert _build_activity_payload(act)["associated_objects"] == read_shape


def test_activity_payload_defaults_the_mode_for_associated_objects():
    """Without a mode the server stores all_objects_associated (probe K)."""
    act = ActivityDef.model_validate(
        {"name": "Site Visit", "associated_objects": [{"custom_object": {"id": OBJ_A}}]}
    )
    assert _build_activity_payload(act)["association_mode"] == (
        "selected_objects_associated"
    )


@pytest.mark.parametrize("mode", ["all_objects_associated", "no_objects_associated"])
def test_activity_payload_without_objects_sends_no_association_keys(mode):
    act = ActivityDef.model_validate({"name": "Site Visit", "association_mode": mode})
    assert _build_activity_payload(act) == {
        "name": "Site Visit",
        "association_mode": mode,
    }


def test_spec_with_custom_object_ids_is_rejected():
    with pytest.raises(ValueError, match="'custom_object_ids'.*'selected_object_ids'"):
        ActivityDef.model_validate({"name": "X", "custom_object_ids": [OBJ_A]})


def test_spec_with_both_association_keys_is_rejected():
    with pytest.raises(
        ValueError, match="'associated_objects' or 'selected_object_ids', not both"
    ):
        ActivityDef.model_validate(
            {
                "name": "X",
                "selected_object_ids": [OBJ_A],
                "associated_objects": [{"custom_object": {"id": OBJ_B}}],
            }
        )


# ---------------------------------------------------------------------------
# Activity-field payload
# ---------------------------------------------------------------------------


def test_field_text_payload_with_order():
    fd = ActivityFieldDef.model_validate(
        {"name": "Summary", "api_name": "summary", "field_type": "longtext"}
    )
    assert _build_activity_field_payload(fd, default_order=3) == {
        "display_name": "Summary",
        "name": "summary",
        "field_type": "longtext",
        "is_required": False,
        "is_read_only": False,
        "is_hidden": False,
        "order": 3,
    }


def test_field_api_name_optional():
    fd = ActivityFieldDef.model_validate({"name": "Summary", "field_type": "text"})
    payload = _build_activity_field_payload(fd)
    assert "name" not in payload
    assert payload["display_name"] == "Summary"


def test_field_wysiwyg_translates_to_longtext_with_markdown_meta():
    fd = ActivityFieldDef.model_validate({"name": "Notes", "field_type": "wysiwyg"})
    payload = _build_activity_field_payload(fd)
    assert payload["field_type"] == "longtext"
    assert payload["meta"] == {"is_markdown": True}


def test_field_dropdown_options_shape_and_required():
    fd = ActivityFieldDef.model_validate(
        {
            "name": "Outcome",
            "api_name": "outcome",
            "field_type": "dropdown",
            "options": ["A", "B"],
            "required": True,
        }
    )
    payload = _build_activity_field_payload(fd)
    assert payload["is_required"] is True
    assert payload["options"] == [
        {"name": "A", "code": "A"},
        {"name": "B", "code": "B"},
    ]


def test_field_rating_defaults_applied():
    fd = ActivityFieldDef.model_validate({"name": "Score", "field_type": "rating"})
    payload = _build_activity_field_payload(fd)
    assert payload["rating"] == {
        "min_value": 1,
        "max_value": 5,
        "min_label": "Low",
        "max_label": "High",
    }


def test_field_explicit_order_wins_over_default():
    fd = ActivityFieldDef.model_validate(
        {"name": "X", "field_type": "text", "order": 9}
    )
    assert _build_activity_field_payload(fd, default_order=0)["order"] == 9


def test_dropdown_without_options_rejected():
    with pytest.raises(ValueError, match="requires a non-empty 'options'"):
        ActivityFieldDef.model_validate({"name": "Bad", "field_type": "dropdown"})


def test_non_option_type_with_options_rejected():
    with pytest.raises(ValueError, match="cannot have 'options'"):
        ActivityFieldDef.model_validate(
            {"name": "Bad", "field_type": "text", "options": ["x"]}
        )

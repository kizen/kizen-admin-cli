"""Golden tests for the form/survey plan builders: spec in -> exact wire payload out.

Forms and surveys are structurally identical (same wire shape, different
base path), so ``FormDef``/``FormFieldDef`` and the payload builders in
``tools/planners/forms.py`` are shared by both. These lock the wire-format
rules (wysiwyg -> longtext, dropdown option shape, rating defaults, order
handling) so refactors can't silently break them. The payload builders are
pure, so no live-API stubbing is needed.
"""

from __future__ import annotations

import pytest

from kizen_builder.models.spec import FormDef, FormFieldDef
from kizen_builder.tools.planners.forms import (
    _build_form_field_payload,
    _build_form_payload,
)

# ---------------------------------------------------------------------------
# Form/survey payload
# ---------------------------------------------------------------------------


def test_form_payload_minimal_is_just_name_and_default_template():
    form = FormDef.model_validate({"name": "Contact Us"})
    assert _build_form_payload(form) == {
        "name": "Contact Us",
        "template_type": "modern",
    }


def test_form_payload_includes_only_set_keys():
    form = FormDef.model_validate(
        {
            "name": "Contact Us",
            "api_name": "contact_us",
            "description": "desc",
            "related_object_id": "obj-uuid-123",
            "submission_action": "go_to_url",
            "redirect_url": "https://example.com/thanks",
        }
    )
    assert _build_form_payload(form) == {
        "name": "Contact Us",
        "template_type": "modern",
        "api_name": "contact_us",
        "description": "desc",
        "related_object_id": "obj-uuid-123",
        "submission_action": "go_to_url",
        "redirect_url": "https://example.com/thanks",
    }


def test_form_payload_resolves_related_object_api_name(monkeypatch):
    import kizen_builder.tools.planners.forms as forms_planners

    monkeypatch.setattr(
        forms_planners, "get_object", lambda api_name: {"id": f"resolved-{api_name}"}
    )
    form = FormDef.model_validate(
        {"name": "Contact Us", "related_object": "client_client"}
    )
    payload = _build_form_payload(form)
    assert payload["related_object_id"] == "resolved-client_client"


# ---------------------------------------------------------------------------
# Form/survey field payload
# ---------------------------------------------------------------------------


def test_field_without_order_omits_it_so_server_appends():
    """No `order` in the spec -> not sent. The server appends after the
    form's highest order (FormFieldViewSet.perform_create), which is also
    what the UI's createField does (it never sends order)."""
    fd = FormFieldDef.model_validate(
        {"name": "Summary", "api_name": "summary", "field_type": "longtext"}
    )
    assert _build_form_field_payload(fd) == {
        "display_name": "Summary",
        "name": "summary",
        "field_type": "longtext",
        "is_required": False,
        "is_read_only": False,
        "is_hidden": False,
    }


def test_field_api_name_optional():
    fd = FormFieldDef.model_validate({"name": "Summary", "field_type": "text"})
    payload = _build_form_field_payload(fd)
    assert "name" not in payload
    assert payload["display_name"] == "Summary"


def test_field_wysiwyg_is_a_native_form_field_type():
    """Unlike custom-object/activity fields, the live FormFieldFieldTypeEnum
    lists `wysiwyg` directly — no longtext/meta remap needed here."""
    fd = FormFieldDef.model_validate({"name": "Notes", "field_type": "wysiwyg"})
    payload = _build_form_field_payload(fd)
    assert payload["field_type"] == "wysiwyg"
    assert "meta" not in payload


def test_field_dropdown_options_shape_and_required():
    fd = FormFieldDef.model_validate(
        {
            "name": "Outcome",
            "api_name": "outcome",
            "field_type": "dropdown",
            "options": ["A", "B"],
            "required": True,
        }
    )
    payload = _build_form_field_payload(fd)
    assert payload["is_required"] is True
    assert payload["options"] == [
        {"name": "A", "code": "A"},
        {"name": "B", "code": "B"},
    ]


def test_field_rating_defaults_applied():
    fd = FormFieldDef.model_validate({"name": "Score", "field_type": "rating"})
    payload = _build_form_field_payload(fd)
    assert payload["rating"] == {
        "min_value": 1,
        "max_value": 5,
        "min_label": "Low",
        "max_label": "High",
    }


def test_field_explicit_order_is_sent():
    fd = FormFieldDef.model_validate({"name": "X", "field_type": "text", "order": 9})
    assert _build_form_field_payload(fd)["order"] == 9


def test_dropdown_without_options_rejected():
    with pytest.raises(ValueError, match="requires a non-empty 'options'"):
        FormFieldDef.model_validate({"name": "Bad", "field_type": "dropdown"})


def test_non_option_type_with_options_rejected():
    with pytest.raises(ValueError, match="cannot have 'options'"):
        FormFieldDef.model_validate(
            {"name": "Bad", "field_type": "text", "options": ["x"]}
        )


def test_activity_custom_field_type_not_valid_for_forms():
    """Unlike activities, forms/surveys aren't tied to a custom object, so the
    activity_custom_field linked-field type doesn't apply here."""
    with pytest.raises(ValueError):
        FormFieldDef.model_validate(
            {"name": "Bad", "field_type": "activity_custom_field"}
        )

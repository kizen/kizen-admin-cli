"""Every rejection the object spec validators raise, and the two defaults they fill."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from kizen_builder.models.spec import (
    ActivityFieldDef,
    FieldCategory,
    FieldDef,
    FormFieldDef,
    MoneyConfig,
    ObjectDef,
    RatingConfig,
    SolutionSpec,
)


def _field(api_name: str = "notes", field_type: str = "text", **extra) -> dict:
    return {"name": "Field", "api_name": api_name, "field_type": field_type, **extra}


def _category(api_name: str, *fields: dict) -> dict:
    return {"name": "Category", "api_name": api_name, "fields": list(fields)}


def _object(api_name: str, *categories: dict) -> dict:
    return {
        "name": "Object",
        "api_name": api_name,
        "field_categories": list(categories),
    }


RELATION = {"target_object": "patients"}
STATUS_OPTIONS = [{"name": "Open"}]


@pytest.mark.parametrize(
    "model, data, message",
    [
        pytest.param(
            FieldDef,
            _field("email"),
            "field api_name 'email' collides with a Kizen-reserved name",
            id="reserved-api-name",
        ),
        pytest.param(
            FieldDef,
            _field(field_type="dropdown"),
            "of type 'dropdown' requires a non-empty 'options' list",
            id="options-missing",
        ),
        pytest.param(
            FieldDef,
            _field(field_type="relationship"),
            "of type 'relationship' requires a 'relation' block",
            id="relation-missing",
        ),
        pytest.param(
            FieldDef,
            _field(relation=RELATION),
            "of type 'text' cannot have a 'relation' block",
            id="relation-unexpected",
        ),
        pytest.param(
            FieldDef,
            _field(field_type="status"),
            "of type 'status' requires 'status_options'",
            id="status-missing",
        ),
        pytest.param(
            FieldCategory,
            _category("info", _field("notes"), _field("notes")),
            "duplicate field api_name 'notes' within category",
            id="duplicate-field-in-category",
        ),
        pytest.param(
            ObjectDef,
            _object("cases", _category("info"), _category("info")),
            "duplicate category api_name 'info' within object",
            id="duplicate-category",
        ),
        pytest.param(
            ObjectDef,
            _object(
                "cases",
                _category("info", _field("notes")),
                _category("extra", _field("notes")),
            ),
            "duplicate field api_name 'notes' within object 'cases'",
            id="duplicate-field-across-categories",
        ),
        pytest.param(
            SolutionSpec,
            {"name": "S", "custom_objects": [_object("cases"), _object("cases")]},
            "duplicate object api_name 'cases'",
            id="duplicate-object",
        ),
        pytest.param(
            SolutionSpec,
            {
                "name": "S",
                "automations": [
                    {"name": "A", "api_name": "intake", "type": "global"},
                    {"name": "B", "api_name": "intake", "type": "global"},
                ],
            },
            "duplicate automation api_name 'intake'",
            id="duplicate-automation",
        ),
    ],
)
def test_spec_rejects(model, data, message):
    with pytest.raises(ValidationError, match=re.escape(message)):
        model.model_validate(data)


FIELD_MODELS = [FieldDef, ActivityFieldDef, FormFieldDef]


@pytest.mark.parametrize("model", FIELD_MODELS)
@pytest.mark.parametrize(
    "key, value, message",
    [
        (
            "is_required",
            True,
            "field 'Field': write 'required', not 'is_required' (the API's spelling)",
        ),
        (
            "is_read_only",
            True,
            "field 'Field': write 'read_only', not 'is_read_only' (the API's spelling)",
        ),
        (
            "is_hidden",
            True,
            "field 'Field': write 'hidden', not 'is_hidden' (the API's spelling)",
        ),
        (
            "display_name",
            "Field",
            "field 'Field': write 'name', not 'display_name' (the API's spelling; "
            "the API's 'name' is 'api_name' in a spec)",
        ),
        (
            "phonenumber_options",
            {},
            "field 'Field': write 'phone_options', not 'phonenumber_options' "
            "(the API's spelling)",
        ),
    ],
)
def test_field_spec_rejects_the_apis_spelling(model, key, value, message):
    with pytest.raises(ValidationError, match=re.escape(message)):
        model.model_validate(_field(**{key: value}))


@pytest.mark.parametrize("model", FIELD_MODELS)
@pytest.mark.parametrize(
    "block, value",
    [
        ("options", ["A"]),
        ("money_options", {}),
        ("rating", {}),
        ("decimal_options", {}),
        ("phone_options", {}),
        ("status_options", STATUS_OPTIONS),
    ],
)
def test_field_spec_rejects_a_block_for_another_type(model, block, value):
    with pytest.raises(ValidationError, match=f"of type 'text' cannot have '{block}'"):
        model.model_validate(_field(**{block: value}))


@pytest.mark.parametrize("model", FIELD_MODELS)
def test_field_spec_reports_api_spellings_and_unknown_keys_together(model):
    data = {
        "display_name": "Decision",
        "field_type": "text",
        "is_required": True,
        "id": 7,
        "colour": "red",
    }
    message = (
        "field 'Decision': write 'name', not 'display_name' (the API's spelling; "
        "the API's 'name' is 'api_name' in a spec); "
        "write 'required', not 'is_required' (the API's spelling); "
        "unknown keys: 'colour', 'id'"
    )
    with pytest.raises(ValidationError, match=re.escape(message)):
        model.model_validate(data)


@pytest.mark.parametrize("model", FIELD_MODELS)
def test_field_spec_rejects_an_unknown_key(model):
    with pytest.raises(ValidationError) as e:
        model.model_validate(_field(colour="red"))
    (err,) = e.value.errors()
    assert err["type"] == "extra_forbidden"
    assert err["loc"] == ("colour",)


def test_money_without_options_defaults_to_usd():
    field = FieldDef.model_validate(_field(field_type="money"))
    assert field.money_options == MoneyConfig()
    assert field.money_options.currency == "USD"


def test_rating_without_config_gets_the_default_scale():
    field = FieldDef.model_validate(_field(field_type="rating"))
    assert field.rating == RatingConfig()

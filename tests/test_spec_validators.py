"""Every rejection the object spec validators raise, and the two defaults they fill."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from kizen_builder.models.spec import (
    FieldCategory,
    FieldDef,
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
            _field(options=["A"]),
            "of type 'text' cannot have 'options'",
            id="options-unexpected",
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
            _field(money_options={"currency": "USD"}),
            "of type 'text' cannot have 'money_options'",
            id="money-unexpected",
        ),
        pytest.param(
            FieldDef,
            _field(rating={}),
            "of type 'text' cannot have 'rating'",
            id="rating-unexpected",
        ),
        pytest.param(
            FieldDef,
            _field(decimal_options={}),
            "of type 'text' cannot have 'decimal_options'",
            id="decimal-unexpected",
        ),
        pytest.param(
            FieldDef,
            _field(phone_options={}),
            "of type 'text' cannot have 'phone_options'",
            id="phone-unexpected",
        ),
        pytest.param(
            FieldDef,
            _field(field_type="status"),
            "of type 'status' requires 'status_options'",
            id="status-missing",
        ),
        pytest.param(
            FieldDef,
            _field(status_options=STATUS_OPTIONS),
            "of type 'text' cannot have 'status_options'",
            id="status-unexpected",
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


def test_money_without_options_defaults_to_usd():
    field = FieldDef.model_validate(_field(field_type="money"))
    assert field.money_options == MoneyConfig()
    assert field.money_options.currency == "USD"


def test_rating_without_config_gets_the_default_scale():
    field = FieldDef.model_validate(_field(field_type="rating"))
    assert field.rating == RatingConfig()

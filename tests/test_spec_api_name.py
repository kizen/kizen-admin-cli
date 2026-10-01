"""`ApiName` accepts every api_name Kizen itself produces."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from kizen_builder.models.spec import ApiName, ObjectDef

API_NAME = TypeAdapter(ApiName)


@pytest.mark.parametrize(
    "name", ["policies", "1099_forms", "_internal", "employee_m7SZCzg3"]
)
def test_api_name_accepts_names_kizen_produces(name):
    assert API_NAME.validate_python(name) == name


@pytest.mark.parametrize("name", ["", "has space", "dash-name", "dot.name"])
def test_api_name_rejects_non_identifier_characters(name):
    with pytest.raises(ValidationError):
        API_NAME.validate_python(name)


def test_object_spec_with_leading_digit_api_name_validates():
    obj = ObjectDef.model_validate({"name": "1099 Forms", "api_name": "1099_forms"})
    assert obj.api_name == "1099_forms"

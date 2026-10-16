"""`ApiName` accepts every api_name Kizen itself produces."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from kizen_builder.models.spec import ApiName, ObjectDef, SolutionSpec
from kizen_builder.utils import derived_object_api_name

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


@pytest.mark.parametrize(
    ("object_name", "api_name"),
    [
        # The cases probed live on 2026-10-14 (and the worked example's).
        ("Acme-038 Control Things", "acme_038_control_things"),
        ("Acme-038 Control-Things", "acme_038_control_things"),
        ("Acme-038 Señor's  Policy & Claims", "acme_038_senors_policy_claims"),
        ("Service Tickets", "service_tickets"),
    ],
)
def test_derived_object_api_name_matches_what_kizen_stored(object_name, api_name):
    assert derived_object_api_name(object_name) == api_name


def test_object_spec_api_name_is_optional_and_falls_back_to_the_derived_one():
    obj = ObjectDef.model_validate({"name": "Service Tickets"})
    assert obj.api_name is None
    assert obj.effective_api_name == "service_tickets"


def test_solution_spec_catches_duplicate_derived_object_api_names():
    with pytest.raises(ValidationError, match="duplicate object api_name 'invoices'"):
        SolutionSpec.model_validate(
            {
                "name": "s",
                "custom_objects": [
                    {"name": "Invoices"},
                    {"name": "Invoices!", "api_name": "invoices"},
                ],
            }
        )

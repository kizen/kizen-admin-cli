"""Golden tests for the object plan builder's pipeline payload handling.

Regression coverage: the live API 400s on pipeline-type object creation
without a non-empty `pipeline.stages` list, even though the documented
OpenAPI schema doesn't mark `pipeline` required.
"""

from __future__ import annotations

from kizen_builder.models.spec import ObjectDef
from kizen_builder.tools.planners.objects import _build_object_payload


def test_payload_never_sends_an_api_name():
    # Kizen 403s `name` on create and update (forbidden_field_write,
    # confirmed live 2026-10-14) and has no `api_name` key: never "fix" this.
    obj = ObjectDef.model_validate({"api_name": "invoices", "name": "Invoices"})
    payload = _build_object_payload(obj)
    assert "name" not in payload
    assert "api_name" not in payload


def test_standard_object_has_no_pipeline_key():
    obj = ObjectDef.model_validate({"api_name": "invoice", "name": "Invoices"})
    payload = _build_object_payload(obj)
    assert "pipeline" not in payload


def test_pipeline_object_without_stages_defaults_to_one_open_stage():
    obj = ObjectDef.model_validate(
        {"api_name": "deal", "name": "Deals", "object_type": "pipeline"}
    )
    payload = _build_object_payload(obj)
    assert payload["pipeline"] == {
        "stages": [{"name": "Open", "status": "open", "order": 0}]
    }


def test_pipeline_object_with_explicit_stages_uses_them_as_given():
    obj = ObjectDef.model_validate(
        {
            "api_name": "deal",
            "name": "Deals",
            "object_type": "pipeline",
            "pipeline": {
                "stages": [
                    {"name": "New", "status": "open"},
                    {"name": "Won", "status": "won", "percentage_chance_to_close": 100},
                ]
            },
        }
    )
    payload = _build_object_payload(obj)
    assert payload["pipeline"] == {
        "stages": [
            {"name": "New", "status": "open", "order": 0},
            {
                "name": "Won",
                "status": "won",
                "order": 1,
                "percentage_chance_to_close": 100,
            },
        ]
    }

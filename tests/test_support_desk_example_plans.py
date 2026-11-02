"""The support_desk doc example plans offline against a fixture business."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import kizen_builder.tools.planners.automations as ma
import kizen_builder.tools.planners.forms as mf
from kizen_builder import filtering
from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools.planners.automations import (
    LiveContext,
    _build_automation_payload,
)
from kizen_builder.tools.planners.forms import plan_create_form
from tests.conftest import fake_get_object

EXAMPLE = Path(__file__).parent / "fixtures" / "examples" / "support_desk"

REQUESTS_ID = "5d000000-0000-4000-8000-0000000000a0"
CONTACTS_ID = "5d000000-0000-4000-8000-0000000000c0"
EMAIL_FIELD = "5d000000-0000-4000-8000-000000000003"
OBJECTS = {
    "support_requests": {
        "id": REQUESTS_ID,
        "api_name": "support_requests",
        "fields": [
            {
                "id": "5d000000-0000-4000-8000-000000000001",
                "api_name": "customer_email",
                "field_type": "email",
                "is_default": False,
            },
            {
                "id": "5d000000-0000-4000-8000-000000000002",
                "api_name": "customer",
                "field_type": "relationship",
                "is_default": False,
                "relation": {"related_object": CONTACTS_ID},
            },
            {
                "id": "5d000000-0000-4000-8000-000000000005",
                "api_name": "subject",
                "field_type": "text",
                "is_default": False,
            },
            {
                "id": "5d000000-0000-4000-8000-000000000006",
                "api_name": "message",
                "field_type": "longtext",
                "is_default": False,
            },
            {
                "id": "5d000000-0000-4000-8000-000000000007",
                "api_name": "channel",
                "field_type": "dropdown",
                "is_default": False,
            },
        ],
    },
    "client_client": {
        "id": CONTACTS_ID,
        "api_name": "client_client",
        "fetch_url": "client",
        "fields": [
            {
                "id": EMAIL_FIELD,
                "api_name": "email",
                "field_type": "email",
                "is_default": True,
            },
            {
                "id": "5d000000-0000-4000-8000-000000000004",
                "api_name": "tags",
                "field_type": "dynamictags",
                "is_default": True,
            },
        ],
    },
}


def _get_object(ref: str) -> dict[str, Any]:
    by_id = {o["id"]: o for o in OBJECTS.values()}
    obj = OBJECTS.get(ref) or by_id.get(ref)
    return {**obj, "raw": None} if obj else fake_get_object(ref)


@pytest.fixture
def support_desk(patch_live_lookups, monkeypatch):
    monkeypatch.setattr(ma, "get_object", _get_object)
    monkeypatch.setattr(
        ma.LiveContext,
        "_fields_with_options",
        lambda self, ref: _get_object(ref)["fields"],
    )

    class _Schema:
        def custom_object(self, ref):
            return _get_object(ref)

        def get_field(self, obj_id, name):
            for f in _get_object(obj_id)["fields"]:
                if name in (f["api_name"], f["id"]):
                    return {
                        "name": f["api_name"],
                        "id": f["id"],
                        "field_type": f["field_type"],
                        "is_default": f["is_default"],
                        "options": [],
                        "relation": f.get("relation"),
                    }
            return None

    filtering.set_default_client(_Schema())
    yield
    filtering.set_default_client(None)


def test_find_contact_automation_plans(support_desk):
    text = (EXAMPLE / "find_contact.automation.json").read_text()
    text = text.replace('"<client_client_object_uuid>"', json.dumps(CONTACTS_ID))
    spec = json.loads(text)
    payload = _build_automation_payload(
        AutomationDef.model_validate(spec), LiveContext()
    )
    assert [s["key"] for s in payload["steps"]] == [s["key"] for s in spec["steps"]]
    find = next(s for s in payload["steps"] if s["key"] == "find")
    (clause,) = find["action_search_records"]["filter_config"]["query"][0]["filters"]
    assert {k: v for k, v in clause.items() if k != "view_model"} == {
        "type": "fields",
        "subtype": "non_custom",
        "field": "email",
        "condition": "=",
        "value": "customer_email",
        "value_type": "variable",
    }
    assert clause["view_model"][1][1]["url"] == f"/client/fields/{EMAIL_FIELD}"


def test_contact_support_form_links_every_field(monkeypatch):
    monkeypatch.setattr(mf, "get_object", _get_object)
    monkeypatch.setattr(mf, "list_forms", lambda base_path: [])
    spec = json.loads((EXAMPLE / "contact_support.form.json").read_text())
    form, *fields = plan_create_form(spec).operations
    assert form.payload["related_object_id"] == REQUESTS_ID
    by_id = {f["id"]: f for f in OBJECTS["support_requests"]["fields"]}
    for op in fields:
        linked = by_id[op.payload["custom_object_field"]]
        assert linked["api_name"] == op.payload["name"]
        assert op.payload["field_type"] == linked["field_type"]
        assert "options" not in op.payload
    assert [op.payload["is_required"] for op in fields] == [True, True, True]

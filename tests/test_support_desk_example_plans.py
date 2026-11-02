"""The support_desk doc example plans offline against a fixture business."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import kizen_builder.tools.planners.automations as ma
from kizen_builder import filtering
from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools.planners.automations import (
    LiveContext,
    _build_automation_payload,
)
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


def _plan(name: str) -> dict[str, Any]:
    spec = json.loads((EXAMPLE / name).read_text())
    return _build_automation_payload(AutomationDef.model_validate(spec), LiveContext())


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


def test_read_reply_automation_plans(support_desk):
    payload = _plan("read_reply.automation.json")
    (inbox,) = [t for t in payload["triggers"] if t["type"] == "email_received"]
    assert inbox["trigger_email_received_from_contact"]["receiving_inbox"] == "any"
    code = next(s for s in payload["steps"] if s["type"] == "code_step")
    (contact_email,) = code["action_code_step"]["inputs"]
    assert contact_email["field"] == {"id": EMAIL_FIELD}
    llm = next(s for s in payload["steps"] if s["type"] == "call_llm")
    assert "{{ automation_variable.email_body }}" in llm["action_call_llm"]["prompt"]


def test_draft_reply_automation_plans(support_desk, monkeypatch):
    from kizen_builder.api import automations as api
    from kizen_builder.api import client

    class _Client:
        def __init__(self, cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    server = {"id": "5d000000-0000-4000-8000-0000000000e0", "api_name": "readonly"}
    account = {"id": "5d000000-0000-4000-8000-0000000000e1"}
    monkeypatch.setattr(client, "KizenClient", _Client)
    monkeypatch.setattr(
        api, "list_mcp_step_options", lambda _c: [{**server, "name": "Read Only"}]
    )
    monkeypatch.setattr(
        api,
        "list_run_tools_as_options",
        lambda _c: [{**account, "name": "Agentic Workflow Code"}],
    )
    payload = _plan("draft_reply.automation.json")
    llm = next(s for s in payload["steps"] if s["type"] == "call_llm")
    block = llm["action_call_llm"]
    assert (block["mcp_server_id"], block["run_tools_as_id"]) == (
        server["id"],
        account["id"],
    )

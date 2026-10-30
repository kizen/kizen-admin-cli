"""The first_build doc examples plan offline against fixture Deals/Accounts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import kizen_builder.tools.planners.automations as ma
from kizen_builder import filtering
from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools.planners.activities import plan_create_activity
from kizen_builder.tools.planners.automations import (
    LiveContext,
    _build_automation_payload,
)
from tests.conftest import fake_get_object, load_fixture

EXAMPLES = Path(__file__).parent / "fixtures" / "examples" / "first_build"

DEALS_ID = "fb000000-0000-4000-8000-0000000000d0"
ACCOUNTS_ID = "fb000000-0000-4000-8000-0000000000a0"


def _field(n: int, api_name: str, field_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"fb000000-0000-4000-8000-{n:012d}",
        "api_name": api_name,
        "display_name": api_name.replace("_", " ").title(),
        "field_type": field_type,
        "deleted": False,
        "relation": None,
        **extra,
    }


def _options(*names: str) -> list[dict[str, str]]:
    return [
        {"id": f"fb000000-0000-4000-8000-0000000001{i:02d}", "name": n, "code": n}
        for i, n in enumerate(names)
    ]


DEAL_FIELDS = [
    _field(
        1,
        "account",
        "relationship",
        relation={"related_object": ACCOUNTS_ID, "cardinality": "many_to_one"},
    ),
    _field(2, "close_date", "date"),
    _field(
        3,
        "health",
        "dropdown",
        options=_options("On Track", "Needs Attention", "At Risk"),
    ),
    _field(
        4,
        "call_outcome",
        "dropdown",
        options=_options("Connected", "Left Voicemail", "No Answer"),
    ),
    _field(5, "call_notes", "longtext"),
    _field(6, "ai_deal_brief", "longtext"),
    _field(7, "last_activity_date", "date"),
]
ACCOUNT_FIELDS = [_field(8, "industry", "dropdown", options=_options("Banking"))]
OBJECTS = {
    "deals": {"id": DEALS_ID, "api_name": "deals", "fields": DEAL_FIELDS},
    "accounts": {"id": ACCOUNTS_ID, "api_name": "accounts", "fields": ACCOUNT_FIELDS},
}
TOKENS = {
    "<deals_object_uuid>": DEALS_ID,
    "<deals.account_field_uuid>": DEAL_FIELDS[0]["id"],
    "<deals.call_outcome_field_uuid>": DEAL_FIELDS[3]["id"],
    "<deals.call_notes_field_uuid>": DEAL_FIELDS[4]["id"],
    "<accounts.industry_field_uuid>": ACCOUNT_FIELDS[0]["id"],
    "<closing_soon_message_uuid>": "fb000000-0000-4000-8000-0000000000e0",
}


def _example(name: str) -> dict[str, Any]:
    text = (EXAMPLES / name).read_text()
    for token, value in TOKENS.items():
        text = text.replace(f'"{token}"', json.dumps(value))
    assert "<" not in text.replace("<=", ""), f"unfilled token in {name}"
    return json.loads(text)


def _get_object(ref: str) -> dict[str, Any]:
    by_id = {o["id"]: o for o in OBJECTS.values()}
    obj = OBJECTS.get(ref) or by_id.get(ref)
    return {**obj, "raw": None} if obj else fake_get_object(ref)


@pytest.fixture
def first_build_business(patch_live_lookups, monkeypatch):
    monkeypatch.setattr(ma, "get_object", _get_object)
    monkeypatch.setattr(
        ma.LiveContext,
        "_fields_with_options",
        lambda self, ref: _get_object(ref)["fields"],
    )
    target = {
        "id": "fb000000-0000-4000-8000-0000000000f0",
        "api_name": "refresh_deal_brief",
        "active": True,
    }
    automations = [*load_fixture("automations/list.json"), target]
    monkeypatch.setattr(ma, "list_automations", lambda: automations)

    class _Schema:
        def custom_object(self, api_name):
            return _get_object(api_name)

        def get_field(self, obj_id, name):
            for f in _get_object(obj_id)["fields"]:
                if name in (f["api_name"], f["id"]):
                    return {
                        "name": f["api_name"],
                        "id": f["id"],
                        "field_type": f["field_type"],
                        "is_default": False,
                        "options": f.get("options") or [],
                    }
            return None

    filtering.set_default_client(_Schema())
    yield
    filtering.set_default_client(None)


@pytest.mark.parametrize(
    "name",
    [
        "log_call.automation.json",
        "closing_soon.automation.json",
        "refresh_deal_brief.automation.json",
        "daily_deal_summary.automation.json",
    ],
)
def test_first_build_automation_plans(first_build_business, name):
    spec = _example(name)
    payload = _build_automation_payload(
        AutomationDef.model_validate(spec), LiveContext()
    )
    assert [s["key"] for s in payload["steps"]] == [s["key"] for s in spec["steps"]]
    spec_types = {t["trigger_type"] for t in spec["triggers"]}
    assert spec_types <= {t["type"] for t in payload["triggers"]}


def test_first_build_activity_plans(first_build_business, monkeypatch):
    import kizen_builder.tools.planners.activities as pa

    monkeypatch.setattr(pa, "list_activities", lambda: [])
    plan = plan_create_activity(_example("call.activity.json"))
    assert plan.operations

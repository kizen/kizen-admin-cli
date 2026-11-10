"""`kizen activities scheduled create|update|delete` and their planners.

Logging an activity is done in the Kizen UI by users, so the CLI has no command
for it; completing a scheduled activity is a log, so there's none for that
either.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools.planners.activities import (
    plan_delete_scheduled_activity,
    plan_schedule_activity,
    plan_update_scheduled_activity,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

ACTIVITY_ID = "00000000-0000-4000-8000-000000000b01"
DEALS_ID = "00000000-0000-4000-8000-000000000b06"
DEAL_RECORD_ID = "00000000-0000-4000-8000-000000000b07"
ACTIVITY_DETAIL = {
    "id": ACTIVITY_ID,
    "name": "Call",
    "api_name": "call",
    "association_mode": "selected_objects_associated",
    "custom_objects": [],
    "associated_objects": [],
}


OBJECTS = {
    "results": [
        {"id": DEALS_ID, "name": "deals", "object_name": "Deals"},
    ],
    "next": None,
}


def _mock_reads() -> None:
    respx.get(f"{FAKE_BASE_URL}/api/activities/call").mock(
        return_value=httpx.Response(200, json=ACTIVITY_DETAIL)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECTS)
    )


@respx.mock
def test_schedule_rejects_an_unknown_object():
    _mock_reads()
    _mock_team()
    with pytest.raises(PlanError, match="object 'deal' not found.*deals"):
        plan_schedule_activity(
            "call",
            "2026-11-16T15:30:00Z",
            [("deal", DEAL_RECORD_ID)],
            assignee="sam@example.com",
        )


def test_cli_rejects_a_record_without_an_object():
    result = CliRunner().invoke(
        cli.app,
        [
            "activities", "scheduled", "create", "call",
            "--due", "2026-11-16T15:30:00Z",
            "--assignee", "sam@example.com",
            "--record", DEAL_RECORD_ID,
        ],
    )  # fmt: skip
    assert result.exit_code == 2
    assert "object:record_uuid" in result.output


REP_ID = "00000000-0000-4000-8000-000000000b16"


def _mock_team() -> None:
    respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": REP_ID, "full_name": "Sam Rep", "email": "sam@example.com"}
                ]
            },
        )
    )


@respx.mock
def test_schedule_resolves_assignee_and_nests_associated_entities():
    _mock_reads()
    _mock_team()
    plan = plan_schedule_activity(
        "call",
        "2026-11-16T15:30:00-05:00",
        [("deals", DEAL_RECORD_ID)],
        assignee="sam@example.com",
        note="Follow up on pricing",
    )
    (op,) = plan.operations
    assert (op.kind, op.action) == ("scheduled_activity", "create")
    assert op.payload == {
        "activity_object_id": ACTIVITY_ID,
        "due_datetime": "2026-11-16T15:30:00-05:00",
        "employee_id": REP_ID,
        "associated_entities": [
            {"custom_object": {"id": DEALS_ID}, "entity": {"id": DEAL_RECORD_ID}}
        ],
        "note": "Follow up on pricing",
    }


@pytest.mark.parametrize(
    "due, match",
    [
        ("2026-11-16T15:30:00", "no UTC offset"),
        ("next tuesday", "not an ISO datetime"),
    ],
)
def test_schedule_rejects_a_due_time_without_an_offset(due, match):
    with pytest.raises(PlanError, match=match):
        plan_schedule_activity("call", due, [], assignee="sam@example.com")


@respx.mock
def test_cli_schedule_yes_posts_the_planned_body():
    _mock_reads()
    _mock_team()
    post = respx.post(f"{FAKE_BASE_URL}/api/activities/scheduled-activity").mock(
        return_value=httpx.Response(201, json={"id": "scheduled-1"})
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "activities", "scheduled", "create", "call",
            "--due", "2026-11-16T15:30:00Z",
            "--assignee", REP_ID,
            "--record", f"deals:{DEAL_RECORD_ID}",
            "--yes",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    body = json.loads(post.calls.last.request.content)
    assert body["employee_id"] == REP_ID
    assert body["due_datetime"] == "2026-11-16T15:30:00+00:00"


ROLE_ID = "00000000-0000-4000-8000-000000000b17"
SCHEDULED_ID = "00000000-0000-4000-8000-000000000b18"

# Shape of GET /api/activities/scheduled-activity/{id}, captured on staging
# 2026-11-09 and trimmed.
SCHEDULED = {
    "id": SCHEDULED_ID,
    "note": "Follow up on pricing",
    "due_datetime": "2026-11-16T09:30:00-06:00",
    "completed_at": None,
    "logged_activity_id": None,
    "activity_object": {"id": ACTIVITY_ID, "name": "Call", "api_name": "call"},
    "employee": {
        "id": REP_ID,
        "display_name": "Sam Rep (sam@example.com)",
        "email": "sam@example.com",
    },
    "role": None,
    "associated_entities": [
        {
            "custom_object": {"id": DEALS_ID, "name": "deals", "object_name": "Deals"},
            "entity": {"id": DEAL_RECORD_ID, "name": "Acme renewal"},
            "custom_object_id": DEALS_ID,
            "entity_id": DEAL_RECORD_ID,
            "display_name": "Acme renewal",
        }
    ],
    "notifications": [],
}


def _mock_scheduled(body: dict | None = None) -> None:
    respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity/{SCHEDULED_ID}").mock(
        return_value=httpx.Response(200, json=body or SCHEDULED)
    )


def _mock_roles() -> None:
    respx.get(f"{FAKE_BASE_URL}/api/role").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": ROLE_ID, "name": "Sales"}], "next": None}
        )
    )


@respx.mock
def test_schedule_for_a_role_with_notifications():
    _mock_reads()
    _mock_roles()
    plan = plan_schedule_activity(
        "call",
        "2026-11-16T15:30:00Z",
        [("deals", DEAL_RECORD_ID)],
        role="sales",
        notifications=["email:1:day", "text:30:minute"],
    )
    payload = plan.operations[0].payload
    assert payload["role_id"] == ROLE_ID
    assert "employee_id" not in payload
    assert payload["notifications"] == [
        {"type": "email", "time_amount": 1, "time_unit": "day"},
        {"type": "text", "time_amount": 30, "time_unit": "minute"},
    ]


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({}, "exactly one of --assignee or --role"),
        ({"assignee": "sam@example.com", "role": "Sales"}, "exactly one"),
        (
            {"assignee": "sam@example.com", "notifications": ["email:0:day"]},
            "type:amount:unit",
        ),
        (
            {"assignee": "sam@example.com", "notifications": ["slack:1:day"]},
            "type:amount:unit",
        ),
    ],
)
@respx.mock
def test_schedule_rejects_bad_assignment_and_notifications(kwargs, match):
    _mock_reads()
    with pytest.raises(PlanError, match=match):
        plan_schedule_activity("call", "2026-11-16T15:30:00Z", [], **kwargs)


@respx.mock
def test_update_sends_only_what_changed():
    _mock_scheduled()
    plan = plan_update_scheduled_activity(
        SCHEDULED_ID, due="2026-11-17T10:00:00-06:00", note=""
    )
    (op,) = plan.operations
    assert (op.kind, op.action, op.existing_uuid) == (
        "scheduled_activity",
        "update",
        SCHEDULED_ID,
    )
    assert op.payload == {"due_datetime": "2026-11-17T10:00:00-06:00", "note": ""}


@respx.mock
def test_update_clears_notifications_with_an_empty_list():
    _mock_scheduled()
    plan = plan_update_scheduled_activity(SCHEDULED_ID, clear_notifications=True)
    assert plan.operations[0].payload == {"notifications": []}


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({}, "nothing to change"),
        ({"records": []}, "can't be cleared"),
        (
            {"notifications": ["email:1:day"], "clear_notifications": True},
            "not both",
        ),
    ],
)
@respx.mock
def test_update_rejects(kwargs, match):
    _mock_scheduled()
    with pytest.raises(PlanError, match=match):
        plan_update_scheduled_activity(SCHEDULED_ID, **kwargs)


@respx.mock
def test_delete_previews_what_goes():
    _mock_scheduled()
    plan = plan_delete_scheduled_activity(SCHEDULED_ID)
    (op,) = plan.operations
    assert (op.kind, op.action, op.existing_uuid) == (
        "scheduled_activity",
        "delete",
        SCHEDULED_ID,
    )
    assert op.preview["assignee"] == "sam@example.com"
    assert op.preview["records"] == ["Acme renewal"]
    assert op.preview["permanent"] is True


@respx.mock
def test_delete_of_a_missing_scheduled_activity_is_a_plan_error():
    respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity/{SCHEDULED_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(PlanError, match="not found"):
        plan_delete_scheduled_activity(SCHEDULED_ID)

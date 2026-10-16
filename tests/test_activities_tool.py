"""tools/activities.py: the read/resolve layer over activity types and their
logged/scheduled instances — projection shapes, `resolve_activity_id`'s
fallback scan, and the private normalization helpers.

Every public function builds its own `KizenClient` from config, so these go
through `respx` against `FAKE_BASE_URL`, same seam as
`tests/test_permissions_tool.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.activities import (
    _employee_label,
    _linked_field_label,
    _normalize_field,
    get_activity,
    get_logged_activity,
    get_scheduled,
    list_activities,
    list_logged,
    list_scheduled,
    resolve_activity_id,
)
from tests.conftest import FAKE_BASE_URL

ACTIVITY_ID = "00000000-0000-4000-8000-000000000a01"
FIELD_ID = "00000000-0000-4000-8000-000000000a02"
LOGGED_ID = "00000000-0000-4000-8000-000000000a05"
SCHEDULED_ID = "00000000-0000-4000-8000-000000000a06"
OBJ_ID = "00000000-0000-4000-8000-000000000a07"

ACTIVITY_DETAIL = {
    "id": ACTIVITY_ID,
    "name": "Site Visit",
    "api_name": "site_visit",
    "description": "desc",
    "is_editable": True,
    "association_mode": "all_objects_associated",
    "submission_action": "redirect",
    "webhook_url": None,
    "redirect_url": None,
    "n_submissions": 3,
    "visibility_rules": [],
    "calendar_sync_enabled": False,
    "custom_objects": [{"id": OBJ_ID, "name": "Patients"}],
    "selected_objects": [],
    "loggable_sharing_settings": {},
    "deleted": False,
    "created": "2024-01-01T00:00:00Z",
}

ACTIVITY_FIELDS = [
    {
        "id": FIELD_ID,
        "name": "outcome",
        "display_name": "Outcome",
        "field_type": "dropdown",
        "custom_object_field": None,
        "is_default": False,
        "is_required": True,
        "is_read_only": False,
        "is_hidden": False,
        "is_deletable": True,
        "order": 0,
        "options": [{"id": "opt-1", "name": "Yes", "code": "yes"}],
        "relation": None,
    }
]


def _mock_activity_detail(identifier: str = "site_visit", body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities/{identifier}").mock(
        return_value=httpx.Response(
            200, json=body if body is not None else ACTIVITY_DETAIL
        )
    )


def _mock_activity_fields(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(
            200, json=body if body is not None else ACTIVITY_FIELDS
        )
    )


def _mock_activity_list(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(
            200, json=body or {"results": [ACTIVITY_DETAIL], "next": None}
        )
    )


# ---------------------------------------------------------------------------
# list_activities
# ---------------------------------------------------------------------------


@respx.mock
def test_list_activities_projects_expected_fields():
    _mock_activity_list()
    (row,) = list_activities()
    assert row["id"] == ACTIVITY_ID
    assert row["name"] == "Site Visit"
    assert row["api_name"] == "site_visit"
    assert row["n_submissions"] == 3
    assert row["deleted"] is False
    assert row["env"] == load_env_config().name


@respx.mock
def test_list_activities_defaults_deleted_to_false_when_absent():
    _mock_activity_list(body={"results": [{"id": ACTIVITY_ID}], "next": None})
    (row,) = list_activities()
    assert row["deleted"] is False


# ---------------------------------------------------------------------------
# resolve_activity_id
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_activity_id_direct_get_succeeds():
    _mock_activity_detail()
    with KizenClient(load_env_config()) as client:
        act_id, name = resolve_activity_id(client, "site_visit")
    assert act_id == ACTIVITY_ID
    assert name == "Site Visit"


@respx.mock
def test_resolve_activity_id_falls_back_to_list_scan_on_404():
    """The direct GET by identifier 404s (e.g. a bare display name, which the
    API doesn't accept in the path) — resolution falls back to scanning the
    list endpoint for a matching id/api_name/name."""
    respx.get(f"{FAKE_BASE_URL}/api/activities/Site Visit").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    _mock_activity_list()
    with KizenClient(load_env_config()) as client:
        act_id, name = resolve_activity_id(client, "Site Visit")
    assert act_id == ACTIVITY_ID
    assert name == "Site Visit"


@respx.mock
def test_resolve_activity_id_raises_lookup_error_when_unresolvable():
    respx.get(f"{FAKE_BASE_URL}/api/activities/nope").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    _mock_activity_list()
    with (
        KizenClient(load_env_config()) as client,
        pytest.raises(LookupError, match="activity 'nope' not found"),
    ):
        resolve_activity_id(client, "nope")


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
def test_resolve_activity_id_does_not_report_other_errors_as_not_found(status):
    respx.get(f"{FAKE_BASE_URL}/api/activities/site_visit").mock(
        return_value=httpx.Response(status, json={"detail": "boom"})
    )
    list_route = _mock_activity_list()
    with (
        KizenClient(load_env_config()) as client,
        pytest.raises(KizenAPIError) as exc,
    ):
        resolve_activity_id(client, "site_visit")
    assert exc.value.status_code == status
    assert list_route.call_count == 0


# ---------------------------------------------------------------------------
# _linked_field_label / _normalize_field
# ---------------------------------------------------------------------------


def test_linked_field_label_none_for_non_dict():
    assert _linked_field_label(None) is None
    assert _linked_field_label("bare-uuid") is None


def test_linked_field_label_combines_object_and_field_name():
    cof = {"name": "status", "custom_object": {"name": "Patients"}}
    assert _linked_field_label(cof) == "Patients.status"


def test_linked_field_label_falls_back_to_bare_field_name_without_object():
    cof = {"name": "status", "custom_object": None}
    assert _linked_field_label(cof) == "status"


def test_normalize_field_projects_and_labels_options():
    normalized = _normalize_field(ACTIVITY_FIELDS[0])
    assert normalized["api_name"] == "outcome"
    assert normalized["display_name"] == "Outcome"
    assert normalized["options"] == [{"id": "opt-1", "name": "Yes", "code": "yes"}]
    assert normalized["linked_field"] is None


def test_normalize_field_carries_description():
    field = dict(ACTIVITY_FIELDS[0], description="Why the visit ended")
    assert _normalize_field(field)["description"] == "Why the visit ended"


def test_normalize_field_options_none_when_empty():
    field = dict(ACTIVITY_FIELDS[0], options=[])
    assert _normalize_field(field)["options"] is None


# ---------------------------------------------------------------------------
# get_activity
# ---------------------------------------------------------------------------


@respx.mock
def test_get_activity_includes_fields_by_default():
    _mock_activity_detail()
    _mock_activity_fields()
    result = get_activity("site_visit")
    assert result["id"] == ACTIVITY_ID
    assert result["custom_objects"] == [{"id": OBJ_ID, "name": "Patients"}]
    assert len(result["fields"]) == 1
    assert result["fields"][0]["api_name"] == "outcome"
    assert result["raw"] == ACTIVITY_DETAIL


@respx.mock
def test_get_activity_skips_fields_call_when_include_fields_false():
    route = _mock_activity_fields()
    _mock_activity_detail()
    result = get_activity("site_visit", include_fields=False)
    assert result["fields"] == []
    assert route.call_count == 0


@respx.mock
def test_get_activity_projects_custom_objects_using_object_name_fallback():
    """`custom_objects` entries sometimes carry `object_name` instead of
    `name` (server inconsistency across endpoints) — the projection must
    fall back to it rather than emitting a `None` label."""
    detail = dict(
        ACTIVITY_DETAIL, custom_objects=[{"id": OBJ_ID, "object_name": "Patients"}]
    )
    _mock_activity_detail(body=detail)
    _mock_activity_fields(body=[])
    result = get_activity("site_visit")
    assert result["custom_objects"] == [{"id": OBJ_ID, "name": "Patients"}]


# ---------------------------------------------------------------------------
# get_logged_activity / _employee_label
# ---------------------------------------------------------------------------


def test_employee_label_none_for_falsy():
    assert _employee_label(None) is None
    assert _employee_label("") is None


def test_employee_label_prefers_display_name_then_falls_back():
    assert _employee_label({"display_name": "Jane"}) == "Jane"
    assert _employee_label({"full_name": "Jane"}) == "Jane"
    assert _employee_label({"name": "Jane"}) == "Jane"
    assert _employee_label({"email": "jane@example.com"}) == "jane@example.com"
    assert _employee_label({"id": "emp-1"}) == "emp-1"


def test_employee_label_bare_string_passthrough():
    assert _employee_label("emp-1") == "emp-1"


@respx.mock
def test_get_logged_activity_projects_expected_shape():
    raw = {
        "id": LOGGED_ID,
        "activity_object": {"id": ACTIVITY_ID, "name": "Site Visit"},
        "notes": "all good",
        "logged_at": "2024-01-01T00:00:00Z",
        "logged_by": {"display_name": "Jane"},
        "completed_at": None,
        "completed_by": None,
        "scheduled_activity_id": SCHEDULED_ID,
        "associated_entities": [
            {"object_api_name": "patients", "entity_id": "p1", "name": "Bob"}
        ],
        "fields": [
            {
                "name": "outcome",
                "display_name": "Outcome",
                "field_type": "dropdown",
                "value": "Yes",
            }
        ],
    }
    respx.get(f"{FAKE_BASE_URL}/api/activities/logged/{LOGGED_ID}").mock(
        return_value=httpx.Response(200, json=raw)
    )
    result = get_logged_activity(LOGGED_ID)
    assert result["activity_name"] == "Site Visit"
    assert result["activity_id"] == ACTIVITY_ID
    assert result["logged_by"] == "Jane"
    assert result["associated_entities"] == [
        {"object_api_name": "patients", "entity_id": "p1", "display_name": "Bob"}
    ]
    assert result["fields"] == [
        {
            "api_name": "outcome",
            "display_name": "Outcome",
            "field_type": "dropdown",
            "value": "Yes",
        }
    ]
    assert result["raw"] == raw


# ---------------------------------------------------------------------------
# list_logged
# ---------------------------------------------------------------------------


@respx.mock
def test_list_logged_resolves_identifier_then_lists_responses():
    resolve_route = _mock_activity_detail()
    responses_route = respx.post(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/responses"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "r1",
                        "logged_at": "2024-01-01T00:00:00Z",
                        "logged_by": {"display_name": "Jane"},
                        "associated_entities": [
                            {"display_name": "Bob"},
                            {"name": "Sue"},
                        ],
                        "fields_with_values": 2,
                    }
                ],
                "next": None,
            },
        )
    )
    out = list_logged("site_visit")
    assert resolve_route.call_count == 1
    assert responses_route.call_count == 1
    (row,) = out
    assert row["id"] == "r1"
    assert row["logged_by"] == "Jane"
    assert row["associated"] == "Bob, Sue"
    assert row["fields_with_values"] == 2


# ---------------------------------------------------------------------------
# list_scheduled / get_scheduled
# ---------------------------------------------------------------------------


@respx.mock
def test_list_scheduled_without_activity_filter_skips_resolution():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    list_scheduled()
    assert route.call_count == 1


@respx.mock
def test_list_scheduled_with_activity_resolves_then_filters():
    resolve_route = _mock_activity_detail()
    search_route = respx.get(
        f"{FAKE_BASE_URL}/api/activities/scheduled-activity/search"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": SCHEDULED_ID,
                        "activity_object": {"id": ACTIVITY_ID},
                        "due_datetime": "2024-02-01T00:00:00Z",
                        "completed_at": None,
                        "logged_activity_id": None,
                        "employee": {"id": "emp-1"},
                        "associated_entities": [{"display_name": "Bob"}, "raw-string"],
                    }
                ],
                "next": None,
            },
        )
    )
    (row,) = list_scheduled(activity="site_visit")
    assert resolve_route.call_count == 1
    assert search_route.calls[0].request.url.params["activity_id"] == ACTIVITY_ID
    assert row["id"] == SCHEDULED_ID
    assert row["associated"] == "Bob, raw-string"


@respx.mock
def test_get_scheduled_stamps_env():
    respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity/{SCHEDULED_ID}").mock(
        return_value=httpx.Response(200, json={"id": SCHEDULED_ID})
    )
    result = get_scheduled(SCHEDULED_ID)
    assert result["id"] == SCHEDULED_ID
    assert result["env"] == load_env_config().name

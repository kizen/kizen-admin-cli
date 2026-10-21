"""api/activities.py: the thin HTTP layer for activity types, their fields
and options, and read-only logged/scheduled instances.

Every function takes a `KizenClient` explicitly, so these are exercised
directly against a client (no config/env plumbing needed beyond the
autouse `fake_env` fixture) via `respx` against `FAKE_BASE_URL`.
"""

from __future__ import annotations

import json

import httpx
import respx

from kizen_builder.api import activities as act_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from tests.conftest import FAKE_BASE_URL

ACTIVITY_ID = "00000000-0000-4000-8000-000000000a01"
FIELD_ID = "00000000-0000-4000-8000-000000000a02"
OPTION_ID = "00000000-0000-4000-8000-000000000a03"
OTHER_OPTION_ID = "00000000-0000-4000-8000-000000000a04"
LOGGED_ID = "00000000-0000-4000-8000-000000000a05"
SCHEDULED_ID = "00000000-0000-4000-8000-000000000a06"


def _client() -> KizenClient:
    return KizenClient(load_env_config())


# ---------------------------------------------------------------------------
# list_activities — paging
# ---------------------------------------------------------------------------


@respx.mock
def test_list_activities_follows_next_link_across_two_pages():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [{"id": "1"}],
                    "next": f"{FAKE_BASE_URL}/api/activities?page=2",
                },
            ),
            httpx.Response(200, json={"results": [{"id": "2"}], "next": None}),
        ]
    )

    with _client() as client:
        items = act_api.list_activities(client)

    assert [i["id"] for i in items] == ["1", "2"]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_list_activities_handles_bare_list_response():
    respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
    )
    with _client() as client:
        items = act_api.list_activities(client)
    assert [i["id"] for i in items] == ["1", "2"]


# ---------------------------------------------------------------------------
# Activity types
# ---------------------------------------------------------------------------


@respx.mock
def test_list_activities_plain():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": ACTIVITY_ID}], "next": None}
        )
    )
    with _client() as client:
        out = act_api.list_activities(client)
    assert out == [{"id": ACTIVITY_ID}]
    assert route.call_count == 1


@respx.mock
def test_list_activities_builds_query_params():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    with _client() as client:
        act_api.list_activities(
            client,
            custom_object_id="obj-1",
            search="site visit",
            show_no_objects_associated=True,
        )
    params = route.calls[0].request.url.params
    assert params["custom_object_id"] == "obj-1"
    assert params["search"] == "site visit"
    assert params["show_no_objects_associated"] == "true"


@respx.mock
def test_get_activity():
    respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}").mock(
        return_value=httpx.Response(200, json={"id": ACTIVITY_ID, "name": "Call"})
    )
    with _client() as client:
        detail = act_api.get_activity(client, ACTIVITY_ID)
    assert detail["name"] == "Call"


@respx.mock
def test_create_activity_posts_payload():
    route = respx.post(f"{FAKE_BASE_URL}/api/activities").mock(
        return_value=httpx.Response(201, json={"id": ACTIVITY_ID, "name": "Call"})
    )
    with _client() as client:
        created = act_api.create_activity(client, {"name": "Call"})
    assert created["id"] == ACTIVITY_ID
    assert json.loads(route.calls.last.request.content) == {"name": "Call"}


@respx.mock
def test_update_activity_patches_payload():
    route = respx.patch(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}").mock(
        return_value=httpx.Response(200, json={"id": ACTIVITY_ID, "name": "New"})
    )
    with _client() as client:
        updated = act_api.update_activity(client, ACTIVITY_ID, {"name": "New"})
    assert updated["name"] == "New"
    assert json.loads(route.calls.last.request.content) == {"name": "New"}


@respx.mock
def test_delete_activity_returns_empty_dict_on_no_content():
    respx.delete(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}").mock(
        return_value=httpx.Response(204)
    )
    with _client() as client:
        result = act_api.delete_activity(client, ACTIVITY_ID)
    assert result == {}


@respx.mock
def test_duplicate_activity_posts_an_empty_body():
    route = respx.post(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/duplicate").mock(
        return_value=httpx.Response(201, json={"id": "new-id"})
    )
    with _client() as client:
        result = act_api.duplicate_activity(client, ACTIVITY_ID)
    assert result == {"id": "new-id"}
    assert json.loads(route.calls.last.request.content) == {}


# ---------------------------------------------------------------------------
# Activity fields
# ---------------------------------------------------------------------------


@respx.mock
def test_list_activity_fields_handles_bare_list_response():
    respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(200, json=[{"id": FIELD_ID}])
    )
    with _client() as client:
        fields = act_api.list_activity_fields(client, ACTIVITY_ID)
    assert fields == [{"id": FIELD_ID}]


@respx.mock
def test_list_activity_fields_handles_paginated_dict_response():
    respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(200, json={"results": [{"id": FIELD_ID}]})
    )
    with _client() as client:
        fields = act_api.list_activity_fields(client, ACTIVITY_ID)
    assert fields == [{"id": FIELD_ID}]


@respx.mock
def test_list_activity_fields_falls_back_to_empty_list_for_unrecognized_shape():
    """Neither a bare list nor a `results`-keyed dict — the response is
    something else entirely (e.g. `None` from a 204). The function must not
    raise; it returns an empty list."""
    respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(204)
    )
    with _client() as client:
        fields = act_api.list_activity_fields(client, ACTIVITY_ID)
    assert fields == []


@respx.mock
def test_list_activity_fields_requests_ordering_by_order():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )
    with _client() as client:
        act_api.list_activity_fields(client, ACTIVITY_ID)
    assert route.calls[0].request.url.params["ordering"] == "order"


@respx.mock
def test_create_activity_field_posts_to_fields_endpoint():
    route = respx.post(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields").mock(
        return_value=httpx.Response(201, json={"id": FIELD_ID})
    )
    with _client() as client:
        result = act_api.create_activity_field(
            client, ACTIVITY_ID, {"display_name": "Notes"}
        )
    assert result == {"id": FIELD_ID}
    assert json.loads(route.calls.last.request.content) == {"display_name": "Notes"}


@respx.mock
def test_update_activity_field_patches_field_endpoint():
    route = respx.patch(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields/{FIELD_ID}"
    ).mock(return_value=httpx.Response(200, json={"id": FIELD_ID, "is_hidden": True}))
    with _client() as client:
        result = act_api.update_activity_field(
            client, ACTIVITY_ID, FIELD_ID, {"is_hidden": True}
        )
    assert result["is_hidden"] is True
    assert json.loads(route.calls.last.request.content) == {"is_hidden": True}


@respx.mock
def test_delete_activity_field_returns_empty_dict_on_no_content():
    respx.delete(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields/{FIELD_ID}"
    ).mock(return_value=httpx.Response(204))
    with _client() as client:
        result = act_api.delete_activity_field(client, ACTIVITY_ID, FIELD_ID)
    assert result == {}


@respx.mock
def test_add_activity_field_option_posts_to_options_endpoint():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields/{FIELD_ID}/options"
    ).mock(return_value=httpx.Response(201, json={"id": OPTION_ID, "name": "Yes"}))
    with _client() as client:
        result = act_api.add_activity_field_option(
            client, ACTIVITY_ID, FIELD_ID, {"name": "Yes"}
        )
    assert result["id"] == OPTION_ID
    assert json.loads(route.calls.last.request.content) == {"name": "Yes"}


@respx.mock
def test_delete_activity_field_option_returns_empty_dict_on_no_content():
    respx.delete(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields/{FIELD_ID}"
        f"/options/{OPTION_ID}"
    ).mock(return_value=httpx.Response(204))
    with _client() as client:
        result = act_api.delete_activity_field_option(
            client, ACTIVITY_ID, FIELD_ID, OPTION_ID
        )
    assert result == {}


@respx.mock
def test_replace_activity_field_option_posts_replacement_to_replace_endpoint():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/fields/{FIELD_ID}"
        f"/options/{OPTION_ID}/replace"
    ).mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with _client() as client:
        result = act_api.replace_activity_field_option(
            client, ACTIVITY_ID, FIELD_ID, OPTION_ID, {"id": OTHER_OPTION_ID}
        )
    assert result == {"status": "ok"}
    assert json.loads(route.calls.last.request.content) == {"id": OTHER_OPTION_ID}


# ---------------------------------------------------------------------------
# Logged activities (read-only)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_logged_activity():
    respx.get(f"{FAKE_BASE_URL}/api/activities/logged/{LOGGED_ID}").mock(
        return_value=httpx.Response(200, json={"id": LOGGED_ID, "notes": "hi"})
    )
    with _client() as client:
        result = act_api.get_logged_activity(client, LOGGED_ID)
    assert result["notes"] == "hi"


@respx.mock
def test_list_responses_sends_filter_body_on_first_page_only():
    """The filter body only makes sense on the first request — `next`-link
    follow-ups must be bare POSTs, or the server would double-apply filters
    (or reject an unexpected body) on every subsequent page."""
    route = respx.post(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/responses").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [{"id": "r1"}],
                    "next": (
                        f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/responses?page=2"
                    ),
                },
            ),
            httpx.Response(200, json={"results": [{"id": "r2"}], "next": None}),
        ]
    )
    with _client() as client:
        items = act_api.list_responses(client, ACTIVITY_ID, body={"filter": "open"})
    assert [i["id"] for i in items] == ["r1", "r2"]
    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content) == {"filter": "open"}
    assert json.loads(route.calls[1].request.content) == {}


@respx.mock
def test_list_responses_builds_query_params():
    route = respx.post(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/responses").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    with _client() as client:
        act_api.list_responses(
            client, ACTIVITY_ID, custom_object_id="obj-1", search="jane"
        )
    params = route.calls[0].request.url.params
    assert params["custom_object_id"] == "obj-1"
    assert params["search"] == "jane"


@respx.mock
def test_list_responses_handles_bare_list_response():
    respx.post(f"{FAKE_BASE_URL}/api/activities/{ACTIVITY_ID}/responses").mock(
        return_value=httpx.Response(200, json=[{"id": "r1"}])
    )
    with _client() as client:
        items = act_api.list_responses(client, ACTIVITY_ID)
    assert items == [{"id": "r1"}]


# ---------------------------------------------------------------------------
# Scheduled activities (read-only)
# ---------------------------------------------------------------------------


@respx.mock
def test_list_scheduled_uses_plain_endpoint_without_activity_filter():
    route = respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    with _client() as client:
        act_api.list_scheduled(client)
    assert route.call_count == 1


@respx.mock
def test_list_scheduled_uses_search_endpoint_with_activity_filter():
    """`/search` is a different endpoint than the plain list — only it
    accepts the `activity_id` filter."""
    route = respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity/search").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    with _client() as client:
        act_api.list_scheduled(
            client, activity_id=ACTIVITY_ID, assigned_to_me=True, completed=False
        )
    params = route.calls[0].request.url.params
    assert params["activity_id"] == ACTIVITY_ID
    assert params["assigned_to_me"] == "true"
    assert params["completed"] == "false"


@respx.mock
def test_get_scheduled():
    respx.get(f"{FAKE_BASE_URL}/api/activities/scheduled-activity/{SCHEDULED_ID}").mock(
        return_value=httpx.Response(200, json={"id": SCHEDULED_ID})
    )
    with _client() as client:
        result = act_api.get_scheduled(client, SCHEDULED_ID)
    assert result["id"] == SCHEDULED_ID

"""api/forms.py: the thin CRUD wrappers shared by forms and surveys.

Every function takes ``base_path`` as an explicit argument (``"/api/forms"``
or ``"/api/surveys"``) rather than being duplicated per object type — see the
module docstring. Most tests here are parametrized over both base paths to
lock in that the survey path is wired identically, not just the forms path.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from kizen_builder.api import forms as forms_api
from kizen_builder.api.client import KizenClient
from tests.conftest import FAKE_BASE_URL

FORM_ID = "00000000-0000-4000-8000-000000000f01"
FIELD_ID = "00000000-0000-4000-8000-000000000f02"
OPTION_ID = "00000000-0000-4000-8000-000000000f03"
REPLACEMENT_ID = "00000000-0000-4000-8000-000000000f04"

BASE_PATHS = ["/api/forms", "/api/surveys"]


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


# ---------------------------------------------------------------------------
# _paginate (via list_forms)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_list_forms_returns_plain_list_response(client, base_path):
    respx.get(f"{FAKE_BASE_URL}{base_path}").mock(
        return_value=Response(200, json=[{"id": "a"}, {"id": "b"}])
    )
    out = forms_api.list_forms(client, base_path)
    assert out == [{"id": "a"}, {"id": "b"}]


@respx.mock
def test_list_forms_follows_next_link_across_pages(client):
    """A first page with a `next` URL (carrying its own querystring) must be
    followed to completion — only the path + query is re-requested, not the
    full URL, since KizenClient already binds a base_url."""
    route = respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        side_effect=[
            Response(
                200,
                json={
                    "results": [{"id": "a"}],
                    "next": f"{FAKE_BASE_URL}/api/forms?page=2&page_size=1",
                },
            ),
            Response(200, json={"results": [{"id": "b"}], "next": None}),
        ]
    )

    out = forms_api.list_forms(client, "/api/forms")

    assert out == [{"id": "a"}, {"id": "b"}]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_list_forms_stops_on_unrecognized_response_shape(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(return_value=Response(200, json=None))
    assert forms_api.list_forms(client, "/api/forms") == []


@respx.mock
def test_list_forms_encodes_search_query(client):
    route = respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    forms_api.list_forms(client, "/api/forms", search="a b")
    assert route.calls[0].request.url.params["search"] == "a b"


# ---------------------------------------------------------------------------
# Form / survey object CRUD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_get_form(client, base_path):
    respx.get(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}").mock(
        return_value=Response(200, json={"id": FORM_ID, "name": "Contact Us"})
    )
    out = forms_api.get_form(client, base_path, FORM_ID)
    assert out == {"id": FORM_ID, "name": "Contact Us"}


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_create_form(client, base_path):
    route = respx.post(f"{FAKE_BASE_URL}{base_path}").mock(
        return_value=Response(200, json={"id": FORM_ID})
    )
    out = forms_api.create_form(client, base_path, {"name": "New"})
    assert out == {"id": FORM_ID}
    assert json.loads(route.calls[0].request.content) == {"name": "New"}


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_update_form_sends_patch(client, base_path):
    route = respx.patch(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}").mock(
        return_value=Response(200, json={"id": FORM_ID, "name": "Renamed"})
    )
    out = forms_api.update_form(client, base_path, FORM_ID, {"name": "Renamed"})
    assert out["name"] == "Renamed"
    assert route.calls[0].request.method == "PATCH"


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_delete_form_on_204_returns_empty_dict(client, base_path):
    respx.delete(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}").mock(
        return_value=Response(204)
    )
    assert forms_api.delete_form(client, base_path, FORM_ID) == {}


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_duplicate_form_posts_an_empty_body(client, base_path):
    route = respx.post(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}/duplicate").mock(
        return_value=Response(200, json={"id": "new-id"})
    )
    out = forms_api.duplicate_form(client, base_path, FORM_ID)
    assert out == {"id": "new-id"}
    assert json.loads(route.calls[0].request.content) == {}


# ---------------------------------------------------------------------------
# Fields sub-resource
# ---------------------------------------------------------------------------


@respx.mock
def test_list_form_fields_orders_by_order_and_unwraps_plain_list(client):
    route = respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(200, json=[{"id": FIELD_ID}])
    )
    out = forms_api.list_form_fields(client, "/api/forms", FORM_ID)
    assert out == [{"id": FIELD_ID}]
    assert route.calls[0].request.url.params["ordering"] == "order"


@respx.mock
def test_list_form_fields_unwraps_paginated_response(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(200, json={"results": [{"id": FIELD_ID}], "next": None})
    )
    out = forms_api.list_form_fields(client, "/api/forms", FORM_ID)
    assert out == [{"id": FIELD_ID}]


@respx.mock
def test_list_form_fields_returns_empty_list_for_unrecognized_shape(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(200, json=None)
    )
    assert forms_api.list_form_fields(client, "/api/forms", FORM_ID) == []


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_create_form_field(client, base_path):
    route = respx.post(f"{FAKE_BASE_URL}{base_path}/{FORM_ID}/fields").mock(
        return_value=Response(200, json={"id": FIELD_ID})
    )
    payload = {"display_name": "Summary", "field_type": "text"}
    out = forms_api.create_form_field(client, base_path, FORM_ID, payload)
    assert out == {"id": FIELD_ID}
    assert json.loads(route.calls[0].request.content) == payload


@respx.mock
def test_update_form_field(client):
    route = respx.patch(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields/{FIELD_ID}").mock(
        return_value=Response(200, json={"id": FIELD_ID, "is_required": True})
    )
    out = forms_api.update_form_field(
        client, "/api/forms", FORM_ID, FIELD_ID, {"is_required": True}
    )
    assert out["is_required"] is True
    assert route.call_count == 1


@respx.mock
def test_delete_form_field(client):
    respx.delete(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields/{FIELD_ID}").mock(
        return_value=Response(204)
    )
    assert forms_api.delete_form_field(client, "/api/forms", FORM_ID, FIELD_ID) == {}


@respx.mock
def test_add_form_field_option(client):
    route = respx.post(
        f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields/{FIELD_ID}/options"
    ).mock(return_value=Response(200, json={"id": OPTION_ID}))
    out = forms_api.add_form_field_option(
        client, "/api/forms", FORM_ID, FIELD_ID, {"name": "A", "code": "A"}
    )
    assert out == {"id": OPTION_ID}
    assert json.loads(route.calls[0].request.content) == {"name": "A", "code": "A"}


@respx.mock
def test_delete_form_field_option(client):
    respx.delete(
        f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields/{FIELD_ID}/options/{OPTION_ID}"
    ).mock(return_value=Response(204))
    out = forms_api.delete_form_field_option(
        client, "/api/forms", FORM_ID, FIELD_ID, OPTION_ID
    )
    assert out == {}


@respx.mock
def test_replace_form_field_option_posts_replacement_body(client):
    route = respx.post(
        f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields/{FIELD_ID}/options/"
        f"{OPTION_ID}/replace"
    ).mock(return_value=Response(200, json={}))
    forms_api.replace_form_field_option(
        client, "/api/forms", FORM_ID, FIELD_ID, OPTION_ID, {"id": REPLACEMENT_ID}
    )
    assert json.loads(route.calls[0].request.content) == {"id": REPLACEMENT_ID}

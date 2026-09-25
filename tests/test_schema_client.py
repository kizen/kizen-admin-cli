"""SchemaClient: endpoint wiring, name/UUID resolution, caching."""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder import filtering
from kizen_builder.api.client import KizenClient
from kizen_builder.api.schema import SchemaClient
from kizen_builder.filtering import filter_context
from tests.conftest import FAKE_BASE_URL

OBJ_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"
CONTACTS_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"

OBJECT_LIST = {
    "results": [
        {"id": OBJ_ID, "name": "policies_policy", "object_name": "Policies"},
        {"id": CONTACTS_ID, "name": "client_client", "object_name": "Contacts"},
    ],
    "next": None,
}

FIELDS = [
    {
        "id": "field-1",
        "name": "ftext",
        "field_type": "text",
        "is_default": False,
        "options": [],
    }
]


@pytest.fixture
def schema(env_config):
    with KizenClient(env_config) as client:
        yield SchemaClient(client)


@respx.mock
def test_custom_object_resolves_api_name(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    obj = schema.custom_object("policies_policy")
    assert obj["id"] == OBJ_ID


@respx.mock
def test_custom_object_unknown_name_lists_available(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    with pytest.raises(LookupError, match="client_client"):
        schema.custom_object("nope")


@respx.mock
def test_custom_object_uuid_fetches_directly(schema):
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}").mock(
        return_value=httpx.Response(200, json={"id": OBJ_ID, "name": "policies_policy"})
    )
    obj = schema.custom_object(OBJ_ID)
    assert obj["name"] == "policies_policy"
    assert route.call_count == 1


@respx.mock
def test_get_field_uses_settings_search_and_caches(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    fields_route = respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields/settings-search"
    ).mock(return_value=httpx.Response(200, json=FIELDS))

    by_name = schema.get_field("policies_policy", "ftext")
    by_id = schema.get_field("policies_policy", "field-1")
    missing = schema.get_field("policies_policy", "nope")

    assert by_name["id"] == "field-1"
    assert by_id["name"] == "ftext"
    assert missing is None
    # both hits served from one fetch; the miss re-fetched once before giving up
    assert fields_route.call_count == 2


NEW_ID = "0d6f3c55-8a51-4a57-9d0c-3f2e1b7a9c10"
OBJECT_LIST_WITH_NEW = {
    "results": [
        *OBJECT_LIST["results"],
        {"id": NEW_ID, "name": "service_tickets", "object_name": "Service Tickets"},
    ],
    "next": None,
}


@respx.mock
def test_custom_object_created_after_cache_filled_resolves(schema):
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        side_effect=[
            httpx.Response(200, json=OBJECT_LIST),
            httpx.Response(200, json=OBJECT_LIST_WITH_NEW),
        ]
    )
    schema.custom_object("policies_policy")  # warms the cache
    assert route.call_count == 1

    assert schema.custom_object("service_tickets")["id"] == NEW_ID
    assert route.call_count == 2
    assert schema.custom_object("service_tickets")["id"] == NEW_ID
    assert route.call_count == 2  # the refreshed list is cached


@respx.mock
def test_custom_object_unknown_name_refetches_only_a_warm_cache(schema):
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        side_effect=[
            httpx.Response(200, json=OBJECT_LIST),
            httpx.Response(200, json=OBJECT_LIST_WITH_NEW),
        ]
    )
    with pytest.raises(LookupError, match="'nope' not found"):
        schema.custom_object("nope")
    assert route.call_count == 1  # the list was fetched by this call

    with pytest.raises(LookupError, match="'nope' not found.*service_tickets"):
        schema.custom_object("nope")
    assert route.call_count == 2


@respx.mock
def test_custom_object_repeated_hit_makes_one_request(schema):
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    schema.custom_object("policies_policy")
    schema.custom_object("client_client")
    schema.custom_object("policies_policy")
    assert route.call_count == 1


NEW_FIELD = {
    "id": "field-2",
    "name": "fnew",
    "field_type": "checkbox",
    "is_default": False,
    "options": [],
}


@respx.mock
def test_get_field_created_after_cache_filled_resolves(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    fields_route = respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields/settings-search"
    ).mock(
        side_effect=[
            httpx.Response(200, json=FIELDS),
            httpx.Response(200, json=[*FIELDS, NEW_FIELD]),
        ]
    )
    schema.get_field("policies_policy", "ftext")  # warms the cache
    assert fields_route.call_count == 1

    assert schema.get_field("policies_policy", "fnew")["id"] == "field-2"
    assert fields_route.call_count == 2
    assert schema.get_field("policies_policy", "fnew")["id"] == "field-2"
    assert fields_route.call_count == 2


@respx.mock
def test_get_field_unknown_name_refetches_only_a_warm_cache(schema):
    fields_route = respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields/settings-search"
    ).mock(return_value=httpx.Response(200, json=FIELDS))
    assert schema.get_field(OBJ_ID, "nope") is None
    assert fields_route.call_count == 1

    assert schema.get_field(OBJ_ID, "nope") is None
    assert fields_route.call_count == 2


@respx.mock
def test_get_field_repeated_hit_makes_one_request(schema):
    fields_route = respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields/settings-search"
    ).mock(return_value=httpx.Response(200, json=FIELDS))
    schema.get_field(OBJ_ID, "ftext")
    schema.get_field(OBJ_ID, "field-1")
    schema.get_field(OBJ_ID, "ftext")
    assert fields_route.call_count == 1


@respx.mock
def test_filter_context_unknown_object_raises_unchained_lookup_error(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    with (
        pytest.raises(LookupError, match="'service_tickets' not found") as exc_info,
        filter_context("service_tickets", client=schema),
    ):
        pass
    assert exc_info.value.__context__ is None


@respx.mock
def test_filter_context_restores_state_when_body_raises(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    with (
        pytest.raises(RuntimeError),
        filter_context("policies_policy", client=schema),
    ):
        assert filtering.get_cx_obj_id() == OBJ_ID
        raise RuntimeError("boom")
    assert filtering._local_filter_cx.client is None
    assert filtering.get_cx_obj_id() is None


@respx.mock
def test_field_tags_path_differs_for_contacts(schema):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}").mock(
        return_value=httpx.Response(200, json={"id": OBJ_ID, "name": "policies_policy"})
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{CONTACTS_ID}").mock(
        return_value=httpx.Response(
            200, json={"id": CONTACTS_ID, "name": "client_client"}
        )
    )
    pipeline_route = respx.get(
        f"{FAKE_BASE_URL}/api/pipelines/{OBJ_ID}/fields/tag-field/tags"
    ).mock(return_value=httpx.Response(200, json={"results": [], "next": None}))
    client_route = respx.get(f"{FAKE_BASE_URL}/api/client/fields/tag-field/tags").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )

    schema.get_field_tags(OBJ_ID, "tag-field")
    assert pipeline_route.call_count == 1

    schema.get_field_tags(CONTACTS_ID, "tag-field")
    assert client_route.call_count == 1


@respx.mock
def test_all_pages_follows_next_links(schema):
    page2 = f"{FAKE_BASE_URL}/api/subscription-list?page=2"
    respx.get(f"{FAKE_BASE_URL}/api/subscription-list").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"id": "a"}], "next": page2}),
            httpx.Response(200, json={"results": [{"id": "b"}], "next": None}),
        ]
    )
    lists = schema.get_subscription_lists()
    assert [x["id"] for x in lists] == ["a", "b"]

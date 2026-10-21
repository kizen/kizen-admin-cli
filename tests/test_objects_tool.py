"""get_object(): custom-object lookup and the client_client (contacts)
UUID-resolution fallback.

Regression coverage for a bug where identifiers not in the /api/custom-objects
list (like `client_client`) fell back to using the literal api_name string as
the object's `id` — wrong anywhere that id is used as a relation target, since
the API expects a real UUID there.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError
from kizen_builder.tools.objects import get_object, list_objects
from tests.conftest import FAKE_BASE_URL

OBJ_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"
CONTACTS_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"

OBJECT_LIST = {
    "results": [
        {
            "id": OBJ_ID,
            "name": "policies_policy",
            "object_name": "Policies",
            "is_custom": True,
        },
    ],
    "next": None,
}

OBJECT_LIST_WITH_CONTACTS = {
    "results": [
        {
            "id": OBJ_ID,
            "name": "policies_policy",
            "object_name": "Policies",
            "is_custom": True,
        },
        {
            "id": CONTACTS_ID,
            "name": "client_client",
            "object_name": "Contacts",
            "is_custom": False,
        },
    ],
    "next": None,
}


@respx.mock
def test_list_objects_includes_builtin_objects_like_contacts():
    """`list_objects()` must ask the server for built-ins too (custom_only=false)
    and must not filter them back out client-side — Contacts should come back
    alongside custom objects in one call, not a second round trip."""
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    objs = list_objects()

    assert route.calls.last.request.url.params["custom_only"] == "false"
    assert len(route.calls) == 1
    by_api_name = {o["api_name"]: o for o in objs}
    assert set(by_api_name) == {"policies_policy", "client_client"}
    contacts = by_api_name["client_client"]
    assert contacts["display_name"] == "Contacts"
    assert contacts["id"] == CONTACTS_ID
    policy = by_api_name["policies_policy"]
    assert policy["id"] == OBJ_ID
    assert policy["display_name"] == "Policies"


@respx.mock
def test_get_object_resolves_custom_object_from_list():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )

    obj = get_object("policies_policy")
    assert obj["id"] == OBJ_ID
    assert obj["is_custom"] is True


@respx.mock
def test_get_object_resolves_real_uuid_for_client_client():
    """`client_client` isn't in the /api/custom-objects list, but GET
    /api/custom-objects/client_client returns the real object with its UUID —
    that UUID (not the literal string) must end up as `id`."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/client_client").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": CONTACTS_ID,
                "name": "client_client",
                "object_name": "Contacts",
                "is_custom": False,
            },
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{CONTACTS_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{CONTACTS_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )

    obj = get_object("client_client")

    assert obj["id"] == CONTACTS_ID
    assert obj["id"] != "client_client"
    assert obj["api_name"] == "client_client"
    assert obj["display_name"] == "Contacts"
    assert obj["is_custom"] is False


@respx.mock
def test_get_object_resolves_relationship_target_from_relation_block():
    """A relationship field's target api_name + cardinality come straight from
    the expanded relation block when present (no extra lookup needed)."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "f1",
                    "name": "claims",
                    "display_name": "Claims",
                    "field_type": "relationship",
                    "relation": {
                        "related_object": "some-uuid",
                        "related_object_object_name": "claims_claim",
                        "cardinality": "one_to_many",
                    },
                }
            ],
        )
    )

    obj = get_object("policies_policy")
    (field,) = obj["fields"]
    assert field["relation_target"] == "claims_claim"
    assert field["relation_cardinality"] == "one_to_many"


@respx.mock
def test_get_object_resolves_relationship_target_via_uuid_fallback():
    """When the relation block lacks the api_name, the target UUID is resolved
    against the object list."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "f1",
                    "name": "self_ref",
                    "field_type": "relationship",
                    "relation": {
                        "related_object": OBJ_ID,
                        "cardinality": "many_to_one",
                    },
                }
            ],
        )
    )

    obj = get_object("policies_policy")
    (field,) = obj["fields"]
    assert field["relation_target"] == "policies_policy"
    assert field["relation_cardinality"] == "many_to_one"


@respx.mock
def test_get_object_non_relationship_field_has_no_target():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "f1", "name": "total", "field_type": "money"}],
        )
    )

    obj = get_object("policies_policy")
    (field,) = obj["fields"]
    assert field["relation_target"] is None
    assert field["relation_cardinality"] is None


@respx.mock
def test_get_object_options_round_trip_unchanged():
    """A choice field's options survive `get_object()` as `{id, name, code}`
    per option — the shape the CLI layer's table/CSV rendering builds on."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "f1",
                    "name": "status",
                    "field_type": "dropdown",
                    "options": [
                        {"id": "opt-1", "name": "Open", "code": "OPEN"},
                        {"id": "opt-2", "name": "Closed", "code": "CLOSED"},
                    ],
                }
            ],
        )
    )

    obj = get_object("policies_policy")
    (field,) = obj["fields"]
    assert field["options"] == [
        {"id": "opt-1", "name": "Open", "code": "OPEN"},
        {"id": "opt-2", "name": "Closed", "code": "CLOSED"},
    ]


@respx.mock
def test_get_object_unknown_identifier_falls_back_to_literal_string():
    """A truly unknown identifier (404 on direct fetch too) still falls back
    to the literal string, matching prior behavior for edge cases."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/nope").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/nope/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/nope/fields").mock(
        return_value=httpx.Response(200, json=[])
    )

    obj = get_object("nope")
    assert obj["id"] == "nope"


@respx.mock
def test_get_object_field_carries_description():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "f1", "name": "total", "field_type": "money", "description": "d"}
            ],
        )
    )

    (field,) = get_object("policies_policy")["fields"]
    assert field["description"] == "d"


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
def test_get_object_direct_fetch_error_other_than_404_propagates(status):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/nope").mock(
        return_value=httpx.Response(status, json={"detail": "boom"})
    )

    with pytest.raises(KizenAPIError) as exc:
        get_object("nope")
    assert exc.value.status_code == status

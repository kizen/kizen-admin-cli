"""`cli/activities.py`'s object-resolution helpers must resolve Contacts
(`client_client`), not just custom objects.

Both helpers depend on `tools/objects.py::list_objects()` for their candidate
list. Before that function included built-ins, `client_client` resolved to
nothing and these raised `object 'client_client' not found` — a functional
break for `kizen activities list --object client_client` and for associating
an activity type with Contacts via `--object` on `activities update`. No test
covered this path before this change (confirmed: `test_activities_tool.py`,
`test_activity_plans.py`, and `test_cli.py` have no `client_client` coverage
for activities).
"""

from __future__ import annotations

import httpx
import pytest
import respx
import typer

from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli.activities import (
    _resolve_associated_objects,
    _resolve_linked_field,
    _resolve_object_id,
)
from tests.conftest import FAKE_BASE_URL

OBJ_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"
CONTACTS_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"

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
def test_resolve_object_id_resolves_client_client():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    assert _resolve_object_id("client_client") == CONTACTS_ID
    assert _resolve_object_id("policies_policy") == OBJ_ID


@respx.mock
def test_resolve_associated_objects_resolves_client_client_alongside_custom():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    resolved = _resolve_associated_objects(["client_client", "policies_policy"])

    assert resolved == [
        {"custom_object": {"id": CONTACTS_ID}},
        {"custom_object": {"id": OBJ_ID}},
    ]


@respx.mock
def test_resolve_object_id_still_raises_for_truly_unknown_object():
    """Unaffected behavior: an api_name absent from the (now custom+built-in)
    list still raises, listing what's actually available."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    with pytest.raises(typer.BadParameter, match="nope"):
        _resolve_object_id("nope")


@pytest.mark.parametrize("status", [401, 500])
def test_resolve_linked_field_does_not_report_api_errors_as_not_found(
    monkeypatch, status
):
    def get_object(_api_name):
        raise KizenAPIError(status, "boom")

    monkeypatch.setattr("kizen_builder.tools.objects.get_object", get_object)
    with pytest.raises(KizenAPIError) as info:
        _resolve_linked_field("policies_policy.total")
    assert info.value.status_code == status


def test_resolve_linked_field_reports_404_as_not_found(monkeypatch):
    def get_object(_api_name):
        raise KizenAPIError(404, "gone")

    monkeypatch.setattr("kizen_builder.tools.objects.get_object", get_object)
    with pytest.raises(typer.BadParameter, match="object 'policies_policy' not found"):
        _resolve_linked_field("policies_policy.total")

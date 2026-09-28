"""The read/resolve layer over roles and permission groups: name/UUID
resolution, the level-label mapping, and the `describe_*` views the CLI
renders.

Everything here builds its own `KizenClient`, so it goes through `respx`
against `FAKE_BASE_URL`, the same seam `tests/test_objects_tool.py` uses.
"""

from __future__ import annotations

import json

import httpx
import respx

from kizen_builder.tools.permissions import (
    describe_group,
    describe_role,
    get_meta_data,
    get_permission_group,
    get_role,
    level_label,
    list_permission_groups,
    list_roles,
    resolve_group,
    resolve_role,
)
from tests.conftest import FAKE_BASE_URL, load_fixture

ROLE_ID = "00000000-0000-4000-8000-000000000101"
GROUP_ID = "00000000-0000-4000-8000-000000000201"
GROUP_ID_2 = "00000000-0000-4000-8000-000000000202"
OBJ_ID = "00000000-0000-4000-8000-000000000301"
CONTACTS_OBJ_ID = "00000000-0000-4000-8000-000000000302"
CONTACT_CUSTOM_FIELD_ID = "00000000-0000-4000-8000-000000000401"
OBJECT_FIELD_ID = "00000000-0000-4000-8000-000000000402"

ROLE_LIST = load_fixture("permissions/role_list.json")
ROLE_DETAIL = load_fixture("permissions/role_detail.json")
GROUP_LIST = load_fixture("permissions/permission_group_list.json")
GROUP_DETAIL = load_fixture("permissions/permission_group_detail.json")
META = load_fixture("permissions/permissions_meta_data.json")

OBJECT_LIST = {
    "results": [
        {
            "id": OBJ_ID,
            "name": "policies_policy",
            "object_name": "Policies",
            "entity_name": "Policy",
            "is_custom": True,
        }
    ],
    "next": None,
}


def _mock_role_list(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/role").mock(
        return_value=httpx.Response(200, json=body or ROLE_LIST)
    )


def _mock_role_detail(role_id: str = ROLE_ID, body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/role/{role_id}").mock(
        return_value=httpx.Response(200, json=body or ROLE_DETAIL)
    )


def _mock_group_list(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/permission-group").mock(
        return_value=httpx.Response(200, json=body or GROUP_LIST)
    )


def _mock_group_detail(group_id: str = GROUP_ID, body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/permission-group/{group_id}").mock(
        return_value=httpx.Response(200, json=body or GROUP_DETAIL)
    )


def _mock_meta():
    return respx.get(f"{FAKE_BASE_URL}/api/permissions/meta-data").mock(
        return_value=httpx.Response(200, json=META)
    )


# ---------------------------------------------------------------------------
# level_label — levels 0-3 map none/view/edit/remove
# ---------------------------------------------------------------------------


def test_level_label_maps_known_integers():
    assert level_label(0) == "none"
    assert level_label(1) == "view"
    assert level_label(2) == "edit"
    assert level_label(3) == "remove"


def test_level_label_coerces_numeric_strings():
    assert level_label("2") == "edit"


def test_level_label_falls_back_to_str_for_unmapped_or_uncoercible_values():
    assert level_label(99) == "99"
    assert level_label("bogus") == "bogus"
    assert level_label(None) == "None"


# ---------------------------------------------------------------------------
# resolve_role / resolve_group
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_role_by_uuid():
    _mock_role_list()
    role = resolve_role(ROLE_ID)
    assert role["name"] == "Sales Rep"


@respx.mock
def test_resolve_role_by_exact_name():
    _mock_role_list()
    role = resolve_role("Sales Rep")
    assert role["id"] == ROLE_ID


@respx.mock
def test_resolve_role_by_case_insensitive_name():
    _mock_role_list()
    role = resolve_role("sales rep")
    assert role["id"] == ROLE_ID


@respx.mock
def test_resolve_role_unknown_name_lists_available():
    _mock_role_list()
    try:
        resolve_role("Nope")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "not found" in str(exc)
        assert "Sales Rep" in str(exc)


@respx.mock
def test_resolve_role_ambiguous_name_raises_with_candidate_count():
    _mock_role_list(
        body={
            "results": [
                {"id": "id-a", "name": "Ops"},
                {"id": "id-b", "name": "Ops"},
            ],
            "next": None,
        }
    )
    try:
        resolve_role("Ops")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "ambiguous" in str(exc)
        assert "2 matches" in str(exc)


@respx.mock
def test_resolve_group_by_uuid_name_and_unknown():
    _mock_group_list()
    assert resolve_group(GROUP_ID)["name"] == "Sample Group"
    assert resolve_group("Other Group")["id"] == GROUP_ID_2
    try:
        resolve_group("does-not-exist")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "permission group 'does-not-exist' not found" in str(exc)


# ---------------------------------------------------------------------------
# thin list/get wrappers
# ---------------------------------------------------------------------------


@respx.mock
def test_list_roles_projects_expected_fields():
    _mock_role_list()
    (role,) = list_roles()
    assert role == {
        "id": ROLE_ID,
        "name": "Sales Rep",
        "user_count": 5,
        "permission_groups": [GROUP_ID],
        "default_for_new_users": False,
    }


@respx.mock
def test_get_role_returns_raw_detail():
    _mock_role_detail()
    assert get_role(ROLE_ID) == ROLE_DETAIL


@respx.mock
def test_list_permission_groups_returns_raw_list():
    _mock_group_list()
    groups = list_permission_groups()
    assert [g["name"] for g in groups] == ["Sample Group", "Other Group"]


@respx.mock
def test_get_permission_group_returns_raw_detail():
    _mock_group_detail()
    assert get_permission_group(GROUP_ID) == GROUP_DETAIL


@respx.mock
def test_get_meta_data_returns_raw_catalog():
    _mock_meta()
    assert get_meta_data() == META


# ---------------------------------------------------------------------------
# describe_role
# ---------------------------------------------------------------------------


@respx.mock
def test_describe_role_expands_permission_groups_to_names_and_summaries():
    _mock_role_list()
    _mock_role_detail()
    _mock_group_list()
    _mock_group_detail()

    d = describe_role("Sales Rep")

    assert d["id"] == ROLE_ID
    assert d["name"] == "Sales Rep"
    assert d["permissions"] == ["manage_users"]
    assert d["default_for_new_users"] is False
    assert d["groups"] == [
        {"id": GROUP_ID, "name": "Sample Group", "summary": GROUP_DETAIL["summary"]}
    ]


@respx.mock
def test_describe_role_falls_back_to_listed_name_when_group_detail_fails():
    """The list endpoint zeroes `summary`; the detail GET has real counts.
    When the detail GET 404s (e.g. the group was deleted after the role kept
    a stale reference), describe_role must still return a row — using the
    list's name and a `None` summary — not raise."""
    _mock_role_list()
    _mock_role_detail()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/permission-group/{GROUP_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    d = describe_role("Sales Rep")

    assert d["groups"] == [{"id": GROUP_ID, "name": "Sample Group", "summary": None}]


# ---------------------------------------------------------------------------
# describe_group
# ---------------------------------------------------------------------------


@respx.mock
def test_describe_group_orders_and_labels_blocks_without_fields():
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )

    d = describe_group(GROUP_ID, include_fields=False)

    assert d["id"] == GROUP_ID
    assert d["name"] == "Sample Group"
    assert d["user_count"] == 3  # merged in from the list entry
    assert d["role_count"] == 1

    labels = [b["label"] for b in d["blocks"]]
    areas = [b["area"] for b in d["blocks"]]
    # meta["order"] puts homepages < dashboards < contacts_section <
    # custom_object_entities; the object block is labeled from the resolved
    # object list, not the raw block key.
    assert labels == ["Homepage", "Dashboards", "Contacts", "Policies"]
    assert areas == ["section", "section", "contacts", "object"]

    homepages = d["blocks"][0]
    assert homepages["enabled"] is True
    assert homepages["rows"] == [
        {
            "label": "Customize Homepages",
            "category": None,
            "level": "remove",  # bare-bool dialect: on = highest non-none
            "allowed": ["none", "remove"],
            "affordance": "range",
        }
    ]

    dashboards = d["blocks"][1]
    assert dashboards["enabled"] is True
    assert dashboards["rows"][0]["label"] == "Customize Dashboards"
    assert dashboards["rows"][0]["level"] == "edit"  # dict dialect, read back

    # no include_fields -> no per-field rows anywhere
    assert not any(
        r.get("category") in ("default_fields",) for b in d["blocks"] for r in b["rows"]
    )


@respx.mock
def test_describe_group_sorts_object_blocks_at_the_custom_object_entities_slot():
    # Live order puts the object slot last, where the missing-slot fallback
    # would also land; move it ahead of contacts so only a real lookup passes.
    reordered_meta = json.loads(json.dumps(META))
    reordered_meta["order"] = [
        "homepages_section",
        "dashboards_section",
        "custom_object_entities",
        "contacts_section",
    ]
    _mock_group_detail()
    respx.get(f"{FAKE_BASE_URL}/api/permissions/meta-data").mock(
        return_value=httpx.Response(200, json=reordered_meta)
    )
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )

    d = describe_group(GROUP_ID, include_fields=False)

    assert [b["label"] for b in d["blocks"]] == [
        "Homepage",
        "Dashboards",
        "Policies",
        "Contacts",
    ]


def _mock_client_client_object(fields=None):
    """respx routes for `obj_tools.get_object("client_client")` — the direct
    lookup plus its categories/fields calls, matching the pattern in
    `tests/test_objects_tool.py::test_get_object_resolves_real_uuid_for_client_client`."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/client_client").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": CONTACTS_OBJ_ID,
                "name": "client_client",
                "object_name": "Contacts",
                "is_custom": False,
            },
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{CONTACTS_OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    return respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/{CONTACTS_OBJ_ID}/fields"
    ).mock(return_value=httpx.Response(200, json=fields if fields is not None else []))


@respx.mock
def test_describe_group_include_fields_resolves_object_and_contact_field_names():
    """Per-field rows nest under their parent block. Object fields get real
    names via a second `get_object` call — and so do contacts custom fields,
    via a similar `get_object("client_client")` call, since they live under
    contacts_section rather than group["custom_objects"]."""
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
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
                    "id": OBJECT_FIELD_ID,
                    "name": "notes",
                    "display_name": "Notes",
                    "field_type": "text",
                }
            ],
        )
    )
    _mock_client_client_object(
        fields=[
            {
                "id": CONTACT_CUSTOM_FIELD_ID,
                "name": "referral_source",
                "display_name": "Referral Source",
                "field_type": "text",
            }
        ]
    )

    d = describe_group(GROUP_ID, include_fields=True)

    object_block = next(b for b in d["blocks"] if b["area"] == "object")
    object_field_row = next(
        r for r in object_block["rows"] if r["category"] == "Fields"
    )
    assert object_field_row["label"] == "Notes"  # resolved via get_object

    contacts_block = next(b for b in d["blocks"] if b["area"] == "contacts")
    contact_field_row = next(
        r for r in contacts_block["rows"] if r["category"] == "Fields"
    )
    assert contact_field_row["label"] == "Referral Source"  # resolved, not raw UUID

    default_field_row = next(
        r for r in contacts_block["rows"] if r["category"] == "Default Fields"
    )
    assert default_field_row["label"] == "email"


@respx.mock
def test_describe_group_skips_client_client_lookup_when_no_contact_custom_fields():
    """get_object("client_client") is a multi-request round trip. A group
    whose contacts_section has no custom_fields must not pay for it."""
    body = {**GROUP_DETAIL, "contacts_section": {**GROUP_DETAIL["contacts_section"]}}
    body["contacts_section"]["custom_fields"] = []
    _mock_group_detail(body=body)
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )
    client_client_route = respx.get(
        f"{FAKE_BASE_URL}/api/custom-objects/client_client"
    ).mock(return_value=httpx.Response(200, json={}))

    describe_group(GROUP_ID, include_fields=True)

    assert not client_client_route.called


@respx.mock
def test_describe_group_falls_back_to_raw_uuid_when_client_client_lookup_fails():
    """A failing client_client lookup must degrade to the raw UUID label, not
    raise — same narrow except (LookupError, KizenAPIError) the per-object loop
    already uses. `get_object` swallows a failing *direct* lookup itself and
    retries with the bare identifier, so the error that actually escapes it
    comes from the categories/fields calls underneath."""
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/client_client").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/client_client/categories").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    d = describe_group(GROUP_ID, include_fields=True)

    contacts_block = next(b for b in d["blocks"] if b["area"] == "contacts")
    contact_field_row = next(
        r for r in contacts_block["rows"] if r["category"] == "Fields"
    )
    assert contact_field_row["label"] == CONTACT_CUSTOM_FIELD_ID  # degraded, not raised
    assert any("contacts field names" in w for w in d["warnings"])


@respx.mock
def test_describe_group_reports_object_field_resolution_failure():
    """A per-object field lookup that fails names the object it was for, so an
    unresolved row is distinguishable from a field with no display name."""
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/categories").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    _mock_client_client_object()

    d = describe_group(GROUP_ID, include_fields=True)

    # The block label still resolved (that came off the object list); only the
    # per-field lookup failed.
    object_block = next(b for b in d["blocks"] if b["area"] == "object")
    assert object_block["label"] == "Policies"
    assert any("policies_policy" in w for w in d["warnings"])


@respx.mock
def test_describe_group_reports_name_resolution_failures_without_raising():
    """Name resolution is best-effort — a 500 from /api/custom-objects still
    renders the view with unresolved labels rather than raising. The failure is
    reported in `warnings` rather than swallowed, so a broken lookup is
    distinguishable from a name that legitimately has no label."""
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    d = describe_group(GROUP_ID, include_fields=False)

    object_block = next(b for b in d["blocks"] if b["area"] == "object")
    assert object_block["label"] == f"object:{OBJ_ID}"  # fell back, didn't raise
    assert len(d["warnings"]) == 1
    assert "could not resolve object and field names" in d["warnings"][0]


@respx.mock
def test_describe_group_reports_no_warnings_on_a_clean_resolve():
    _mock_group_detail()
    _mock_meta()
    _mock_group_list()
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST)
    )

    assert describe_group(GROUP_ID, include_fields=False)["warnings"] == []

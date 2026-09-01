"""`tools/team.py` — team-member search and role resolution.

Same seam as `tests/test_permissions_tool.py`: builds its own `KizenClient`,
mocked via `respx` against `FAKE_BASE_URL`.
"""

from __future__ import annotations

import httpx
import respx

from kizen_builder.tools.team import get_team_member, search_team
from tests.conftest import FAKE_BASE_URL, load_fixture

MEMBER_ID = "00000000-0000-4000-8000-000000000901"
ROLE_ID = "00000000-0000-4000-8000-000000000101"  # "Sales Rep" in role_list.json

TEAM_GET = load_fixture("team/get.json")
ROLE_LIST = load_fixture("permissions/role_list.json")


def _mock_team_get(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/team/{MEMBER_ID}").mock(
        return_value=httpx.Response(200, json=body or TEAM_GET)
    )


def _mock_role_list(body=None):
    return respx.get(f"{FAKE_BASE_URL}/api/role").mock(
        return_value=httpx.Response(200, json=body or ROLE_LIST)
    )


def _mock_typeahead(results):
    return respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(200, json={"results": results})
    )


# ---------------------------------------------------------------------------
# search_team — unchanged existing behavior
# ---------------------------------------------------------------------------


@respx.mock
def test_search_team_projects_expected_fields():
    _mock_typeahead(
        [
            {
                "id": MEMBER_ID,
                "full_name": "Alex Example",
                "email": "alex@example.com",
                "title": None,
            }
        ]
    )
    (member,) = search_team("Alex")
    assert member == {
        "id": MEMBER_ID,
        "full_name": "Alex Example",
        "email": "alex@example.com",
        "title": None,
    }


# ---------------------------------------------------------------------------
# get_team_member — id, name, and email resolution
# ---------------------------------------------------------------------------


@respx.mock
def test_get_team_member_by_uuid_resolves_role_names():
    _mock_team_get()
    _mock_role_list()

    d = get_team_member(MEMBER_ID)

    assert d["id"] == MEMBER_ID
    assert d["full_name"] == "Alex Example"
    assert d["email"] == "alex@example.com"
    assert d["roles"] == [{"id": ROLE_ID, "name": "Sales Rep"}]


@respx.mock
def test_get_team_member_by_uuid_skips_typeahead():
    """A UUID ref goes straight to the retrieve endpoint — no search call."""
    _mock_team_get()
    _mock_role_list()
    typeahead = _mock_typeahead([])

    get_team_member(MEMBER_ID)

    assert not typeahead.called


@respx.mock
def test_get_team_member_unknown_role_id_degrades_to_placeholder():
    """A role id the role list no longer has (e.g. deleted after assignment)
    degrades to a labeled placeholder rather than raising."""
    _mock_team_get()
    _mock_role_list(body={"results": [], "next": None})

    d = get_team_member(MEMBER_ID)

    assert d["roles"] == [{"id": ROLE_ID, "name": "(unknown)"}]


@respx.mock
def test_get_team_member_unknown_uuid_raises_lookup_error():
    """A nonexistent UUID 404s server-side; that should surface as the same
    LookupError a bad name/email gets, not a raw HTTP error."""
    respx.get(f"{FAKE_BASE_URL}/api/team/{MEMBER_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    try:
        get_team_member(MEMBER_ID)
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "not found" in str(exc)


@respx.mock
def test_get_team_member_by_exact_email_resolves_via_typeahead():
    _mock_typeahead(
        [{"id": MEMBER_ID, "full_name": "Alex Example", "email": "alex@example.com"}]
    )
    _mock_team_get()
    _mock_role_list()

    d = get_team_member("alex@example.com")

    assert d["id"] == MEMBER_ID
    assert d["roles"] == [{"id": ROLE_ID, "name": "Sales Rep"}]


@respx.mock
def test_get_team_member_email_match_is_case_insensitive():
    _mock_typeahead(
        [{"id": MEMBER_ID, "full_name": "Alex Example", "email": "alex@example.com"}]
    )
    _mock_team_get()
    _mock_role_list()

    d = get_team_member("ALEX@EXAMPLE.COM")

    assert d["id"] == MEMBER_ID


@respx.mock
def test_get_team_member_by_exact_name_resolves_via_typeahead():
    _mock_typeahead(
        [
            {"id": MEMBER_ID, "full_name": "Alex Example", "email": "alex@example.com"},
            {
                "id": "other-id",
                "full_name": "Other Person",
                "email": "other@example.com",
            },
        ]
    )
    _mock_team_get()
    _mock_role_list()

    d = get_team_member("Alex Example")

    assert d["id"] == MEMBER_ID


@respx.mock
def test_get_team_member_ambiguous_search_raises_with_candidates():
    _mock_typeahead(
        [
            {"id": "id-a", "full_name": "Alex A", "email": "a@example.com"},
            {"id": "id-b", "full_name": "Alex B", "email": "b@example.com"},
        ]
    )
    try:
        get_team_member("alex")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "ambiguous" in str(exc)
        assert "2 matches" in str(exc)


@respx.mock
def test_get_team_member_no_match_raises():
    _mock_typeahead([])
    try:
        get_team_member("nobody")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "not found" in str(exc)

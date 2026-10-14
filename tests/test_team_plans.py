"""`plan_update_team_member_roles` — the add/remove lists it computes against
live state, the skip case and the last-Admin guard.

Same seam as `tests/test_permission_plans.py`: the planner builds its own
`KizenClient`, mocked via `respx` against `FAKE_BASE_URL`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.tools.planners.team import plan_update_team_member_roles
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL, load_fixture

MEMBER_ID = "00000000-0000-4000-8000-000000000901"
OTHER_ID = "00000000-0000-4000-8000-000000000902"
SALES = "00000000-0000-4000-8000-000000000101"  # held by the fixture member
ADMIN = "00000000-0000-4000-8000-000000000102"
SUPPORT = "00000000-0000-4000-8000-000000000103"

TEAM_GET = load_fixture("team/get.json")


ROLES = {
    "results": [
        {"id": SALES, "name": "Sales Rep", "user_count": 5},
        # user_count may include deactivated members; the guard ignores it.
        {"id": ADMIN, "name": "Admin", "user_count": 2},
        {"id": SUPPORT, "name": "Support", "user_count": 0},
    ],
    "next": None,
}


def _list_member(member_id, role_ids, **flags):
    return {
        "id": member_id,
        "roles": [{"id": r, "name": "?"} for r in role_ids],
        "verified": True,
        "deleted": False,
        **flags,
    }


def _mock(member_roles=(SALES,), others=()):
    respx.get(f"{FAKE_BASE_URL}/api/team/{MEMBER_ID}").mock(
        return_value=httpx.Response(200, json={**TEAM_GET, "roles": list(member_roles)})
    )
    respx.get(f"{FAKE_BASE_URL}/api/role").mock(
        return_value=httpx.Response(200, json=ROLES)
    )
    team = [_list_member(MEMBER_ID, member_roles), *others]
    respx.get(f"{FAKE_BASE_URL}/api/team").mock(
        return_value=httpx.Response(200, json={"results": team, "next": None})
    )
    # Planners never write: a call here fails the test.
    return respx.post(f"{FAKE_BASE_URL}/api/team/change-roles").mock(
        return_value=httpx.Response(500)
    )


def _body(add, remove):
    return {
        "team_member_ids": [MEMBER_ID],
        "role_ids_to_add": add,
        "role_ids_to_remove": remove,
    }


@respx.mock
def test_add_names_only_the_new_role():
    write = _mock()

    (op,) = plan_update_team_member_roles(MEMBER_ID, add=[SUPPORT]).operations

    assert op.action == "update"
    assert op.kind == "team_member"
    assert op.existing_uuid == MEMBER_ID
    assert op.payload == _body([SUPPORT], [])
    assert op.preview["member"] == "Alex Example"
    assert op.preview["roles"] == "['Sales Rep'] → ['Sales Rep', 'Support']"
    assert not write.called


@respx.mock
def test_remove_names_only_the_removed_role():
    _mock(member_roles=(SALES, SUPPORT))

    (op,) = plan_update_team_member_roles(MEMBER_ID, remove=[SUPPORT]).operations

    assert op.payload == _body([], [SUPPORT])
    assert op.preview["roles"] == "['Sales Rep', 'Support'] → ['Sales Rep']"


@respx.mock
def test_replace_computes_add_and_remove_from_live_roles():
    _mock(member_roles=(SALES, SUPPORT))

    (op,) = plan_update_team_member_roles(
        MEMBER_ID, set_roles=[ADMIN, SUPPORT]
    ).operations

    assert op.payload == _body([ADMIN], [SALES])


@respx.mock
def test_replace_with_the_current_set_is_a_skip():
    _mock()

    plan = plan_update_team_member_roles(MEMBER_ID, set_roles=[SALES])

    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}
    assert op.preview["roles"] == "no changes"
    assert plan.summary == "No role changes for 'Alex Example'"


@respx.mock
def test_add_of_a_held_role_and_remove_of_an_unheld_role_are_dropped():
    _mock()

    (op,) = plan_update_team_member_roles(
        MEMBER_ID, add=[SALES, ADMIN], remove=[SUPPORT]
    ).operations

    assert op.payload == _body([ADMIN], [])


@respx.mock
def test_same_role_added_and_removed_is_a_plan_error():
    _mock()

    with pytest.raises(PlanError, match=r"\['Support'\] are both added and removed"):
        plan_update_team_member_roles(MEMBER_ID, add=[SUPPORT], remove=[SUPPORT])


@pytest.mark.parametrize(
    "others",
    [
        (),
        (_list_member(OTHER_ID, [ADMIN], deleted=True),),
        (_list_member(OTHER_ID, [ADMIN], verified=False),),
        (_list_member(OTHER_ID, [SALES]),),
    ],
    ids=[
        "sole-member",
        "other-admin-deleted",
        "other-admin-unverified",
        "other-not-admin",
    ],
)
@respx.mock
def test_removing_the_last_active_admin_is_refused(others):
    _mock(member_roles=(SALES, ADMIN), others=others)

    with pytest.raises(PlanError, match="would leave the business with no Admin"):
        plan_update_team_member_roles(MEMBER_ID, remove=[ADMIN])


@respx.mock
def test_replace_that_drops_the_last_admin_is_refused():
    _mock(member_roles=(ADMIN,))

    with pytest.raises(PlanError, match="removing Admin from Alex Example"):
        plan_update_team_member_roles(MEMBER_ID, set_roles=[SALES])


@respx.mock
def test_removing_admin_is_allowed_when_another_active_member_holds_it():
    _mock(member_roles=(SALES, ADMIN), others=(_list_member(OTHER_ID, [ADMIN]),))

    (op,) = plan_update_team_member_roles(MEMBER_ID, remove=[ADMIN]).operations

    assert op.payload == _body([], [ADMIN])


@respx.mock
def test_unknown_member_id_is_a_plan_error():
    respx.get(f"{FAKE_BASE_URL}/api/team/{MEMBER_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )

    with pytest.raises(PlanError, match=f"team member with id '{MEMBER_ID}' not found"):
        plan_update_team_member_roles(MEMBER_ID, add=[SUPPORT])


@respx.mock
def test_unknown_member_name_is_a_plan_error():
    respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    with pytest.raises(PlanError, match="team member 'nobody' not found"):
        plan_update_team_member_roles("nobody", add=[SUPPORT])

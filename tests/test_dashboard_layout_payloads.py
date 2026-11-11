"""Golden tests for dashboard/layout plan builders + apply dispatch.

These pin the wire-format rules recorded in `kizen docs show reference`:
  * dashboard create emits one dashboard op + one dashlet op per dashlet, the
    dashlets carrying deferred_parent_object_key so apply injects the new id;
  * sharing_settings are normalized to bare-UUID permission lists (a template
    copied from `dashboards get --raw` carries {id, display_name} objects);
  * only what the spec sets is sent: no injected style_settings, hidden or
    published on create, and an update never patches a field the spec omits;
  * layout update injects an id at every config level and preserves tabs.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.tools import dashboards as dash_tools
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners import dashboards as dash_planners
from kizen_builder.tools.planners import layouts as layout_planners
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from tests.conftest import FAKE_BASE_URL

ADMIN_ID = "937c92bb-d5dd-4990-bcfc-437757e28070"
DASH_ID = "5abc2e37-33d6-4f9c-9c70-d5fd74a2bea5"
DL_ID = "45580bf3-1111-4111-8111-111111111111"
OBJ_ID = "ceed733b-1111-4111-8111-111111111111"
LAYOUT_ID = "42bf31d7-1111-4111-8111-111111111111"


def _team_sharing() -> dict:
    """A team-visible sharing block, normalized to bare UUIDs."""
    return {
        "private": False,
        "all_team_members": 1,
        "roles": {"view": [], "edit": [], "admin": [ADMIN_ID]},
        "team_members": {"view": [], "edit": [], "admin": []},
    }


@pytest.fixture
def no_dashboards(monkeypatch):
    monkeypatch.setattr(dash_planners, "list_dashboards", lambda: [])
    # The real default sharing, minus the live Admin-role lookup.
    monkeypatch.setattr(dash_tools, "admin_role_id", lambda: ADMIN_ID)


# ---------------------------------------------------------------------------
# dashboard create
# ---------------------------------------------------------------------------


def test_create_dashboard_emits_dashboard_then_deferred_dashlets(no_dashboards):
    spec = {
        "api_name": "cli_test_dashboard",
        "name": "CLI Test",
        "type": "generic_dashboard",
        "dashlets": [
            {
                "name": "Emails Sent",
                "custom_object": None,
                "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
                "config": {"report_type": "email_sent", "chart_type": "numeric"},
            }
        ],
    }
    plan = dash_planners.plan_create_dashboard(spec)

    board, dashlet = plan.operations
    assert board.kind == "dashboard" and board.action == "create"
    assert board.key == "cli_test_dashboard"
    assert board.payload["type"] == "generic_dashboard"
    assert board.payload["sharing_settings"]["roles"]["admin"] == [ADMIN_ID]

    assert dashlet.kind == "dashlet" and dashlet.action == "create"
    assert dashlet.deferred_parent_object_key == "cli_test_dashboard"
    assert dashlet.key.startswith("cli_test_dashboard.")
    assert dashlet.payload["config"]["report_type"] == "email_sent"


def test_create_dashboard_sends_no_style_settings_when_spec_has_none(no_dashboards):
    # KZN-19683: the dashboard's style_settings paint every dashlet header, so
    # the CLI must not supply one of its own.
    plan = dash_planners.plan_create_dashboard({"api_name": "d1", "name": "D1"})
    (board,) = plan.operations
    assert "style_settings" not in board.payload


def test_create_dashboard_sends_explicit_empty_style_settings(no_dashboards):
    plan = dash_planners.plan_create_dashboard(
        {"api_name": "d1", "name": "D1", "style_settings": {}}
    )
    (board,) = plan.operations
    assert board.payload["style_settings"] == {}


def test_create_dashboard_sends_only_what_the_spec_sets(no_dashboards):
    """hidden, published and custom_object are left to the server; type falls
    back to generic_dashboard because the API requires one."""
    plan = dash_planners.plan_create_dashboard({"api_name": "d1", "name": "D1"})
    (board,) = plan.operations
    assert board.payload == {
        "name": "D1",
        "api_name": "d1",
        "type": "generic_dashboard",
        "sharing_settings": board.payload["sharing_settings"],
    }
    assert board.preview["type"] == "generic_dashboard"


def test_create_dashboard_sends_hidden_and_published_when_set(no_dashboards):
    plan = dash_planners.plan_create_dashboard(
        {
            "api_name": "d1",
            "name": "D1",
            "type": "homepage",
            "hidden": False,
            "published": True,
        }
    )
    (board,) = plan.operations
    assert board.payload["type"] == "homepage"
    assert board.payload["hidden"] is False
    assert board.payload["published"] is True


def test_create_dashboard_default_sharing_is_private_with_admin(no_dashboards):
    plan = dash_planners.plan_create_dashboard({"api_name": "d1", "name": "D1"})
    (board,) = plan.operations
    assert board.payload["sharing_settings"] == {
        "private": True,
        "all_team_members": 0,
        "roles": {"view": [], "edit": [], "admin": [ADMIN_ID]},
        "team_members": {"view": [], "edit": [], "admin": []},
    }


def test_create_dashlet_without_name_or_layout(no_dashboards):
    """The API requires a dashlet name, so an unnamed new dashlet is sent as
    [default]; layout and custom_object are left out, not sent empty."""
    plan = dash_planners.plan_create_dashboard(
        {
            "api_name": "d1",
            "name": "D1",
            "dashlets": [{"config": {"report_type": "email_sent"}}],
        }
    )
    _, dashlet = plan.operations
    assert dashlet.payload == {
        "name": "[default]",
        "config": {"report_type": "email_sent"},
    }


def test_create_dashboard_normalizes_template_sharing(no_dashboards):
    # sharing copied from `dashboards get --raw` carries {id, display_name}
    spec = {
        "api_name": "d1",
        "name": "D1",
        "sharing_settings": {
            "private": False,
            "all_team_members": 1,
            "roles": {
                "view": [],
                "edit": [],
                "admin": [{"id": ADMIN_ID, "display_name": "Admin"}],
            },
            "team_members": {"view": [], "edit": [], "admin": []},
        },
    }
    plan = dash_planners.plan_create_dashboard(spec)
    (board,) = plan.operations
    assert board.payload["sharing_settings"]["roles"]["admin"] == [ADMIN_ID]


def test_create_dashboard_collision_errors(monkeypatch):
    monkeypatch.setattr(
        dash_planners,
        "list_dashboards",
        lambda: [{"id": DASH_ID, "api_name": "dup"}],
    )
    with pytest.raises(PlanError, match="already exists"):
        dash_planners.plan_create_dashboard({"api_name": "dup", "name": "Dup"})


def test_chart_group_requires_custom_object(no_dashboards):
    with pytest.raises(PlanError, match="chart_group"):
        dash_planners.plan_create_dashboard(
            {"api_name": "cg", "name": "CG", "type": "chart_group"}
        )


# ---------------------------------------------------------------------------
# dashboard update
# ---------------------------------------------------------------------------


def _fake_detail() -> dict:
    return {
        "id": DASH_ID,
        "raw": {
            "id": DASH_ID,
            "name": "Old Name",
            "type": "generic_dashboard",
            "custom_object": None,
            "hidden": False,
            "published": True,
            "dashlets": [{"id": DL_ID}],
        },
    }


def test_update_dashboard_diffs_metadata_and_dashlets(monkeypatch):
    monkeypatch.setattr(dash_planners, "get_dashboard_detail", lambda d: _fake_detail())
    spec = {
        "api_name": "cli_test_dashboard",
        "name": "New Name",
        "type": "generic_dashboard",
        "dashlets": [
            {"id": DL_ID, "name": "existing", "layout": {}, "config": {}},
            {"name": "brand new", "layout": {}, "config": {}},
        ],
    }
    plan = dash_planners.plan_update_dashboard("cli_test_dashboard", spec)
    board, upd, new = plan.operations

    assert board.action == "update" and board.payload == {"name": "New Name"}
    assert board.existing_uuid == DASH_ID

    assert upd.kind == "dashlet" and upd.action == "update"
    assert upd.existing_uuid == DL_ID and upd.parent_object_uuid == DASH_ID

    assert new.kind == "dashlet" and new.action == "create"
    assert new.existing_uuid is None and new.parent_object_uuid == DASH_ID


def test_update_dashboard_no_metadata_change_is_skip(monkeypatch):
    monkeypatch.setattr(dash_planners, "get_dashboard_detail", lambda d: _fake_detail())
    spec = {
        "api_name": "x",
        "name": "Old Name",
        "type": "generic_dashboard",
        "hidden": False,
        "published": True,
    }
    plan = dash_planners.plan_update_dashboard(DASH_ID, spec)
    (board,) = plan.operations
    assert board.action == "skip"


def _live_homepage() -> dict:
    """A published, hidden homepage with one named, placed dashlet."""
    return {
        "id": DASH_ID,
        "raw": {
            "id": DASH_ID,
            "name": "Team Home",
            "type": "homepage",
            "custom_object": None,
            "hidden": True,
            "published": True,
            "style_settings": {"titleBackgroundColor": "rgba(1,2,3,1)"},
            "dashlets": [
                {
                    "id": DL_ID,
                    "name": "Open deals",
                    "custom_object": OBJ_ID,
                    "layout": {"x": 0, "y": 0, "w": 6, "h": 3},
                    "config": {"report_type": "table_of_records"},
                }
            ],
        },
    }


def test_update_omitting_hidden_published_type_patches_nothing(monkeypatch):
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    plan = dash_planners.plan_update_dashboard(
        DASH_ID, {"api_name": "team_home", "name": "Team Home"}
    )
    (board,) = plan.operations
    assert board.action == "skip"
    assert board.payload == {}


def test_update_explicit_false_hidden_and_published_patches_both(monkeypatch):
    """False is a value, not "unset": it must patch a live true."""
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    plan = dash_planners.plan_update_dashboard(
        DASH_ID,
        {
            "api_name": "team_home",
            "name": "Team Home",
            "hidden": False,
            "published": False,
        },
    )
    (board,) = plan.operations
    assert board.action == "update"
    assert board.payload == {"hidden": False, "published": False}


def test_create_refuses_a_new_dashlet_without_config(no_dashboards):
    with pytest.raises(PlanError, match=r"dashlet '1:Totals': config is required"):
        dash_planners.plan_create_dashboard(
            {
                "api_name": "d1",
                "name": "D1",
                "dashlets": [
                    {"name": "Sent", "config": {"report_type": "email_sent"}},
                    {"name": "Totals"},
                ],
            }
        )


def test_update_refuses_a_new_dashlet_without_config(monkeypatch):
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    with pytest.raises(PlanError, match=r"dashlet '0:Totals': config is required"):
        dash_planners.plan_update_dashboard(
            DASH_ID,
            {
                "api_name": "team_home",
                "name": "Team Home",
                "dashlets": [{"name": "Totals"}],
            },
        )


def test_update_preview_leaves_out_an_unset_dashlet_name(monkeypatch):
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    spec = {
        "api_name": "team_home",
        "name": "Team Home",
        "dashlets": [{"id": DL_ID, "layout": {"x": 0, "y": 0, "w": 6, "h": 3}}],
    }
    _, upd = dash_planners.plan_update_dashboard(DASH_ID, spec).operations
    assert "dashlet" not in upd.preview
    assert upd.preview["id"] == DL_ID[:8]


def test_update_dashlet_without_name_does_not_send_name(monkeypatch):
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    spec = {
        "api_name": "team_home",
        "name": "Team Home",
        "dashlets": [{"id": DL_ID, "config": {"report_type": "table_of_records"}}],
    }
    _, upd = dash_planners.plan_update_dashboard(DASH_ID, spec).operations
    assert upd.action == "update"
    assert "name" not in upd.payload


def test_update_dashlet_sends_only_the_fields_the_spec_sets(monkeypatch):
    """A PATCH without layout/config/custom_object keeps the live values; an
    empty layout or a null custom_object would wipe them."""
    monkeypatch.setattr(
        dash_planners, "get_dashboard_detail", lambda d: _live_homepage()
    )
    new_layout = {"x": 6, "y": 0, "w": 6, "h": 3}
    spec = {
        "api_name": "team_home",
        "name": "Team Home",
        "dashlets": [{"id": DL_ID, "layout": new_layout}],
    }
    _, upd = dash_planners.plan_update_dashboard(DASH_ID, spec).operations
    assert upd.payload == {"layout": new_layout}


# ---------------------------------------------------------------------------
# dashboard duplicate
# ---------------------------------------------------------------------------


def _fake_source() -> dict:
    return {
        "id": DASH_ID,
        "name": "Ops Board",
        "api_name": "ops_board",
        "sharing_settings": {
            "private": False,
            "all_team_members": 1,
            "roles": {
                "view": [],
                "edit": [],
                "admin": [{"id": ADMIN_ID, "display_name": "Admin"}],
            },
            "team_members": {"view": [], "edit": [], "admin": []},
        },
    }


def test_duplicate_dashboard_carries_the_source_sharing(monkeypatch):
    monkeypatch.setattr(dash_planners, "get_dashboard_detail", lambda d: _fake_source())
    plan = dash_planners.plan_duplicate_dashboard("ops_board")
    (op,) = plan.operations

    assert op.action == "duplicate" and op.kind == "dashboard"
    assert op.existing_uuid == DASH_ID
    assert op.payload == {"sharing_settings": _team_sharing()}
    assert op.preview["new_name"] == "server default: 'Ops Board (copy #N)'"
    assert op.preview["new_api_name"] == "server default: 'ops_board_copy_N'"
    assert op.preview["published"].startswith("false")
    assert op.preview["sharing"] == "copied from source"


def test_duplicate_dashboard_name_and_api_name_go_in_the_payload(monkeypatch):
    monkeypatch.setattr(dash_planners, "get_dashboard_detail", lambda d: _fake_source())
    plan = dash_planners.plan_duplicate_dashboard(
        DASH_ID, name="Ops Board 2", api_name="ops_board_2"
    )
    (op,) = plan.operations
    assert op.payload == {
        "sharing_settings": _team_sharing(),
        "name": "Ops Board 2",
        "api_name": "ops_board_2",
    }
    assert op.preview["new_name"] == "Ops Board 2"
    assert op.preview["new_api_name"] == "ops_board_2"


def test_duplicate_dashboard_unknown_source_is_a_plan_error(monkeypatch):
    def missing(d):
        raise LookupError(f"no dashboard with api_name '{d}'")

    monkeypatch.setattr(dash_planners, "get_dashboard_detail", missing)
    with pytest.raises(PlanError, match="dashboard 'nope' not found"):
        dash_planners.plan_duplicate_dashboard("nope")


# ---------------------------------------------------------------------------
# layout update
# ---------------------------------------------------------------------------


def _fake_layouts():
    return OBJ_ID, [
        {
            "id": LAYOUT_ID,
            "name": "Standard View",
            "active": True,
            "order": 0.0,
            "tabs": {"automations": True},
            "config": [{"columns": [{"items": [{"type": "fields"}]}]}],
        }
    ]


def test_update_layout_injects_ids_and_preserves_tabs(monkeypatch):
    monkeypatch.setattr(layout_planners, "_fetch_layouts", lambda o: _fake_layouts())
    spec = {
        "name": "Standard View",
        "config": [
            {
                "columns": [
                    {"width": "third-width", "items": [{"type": "fields"}]},
                    {"width": "two-third-width", "items": [{"type": "timeline"}]},
                ]
            }
        ],
    }
    plan = layout_planners.plan_update_layout("patients", spec)
    (op,) = plan.operations
    assert op.kind == "layout" and op.action == "update"
    assert op.existing_uuid == LAYOUT_ID and op.parent_object_uuid == OBJ_ID
    assert op.payload["tabs"] == {"automations": True}

    group = op.payload["config"][0]
    assert "id" in group
    for column in group["columns"]:
        assert "id" in column
        for item in column["items"]:
            assert "id" in item  # every level got a uuid


def test_update_layout_unknown_name_errors(monkeypatch):
    monkeypatch.setattr(layout_planners, "_fetch_layouts", lambda o: _fake_layouts())
    with pytest.raises(PlanError, match="no layout named"):
        layout_planners.plan_update_layout("patients", {"name": "Nope", "config": []})


# ---------------------------------------------------------------------------
# apply dispatch (respx) — the new kinds hit the right endpoints
# ---------------------------------------------------------------------------


@respx.mock
def test_apply_dashboard_create_then_deferred_dashlet():
    board_route = respx.post(f"{FAKE_BASE_URL}/api/dashboards").mock(
        return_value=httpx.Response(201, json={"id": DASH_ID})
    )
    dashlet_route = respx.post(
        f"{FAKE_BASE_URL}/api/dashboards/{DASH_ID}/dashlet"
    ).mock(return_value=httpx.Response(201, json={"id": DL_ID}))

    ops = [
        PlanOperation(
            action="create",
            kind="dashboard",
            key="d",
            payload={"api_name": "d", "name": "D"},
        ),
        PlanOperation(
            action="create",
            kind="dashlet",
            key="d.0",
            payload={"name": "dl", "config": {}, "layout": {}},
            deferred_parent_object_key="d",
        ),
    ]
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=ops)
    )

    assert board_route.call_count == 1 and dashlet_route.call_count == 1
    assert result.all_ok
    assert [r.server_uuid for r in result.results] == [DASH_ID, DL_ID]


@respx.mock
def test_apply_layout_puts_to_object_layout_endpoint():
    route = respx.put(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/layouts/{LAYOUT_ID}"
    ).mock(return_value=httpx.Response(200, json={"id": LAYOUT_ID}))
    op = PlanOperation(
        action="update",
        kind="layout",
        key="patients.Standard View",
        payload={"name": "Standard View", "config": []},
        existing_uuid=LAYOUT_ID,
        parent_object_uuid=OBJ_ID,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )

    assert route.call_count == 1
    (r,) = result.results
    assert r.status == "ok" and r.server_uuid == LAYOUT_ID

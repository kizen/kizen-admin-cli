"""Toolbar template planners: entry building, plugin rebuilds, update matching.

Planners build their own client from config, so these go through respx
against `FAKE_BASE_URL`, served from the sanitized live captures in
`tests/fixtures/toolbar_templates/` (bootstrap, business apps, objects and a
template saved in the toolbar editor). The template's plugin entries carry
older scripts than bootstrap, as after a plugin release. Bootstrap's
`mistral.toolbar_items` entry is added by hand: no install on the capture
business had a toolbar item.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools import dashboards as dash_tools
from kizen_builder.tools.planners.toolbar_templates import (
    plan_create_toolbar_template,
    plan_delete_toolbar_template,
    plan_update_toolbar_template,
)
from kizen_builder.tools.plans import PlanError, PlanOperation
from tests.conftest import FAKE_BASE_URL, FAKE_BUSINESS_ID, load_fixture

BASE = f"{FAKE_BASE_URL}/api/toolbar-templates"
BOOTSTRAP = load_fixture("toolbar_templates/bootstrap.json")
APPS = load_fixture("toolbar_templates/apps.json")
OBJECTS = load_fixture("toolbar_templates/objects.json")
TEMPLATE = load_fixture("toolbar_templates/template.json")
TID = TEMPLATE["id"]
ADMIN_ID = "00000000-0000-4000-8000-00000000ad01"
PATIENTS = next(o for o in OBJECTS if o["name"] == "patients")


@pytest.fixture(autouse=True)
def _admin_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash_tools, "admin_role_id", lambda: ADMIN_ID)


def _serve(template: dict[str, Any] | None = None, templates: list | None = None):
    tmpl = template or TEMPLATE
    rows = templates if templates is not None else [tmpl]
    respx.get(BASE).respond(json={"results": rows, "next": None})
    respx.get(f"{BASE}/{tmpl['id']}").respond(json=tmpl)
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").respond(
        json={"results": OBJECTS, "next": None}
    )
    respx.get(f"{FAKE_BASE_URL}/api/external-integrations/bootstrap").respond(
        json=BOOTSTRAP
    )
    respx.get(
        f"{FAKE_BASE_URL}/api/external-integrations/plugin-apps/{FAKE_BUSINESS_ID}/apps"
    ).respond(json=APPS)


def _op(plan) -> PlanOperation:
    assert len(plan.operations) == 1
    return plan.operations[0]


def _create(entries: list[dict[str, Any]], **extra: Any) -> PlanOperation:
    return _op(
        plan_create_toolbar_template({"name": "New", "entries": entries, **extra})
    )


def _by_id(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for e in entries:
        out[e["id"]] = e
        out.update(_by_id(e.get("children") or []))
    return out


def _page(key: str, api_name: str) -> dict[str, Any]:
    return next(
        p for p in BOOTSTRAP[key]["routable_pages"] if p["api_name"] == api_name
    )


# --- create ---------------------------------------------------------------


@respx.mock
def test_create_builds_each_kind_and_appends_locked_entries():
    _serve()
    op = _create(
        [
            {"object": "patients"},
            {"object": "contacts"},
            {"plugin": "ops_hub", "page": "home"},
            {"url": "https://example.com", "label": "Example"},
            {"builtin": "dashboard"},
            {
                "category": "Ops",
                "entries": [
                    {"object": "support_tickets", "width": 50},
                    {"builtin": "settings", "width": 50},
                ],
            },
            {"category": "data", "entries": [{"builtin": "custom_objects"}]},
            {"raw": {"id": "default__homepages", "type": "item", "label": "Home"}},
        ]
    )
    assert op.action == "create"
    assert op.kind == "toolbar_template"
    config = op.payload["configuration_json"]
    assert [e["id"] for e in config] == [
        PATIENTS["id"],
        "default__contacts",
        "ops_hub-home",
        "new_custom_url--1",
        "default__dashboard",
        "new_category--1",
        "default__data",
        "default__homepages",
        "default__notification_center",
        "default__my_account",
    ]
    assert config[0] == {
        "id": PATIENTS["id"],
        "icon": PATIENTS["default_icon"],
        "type": "item",
        "color": PATIENTS["default_color"],
        "label": "Patients",
        "isLocked": False,
        "isDefault": False,
        "widthPercent": 100,
        "isCustomObject": True,
    }
    url = config[3]
    assert url["url"] == "https://example.com"
    assert url["displayName"] == "Example"
    ops = config[5]
    assert ops["customLabel"] == "Ops"
    assert ops["type"] == "category"
    assert [c["widthPercent"] for c in ops["children"]] == [50, 50]
    assert [c["id"] for c in config[-1]["children"]] == [
        "default__my_profile",
        "default__change_business",
        "default__logout",
    ]
    assert op.preview["notes"] == [
        "added: Notification Center, My Account (every template carries them)"
    ]


@respx.mock
def test_create_defaults_to_private_with_admin_role_only():
    _serve()
    op = _create([{"builtin": "dashboard"}])
    assert op.payload["sharing_settings"] == {
        "private": True,
        "all_team_members": 0,
        "roles": {"view": [], "edit": [], "admin": [ADMIN_ID]},
        "team_members": {"view": [], "edit": [], "admin": []},
    }
    assert "owner" not in op.payload


@respx.mock
def test_create_plugin_entry_is_the_bootstrap_page_plus_plugin_keys():
    _serve()
    entry = _create([{"plugin": "ops_hub", "page": "home"}]).payload[
        "configuration_json"
    ][0]
    page = _page("ops_hub", "home")
    assert entry == {
        "id": "ops_hub-home",
        **page,
        "type": "item",
        "isLocked": False,
        "isDefault": False,
        "isDynamic": False,
        "widthPercent": 100,
        "isPluginItem": True,
        "plugin_api_name": "ops_hub",
        "plugin_name": BOOTSTRAP["ops_hub"]["name"],
        "args": BOOTSTRAP["ops_hub"]["business_config"],
        "employee_config": {},
        "label": page["name"],
        "icon": page["toolbar_icon"],
        "color": page["toolbar_color"],
        "script": "",
    }


@respx.mock
def test_create_never_copies_the_callers_employee_config():
    _serve()
    entry = _create([{"plugin": "kizen_ai", "page": "build"}]).payload[
        "configuration_json"
    ][0]
    assert BOOTSTRAP["kizen_ai"]["employee_config"]
    assert entry["employee_config"] == {}


@respx.mock
def test_create_builds_a_toolbar_item():
    _serve()
    entry = _create([{"plugin": "mistral", "page": "quick_ask"}]).payload[
        "configuration_json"
    ][0]
    item = BOOTSTRAP["mistral"]["toolbar_items"][0]
    assert entry["id"] == "mistral-quick_ask"
    assert {k: entry[k] for k in item} == item
    assert entry["isPluginItem"] is True


@respx.mock
def test_business_app_resolves_by_base_api_name_and_warns_when_unpublished():
    _serve()
    op = _create(
        [
            {"plugin": "route_planner", "page": "routes"},
            {"plugin": "stock_tracker", "page": "inventory"},
        ]
    )
    ids = [e["id"] for e in op.payload["configuration_json"][:2]]
    assert ids == [
        "route_planner_b1d0aa-routes",
        "stock_tracker_b1d0aa-inventory",
    ]
    assert op.preview["warnings"] == [
        "route_planner_b1d0aa is not published; "
        "route_planner_b1d0aa-routes carries your preview"
    ]


@respx.mock
def test_create_from_configuration_json_rebuilds_old_plugin_snapshots():
    _serve()
    config = copy.deepcopy(TEMPLATE["configuration_json"])
    op = _op(
        plan_create_toolbar_template({"name": "Copy", "configuration_json": config})
    )
    built = _by_id(op.payload["configuration_json"])
    old = _by_id(TEMPLATE["configuration_json"])["ops_hub-home"]
    home = built["ops_hub-home"]
    assert old["css"] != home["css"] == _page("ops_hub", "home")["css"]
    assert home["dimensions"] == old["dimensions"]
    # The editor's own minimal reference entry is left alone.
    assert (
        built["kizen_ai-build"]
        == _by_id(TEMPLATE["configuration_json"])["kizen_ai-build"]
    )


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"object": "patients", "builtin": "dashboard"}, "exactly one of"),
        ({"plugin": "ops_hub"}, "plugin and page go together"),
        ({"object": "patients", "entries": []}, "only a category takes entries"),
        ({"url": "https://example.com"}, "needs a label"),
        ({"raw": {"id": "x"}}, "needs 'id' and 'type'"),
        ({"object": "patients", "width": 75}, "width"),
        ({"object": "patients", "colour": "#000"}, "colour"),
    ],
)
def test_spec_validation_errors(entry, message):
    with pytest.raises(ValidationError, match=message):
        plan_create_toolbar_template({"name": "New", "entries": [entry]})


def test_entries_and_configuration_json_together_is_an_error():
    with pytest.raises(ValidationError, match="not both"):
        plan_create_toolbar_template(
            {"name": "New", "entries": [], "configuration_json": []}
        )


@pytest.mark.parametrize(
    "entries, message",
    [
        ([{"object": "nope"}], "object 'nope' not found"),
        ([{"plugin": "nope", "page": "home"}], "plugin 'nope' is not installed"),
        (
            [{"plugin": "ops_hub", "page": "nope"}],
            "plugin 'ops_hub' has no toolbar page 'nope'",
        ),
        (
            [{"category": "A", "entries": [{"category": "B", "entries": []}]}],
            "category 'B' is inside a category",
        ),
        ([{"builtin": "data"}], "builtin 'data' is a category"),
        ([{"builtin": "nope"}], "unknown builtin 'nope'"),
        (
            [{"builtin": "dashboard"}, {"builtin": "dashboard"}],
            "duplicate entry id",
        ),
        ([{"builtin": "logout"}], r"default__logout \(the added My Account"),
        (
            [{"category": "Ops", "entries": [{"builtin": "my_profile"}]}],
            "duplicate entry id.*default__my_profile",
        ),
        (
            [{"category": "Ops", "entries": [{"builtin": "notification_center"}]}],
            "'Notification Center' goes at the top level, not inside category 'Ops'",
        ),
        (
            [{"raw": {"id": "00000000-0000-4000-8000-000000000000", "type": "item"}}],
            "object '00000000-0000-4000-8000-000000000000' not found",
        ),
    ],
)
@respx.mock
def test_plan_errors(entries, message):
    _serve()
    with pytest.raises(PlanError, match=message):
        _create(entries)
    assert not [c for c in respx.calls if c.request.method != "GET"]


@respx.mock
def test_create_needs_a_name_and_a_free_one():
    _serve()
    with pytest.raises(PlanError, match="needs a name"):
        plan_create_toolbar_template({"entries": []})
    with pytest.raises(PlanError, match="already exists"):
        plan_create_toolbar_template({"name": TEMPLATE["name"], "entries": []})


@respx.mock
def test_configuration_json_must_be_a_list_of_entries():
    _serve()
    with pytest.raises(PlanError, match="expected a list"):
        plan_create_toolbar_template({"name": "New", "configuration_json": {}})
    with pytest.raises(PlanError, match="'id' and 'type'"):
        plan_create_toolbar_template({"name": "New", "configuration_json": [{}]})


# --- update ---------------------------------------------------------------


def _refreshed_template() -> dict[str, Any]:
    """TEMPLATE as it reads after a no-spec update was applied."""
    _serve()
    op = _op(plan_update_toolbar_template(TID, None))
    respx.reset()
    return {**TEMPLATE, "configuration_json": op.payload["configuration_json"]}


@respx.mock
def test_update_without_spec_refreshes_old_plugin_snapshots():
    _serve()
    op = _op(plan_update_toolbar_template(TID, None))
    assert op.action == "update"
    assert op.existing_uuid == TID
    assert list(op.payload) == ["configuration_json"]
    assert op.preview["diff"] == {"entries": "same labels; entry details changed"}
    refreshed = [n for n in op.preview["notes"] if "refreshed" in n]
    assert "plugin snapshot refreshed: ops_hub-home (0.1.6)" in refreshed
    built = _by_id(op.payload["configuration_json"])
    live = _by_id(TEMPLATE["configuration_json"])
    for key in ("employee_config", "dimensions", "label", "icon"):
        assert built["ops_hub-home"][key] == live["ops_hub-home"][key]


@respx.mock
def test_update_without_spec_skips_a_current_template():
    current = _refreshed_template()
    _serve(current)
    op = _op(plan_update_toolbar_template(TID, None))
    assert op.action == "skip"
    assert op.payload == {}


@respx.mock
def test_get_json_round_trips_to_a_skip():
    current = _refreshed_template()
    _serve(current)
    result = CliRunner().invoke(cli.app, ["toolbar-templates", "get", TID, "--json"])
    assert result.exit_code == 0, result.output
    spec = json.loads(result.output)
    assert spec["sharing_settings"]["roles"]["admin"] == [ADMIN_ID]
    op = _op(plan_update_toolbar_template(TID, spec))
    assert op.action == "skip", op.preview


@respx.mock
def test_update_keeps_a_matched_entrys_icon_dict_and_dimensions():
    icon = {"label": "star", "value": "star", "className": "fa-light fa-star"}
    template = copy.deepcopy(TEMPLATE)
    template["configuration_json"].insert(
        0,
        {
            "id": PATIENTS["id"],
            "icon": icon,
            "type": "item",
            "color": "#123456",
            "label": "Patients",
            "isLocked": False,
            "isDefault": False,
            "widthPercent": 50,
            "isCustomObject": True,
            "dimensions": {"top": 1, "width": 2, "height": 3},
            "fromColumn": "find-options",
        },
    )
    _serve(template)
    op = _op(
        plan_update_toolbar_template(
            TID, {"entries": [{"object": "patients"}, {"builtin": "dashboard"}]}
        )
    )
    config = op.payload["configuration_json"]
    assert config[0] == template["configuration_json"][0]
    assert config[1] == _by_id(TEMPLATE["configuration_json"])["default__dashboard"]
    assert [e["id"] for e in config[2:]] == [
        "default__notification_center",
        "default__my_account",
    ]


@respx.mock
def test_update_matched_plugin_keeps_display_and_refreshes_scripts():
    _serve()
    op = _op(
        plan_update_toolbar_template(
            TID, {"entries": [{"plugin": "ops_hub", "page": "home", "width": 50}]}
        )
    )
    entry = op.payload["configuration_json"][0]
    live = _by_id(TEMPLATE["configuration_json"])["ops_hub-home"]
    assert entry["css"] == _page("ops_hub", "home")["css"]
    assert entry["dimensions"] == live["dimensions"]
    assert entry["widthPercent"] == 50


@respx.mock
def test_update_numbers_new_links_after_existing_ones():
    _serve()
    op = _op(
        plan_update_toolbar_template(
            TID,
            {
                "entries": [
                    {"url": "https://example.com", "label": "Example"},
                    {"url": "https://example.org", "label": "Other"},
                ]
            },
        )
    )
    ids = [e["id"] for e in op.payload["configuration_json"][:2]]
    assert ids == ["new_custom_url--1", "new_custom_url--2"]


@respx.mock
def test_update_warns_on_a_carried_plugin_that_is_not_installed():
    template = copy.deepcopy(TEMPLATE)
    entry = _by_id(template["configuration_json"])["ops_hub-home"]
    entry["plugin_api_name"] = "gone"
    _serve(template)
    op = _op(plan_update_toolbar_template(TID, None))
    assert (
        "plugin entry ops_hub-home: 'gone' is not installed; left as saved"
        in op.preview["warnings"]
    )
    with pytest.raises(PlanError, match="'gone' is not installed"):
        plan_update_toolbar_template(
            TID, {"configuration_json": template["configuration_json"]}
        )


@respx.mock
def test_update_sharing_and_name():
    _serve()
    op = _op(
        plan_update_toolbar_template(
            TID,
            {
                "name": "Renamed",
                "sharing_settings": {**TEMPLATE["sharing_settings"], "private": False},
            },
        )
    )
    assert op.payload["name"] == "Renamed"
    assert op.payload["sharing_settings"]["private"] is False
    assert op.payload["sharing_settings"]["roles"]["admin"] == [ADMIN_ID]


@respx.mock
def test_delete_by_name():
    _serve()
    op = _op(plan_delete_toolbar_template(TEMPLATE["name"]))
    assert op.action == "delete"
    assert op.existing_uuid == TID

"""MCP server planners: create/update/delete, the tool check and sharing diffs.

Planners build their own client from config, so these go through respx
against `FAKE_BASE_URL`, served from the sanitized live captures in
`tests/fixtures/mcp_servers/`.
"""

from __future__ import annotations

import copy
import json

import httpx
import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners.mcp_servers import (
    DEFAULT_SHARING,
    plan_create_mcp_server,
    plan_delete_mcp_server,
    plan_update_mcp_server,
)
from kizen_builder.tools.plans import PlanError, PlanOperation
from tests.conftest import FAKE_BASE_URL, load_fixture

BASE = f"{FAKE_BASE_URL}/api/mcp-servers"
LIST = load_fixture("mcp_servers/list.json")
DETAIL = load_fixture("mcp_servers/detail.json")
READONLY = load_fixture("mcp_servers/detail_readonly.json")
TOOLS = load_fixture("mcp_servers/tools.json")
SID = DETAIL["id"]
SA_ID = DETAIL["sharing_settings"]["team_members"]["view"][0]["id"]


def _serve(
    detail: dict | None = None,
    servers: list | None = None,
    readonly: dict | None = None,
) -> None:
    """Serve the list, the catalogue, and each server's detail by id."""
    rows = servers if servers is not None else LIST["results"]
    respx.get(BASE).respond(json={**LIST, "results": rows})
    respx.get(f"{BASE}/tools").respond(json=TOOLS)
    respx.get(f"{BASE}/{SID}").respond(json=detail or DETAIL)
    respx.get(f"{BASE}/{READONLY['id']}").respond(json=readonly or READONLY)


def _op(plan) -> PlanOperation:
    assert len(plan.operations) == 1
    return plan.operations[0]


# --- create ---------------------------------------------------------------


@respx.mock
def test_create_payload_carries_only_write_keys_and_default_sharing():
    _serve()
    op = _op(
        plan_create_mcp_server(
            {
                "name": "Intake",
                "api_name": "intake",
                "tool_names": ["listCatalogs", "getDocument", "listCatalogs"],
            }
        )
    )
    assert op.action == "create"
    assert op.kind == "mcp_server"
    assert op.payload == {
        "name": "Intake",
        "api_name": "intake",
        "tool_names": ["listCatalogs", "getDocument"],
        "sharing_settings": DEFAULT_SHARING,
    }


@respx.mock
def test_create_normalises_read_shape_sharing():
    _serve()
    op = _op(
        plan_create_mcp_server(
            {
                "name": "Intake",
                "api_name": "intake",
                "sharing_settings": DETAIL["sharing_settings"],
            }
        )
    )
    assert op.payload["sharing_settings"]["team_members"]["view"] == [SA_ID]


@respx.mock
def test_create_refuses_an_existing_api_name():
    _serve()
    with pytest.raises(PlanError) as e:
        plan_create_mcp_server({"name": "Again", "api_name": "support_tools"})
    assert str(e.value) == (
        f"MCP server 'support_tools' already exists (id {SID}). "
        "Use `kizen mcp-servers update` instead."
    )


def test_create_needs_name_and_api_name():
    with pytest.raises(PlanError, match="needs name and api_name"):
        plan_create_mcp_server({"description": "no names"})


@respx.mock
def test_create_refuses_unknown_tools_naming_the_valid_ones():
    _serve()
    with pytest.raises(PlanError) as e:
        plan_create_mcp_server(
            {
                "name": "X",
                "api_name": "intake",
                "tool_names": ["nope", "getDocument", "nah"],
            }
        )
    assert str(e.value) == (
        "unknown MCP tool(s): nope, nah. Valid tools: getCustomObject, "
        "getDocument, listCatalogs, listCustomObjects, listObjectFieldOptions, "
        "listObjectFields, searchCatalog. List them with `kizen mcp-servers tools`."
    )


@respx.mock
def test_unknown_tool_sends_no_request(tmp_path):
    _serve()
    post = respx.post(BASE).respond(201, json={})
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"name": "X", "api_name": "intake", "tool_names": ["nope"]})
    )
    result = CliRunner().invoke(
        cli.app, ["mcp-servers", "create", "--spec-file", str(spec), "-y"]
    )
    assert result.exit_code == 1
    assert "unknown MCP tool(s): nope" in result.stderr
    assert post.call_count == 0


@respx.mock
def test_create_as_default_names_the_current_default():
    _serve()
    op = _op(
        plan_create_mcp_server(
            {"name": "X", "api_name": "intake", "is_default_for_business": True}
        )
    )
    assert op.payload["is_default_for_business"] is True
    assert op.preview["business_default"] == (
        "replaces 'readonly', which stops being the default. To restore it, run "
        '`kizen mcp-servers update readonly` with {"is_default_for_business": true}'
    )


# --- update ---------------------------------------------------------------


@respx.mock
def test_get_raw_output_resent_unchanged_plans_skip():
    _serve()
    op = _op(plan_update_mcp_server("support_tools", copy.deepcopy(DETAIL)))
    assert op.action == "skip"
    assert op.payload == {}
    assert op.preview["ignored_read_only_keys"] == [
        "id",
        "tools",
        "is_system_managed",
        "endpoint_url",
        "created",
        "updated",
        "owner",
        "employee_access",
        "sharing_stats",
    ]


@respx.mock
def test_reordered_tools_plan_skip():
    _serve()
    op = _op(
        plan_update_mcp_server(
            SID, {"tool_names": list(reversed(DETAIL["tool_names"]))}
        )
    )
    assert op.action == "skip"


@respx.mock
def test_read_shape_sharing_with_reordered_grants_plans_skip():
    detail = copy.deepcopy(DETAIL)
    detail["sharing_settings"]["roles"]["view"] = [
        {"id": "00000000-0000-4000-8000-0000000000a1", "display_name": "A"},
        {"id": "00000000-0000-4000-8000-0000000000a2", "display_name": "B"},
    ]
    _serve(detail)
    sharing = copy.deepcopy(detail["sharing_settings"])
    sharing["roles"]["view"] = [
        "00000000-0000-4000-8000-0000000000a2",
        "00000000-0000-4000-8000-0000000000a1",
    ]
    op = _op(plan_update_mcp_server(SID, {"sharing_settings": sharing}))
    assert op.action == "skip"


@respx.mock
def test_changed_sharing_is_sent_whole():
    _serve()
    sharing = copy.deepcopy(DETAIL["sharing_settings"])
    sharing["all_team_members"] = 1
    sharing["roles"]["view"] = ["00000000-0000-4000-8000-0000000000a1"]
    sharing["team_members"]["view"] = []
    op = _op(plan_update_mcp_server(SID, {"sharing_settings": sharing}))
    assert op.action == "update"
    assert op.preview["diff"]["sharing_settings"] == (
        "all_team_members 0 → 1, +roles.view:00000000-0000-4000-8000-0000000000a1, "
        "-team_members.view:Sample Service Account (the whole block is sent)"
    )
    assert op.payload == {
        "sharing_settings": {
            "private": False,
            "all_team_members": 1,
            "roles": {
                "view": ["00000000-0000-4000-8000-0000000000a1"],
                "edit": [],
                "admin": [],
            },
            "team_members": {"view": [], "edit": [], "admin": []},
        }
    }


@respx.mock
def test_update_diffs_only_the_keys_the_spec_sets():
    _serve()
    op = _op(
        plan_update_mcp_server(
            SID,
            {"description": None, "tool_names": ["listCustomObjects", "getDocument"]},
        )
    )
    assert op.existing_uuid == SID
    assert op.payload == {
        "description": None,
        "tool_names": ["listCustomObjects", "getDocument"],
    }
    assert op.preview["diff"]["tool_names"] == (
        "+getDocument, -listObjectFields, -searchCatalog"
    )


@respx.mock
def test_update_checks_tools_against_the_catalogue():
    _serve()
    with pytest.raises(PlanError, match=r"unknown MCP tool\(s\): bogus\."):
        plan_update_mcp_server(SID, {"tool_names": ["bogus"]})


@respx.mock
def test_update_refuses_an_api_name_change():
    _serve()
    with pytest.raises(PlanError) as e:
        plan_update_mcp_server(SID, {"api_name": "renamed"})
    assert str(e.value) == (
        "api_name can't change after creation; create a new server instead."
    )


@respx.mock
@pytest.mark.parametrize(
    "plan",
    [
        lambda: plan_update_mcp_server("readonly", {"is_enabled": False}),
        lambda: plan_update_mcp_server(
            "readonly", {"is_default_for_business": True, "name": "Read Only"}
        ),
        lambda: plan_update_mcp_server("readonly", copy.deepcopy(READONLY)),
        lambda: plan_delete_mcp_server("readonly"),
    ],
    ids=["update", "default-plus-extra-key", "get-raw", "delete"],
)
def test_system_server_is_refused(plan):
    _serve(readonly={**READONLY, "is_default_for_business": False})
    with pytest.raises(PlanError) as e:
        plan()
    assert str(e.value) == (
        "'readonly' is managed by Kizen and can't be changed or deleted from the "
        'CLI. The one exception: an update that sets only {"is_default_for_business": '
        "true}."
    )


@respx.mock
def test_system_server_can_be_made_the_default_again():
    readonly = {**READONLY, "is_default_for_business": False}
    servers = [
        {**LIST["results"][0], "is_default_for_business": False},
        {**LIST["results"][1], "is_default_for_business": True},
    ]
    _serve(servers=servers, readonly=readonly)
    op = _op(plan_update_mcp_server("readonly", {"is_default_for_business": True}))
    assert op.action == "update"
    assert op.existing_uuid == READONLY["id"]
    assert op.payload == {"is_default_for_business": True}
    assert op.preview["business_default"].startswith(
        "replaces 'support_tools', which stops being the default."
    )


@respx.mock
def test_system_server_already_default_plans_skip():
    _serve()
    op = _op(plan_update_mcp_server("readonly", {"is_default_for_business": True}))
    assert op.action == "skip"


@respx.mock
def test_unsetting_the_current_default_is_refused():
    _serve({**DETAIL, "is_default_for_business": True})
    with pytest.raises(PlanError) as e:
        plan_update_mcp_server(SID, {"is_default_for_business": False})
    assert str(e.value) == (
        "'support_tools' is the business default and Kizen keeps one. To change "
        "it, set another server as the default instead "
        '({"is_default_for_business": true} on that server).'
    )


@respx.mock
def test_unsetting_a_non_default_is_a_skip():
    _serve()
    op = _op(plan_update_mcp_server(SID, {"is_default_for_business": False}))
    assert op.action == "skip"


@respx.mock
def test_unknown_server_lists_api_names():
    _serve()
    with pytest.raises(PlanError, match="available: readonly, support_tools"):
        plan_update_mcp_server("Support Tools", {"is_enabled": False})


# --- delete ---------------------------------------------------------------


@respx.mock
def test_delete_preview():
    _serve()
    op = _op(plan_delete_mcp_server("support_tools"))
    assert op.action == "delete"
    assert op.existing_uuid == SID
    assert {k: op.preview[k] for k in ("name", "api_name", "id", "tool_count")} == {
        "name": "Support Tools",
        "api_name": "support_tools",
        "id": SID,
        "tool_count": 3,
    }
    assert "is_business_default" not in op.preview


@respx.mock
def test_delete_preview_flags_the_business_default():
    _serve({**DETAIL, "is_default_for_business": True})
    op = _op(plan_delete_mcp_server(SID))
    assert op.preview["is_business_default"] is True


# --- spec validation ------------------------------------------------------


def test_unknown_sharing_key_names_the_team_members_route():
    sharing = {**DEFAULT_SHARING, "service_accounts": {"view": [SA_ID]}}
    with pytest.raises(ValidationError) as e:
        plan_create_mcp_server(
            {"name": "X", "api_name": "intake", "sharing_settings": sharing}
        )
    assert e.value.errors()[0]["msg"] == (
        "unknown key(s) service_accounts. sharing_settings takes private, all_team_members, roles and team_members. "
        "Service accounts are shared as team members: put the account's id in "
        "team_members."
    )


@pytest.mark.parametrize(
    ("spec", "loc"),
    [
        ({"name": "X", "api_name": "Has-Caps"}, "api_name"),
        ({"name": "X", "api_name": "9lives"}, "api_name"),
        ({"name": "X", "api_name": "intake", "colour": "red"}, "colour"),
        (
            {"name": "X", "api_name": "intake", "sharing_settings": {"private": True}},
            "sharing_settings.all_team_members",
        ),
        (
            {
                "name": "X",
                "api_name": "intake",
                "sharing_settings": {
                    **DEFAULT_SHARING,
                    "roles": {"view": [{"display_name": "No id"}]},
                },
            },
            "sharing_settings.roles.view.0.GrantRef.id",
        ),
        ({"name": None}, "spec"),
        ({"is_enabled": None}, "spec"),
    ],
    ids=[
        "caps",
        "leading-digit",
        "unknown-key",
        "partial-sharing",
        "grant-without-id",
        "null-name",
        "null-is-enabled",
    ],
)
def test_bad_specs_fail_validation(spec, loc):
    with pytest.raises(ValidationError) as e:
        plan_create_mcp_server(spec)
    errors = e.value.errors()
    locs = {".".join(str(p) for p in err["loc"]) or "spec" for err in errors}
    assert loc in locs


# --- execute --------------------------------------------------------------


def test_execute_refuses_an_unsupported_action():
    op = PlanOperation(
        action="duplicate", kind="mcp_server", key="x", existing_uuid=SID
    )
    with KizenClient(load_env_config()) as c, pytest.raises(PlanError) as e:
        plan_tools._execute(c, op)
    assert str(e.value) == "unsupported mcp_server action 'duplicate'"


@respx.mock
def test_execute_create_reaches_the_api():
    route = respx.post(BASE).mock(return_value=httpx.Response(201, json={"id": SID}))
    op = PlanOperation(
        action="create", kind="mcp_server", key="x", payload={"name": "X"}
    )
    with KizenClient(load_env_config()) as c:
        assert plan_tools._execute(c, op) == {"id": SID}
    assert route.call_count == 1

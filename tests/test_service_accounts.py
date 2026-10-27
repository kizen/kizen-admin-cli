"""`kizen service-accounts`: the API wrappers, the three planners, the CLI,
and the guarantee that a create response's API key reaches no output.

Planners build their own `KizenClient` from config, so these go through
`respx` against `FAKE_BASE_URL`, as `test_permission_plans.py` does.
"""

from __future__ import annotations

import copy
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api import service_accounts as sa_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools import service_accounts as sa_tools
from kizen_builder.tools.planners.service_accounts import (
    plan_create_service_account,
    plan_delete_service_account,
    plan_update_service_account,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

runner = CliRunner()

SA_URL = f"{FAKE_BASE_URL}/api/service-accounts"
GROUPS_URL = f"{FAKE_BASE_URL}/api/permission-group"
SENTINEL = "SENTINEL-KEY-DO-NOT-PRINT"

ACCOUNT_ID = "00000000-0000-4000-8000-0000000005a1"
AUTOMATION_ID = "00000000-0000-4000-8000-0000000005a2"
PLUGIN_ID = "00000000-0000-4000-8000-0000000005a3"
NEW_ID = "00000000-0000-4000-8000-0000000005a4"
GROUP_ID = "00000000-0000-4000-8000-000000000201"
GROUP_ID_2 = "00000000-0000-4000-8000-000000000202"
UNKNOWN_GROUP_ID = "00000000-0000-4000-8000-0000000009ff"

GROUPS = [
    {"id": GROUP_ID, "name": "Integrations"},
    {"id": GROUP_ID_2, "name": "Reporting"},
]


def _account(id_, name, api_name, account_type="service_integration", groups=()):
    return {
        "id": id_,
        "name": name,
        "api_name": api_name,
        "account_type": account_type,
        "status": "active" if groups else "disabled",
        "email": f"{api_name}@serviceaccounts.example.com",
        "permission_groups": [g for g in GROUPS if g["id"] in groups],
        "api_token_count": 1,
        "primary_api_token_preview": "abc" + "*" * 33,
        "secondary_api_token_preview": None,
        "created": "2026-10-27T12:00:00Z",
        "updated": "2026-10-27T12:00:00Z",
        "last_login": None,
    }


INTEGRATION = _account(ACCOUNT_ID, "Example Bot", "example_bot", groups=(GROUP_ID,))
AUTOMATION = _account(AUTOMATION_ID, "Workflow Code", None, "service_automation")
PLUGIN = _account(
    PLUGIN_ID, "Plugin Service", "plugin__service", "service_automationplugin"
)
ACCOUNTS = [INTEGRATION, AUTOMATION, PLUGIN]


def _envelope(results):
    return {"count": len(results), "next": None, "previous": None, "results": results}


def _mock_reads(accounts=ACCOUNTS):
    respx.get(SA_URL).mock(return_value=httpx.Response(200, json=_envelope(accounts)))
    respx.get(GROUPS_URL).mock(return_value=httpx.Response(200, json=_envelope(GROUPS)))
    for a in accounts:
        respx.get(f"{SA_URL}/{a['id']}").mock(return_value=httpx.Response(200, json=a))


def _created(name, api_name, groups=()):
    body = _account(NEW_ID, name, api_name, groups=groups)
    body["api_key"] = {
        "id": "00000000-0000-4000-8000-0000000005b1",
        "key": SENTINEL,
        "key_preview": "SEN" + "*" * 33,
        "employee": {"id": NEW_ID, "display_name": name},
    }
    return body


def _client():
    return KizenClient(load_env_config())


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------


@respx.mock
def test_list_pages_at_100_and_sends_search():
    route = respx.get(SA_URL).mock(
        return_value=httpx.Response(200, json=_envelope([INTEGRATION]))
    )
    with _client() as c:
        assert sa_api.list_service_accounts(c, search="Example") == [INTEGRATION]
    assert dict(route.calls[0].request.url.params) == {
        "page_size": "100",
        "search": "Example",
    }


@respx.mock
def test_history_makes_one_request_with_page_size_limit():
    route = respx.get(f"{SA_URL}/{ACCOUNT_ID}/history").mock(
        return_value=httpx.Response(
            200,
            json={**_envelope([{"id": "e1"}]), "next": f"{SA_URL}/x?page=2"},
        )
    )
    with _client() as c:
        assert sa_api.list_service_account_history(c, ACCOUNT_ID, 5) == [{"id": "e1"}]
    assert route.call_count == 1
    assert dict(route.calls[0].request.url.params) == {"page_size": "5"}


@respx.mock
def test_create_strips_the_api_key_from_its_return():
    respx.post(SA_URL).mock(
        return_value=httpx.Response(201, json=_created("New Bot", "new_bot"))
    )
    with _client() as c:
        resp = sa_api.create_service_account(c, {"name": "New Bot"})
    assert "api_key" not in resp
    assert SENTINEL not in json.dumps(resp)


def test_no_function_calls_an_api_tokens_endpoint():
    import inspect

    functions = inspect.getmembers(sa_api, inspect.isfunction)
    own = [f for _, f in functions if f.__module__ == sa_api.__name__]
    assert len(own) == 6
    assert not [f.__name__ for f in own if "api-tokens" in inspect.getsource(f)]


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref", [ACCOUNT_ID, "example_bot", "Example Bot", "example bot"]
)
@respx.mock
def test_resolve_by_uuid_api_name_or_name(ref):
    _mock_reads()
    with _client() as c:
        assert sa_tools.resolve_service_account(c, ref)["id"] == ACCOUNT_ID


@respx.mock
def test_resolve_unknown_raises_lookup_error_naming_service_account():
    _mock_reads()
    with _client() as c, pytest.raises(LookupError, match="service account 'nope'"):
        sa_tools.resolve_service_account(c, "nope")


# ---------------------------------------------------------------------------
# plan_create_service_account
# ---------------------------------------------------------------------------


@respx.mock
def test_create_plans_name_and_groups_with_predicted_api_name():
    _mock_reads()
    plan = plan_create_service_account("New Bot (MCP)!", [GROUP_ID])
    (op,) = plan.operations
    assert (op.action, op.kind) == ("create", "service_account")
    assert op.payload == {"name": "New Bot (MCP)!", "permission_groups": [GROUP_ID]}
    assert op.preview["predicted api_name"] == "new_bot_mcp"
    assert op.preview["permission_groups"] == ["Integrations"]
    assert "note" not in op.preview


@respx.mock
def test_create_without_groups_notes_the_account_starts_disabled():
    _mock_reads()
    (op,) = plan_create_service_account("New Bot").operations
    assert op.payload == {"name": "New Bot", "permission_groups": []}
    assert op.preview["note"] == (
        "no permission groups — Kizen creates the account disabled until it has "
        "at least one."
    )


@respx.mock
def test_create_refuses_a_duplicate_name_case_insensitively():
    _mock_reads()
    with pytest.raises(PlanError) as exc:
        plan_create_service_account("EXAMPLE BOT")
    assert str(exc.value) == (
        f"service account 'EXAMPLE BOT' already exists (id {ACCOUNT_ID})."
    )


@respx.mock
def test_create_refuses_a_taken_derived_api_name():
    _mock_reads()
    with pytest.raises(PlanError) as exc:
        plan_create_service_account("Example-Bot")
    assert str(exc.value) == (
        "api_name 'example_bot' (derived from the name) is already used by "
        "service account 'Example Bot'."
    )


@respx.mock
def test_create_refuses_an_unknown_group():
    _mock_reads()
    with pytest.raises(PlanError, match="permission group id"):
        plan_create_service_account("New Bot", [UNKNOWN_GROUP_ID])


# ---------------------------------------------------------------------------
# plan_update_service_account
# ---------------------------------------------------------------------------


@respx.mock
def test_update_with_the_same_group_set_skips():
    account = copy.deepcopy(INTEGRATION)
    account["permission_groups"] = list(GROUPS)
    _mock_reads([account])
    (op,) = plan_update_service_account(
        ACCOUNT_ID, {"permission_groups": [GROUP_ID_2, GROUP_ID]}
    ).operations
    assert op.action == "skip"
    assert op.payload == {}


@respx.mock
def test_rename_keeps_the_current_api_name():
    _mock_reads()
    (op,) = plan_update_service_account(ACCOUNT_ID, {"name": "Renamed Bot"}).operations
    assert op.action == "update"
    assert op.payload == {"name": "Renamed Bot", "api_name": "example_bot"}


@respx.mock
def test_update_sends_only_the_keys_that_changed():
    _mock_reads()
    (op,) = plan_update_service_account(
        ACCOUNT_ID,
        {
            "name": "Example Bot",
            "api_name": "example_bot_v2",
            "permission_groups": [GROUP_ID_2],
        },
    ).operations
    assert op.payload == {
        "api_name": "example_bot_v2",
        "permission_groups": [GROUP_ID_2],
    }
    assert op.preview["diff"]["permission_groups"] == "['Integrations'] → ['Reporting']"


@respx.mock
def test_clearing_groups_is_allowed_and_notes_the_account_is_disabled():
    _mock_reads()
    (op,) = plan_update_service_account(
        ACCOUNT_ID, {"permission_groups": []}
    ).operations
    assert op.payload == {"permission_groups": []}
    assert "disables the account" in op.preview["note"]


@respx.mock
def test_update_refuses_an_unknown_group():
    _mock_reads()
    with pytest.raises(PlanError, match="permission group id"):
        plan_update_service_account(
            ACCOUNT_ID, {"permission_groups": [UNKNOWN_GROUP_ID]}
        )


@respx.mock
def test_update_of_a_missing_account_is_a_plan_error():
    respx.get(f"{SA_URL}/{NEW_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(PlanError, match="not found"):
        plan_update_service_account(NEW_ID, {"name": "x"})


@pytest.mark.parametrize(
    ("account", "kind"),
    [(AUTOMATION, "service_automation"), (PLUGIN, "service_automationplugin")],
)
@pytest.mark.parametrize("planner", ["update", "delete"])
@respx.mock
def test_update_and_delete_refuse_accounts_kizen_or_a_plugin_manages(
    account, kind, planner
):
    _mock_reads()
    with pytest.raises(PlanError) as exc:
        if planner == "update":
            plan_update_service_account(account["id"], {"name": "x"})
        else:
            plan_delete_service_account(account["id"])
    assert str(exc.value) == (
        f"'{account['name']}' is a {kind} account, which Kizen or a plugin "
        "manages. Only integration service accounts can be changed from the CLI."
    )


@respx.mock
def test_delete_plans_one_delete_with_the_archive_note():
    _mock_reads()
    (op,) = plan_delete_service_account(ACCOUNT_ID).operations
    assert (op.action, op.existing_uuid) == ("delete", ACCOUNT_ID)
    assert op.preview["note"] == (
        "Kizen archives the account; it disappears from every read and can't be "
        "restored through the API."
    )


# ---------------------------------------------------------------------------
# apply: the create result message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returned", "suffix"),
    [("new_bot", ""), ("new_bot_2", " (the plan predicted new_bot)")],
)
@respx.mock
def test_create_result_message_reports_api_name_and_never_the_key(returned, suffix):
    _mock_reads()
    respx.post(SA_URL).mock(
        return_value=httpx.Response(201, json=_created("New Bot", returned))
    )
    plan = plan_create_service_account("New Bot")
    result = plan_tools.apply_plan(plan)
    (r,) = result.results
    assert r.status == "ok" and r.server_uuid == NEW_ID
    assert r.message == (
        f"api_name {returned}; Kizen issued an API key, which the CLI does not show"
        + suffix
    )
    assert SENTINEL not in plan_tools.result_to_json(result)
    assert SENTINEL not in plan_tools.plan_to_json(plan)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [["--json"], []])
@respx.mock
def test_cli_create_never_prints_the_api_key(extra):
    _mock_reads()
    post = respx.post(SA_URL).mock(
        return_value=httpx.Response(201, json=_created("New Bot", "new_bot"))
    )
    result = runner.invoke(
        cli.app, ["service-accounts", "create", "--name", "New Bot", "--yes", *extra]
    )
    assert result.exit_code == 0, result.output
    assert post.called
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr
    assert "Kizen issued an API key" in result.stdout


@respx.mock
def test_cli_create_resolves_groups_by_name():
    _mock_reads()
    result = runner.invoke(
        cli.app,
        [
            "service-accounts",
            "create",
            "--name",
            "New Bot",
            "-g",
            "Integrations",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    op = json.loads(result.stdout)["operations"][0]
    assert op["payload"] == {"name": "New Bot", "permission_groups": [GROUP_ID]}


@respx.mock
def test_cli_list_json_emits_the_api_rows():
    _mock_reads()
    result = runner.invoke(cli.app, ["service-accounts", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ACCOUNTS


@respx.mock
def test_cli_list_csv_names_groups():
    _mock_reads()
    result = runner.invoke(cli.app, ["service-accounts", "list", "-o", "csv"])
    assert result.exit_code == 0, result.output
    assert "Example Bot,example_bot,service_integration,active,Integrations,1," in (
        result.stdout
    )


@respx.mock
def test_cli_get_by_api_name_shows_groups():
    _mock_reads()
    result = runner.invoke(cli.app, ["service-accounts", "get", "example_bot"])
    assert result.exit_code == 0, result.output
    assert "Integrations" in result.stdout
    assert "abc***" in result.stdout


@respx.mock
def test_cli_unknown_ref_ends_on_error_and_exits_1():
    _mock_reads()
    result = runner.invoke(cli.app, ["service-accounts", "delete", "nope", "--yes"])
    assert result.exit_code == 1
    assert result.stderr.startswith("error:")


def test_cli_update_refuses_group_with_clear_groups():
    result = runner.invoke(
        cli.app,
        ["service-accounts", "update", "x", "-g", "Integrations", "--clear-groups"],
    )
    assert result.exit_code == 2
    assert "error: pass --group or --clear-groups, not both." in result.stderr


@respx.mock
def test_cli_update_clear_groups_plans_an_empty_set():
    _mock_reads()
    result = runner.invoke(
        cli.app,
        [
            "service-accounts",
            "update",
            "Example Bot",
            "--clear-groups",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    op = json.loads(result.stdout)["operations"][0]
    assert op["payload"] == {"permission_groups": []}


@respx.mock
def test_cli_history_table_and_empty():
    _mock_reads()
    event = {
        "id": "e1",
        "created": "2026-10-27T12:00:00Z",
        "type_name": "employee_created",
        "type_display_name": "Team Member Invited",
        "initiator": {
            "email": "admin@example.com",
            "first_name": "A",
            "last_name": "B",
        },
    }
    route = respx.get(f"{SA_URL}/{ACCOUNT_ID}/history").mock(
        side_effect=[
            httpx.Response(200, json=_envelope([event])),
            httpx.Response(200, json=_envelope([])),
        ]
    )
    result = runner.invoke(
        cli.app, ["service-accounts", "history", "Example Bot", "-n", "3"]
    )
    assert result.exit_code == 0, result.output
    assert "Team Member Invited" in result.stdout
    assert "admin@example.com" in result.stdout
    assert dict(route.calls[0].request.url.params) == {"page_size": "3"}

    result = runner.invoke(cli.app, ["service-accounts", "history", "Example Bot"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No history."

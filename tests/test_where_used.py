"""Where-used: the shared normalizer, the delete-preview line, and
`kizen fields|roles where-used` and `kizen permissions group-where-used`.

The normalizer is fed every response shape captured live on 2026-11-11 —
field, role, permission group, automation and activity — so a new where-used
command adds an API call and no parsing.
"""

from __future__ import annotations

import json
import re

import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api.client import KizenAPIError
from kizen_builder.tools import references as ref_tools
from kizen_builder.tools.planners.fields import plan_delete_field
from kizen_builder.tools.references import normalize, preview_used_by, used_by_line
from tests.conftest import FAKE_BASE_URL, fake_get_object, load_fixture

runner = CliRunner()

PATIENTS_ID = "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"


def _field_id(api_name: str) -> str:
    obj = load_fixture("objects/patients.json")
    return next(f["id"] for f in obj["fields"] if f["api_name"] == api_name)


def _references_url(field: str) -> str:
    return (
        f"{FAKE_BASE_URL}/api/references/custom-objects/{PATIENTS_ID}"
        f"/fields/{_field_id(field)}"
    )


# ---------------------------------------------------------------------------
# normalize — every captured shape
# ---------------------------------------------------------------------------

# The non-field shapes, trimmed from the 2026-11-11 probes.
ROLE = {
    "automations": [
        {
            "id": "a1",
            "display_name": "all triggers",
            "can_view": True,
            "use_types": ["direct"],
            "steps": [
                {
                    "id": "s1",
                    "display_name": "assign_team_member - 30",
                    "type": "assign_team_member",
                }
            ],
            "conditions": [],
            "triggers": [],
            "variables": [],
        }
    ],
    "homepages": [
        {
            "id": "h1",
            "display_name": "Sample Homepage",
            "can_view": True,
            "dashlets": [{"id": "d1", "display_name": "[default]"}],
            "charts": [],
        }
    ],
    "team": {"count": 1, "can_view": True},
    "sharing_settings": [
        {
            "asset_type": "filter_group",
            "asset_id": "fg1",
            "display_name": "My Records",
            "use_types": ["admin"],
            "can_view": True,
        }
    ],
}
GROUP = {
    "filter_groups": [],
    "team": {"count": 0, "can_view": True},
    "service_accounts": [
        {"id": "sa1", "display_name": "Sample Service", "can_view": True}
    ],
    "roles": [{"id": "r1", "name": "Admin", "can_view": True}],
}
AUTOMATION = {
    "objects": [],
    "automations": [
        {
            "id": "a2",
            "display_name": "Link the organization",
            "can_view": True,
            "use_types": ["direct"],
            "steps": [
                {
                    "id": "s7",
                    "display_name": "start_automation - 7",
                    "type": "start_automation",
                },
                {
                    "id": "s11",
                    "display_name": "start_automation - 11",
                    "type": "start_automation",
                },
            ],
        }
    ],
    "messages": [
        {
            "id": None,
            "display_name": None,
            "can_view": True,
            "use_types": ["conditional_content"],
        }
    ],
}
ACTIVITY = {
    "scheduled_activities": 0,
    "logged_activities": 6,
    "automations": [
        {
            "id": "a3",
            "display_name": "Mark follow-ups done",
            "can_view": True,
            "steps": [],
            "triggers": [
                {
                    "id": "t1",
                    "display_name": "activity_logged - 1",
                    "type": "activity_logged",
                }
            ],
            "conditions": [],
            "variables": [],
            "goals": [],
        }
    ],
    "toolbar_templates": [],
}


def test_normalize_field_used():
    result = normalize(load_fixture("references/field_used.json"))
    assert result["counts"] == [{"label": "records with a value", "count": 1}]
    rows = [(r["section"], r["name"], r["where"]) for r in result["used_by"]]
    assert rows == [
        (
            "messages",
            "Lead follow-up with merge {{ entity",
            "use types: merge_field",
        ),
        (
            "automations",
            "Route leads by owner",
            "conditions: condition - 3, condition - 1",
        ),
        ("automations", "Escalate unowned leads", "conditions: condition - 1"),
        ("filter groups", "My Records", "type: records; object name: Inbound Leads"),
    ]
    assert result["used_by"][1]["id"] == "732c7874-4fa0-4ba6-8266-3f42ef201d74"
    assert all(r["can_view"] is True for r in result["used_by"])


def test_normalize_field_unused_keeps_zero_counts():
    result = normalize(load_fixture("references/field_unused.json"))
    assert result == {
        "counts": [{"label": "records with a value", "count": 0}],
        "used_by": [],
    }


def test_normalize_role_reads_team_dict_and_asset_id():
    result = normalize(ROLE)
    assert result["counts"] == [{"label": "team members", "count": 1}]
    by_section = {r["section"]: r for r in result["used_by"]}
    assert by_section["automations"]["where"] == (
        "steps: assign_team_member - 30; use types: direct"
    )
    assert by_section["homepages"]["where"] == "dashlets: [default]"
    sharing = by_section["sharing settings"]
    assert sharing["id"] == "fg1"
    assert sharing["where"] == "asset type: filter_group; use types: admin"


def test_normalize_group_reads_name_key():
    result = normalize(GROUP)
    assert result["counts"] == [{"label": "team members", "count": 0}]
    assert [(r["section"], r["name"], r["id"]) for r in result["used_by"]] == [
        ("service accounts", "Sample Service", "sa1"),
        ("roles", "Admin", "r1"),
    ]


def test_normalize_automation_names_null_message_unnamed():
    result = normalize(AUTOMATION)
    auto, message = result["used_by"]
    assert auto["where"] == (
        "steps: start_automation - 7, start_automation - 11; use types: direct"
    )
    assert (message["name"], message["id"], message["where"]) == (
        "(unnamed)",
        "",
        "use types: conditional_content",
    )


def test_normalize_activity_counts_and_triggers():
    result = normalize(ACTIVITY)
    assert result["counts"] == [
        {"label": "scheduled activities", "count": 0},
        {"label": "logged activities", "count": 6},
    ]
    (row,) = result["used_by"]
    assert row["where"] == "triggers: activity_logged - 1"


def test_normalize_humanizes_an_unknown_count():
    assert normalize({"open_tasks": 2})["counts"] == [
        {"label": "open tasks", "count": 2}
    ]


# ---------------------------------------------------------------------------
# used_by_line / preview_used_by
# ---------------------------------------------------------------------------


def test_used_by_line_nothing_found():
    unused = normalize(load_fixture("references/field_unused.json"))
    assert used_by_line(unused, "x") == "nothing found"


def test_used_by_line_caps_names_and_skips_zero_counts():
    body = {
        "scheduled_activities": 0,
        "logged_activities": 2,
        "homepages": [{"id": str(i), "display_name": f"H{i}"} for i in range(5)],
    }
    assert used_by_line(normalize(body), "kizen x where-used y") == (
        "homepages: 5 (H0, H1, H2, +2 more); logged activities: 2"
        " — run `kizen x where-used y` for the list"
    )


def test_used_by_line_names_a_repeated_name_once():
    body = {
        "sharing_settings": [
            {"asset_id": str(i), "display_name": "My Records"} for i in range(6)
        ]
    }
    assert used_by_line(normalize(body), "x") == (
        "sharing settings: 6 (My Records) — run `x` for the list"
    )


def test_preview_used_by_keeps_names_literal():
    body = {"messages": [{"id": "m1", "display_name": "Q3 [/draft]"}]}
    assert preview_used_by(lambda client: body, "x") == (
        "messages: 1 (Q3 [/draft]) — run `x` for the list"
    )


def test_preview_used_by_reports_a_403_without_credentials_advice():
    def fetch(client):
        raise KizenAPIError(
            403,
            "denied — credentials for 'testenv' may be invalid or revoked. "
            "Run `kizen init --env testenv` to re-enter them.",
            body=load_fixture("references/field_403.json"),
        )

    assert preview_used_by(fetch, "x") == (
        "unknown (HTTP 403: You do not have permission to perform this action.)"
    )


def test_preview_used_by_reports_a_network_error():
    def fetch(client):
        raise KizenAPIError(0, "network error: timed out")

    assert preview_used_by(fetch, "x") == "unknown (HTTP 0: network error: timed out)"


# ---------------------------------------------------------------------------
# fields delete — the preview warns, never refuses
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_delete_field_still_plans_when_lookup_is_refused(patch_live_lookups):
    respx.get(_references_url("gender")).respond(
        403, json=load_fixture("references/field_403.json")
    )
    plan = plan_delete_field("patients", "gender")
    (op,) = plan.operations
    assert op.preview["used_by"] == (
        "unknown (HTTP 403: You do not have permission to perform this action.)"
    )
    assert (op.action, op.kind, op.key) == ("delete", "field", "patients.gender")
    assert op.existing_uuid == _field_id("gender")
    assert op.parent_object_uuid == PATIENTS_ID
    assert op.payload == {}


@respx.mock
def test_fields_delete_dry_run_shows_used_by(patch_live_lookups):
    respx.get(_references_url("gender")).respond(
        json=load_fixture("references/field_unused.json")
    )
    result = runner.invoke(
        cli.app, ["fields", "delete", "patients", "gender", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.stdout)["operations"]
    assert op["preview"]["used_by"] == "nothing found"


# ---------------------------------------------------------------------------
# kizen fields where-used
# ---------------------------------------------------------------------------


@pytest.fixture
def objects_from_fixtures(monkeypatch):
    monkeypatch.setattr(ref_tools, "get_object", fake_get_object)


def _plain(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m|[│┃]", "", text).split())


@respx.mock
def test_where_used_table(objects_from_fixtures):
    respx.get(_references_url("gender")).respond(
        json=load_fixture("references/field_used.json")
    )
    result = runner.invoke(cli.app, ["fields", "where-used", "patients", "gender"])
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert text.startswith("1 records with a value")
    assert "Used by" in text
    assert "Lead follow-up with merge {{ entity" in text
    assert "Route leads by owner conditions: condition - 3, condition - 1" in text


@respx.mock
def test_where_used_unused(objects_from_fixtures):
    respx.get(_references_url("gender")).respond(
        json=load_fixture("references/field_unused.json")
    )
    result = runner.invoke(cli.app, ["fields", "where-used", "patients", "gender"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "Kizen reports nothing using patients.gender."


@respx.mock
def test_where_used_json(objects_from_fixtures):
    respx.get(_references_url("gender")).respond(
        json=load_fixture("references/field_used.json")
    )
    result = runner.invoke(
        cli.app, ["fields", "where-used", "patients", "gender", "-o", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["target"] == {
        "object": "patients",
        "field": "gender",
        "field_id": _field_id("gender"),
    }
    assert data["counts"] == [{"label": "records with a value", "count": 1}]
    assert len(data["used_by"]) == 4


@respx.mock
def test_where_used_csv(objects_from_fixtures):
    respx.get(_references_url("gender")).respond(
        json=load_fixture("references/field_used.json")
    )
    result = runner.invoke(
        cli.app, ["fields", "where-used", "patients", "gender", "-o", "csv"]
    )
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "section,name,where,id"
    assert lines[2] == (
        "automations,Route leads by owner,"
        '"conditions: condition - 3, condition - 1",'
        "732c7874-4fa0-4ba6-8266-3f42ef201d74"
    )
    assert len(lines) == 5


@respx.mock
def test_where_used_403_names_the_built_in_fields(objects_from_fixtures):
    respx.get(_references_url("gender")).respond(
        403, json=load_fixture("references/field_403.json")
    )
    result = runner.invoke(cli.app, ["fields", "where-used", "patients", "gender"])
    assert result.exit_code == 1
    assert _plain(result.stderr) == (
        "error: Kizen refused the where-used lookup for patients.gender "
        "(HTTP 403: You do not have permission to perform this action.). "
        "It refuses it for the built-in created, updated and display_name fields."
    )
    assert "kizen init" not in result.output


@respx.mock
def test_fields_delete_preview_prints_a_closing_tag_literally(patch_live_lookups):
    respx.get(_references_url("gender")).respond(
        json={"messages": [{"id": "m1", "display_name": "Q3 [/draft]"}]}
    )
    result = runner.invoke(
        cli.app, ["fields", "delete", "patients", "gender", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "messages: 1 (Q3 [/draft])" in _plain(result.stdout)


@respx.mock
def test_fields_delete_json_preview_keeps_a_closing_tag(patch_live_lookups):
    respx.get(_references_url("gender")).respond(
        json={"messages": [{"id": "m1", "display_name": "Q3 [/x]"}]}
    )
    result = runner.invoke(
        cli.app, ["fields", "delete", "patients", "gender", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.stdout)["operations"]
    assert "messages: 1 (Q3 [/x])" in op["preview"]["used_by"]


def test_where_used_unknown_object_names_it(monkeypatch):
    def not_found(api_name):
        raise KizenAPIError(
            404,
            "No CustomObject matches the given query.",
            body={"detail": "No CustomObject matches the given query."},
        )

    monkeypatch.setattr(ref_tools, "get_object", not_found)
    result = runner.invoke(cli.app, ["fields", "where-used", "nope", "name"])
    assert result.exit_code == 1
    assert _plain(result.stderr) == (
        "error: object 'nope' not found: No CustomObject matches the given query."
    )


def test_where_used_unknown_field(objects_from_fixtures):
    result = runner.invoke(cli.app, ["fields", "where-used", "patients", "nope"])
    assert result.exit_code == 1
    stderr = _plain(result.stderr)
    assert stderr.startswith("error: field 'nope' not found on 'patients'.")
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# kizen roles where-used / kizen permissions group-where-used
# ---------------------------------------------------------------------------

ROLE_LIST = load_fixture("permissions/role_list.json")
GROUP_LIST = load_fixture("permissions/permission_group_list.json")
ROLE_ID = ROLE_LIST["results"][0]["id"]
GROUP_ID = GROUP_LIST["results"][0]["id"]
ROLE_REFS = f"{FAKE_BASE_URL}/api/references/roles/{ROLE_ID}"
GROUP_REFS = f"{FAKE_BASE_URL}/api/references/permission-groups/{GROUP_ID}"


@pytest.fixture
def roles_and_groups():
    respx.get(f"{FAKE_BASE_URL}/api/role").respond(json=ROLE_LIST)
    respx.get(f"{FAKE_BASE_URL}/api/permission-group").respond(json=GROUP_LIST)
    respx.get(f"{FAKE_BASE_URL}/api/role/{ROLE_ID}").respond(
        json=load_fixture("permissions/role_detail.json")
    )
    respx.get(f"{FAKE_BASE_URL}/api/permission-group/{GROUP_ID}").respond(
        json=load_fixture("permissions/permission_group_detail.json")
    )


@respx.mock
def test_roles_where_used_table(roles_and_groups):
    respx.get(ROLE_REFS).respond(json=load_fixture("references/role_used.json"))
    result = runner.invoke(cli.app, ["roles", "where-used", "Sales Rep"])
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert text.startswith("1 team members")
    assert "all triggers steps: assign_team_member - 30; use types: direct" in text
    assert "Team Homepage dashlets: [default]" in text
    assert "My Records asset type: filter_group; use types: admin" in text


@respx.mock
def test_roles_where_used_by_uuid_json(roles_and_groups):
    respx.get(ROLE_REFS).respond(json=load_fixture("references/role_used.json"))
    result = runner.invoke(cli.app, ["roles", "where-used", ROLE_ID, "-o", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["target"] == {"role": "Sales Rep", "id": ROLE_ID}
    assert data["counts"] == [{"label": "team members", "count": 1}]
    assert [r["section"] for r in data["used_by"]] == [
        "automations",
        "homepages",
        "sharing settings",
    ]


@respx.mock
def test_roles_where_used_unused(roles_and_groups):
    respx.get(ROLE_REFS).respond(json=load_fixture("references/role_unused.json"))
    result = runner.invoke(cli.app, ["roles", "where-used", "Sales Rep"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "Kizen reports nothing using role 'Sales Rep'."


@respx.mock
def test_roles_where_used_csv(roles_and_groups):
    respx.get(ROLE_REFS).respond(json=load_fixture("references/role_used.json"))
    result = runner.invoke(cli.app, ["roles", "where-used", "Sales Rep", "-o", "csv"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "section,name,where,id",
        "automations,all triggers,steps: assign_team_member - 30; use types: direct,"
        "c81687b1-bd87-4c15-bdd8-e79fe5507491",
        "homepages,Team Homepage,dashlets: [default],"
        "0212d6b2-ca89-49a6-a93e-64eca59e8ff8",
        "sharing settings,My Records,asset type: filter_group; use types: admin,"
        "a9397dd6-7a2b-446e-a6c3-014975c1dde3",
    ]


@respx.mock
def test_roles_where_used_unknown_name_lists_roles(roles_and_groups):
    result = runner.invoke(cli.app, ["roles", "where-used", "Nope"])
    assert result.exit_code == 1
    assert _plain(result.stderr) == (
        "error: role 'Nope' not found. Available: ['Sales Rep']"
    )


@respx.mock
def test_roles_where_used_403(roles_and_groups):
    respx.get(ROLE_REFS).respond(403, json=load_fixture("references/field_403.json"))
    result = runner.invoke(cli.app, ["roles", "where-used", "Sales Rep"])
    assert result.exit_code == 1
    assert _plain(result.stderr) == (
        "error: Kizen refused the where-used lookup for role 'Sales Rep' "
        "(HTTP 403: You do not have permission to perform this action.)."
    )


@respx.mock
def test_group_where_used_table(roles_and_groups):
    respx.get(GROUP_REFS).respond(json=load_fixture("references/group_used.json"))
    result = runner.invoke(cli.app, ["permissions", "group-where-used", "Sample Group"])
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert text.startswith("1 team members")
    assert "service accounts Workflow Service" in text
    assert "roles Admin" in text


@respx.mock
def test_group_where_used_csv(roles_and_groups):
    respx.get(GROUP_REFS).respond(json=load_fixture("references/group_used.json"))
    result = runner.invoke(
        cli.app, ["permissions", "group-where-used", "Sample Group", "-o", "csv"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "section,name,where,id",
        "service accounts,Workflow Service,,306b6bd1-4780-45fa-a8e7-2f9675b7e6dd",
        "service accounts,Code Step Service,,afa714ac-33c3-49a9-87b0-be75430a7ec6",
        "roles,Admin,,937c92bb-d5dd-4990-bcfc-437757e28070",
    ]


@respx.mock
def test_group_where_used_unknown_name_lists_groups(roles_and_groups):
    result = runner.invoke(cli.app, ["permissions", "group-where-used", "Nope"])
    assert result.exit_code == 1
    assert _plain(result.stderr) == (
        "error: permission group 'Nope' not found. "
        "Available: ['Sample Group', 'Other Group']"
    )


@respx.mock
def test_roles_delete_dry_run_shows_used_by(roles_and_groups):
    respx.get(ROLE_REFS).respond(json=load_fixture("references/role_unused.json"))
    result = runner.invoke(
        cli.app, ["roles", "delete", "Sales Rep", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.stdout)["operations"]
    assert op["preview"]["used_by"] == "nothing found"


@respx.mock
def test_roles_delete_still_plans_when_lookup_fails(roles_and_groups):
    respx.get(ROLE_REFS).respond(500, json={"detail": "boom"})
    result = runner.invoke(
        cli.app, ["roles", "delete", "Sales Rep", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.stdout)["operations"]
    assert op["preview"]["used_by"] == "unknown (HTTP 500: boom)"
    assert (op["action"], op["kind"], op["existing_uuid"]) == (
        "delete",
        "role",
        ROLE_ID,
    )


@respx.mock
def test_group_delete_still_plans_when_lookup_is_refused(roles_and_groups):
    respx.get(GROUP_REFS).respond(403, json=load_fixture("references/field_403.json"))
    result = runner.invoke(
        cli.app, ["permissions", "group-delete", "Sample Group", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert (
        "unknown (HTTP 403: You do not have permission to perform this action.)" in text
    )

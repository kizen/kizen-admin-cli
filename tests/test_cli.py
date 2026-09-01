"""CLI surface tests: argument wiring, output formats, exit codes."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx
from rich.console import Console
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.cli import objects as objects_cli
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools import forms as form_tools
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools import permissions as perm_tools
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools import records as record_tools
from kizen_builder.tools.planners import automations as auto_planners
from kizen_builder.tools.planners import fields as field_planners
from kizen_builder.tools.planners import forms as form_planners
from kizen_builder.tools.planners import objects as object_planners
from kizen_builder.tools.planners import records as record_planners
from tests.conftest import FAKE_BASE_URL, load_fixture

runner = CliRunner()


def test_objects_list_json(monkeypatch):
    fake = [
        {
            "env": "testenv",
            "id": "abc",
            "api_name": "invoice",
            "display_name": "Invoices",
            "entity_name": "Invoice",
            "object_type": "standard",
            "deleted": False,
        }
    ]
    monkeypatch.setattr(obj_tools, "list_objects", lambda: fake)
    result = runner.invoke(cli.app, ["objects", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fake


def test_objects_list_table(monkeypatch):
    fake = [
        {
            "env": "testenv",
            "id": "abcd1234-x",
            "api_name": "invoice",
            "display_name": "Invoices",
            "entity_name": "Invoice",
            "object_type": "standard",
            "deleted": False,
        }
    ]
    monkeypatch.setattr(obj_tools, "list_objects", lambda: fake)
    result = runner.invoke(cli.app, ["objects", "list"])
    assert result.exit_code == 0
    assert "invoice" in result.stdout
    assert "Invoices" in result.stdout


def _fake_object_with_options():
    # Real object/category/field/option ids and labels from the `encounters`
    # object in the pinned `cli-testing` sandbox (see Verification) — two
    # dropdown fields with 11 options each, plus a long-api_name text field
    # and a relationship field with a long target, so the table's natural
    # width meaningfully exceeds the console's fixed 220 columns and this
    # test actually exercises the width-pressure column-collapse regression,
    # not just "does an options column exist."
    return {
        "env": "testenv",
        "id": "98649f6d-b809-4c9a-980d-8ee54708a8a6",
        "api_name": "encounters",
        "display_name": "Encounters",
        "entity_name": "Encounter",
        "object_type": "standard",
        "stages": None,
        "categories": [
            {"id": "180d7be3-e952-4d98-8efe-871004cdf350", "name": "Reason for Visit"},
            {
                "id": "1213bae3-8e98-4783-b484-7b4b03688b84",
                "name": "Encounter Type & Class",
            },
            {"id": "a97b03d9-3e9f-43bd-9efc-4fe998f941df", "name": "Hospitalization"},
            {"id": "e5f7548b-516d-44ab-a1bc-67e2b11273b0", "name": "Diagnosis Links"},
        ],
        "fields": [
            {
                "id": "9bb801d2-8a40-4934-8e29-1d33a8000fa0",
                "api_name": "clinical_notes",
                "display_name": "Clinical Notes",
                "field_type": "longtext",
                "category_id": "180d7be3-e952-4d98-8efe-871004cdf350",
                "is_required": False,
                "deleted": False,
                "relation": None,
                "relation_target": None,
                "relation_cardinality": None,
                "options": None,
            },
            {
                "id": "c1919144-ad35-45d2-87b5-b8b6b941a41b",
                "api_name": "service_type",
                "display_name": "Service Type",
                "field_type": "dropdown",
                "category_id": "1213bae3-8e98-4783-b484-7b4b03688b84",
                "is_required": False,
                "deleted": False,
                "relation": None,
                "relation_target": None,
                "relation_cardinality": None,
                "options": [
                    {
                        "id": "6c424e05-c021-4267-a1aa-21e1bb9d9780",
                        "name": "Primary Care",
                        "code": "",
                    },
                    {
                        "id": "185ce7d1-88d6-40d2-9d49-0c86af1ce99f",
                        "name": "Cardiology",
                        "code": "",
                    },
                    {
                        "id": "09ee0582-c904-47b8-ac43-edf7f29d224a",
                        "name": "Orthopedics",
                        "code": "",
                    },
                    {
                        "id": "7e8f5020-d35b-487a-b2e8-77ffbfa91823",
                        "name": "Neurology",
                        "code": "",
                    },
                    {
                        "id": "ce58a00b-7fd4-4863-b1e0-7e446bbff332",
                        "name": "Oncology",
                        "code": "",
                    },
                    {
                        "id": "b0c63ec2-7b46-4d9f-be9a-e0e25dac1072",
                        "name": "Behavioral Health",
                        "code": "",
                    },
                    {
                        "id": "b1cf9d6a-db75-462f-9ced-247277ee9aae",
                        "name": "OB/GYN",
                        "code": "",
                    },
                    {
                        "id": "f29ed578-3e2b-40ce-abf4-015a12af71b8",
                        "name": "Pediatrics",
                        "code": "",
                    },
                    {
                        "id": "a0f239a9-3bf0-4ce4-ae22-7505e7ee3d18",
                        "name": "Radiology",
                        "code": "",
                    },
                    {
                        "id": "a7fab285-15b7-4882-b20c-3d708d9b3c0e",
                        "name": "Laboratory",
                        "code": "",
                    },
                    {
                        "id": "5d92c5db-f75a-4979-a1c4-359b2219c9ad",
                        "name": "Other",
                        "code": "",
                    },
                ],
            },
            {
                "id": "c7df5b14-7bfb-4976-90ae-ef6f1486a8f6",
                "api_name": "discharge_disposition",
                "display_name": "Discharge Disposition",
                "field_type": "dropdown",
                "category_id": "a97b03d9-3e9f-43bd-9efc-4fe998f941df",
                "is_required": False,
                "deleted": False,
                "relation": None,
                "relation_target": None,
                "relation_cardinality": None,
                "options": [
                    {
                        "id": "d1e158c6-cec3-4ce1-b149-151b792b816c",
                        "name": "Home / Self Care",
                        "code": "",
                    },
                    {
                        "id": "2e4e5cf8-5825-4ac6-bb75-c5b89336302c",
                        "name": "Home with Home Health",
                        "code": "",
                    },
                    {
                        "id": "f9be7a9a-e1a8-4f5b-b879-8d7048867c72",
                        "name": "Skilled Nursing Facility",
                        "code": "",
                    },
                    {
                        "id": "58314125-fcbf-41c4-ab16-56d762a0e382",
                        "name": "Inpatient Rehab",
                        "code": "",
                    },
                    {
                        "id": "37049eb5-d99f-4264-9603-e558f88f3ebb",
                        "name": "Long-Term Care",
                        "code": "",
                    },
                    {
                        "id": "4ffe7165-9cea-4412-90dc-a7b3abb6c5f7",
                        "name": "Transferred to Another Hospital",
                        "code": "",
                    },
                    {
                        "id": "06474da6-5c75-4c0e-8368-62d42bdc9c32",
                        "name": "Hospice - Home",
                        "code": "",
                    },
                    {
                        "id": "19d35c5f-83cf-4eb4-a19c-06d017b0ec02",
                        "name": "Hospice - Facility",
                        "code": "",
                    },
                    {
                        "id": "d522ecd7-d1d9-498d-9a3f-126be7ac0cd6",
                        "name": "Left Against Medical Advice",
                        "code": "",
                    },
                    {
                        "id": "0e1271dd-169c-4b19-91fc-7126828ac2fc",
                        "name": "Expired",
                        "code": "",
                    },
                    {
                        "id": "d24e5b5e-4e3e-4861-b64d-0d620ddd6b0e",
                        "name": "Other",
                        "code": "",
                    },
                ],
            },
            {
                "id": "0a7fc18c-211f-4b48-9265-34b502b73070",
                "api_name": "primary_diagnosis_description",
                "display_name": "Primary Diagnosis Description",
                "field_type": "text",
                "category_id": "e5f7548b-516d-44ab-a1bc-67e2b11273b0",
                "is_required": False,
                "deleted": False,
                "relation": None,
                "relation_target": None,
                "relation_cardinality": None,
                "options": None,
            },
            {
                "id": "ae78e103-ab10-47d5-9dbc-730c26ae4882",
                "api_name": "conditions",
                "display_name": "Conditions",
                "field_type": "relationship",
                "category_id": "1213bae3-8e98-4783-b484-7b4b03688b84",
                "is_required": False,
                "deleted": False,
                "relation": "many_to_many",
                "relation_target": "conditions",
                "relation_cardinality": "many_to_many",
                "options": None,
            },
        ],
    }


def test_objects_get_table_shows_full_option_uuids(monkeypatch):
    fake = _fake_object_with_options()
    monkeypatch.setattr(obj_tools, "get_object", lambda api_name: fake)
    result = runner.invoke(cli.app, ["objects", "get", "encounters"])
    assert result.exit_code == 0
    assert "6c424e05-c021-4267-a1aa-21e1bb9d9780" in result.stdout
    assert "185ce7d1-88d6-40d2-9d49-0c86af1ce99f" in result.stdout
    assert "4ffe7165-9cea-4412-90dc-a7b3abb6c5f7" in result.stdout
    assert "Primary Care" in result.stdout
    assert "Transferred to Another Hospital" in result.stdout
    assert "…" not in result.stdout


def test_objects_get_csv_shows_full_option_uuids(monkeypatch):
    fake = _fake_object_with_options()
    monkeypatch.setattr(obj_tools, "get_object", lambda api_name: fake)
    result = runner.invoke(cli.app, ["objects", "get", "encounters", "--output", "csv"])
    assert result.exit_code == 0
    assert "6c424e05-c021-4267-a1aa-21e1bb9d9780" in result.stdout
    assert "4ffe7165-9cea-4412-90dc-a7b3abb6c5f7" in result.stdout
    assert "Primary Care" in result.stdout


def test_objects_get_json_options_unchanged(monkeypatch):
    fake = _fake_object_with_options()
    monkeypatch.setattr(obj_tools, "get_object", lambda api_name: fake)
    result = runner.invoke(cli.app, ["objects", "get", "encounters", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["fields"][1]["options"] == fake["fields"][1]["options"]
    assert data["fields"][2]["options"] == fake["fields"][2]["options"]


def test_objects_get_table_survives_narrower_console(monkeypatch):
    # The shared console is fixed at width=220 in production, but Rich's
    # column-collapse algorithm only stops shrinking a column once it's
    # *narrower* than the console — a table that just barely fits at 220
    # gives no evidence the no_wrap protection is doing anything. Forcing a
    # narrower console is how you tell "protected" apart from "happened not
    # to need it": at width=150 the naive rendering this item replaced
    # (no `no_wrap`, no `crop=False`) ellipsis-truncates every UUID in this
    # fixture, while the current rendering does not.
    fake = _fake_object_with_options()
    monkeypatch.setattr(obj_tools, "get_object", lambda api_name: fake)
    monkeypatch.setattr(objects_cli, "console", Console(width=150, color_system=None))
    result = runner.invoke(cli.app, ["objects", "get", "encounters"])
    assert result.exit_code == 0
    assert "6c424e05-c021-4267-a1aa-21e1bb9d9780" in result.stdout
    assert "4ffe7165-9cea-4412-90dc-a7b3abb6c5f7" in result.stdout
    assert "…" not in result.stdout


def test_records_list_forwards_search_and_limit(monkeypatch):
    seen = {}

    def fake_search(object_api_name, filters=None, search=None, limit=100):
        seen.update(object_api_name=object_api_name, search=search, limit=limit)
        return []

    monkeypatch.setattr(record_tools, "search_records", fake_search)
    result = runner.invoke(
        cli.app, ["records", "list", "tax_lot", "--search", "main", "-n", "7"]
    )
    assert result.exit_code == 0
    assert seen == {"object_api_name": "tax_lot", "search": "main", "limit": 7}


def test_automations_get_raw_emits_full_response(monkeypatch):
    raw = load_fixture("automations/two_code_steps.raw.json")
    monkeypatch.setattr(
        auto_tools,
        "get_automation",
        lambda api_name: {"api_name": api_name, "raw": raw},
    )
    result = runner.invoke(
        cli.app, ["automations", "get", "test_two_code_steps", "--raw"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == raw


def test_records_list_csv_flattens_fields(monkeypatch):
    records = load_fixture("records/list_tax_lot.json")
    monkeypatch.setattr(record_tools, "search_records", lambda *a, **k: records)
    result = runner.invoke(cli.app, ["records", "list", "tax_lot", "-o", "csv"])
    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    header = lines[0].split(",")
    assert header[0] == "id"
    assert "ticker_symbol" in header  # a field was flattened into a column
    assert len(lines) == 1 + len(records)  # header + one row per record


def test_json_flag_and_output_json_are_equivalent(monkeypatch):
    records = load_fixture("records/list_tax_lot.json")
    monkeypatch.setattr(record_tools, "search_records", lambda *a, **k: records)
    a = runner.invoke(cli.app, ["records", "list", "tax_lot", "--json"])
    b = runner.invoke(cli.app, ["records", "list", "tax_lot", "-o", "json"])
    assert a.exit_code == b.exit_code == 0
    assert json.loads(a.stdout) == json.loads(b.stdout) == records


def test_records_list_forwards_fields(monkeypatch):
    seen = {}

    def fake_search(
        object_api_name, filters=None, search=None, limit=100, field_names=None
    ):
        seen.update(field_names=field_names)
        return []

    monkeypatch.setattr(record_tools, "search_records", fake_search)
    result = runner.invoke(
        cli.app,
        ["records", "list", "tax_lot", "--fields", "ticker_symbol,purchase_price"],
    )
    assert result.exit_code == 0
    assert seen["field_names"] == ["ticker_symbol", "purchase_price"]


def test_records_list_table_shows_requested_field_columns(monkeypatch):
    records = load_fixture("records/list_tax_lot.json")
    monkeypatch.setattr(record_tools, "search_records", lambda *a, **k: records)
    result = runner.invoke(
        cli.app, ["records", "list", "tax_lot", "--fields", "ticker_symbol"]
    )
    assert result.exit_code == 0
    assert "ticker_symbol" in result.stdout
    assert "VTI" in result.stdout  # the fixture's ticker_symbol value


def test_records_list_unknown_field_rejected_before_table(monkeypatch):
    def fake_search(
        object_api_name, filters=None, search=None, limit=100, field_names=None
    ):
        raise LookupError(
            f"field 'bogus_field' not found on '{object_api_name}'. "
            "Available: ['name', 'ticker_symbol']"
        )

    monkeypatch.setattr(record_tools, "search_records", fake_search)
    result = runner.invoke(
        cli.app, ["records", "list", "tax_lot", "--fields", "bogus_field"]
    )
    assert result.exit_code == 1
    assert "bogus_field" in result.stderr
    assert "Available" in result.stderr
    assert "record(s)" not in result.stdout  # no table rendered on validation failure


# Shaped like the tools layer's own output once field_names has narrowed the
# server's response: only the requested field plus the auto-added `name`,
# never the object's other fields.
_FIELD_SCOPED_RECORDS = [
    {
        "id": "r1",
        "fields": {
            "u1": {"name": "ticker_symbol", "value": "VTI"},
            "u2": {"name": "name", "value": "Lot A"},
        },
    }
]


def test_records_list_csv_shows_id_name_and_requested_fields(monkeypatch):
    monkeypatch.setattr(
        record_tools, "search_records", lambda *a, **k: _FIELD_SCOPED_RECORDS
    )
    result = runner.invoke(
        cli.app,
        ["records", "list", "tax_lot", "--fields", "ticker_symbol", "-o", "csv"],
    )
    assert result.exit_code == 0
    header = result.stdout.strip().splitlines()[0].split(",")
    assert header == ["id", "ticker_symbol", "name"]


def test_records_list_json_shows_id_name_and_requested_fields(monkeypatch):
    monkeypatch.setattr(
        record_tools, "search_records", lambda *a, **k: _FIELD_SCOPED_RECORDS
    )
    result = runner.invoke(
        cli.app, ["records", "list", "tax_lot", "--fields", "ticker_symbol", "--json"]
    )
    assert result.exit_code == 0
    field_names = {f["name"] for f in json.loads(result.stdout)[0]["fields"].values()}
    assert field_names == {"ticker_symbol", "name"}


def test_invalid_output_format_is_usage_error(monkeypatch):
    monkeypatch.setattr(obj_tools, "list_objects", lambda: [])
    result = runner.invoke(cli.app, ["objects", "list", "-o", "yaml"])
    assert result.exit_code == 2


def test_runs_list_csv_uses_untruncated_ids(monkeypatch):
    execs = load_fixture("executions/list_record_test.json")
    monkeypatch.setattr(auto_tools, "list_executions", lambda *a, **k: execs)
    result = runner.invoke(
        cli.app, ["automations", "runs", "list", "record_test", "-o", "csv"]
    )
    assert result.exit_code == 0
    # Full execution_id present (table view truncates to 8 chars + ellipsis).
    assert execs[0]["execution_id"] in result.stdout


def test_retired_run_verbs_are_gone(monkeypatch):
    """The old execution(s) verbs and the show/history split no longer exist —
    runs are `list` + `view` now."""
    monkeypatch.setattr(auto_tools, "list_executions", lambda *a, **k: [])
    for argv in (
        ["automations", "executions", "record_test"],
        ["automations", "runs", "show", "x"],
        ["automations", "runs", "history", "x"],
    ):
        assert runner.invoke(cli.app, argv).exit_code != 0


def test_start_var_flags_reach_the_tool(monkeypatch):
    """--vars-json merges with repeatable --var (--var wins on conflict)."""
    captured = {}

    def fake_start(api_name, record_id, *, variables=None):
        captured["variables"] = variables
        return {
            "execution_id": "e1",
            "record_id": record_id,
            "client_id": None,
            "variable_overrides": [],
            "raw": {},
        }

    monkeypatch.setattr(auto_tools, "start_automation", fake_start)
    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--vars-json",
            '{"a": 1, "b": "x"}',
            "--var",
            "b=override",
            "--var",
            "c=3",
        ],
    )
    assert result.exit_code == 0
    assert captured["variables"] == {"a": 1, "b": "override", "c": "3"}


def test_start_rejects_malformed_var(monkeypatch):
    monkeypatch.setattr(auto_tools, "start_automation", lambda *a, **k: {})
    result = runner.invoke(
        cli.app, ["automations", "start", "flow", "-r", "rec-1", "--var", "novalue"]
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# `start --wait [--show-logs]` (BCLI-021) — composes BCLI-012's
# wait_for_execution()/get_execution_history(); tests mock the tool layer
# (test_start_var_flags_reach_the_tool's own pattern) for the composition
# itself, and respx + a fake clock (test_runs.py's wait_for_execution
# pattern) for the streaming behaviour that only shows up across real polls.
# ---------------------------------------------------------------------------

_START_RESULT = {
    "execution_id": "e0000000-0000-4000-8000-000000000099",
    "record_id": "rec-1",
    "client_id": None,
    "variable_overrides": [],
    "raw": {},
}


def _wait_raw(execution_id, status):
    """A minimal raw GET .../automation-execution/{id} response."""
    return {
        "id": execution_id,
        "status": status,
        "automation": {"api_name": "flow"},
        "record": {"id": "rec-1"},
        "created": "2026-08-13T00:00:00Z",
        "updated": "2026-08-13T00:00:00Z",
    }


def test_start_wait_composes_start_and_wait_in_order(monkeypatch):
    """`start --wait` calls start_automation() then wait_for_execution(),
    in that order, with the first call's execution_id flowing into the
    second — the tool-composition contract, not the HTTP wire."""
    calls = []

    def fake_start(api_name, record_id, *, client_id=None, variables=None):
        calls.append(("start", api_name, record_id))
        return dict(_START_RESULT)

    def fake_wait(execution_id, *, timeout, poll_interval, on_poll=None):
        calls.append(("wait", execution_id))
        return {
            "execution_id": execution_id,
            "status": "completed",
            "timed_out": False,
            "polls": 1,
        }

    monkeypatch.setattr(auto_tools, "start_automation", fake_start)
    monkeypatch.setattr(auto_tools, "wait_for_execution", fake_wait)

    result = runner.invoke(
        cli.app, ["automations", "start", "flow", "-r", "rec-1", "--wait"]
    )

    assert result.exit_code == 0
    assert calls == [
        ("start", "flow", "rec-1"),
        ("wait", _START_RESULT["execution_id"]),
    ]


def test_start_without_wait_never_calls_wait_for_execution(monkeypatch):
    """Without --wait, exactly one call to start_automation() and none to
    wait_for_execution() — the byte-for-byte-unchanged requirement."""
    calls = []
    monkeypatch.setattr(
        auto_tools,
        "start_automation",
        lambda *a, **k: calls.append(1) or dict(_START_RESULT),
    )

    def boom(*a, **k):
        raise AssertionError("wait_for_execution must not run without --wait")

    monkeypatch.setattr(auto_tools, "wait_for_execution", boom)

    result = runner.invoke(cli.app, ["automations", "start", "flow", "-r", "rec-1"])

    assert result.exit_code == 0
    assert len(calls) == 1


@respx.mock
def test_start_wait_streams_new_history_rows_once(monkeypatch):
    """A history row prints exactly once, the poll it first appears on — not
    once per poll, and not again on a later poll that re-fetches it."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    exec_id = _START_RESULT["execution_id"]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    exec_base = f"{FAKE_BASE_URL}/api/automation2/automation-execution"
    respx.get(f"{exec_base}/{exec_id}").mock(
        side_effect=[
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "completed")),
        ]
    )
    trigger_row = {
        "id": "row-1",
        "trigger": {"type": "manual", "description": "Manual", "deleted": False},
        "step": None,
        "status": "completed",
        "execution_time_ms": 5,
        "created": "2026-08-13T00:00:00Z",
        "updated": "2026-08-13T00:00:00Z",
        "error": None,
        "error_description": None,
        "detailed_log": None,
    }
    respx.get(f"{exec_base}/{exec_id}/history").mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(200, json=[trigger_row]),
            httpx.Response(200, json=[trigger_row]),
        ]
    )

    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--wait",
            "--poll-interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.count("Manual") == 1


@respx.mock
def test_start_wait_heartbeats_during_a_gap_with_no_new_rows(monkeypatch):
    """No new history row across several polls, but the fixed heartbeat
    interval has elapsed: a heartbeat line prints (not a step line)."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    exec_id = _START_RESULT["execution_id"]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    exec_base = f"{FAKE_BASE_URL}/api/automation2/automation-execution"
    respx.get(f"{exec_base}/{exec_id}").mock(
        side_effect=[
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "completed")),
        ]
    )
    respx.get(f"{exec_base}/{exec_id}/history").mock(
        return_value=httpx.Response(200, json=[])
    )
    # A fake clock that advances 20s on every read. _RunStream reads
    # time.monotonic() once (no new rows) or more (a heartbeat fires) per
    # poll; wait_for_execution itself never reads it here (--timeout 0
    # disables the deadline check that would otherwise call it too).
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 20.0
        return clock["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--wait",
            "--timeout",
            "0",
            "--poll-interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "still active" in result.stdout
    assert "poll 2" in result.stdout


@respx.mock
def test_start_wait_without_show_logs_never_prints_detailed_log(monkeypatch):
    """--wait without --show-logs streams the step-status line but never a
    detailed_log body, even when a row carries one."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    exec_id = _START_RESULT["execution_id"]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    exec_base = f"{FAKE_BASE_URL}/api/automation2/automation-execution"
    respx.get(f"{exec_base}/{exec_id}").mock(
        return_value=httpx.Response(200, json=_wait_raw(exec_id, "completed"))
    )
    log_row = {
        "id": "row-2",
        "trigger": None,
        "step": {"type": "code_step", "description": "Run script", "deleted": False},
        "status": "completed",
        "execution_time_ms": 10,
        "created": "2026-08-13T00:00:00Z",
        "updated": "2026-08-13T00:00:00Z",
        "error": None,
        "error_description": None,
        "detailed_log": {"stdout": "SENTINEL_LOG_BODY", "traceback": None},
    }
    respx.get(f"{exec_base}/{exec_id}/history").mock(
        return_value=httpx.Response(200, json=[log_row])
    )

    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--wait",
            "--poll-interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Run script" in result.stdout
    assert "SENTINEL_LOG_BODY" not in result.stdout


@respx.mock
def test_start_wait_survives_a_transient_history_fetch_error(monkeypatch):
    """A dropped connection or a 5xx on `_RunStream`'s own
    get_execution_history() call must not abort the wait — that's the same
    bug BCLI-012 fixed for the status poll, one endpoint over. The run still
    reaches its correct terminal status and exit code; only that one poll's
    render is skipped."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    exec_id = _START_RESULT["execution_id"]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    exec_base = f"{FAKE_BASE_URL}/api/automation2/automation-execution"
    respx.get(f"{exec_base}/{exec_id}").mock(
        side_effect=[
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "active")),
            httpx.Response(200, json=_wait_raw(exec_id, "completed")),
        ]
    )
    trigger_row = {
        "id": "row-1",
        "trigger": {"type": "manual", "description": "Manual", "deleted": False},
        "step": None,
        "status": "completed",
        "execution_time_ms": 5,
        "created": "2026-08-13T00:00:00Z",
        "updated": "2026-08-13T00:00:00Z",
        "error": None,
        "error_description": None,
        "detailed_log": None,
    }
    respx.get(f"{exec_base}/{exec_id}/history").mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(503, json={"detail": "temporarily unavailable"}),
            httpx.Response(200, json=[trigger_row]),
        ]
    )

    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--wait",
            "--poll-interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.count("Manual") == 1


@respx.mock
def test_start_wait_history_4xx_surfaces_immediately(monkeypatch):
    """A 4xx from get_execution_history() (e.g. a malformed request) is not
    transient — it must surface right away as a command error, not be
    retried until the wait's own --timeout is reached."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    exec_id = _START_RESULT["execution_id"]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    exec_base = f"{FAKE_BASE_URL}/api/automation2/automation-execution"
    status_route = respx.get(f"{exec_base}/{exec_id}").mock(
        return_value=httpx.Response(200, json=_wait_raw(exec_id, "active"))
    )
    history_route = respx.get(f"{exec_base}/{exec_id}/history").mock(
        return_value=httpx.Response(400, json={"detail": "bad request"})
    )

    result = runner.invoke(
        cli.app,
        [
            "automations",
            "start",
            "flow",
            "-r",
            "rec-1",
            "--wait",
            "--poll-interval",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert status_route.call_count == 1
    assert history_route.call_count == 1


def test_start_wait_json_output_is_valid_json_only(monkeypatch):
    """--json --wait: stdout is exactly one JSON blob — no interleaved
    streaming text — carrying status/timed_out/polls alongside start's
    existing keys."""
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": _START_RESULT["execution_id"],
            "status": "completed",
            "timed_out": False,
            "polls": 2,
        },
    )

    result = runner.invoke(
        cli.app, ["automations", "start", "flow", "-r", "rec-1", "--wait", "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["execution_id"] == _START_RESULT["execution_id"]
    assert payload["record_id"] == _START_RESULT["record_id"]
    assert payload["raw"] == _START_RESULT["raw"]
    assert payload["status"] == "completed"
    assert payload["timed_out"] is False
    assert payload["polls"] == 2


def test_start_show_logs_implies_wait_and_adds_steps_to_json(monkeypatch):
    """--show-logs alone (no --wait) still waits, and --json --show-logs
    adds the full step history under "steps"."""
    history = [
        {
            "id": "row-1",
            "kind": "trigger",
            "type": "manual",
            "description": "Manual",
            "status": "completed",
            "duration_ms": 5,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "detailed_log": None,
        }
    ]
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": _START_RESULT["execution_id"],
            "status": "completed",
            "timed_out": False,
            "polls": 1,
        },
    )
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: history)

    result = runner.invoke(
        cli.app,
        ["automations", "start", "flow", "-r", "rec-1", "--show-logs", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"  # proves --show-logs implied --wait
    assert payload["steps"] == history


def test_start_wait_exit_code_reuses_runs_view_wait_mapping(monkeypatch):
    """`start --wait`'s exit code comes from the same completed/failed/
    cancelled/timeout/paused mapping `runs view --wait` uses — a failed run
    exits 1, matching that shared logic rather than a second copy of it."""
    monkeypatch.setattr(
        auto_tools, "start_automation", lambda *a, **k: dict(_START_RESULT)
    )
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": _START_RESULT["execution_id"],
            "status": "failed",
            "timed_out": False,
            "polls": 3,
        },
    )

    result = runner.invoke(
        cli.app, ["automations", "start", "flow", "-r", "rec-1", "--wait"]
    )

    assert result.exit_code == 1


def test_runs_view_rejects_non_uuid(monkeypatch):
    """An api_name (or truncated id) must fail with a pointer to a real id,
    not a bare 404 from the API — and must not even call the tool."""
    called = []
    monkeypatch.setattr(
        auto_tools, "get_execution", lambda *a, **k: called.append(1) or {}
    )
    result = runner.invoke(cli.app, ["automations", "runs", "view", "llm_comparison"])
    assert result.exit_code == 1
    assert "not an execution UUID" in result.stderr
    assert not called  # guarded before any API call


def test_runs_view_no_steps_skips_history(monkeypatch):
    """view fetches summary + step trace; --no-steps skips the history call."""
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    hist_calls = []
    monkeypatch.setattr(
        auto_tools,
        "get_execution",
        lambda *a, **k: {"execution_id": uuid, "status": "completed"},
    )
    monkeypatch.setattr(
        auto_tools,
        "get_execution_history",
        lambda *a, **k: hist_calls.append(1) or [],
    )

    both = runner.invoke(cli.app, ["automations", "runs", "view", uuid])
    assert both.exit_code == 0
    assert len(hist_calls) == 1  # steps fetched by default

    summary_only = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--no-steps"]
    )
    assert summary_only.exit_code == 0
    assert len(hist_calls) == 1  # unchanged: history not fetched again


def test_runs_view_without_wait_makes_exactly_one_status_call(monkeypatch):
    """Without --wait, behaviour is unchanged: one get_execution call, no
    wait_for_execution call at all, exit 0 whatever the status."""
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    calls = []
    monkeypatch.setattr(
        auto_tools,
        "get_execution",
        lambda *a, **k: calls.append(1) or {"execution_id": uuid, "status": "active"},
    )
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: [])

    def boom(*a, **k):
        raise AssertionError("wait_for_execution must not run without --wait")

    monkeypatch.setattr(auto_tools, "wait_for_execution", boom)

    result = runner.invoke(cli.app, ["automations", "runs", "view", uuid])
    assert result.exit_code == 0
    assert len(calls) == 1


def test_runs_view_wait_rejects_negative_timeout(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    result = runner.invoke(
        cli.app,
        ["automations", "runs", "view", uuid, "--wait", "--timeout", "-1"],
    )
    assert result.exit_code == 2


@pytest.mark.parametrize("poll_interval", ["-1", "0"])
def test_runs_view_wait_rejects_non_positive_poll_interval(monkeypatch, poll_interval):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    result = runner.invoke(
        cli.app,
        [
            "automations",
            "runs",
            "view",
            uuid,
            "--wait",
            "--poll-interval",
            poll_interval,
        ],
    )
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "status,timed_out,expected_exit",
    [
        ("completed", False, 0),
        ("failed", False, 1),
        ("cancelled", False, 1),
        ("paused", False, 3),
        ("paused_by_automation", False, 3),
        ("paused_by_failure", False, 3),
        ("active", True, 3),
    ],
)
def test_runs_view_wait_exit_codes(monkeypatch, status, timed_out, expected_exit):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": status,
            "timed_out": timed_out,
            "polls": 3,
            "automation_api_name": "flow",
            "record_id": None,
            "started_at": None,
            "finished_at": None,
        },
    )
    result = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--wait", "--no-steps"]
    )
    assert result.exit_code == expected_exit


def test_runs_view_wait_timeout_message_avoids_failure_language(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": "active",
            "timed_out": True,
            "polls": 42,
            "automation_api_name": "flow",
            "record_id": None,
            "started_at": None,
            "finished_at": None,
        },
    )
    result = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--wait", "--no-steps"]
    )
    assert result.exit_code == 3
    out = result.stdout.lower()
    assert "failed" not in out
    assert "stalled" not in out
    assert "stuck" not in out
    assert "may still complete" in out
    assert f"kizen automations runs view {uuid}" in out
    assert "--timeout" in out


def test_runs_view_wait_paused_names_resume(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": "paused",
            "timed_out": False,
            "polls": 3,
            "automation_api_name": "flow",
            "record_id": None,
            "started_at": None,
            "finished_at": None,
        },
    )
    result = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--wait", "--no-steps"]
    )
    assert result.exit_code == 3
    assert f"kizen automations runs resume {uuid}" in result.stdout


def test_runs_view_wait_paused_by_failure_says_needs_a_human(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": "paused_by_failure",
            "timed_out": False,
            "polls": 3,
            "automation_api_name": "flow",
            "record_id": None,
            "started_at": None,
            "finished_at": None,
        },
    )
    result = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--wait", "--no-steps"]
    )
    assert result.exit_code == 3
    assert "may still complete" not in result.stdout.lower()
    assert f"kizen automations runs resume {uuid}" in result.stdout


def test_runs_view_wait_success_renders_summary_and_json_still_works(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "wait_for_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": "completed",
            "timed_out": False,
            "polls": 3,
            "automation_api_name": "flow",
            "record_id": None,
            "started_at": "2026-08-13T00:00:00Z",
            "finished_at": "2026-08-13T00:05:00Z",
        },
    )
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: [])
    result = runner.invoke(
        cli.app, ["automations", "runs", "view", uuid, "--wait", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"


def test_runs_view_surfaces_paused_on_step(monkeypatch):
    """paused_on_step is surfaced whenever the execution GET carries it, with
    or without --wait — it names the exact step and whether it branches."""
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    monkeypatch.setattr(
        auto_tools,
        "get_execution",
        lambda *a, **k: {
            "execution_id": uuid,
            "status": "paused_by_failure",
            "paused_on_step": {
                "id": "52ced4b6-0000-4000-8000-000000000009",
                "type": "create_related_entity",
                "branching_step": False,
                "label": "Action: Create Related Entity",
            },
        },
    )
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: [])
    result = runner.invoke(cli.app, ["automations", "runs", "view", uuid, "--no-steps"])
    assert result.exit_code == 0
    assert "Action: Create Related Entity" in result.stdout


def test_runs_list_table_shows_full_execution_id(monkeypatch):
    execs = load_fixture("executions/list_record_test.json")
    monkeypatch.setattr(auto_tools, "list_executions", lambda *a, **k: execs)
    result = runner.invoke(cli.app, ["automations", "runs", "list", "record_test"])
    assert result.exit_code == 0
    # Full id present and not the truncated "…" form the old table used.
    assert execs[0]["execution_id"] in result.stdout


def test_runs_logs_rejects_non_uuid(monkeypatch):
    called = []
    monkeypatch.setattr(
        auto_tools, "get_execution_history", lambda *a, **k: called.append(1) or []
    )
    result = runner.invoke(cli.app, ["automations", "runs", "logs", "llm_comparison"])
    assert result.exit_code == 1
    assert "not an execution UUID" in result.stderr
    assert not called


def test_runs_logs_renders_known_detailed_log_shapes(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    entries = [
        {
            "id": "e1",
            "kind": "step",
            "type": "code_step",
            "description": "Run Python 3.13 Code",
            "status": "failed",
            "detailed_log": {"stdout": "", "traceback": "NameError: name 'foo'"},
        },
        {
            "id": "e2",
            "kind": "step",
            "type": "code_step",
            "description": "Send welcome email",
            "status": "completed",
            "detailed_log": {"logs": ["starting", "sent"]},
        },
        {
            "id": "e3",
            "kind": "step",
            "type": "initialize_variable",
            "description": "org_match = 'No'",
            "status": "completed",
            "detailed_log": None,
        },
    ]
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: entries)
    result = runner.invoke(cli.app, ["automations", "runs", "logs", uuid])
    assert result.exit_code == 0
    assert "NameError: name 'foo'" in result.stdout
    assert "starting" in result.stdout
    assert "sent" in result.stdout
    # The row with no detailed_log isn't rendered at all.
    assert "initialize_variable" not in result.stdout


def test_runs_logs_code_step_full_audit_shape_is_not_dropped(monkeypatch):
    """A real code_step's detailed_log carries logs alongside inputs/values/
    http_requests/duration — the narrow `"logs" in log` check must not
    intercept it and silently drop the rest (the shape §4 of the item's
    Context calls the most diagnostically valuable)."""
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    entries = [
        {
            "id": "e1",
            "kind": "step",
            "type": "code_step",
            "description": "Call sender API",
            "status": "completed",
            "detailed_log": {
                "logs": ["hello"],
                "inputs": {"x": 1},
                "values": {"y": 2},
                "duration": 2.246,
                "http_requests": {"count": 1, "requests": [{"url": "https://x"}]},
            },
        },
    ]
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: entries)
    result = runner.invoke(cli.app, ["automations", "runs", "logs", uuid])
    assert result.exit_code == 0
    assert "http_requests" in result.stdout
    assert "inputs" in result.stdout
    assert "values" in result.stdout
    assert "duration" in result.stdout
    assert "hello" in result.stdout


def test_runs_logs_no_logs_exits_0_with_pointer(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    entries = [
        {
            "id": "e1",
            "kind": "step",
            "type": "initialize_variable",
            "description": "x",
            "status": "completed",
            "detailed_log": None,
        }
    ]
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: entries)
    result = runner.invoke(cli.app, ["automations", "runs", "logs", uuid])
    assert result.exit_code == 0
    assert "outputs.log" in result.stdout
    assert "code-steps" in result.stdout


def test_runs_logs_json_emits_raw_blobs(monkeypatch):
    uuid = "2461cd64-c82c-406c-a6fd-f27e4918e31e"
    entries = [
        {
            "id": "e1",
            "kind": "step",
            "type": "code_step",
            "description": "d",
            "status": "completed",
            "detailed_log": {"logs": ["hi"]},
        },
        {
            "id": "e2",
            "kind": "step",
            "type": "initialize_variable",
            "description": "d2",
            "status": "completed",
            "detailed_log": None,
        },
    ]
    monkeypatch.setattr(auto_tools, "get_execution_history", lambda *a, **k: entries)
    result = runner.invoke(cli.app, ["automations", "runs", "logs", uuid, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["index"] == 1
    assert payload[0]["detailed_log"] == {"logs": ["hi"]}


def test_config_error_exits_nonzero(monkeypatch):
    from kizen_builder.config import ConfigError

    def boom():
        raise ConfigError("No profile named 'sandbox' in credentials.toml.")

    monkeypatch.setattr(obj_tools, "list_objects", boom)
    result = runner.invoke(cli.app, ["objects", "list"])
    assert result.exit_code == 1
    assert "No profile named 'sandbox'" in result.stderr


def test_apply_reads_plan_from_stdin_with_yes(monkeypatch):
    from kizen_builder.tools.plans import ApplyResult, OperationResult

    applied = {}

    def fake_apply(plan):
        applied["plan_id"] = plan.id
        return ApplyResult(
            plan_id=plan.id,
            env=plan.env,
            results=[
                OperationResult(
                    key=op.key,
                    kind=op.kind,
                    action=op.action,
                    status="ok",
                    server_uuid="srv-1",
                )
                for op in plan.operations
            ],
        )

    monkeypatch.setattr(plan_tools, "apply_plan", fake_apply)

    from kizen_builder.tools.plans import Plan, PlanOperation, plan_to_json

    plan = Plan.build(
        env="testenv",
        summary="test",
        operations=[
            PlanOperation(
                action="create",
                kind="field",
                key="invoice.total",
                preview={},
                payload={"name": "total"},
                parent_object_uuid="11111111-1111-4111-8111-111111111111",
            )
        ],
    )
    result = runner.invoke(
        cli.app, ["apply", "--yes", "--json"], input=plan_to_json(plan)
    )
    assert result.exit_code == 0, result.output
    assert applied["plan_id"] == plan.id
    out = json.loads(result.stdout)
    assert out["results"][0]["status"] == "ok"


def test_apply_enriches_known_enum_choice_failures(monkeypatch):
    """BCLI-015: `kizen apply` is the documented second half of plan/apply
    (the dry-run message tells users to feed --dry-run --json output to it),
    so a failed automation op must get the same known-choices enrichment
    `_run_mutation` gives a direct --yes create."""
    from kizen_builder.tools.plans import ApplyResult, OperationResult

    def fake_apply(plan):
        return ApplyResult(
            plan_id=plan.id,
            env=plan.env,
            results=[
                OperationResult(
                    key=op.key,
                    kind=op.kind,
                    action=op.action,
                    status="failed",
                    message='HTTP 400: "bogus" is not a valid choice.',
                    raw={
                        "step_assign_owner": {
                            "action_create_related_entity": {
                                "new_entity_owner_type": [
                                    '"bogus" is not a valid choice.'
                                ],
                            },
                        },
                    },
                )
                for op in plan.operations
            ],
        )

    monkeypatch.setattr(plan_tools, "apply_plan", fake_apply)

    from kizen_builder.tools.plans import Plan, PlanOperation, plan_to_json

    plan = Plan.build(
        env="testenv",
        summary="test",
        operations=[
            PlanOperation(
                action="create",
                kind="automation",
                key="onboarding_flow",
                preview={},
                payload={"name": "onboarding flow"},
            )
        ],
    )
    result = runner.invoke(
        cli.app, ["apply", "--yes", "--json"], input=plan_to_json(plan)
    )
    assert result.exit_code == 1, result.output
    out = json.loads(result.stdout)
    message = out["results"][0]["message"]
    assert "assign_from_context_record" in message
    assert "newly_assigned_owner" in message


def test_apply_rejects_garbage_plan():
    result = runner.invoke(cli.app, ["apply", "--yes"], input="{not json")
    assert result.exit_code == 2
    assert "error parsing plan" in result.stderr


# ---------------------------------------------------------------------------
# mutation verbs (plan → preview → confirm → apply)
# ---------------------------------------------------------------------------


def _fake_plan(action="create"):
    from kizen_builder.tools.plans import Plan, PlanOperation

    return Plan.build(
        env="testenv",
        summary="test plan",
        operations=[
            PlanOperation(
                action=action,
                kind="field",
                key="invoice.total",
                preview={"api_name": "total"},
                payload={"name": "total"},
                parent_object_uuid="11111111-1111-4111-8111-111111111111",
                existing_uuid=None
                if action == "create"
                else "22222222-2222-4222-8222-222222222222",
            )
        ],
    )


def _ok_result(plan):
    from kizen_builder.tools.plans import ApplyResult, OperationResult

    return ApplyResult(
        plan_id=plan.id,
        env=plan.env,
        results=[
            OperationResult(
                key=op.key,
                kind=op.kind,
                action=op.action,
                status="skipped" if op.action == "skip" else "ok",
                server_uuid=None if op.action == "skip" else "srv-1",
            )
            for op in plan.operations
        ],
    )


FIELDS_CREATE_ARGS = [
    "fields",
    "create",
    "invoice",
    "--api-name",
    "total",
    "--name",
    "Total",
    "--type",
    "money",
    "--category",
    "Main",
]


def test_fields_create_dry_run_json_emits_plan_and_never_applies(monkeypatch):
    plan = _fake_plan()
    monkeypatch.setattr(field_planners, "plan_create_field", lambda **kw: plan)
    monkeypatch.setattr(
        plan_tools,
        "apply_plan",
        lambda p: (_ for _ in ()).throw(
            AssertionError("apply_plan called on --dry-run")
        ),
    )
    result = runner.invoke(cli.app, [*FIELDS_CREATE_ARGS, "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["id"] == plan.id
    assert out["operations"][0]["key"] == "invoice.total"


def test_fields_create_confirm_abort_never_applies(monkeypatch):
    monkeypatch.setattr(field_planners, "plan_create_field", lambda **kw: _fake_plan())
    monkeypatch.setattr(
        plan_tools,
        "apply_plan",
        lambda p: (_ for _ in ()).throw(
            AssertionError("apply_plan called after abort")
        ),
    )
    result = runner.invoke(cli.app, FIELDS_CREATE_ARGS, input="n\n")
    assert result.exit_code == 1
    assert "aborted" in result.output


def test_fields_create_yes_applies_and_emits_result_json(monkeypatch):
    applied = {}

    def fake_apply(plan):
        applied["plan_id"] = plan.id
        return _ok_result(plan)

    monkeypatch.setattr(field_planners, "plan_create_field", lambda **kw: _fake_plan())
    monkeypatch.setattr(plan_tools, "apply_plan", fake_apply)
    result = runner.invoke(cli.app, [*FIELDS_CREATE_ARGS, "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert applied  # applied without prompting
    out = json.loads(result.stdout)  # stdout is pure result JSON (preview → stderr)
    assert out["results"][0]["status"] == "ok"


def test_update_with_no_diff_skips_without_prompting(monkeypatch):
    """An all-skip plan applies (as no-ops) without a confirmation prompt."""
    monkeypatch.setattr(
        field_planners, "plan_update_field", lambda **kw: _fake_plan(action="skip")
    )
    monkeypatch.setattr(plan_tools, "apply_plan", _ok_result)
    # no --yes and no input: a prompt would fail the invocation
    result = runner.invoke(
        cli.app, ["fields", "update", "invoice", "total", "--name", "Total"]
    )
    assert result.exit_code == 0, result.output
    assert "no changes" in result.output


def test_automations_update_from_stdin_requires_yes(monkeypatch):
    monkeypatch.setattr(
        auto_planners,
        "plan_update_automation",
        lambda spec: _fake_plan(action="update"),
    )
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "update"], input=spec)
    assert result.exit_code == 2
    assert "cannot prompt" in result.stderr


def test_automations_create_dry_run_reads_spec_from_stdin(monkeypatch):
    seen = {}

    def fake_planner(spec):
        seen["spec"] = spec
        return _fake_plan()

    monkeypatch.setattr(auto_planners, "plan_create_automation", fake_planner)
    spec = {"api_name": "x", "name": "X", "type": "global", "steps": []}
    result = runner.invoke(
        cli.app,
        ["automations", "create", "--dry-run", "--json"],
        input=json.dumps(spec),
    )
    assert result.exit_code == 0, result.output
    assert seen["spec"] == spec
    assert json.loads(result.stdout)["summary"] == "test plan"


def test_automations_diff_reads_spec_from_stdin(monkeypatch):
    seen = {}

    def fake_diff(spec):
        seen["spec"] = spec
        return {
            "env": "testenv",
            "api_name": "x",
            "id": "auto-1",
            "revision": 4,
            "diff": [],
        }

    monkeypatch.setattr(auto_planners, "diff_automation", fake_diff)
    spec = {"api_name": "x", "name": "X", "type": "global", "steps": []}
    result = runner.invoke(
        cli.app, ["automations", "diff", "x"], input=json.dumps(spec)
    )
    assert result.exit_code == 0, result.output
    assert seen["spec"] == spec
    assert "no changes" in result.output


def test_automations_diff_json_emits_full_result(monkeypatch):
    fake_result = {
        "env": "testenv",
        "api_name": "x",
        "id": "auto-1",
        "revision": 4,
        "diff": [
            {
                "path": "steps.76af48bd.action_code_step.script",
                "before": "a",
                "after": "b",
            }
        ],
    }
    monkeypatch.setattr(auto_planners, "diff_automation", lambda spec: fake_result)
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "x", "--json"], input=spec)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == fake_result


def test_automations_diff_renders_change_lines(monkeypatch):
    fake_result = {
        "env": "testenv",
        "api_name": "x",
        "id": "auto-1",
        "revision": 4,
        "diff": [
            {
                "path": "steps.76af48bd.action_code_step.script",
                "before": "a",
                "after": "b",
            }
        ],
    }
    monkeypatch.setattr(auto_planners, "diff_automation", lambda spec: fake_result)
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "x"], input=spec)
    assert result.exit_code == 0, result.output
    assert "1 change" in result.output
    assert "steps.76af48bd.action_code_step.script" in result.output


def test_automations_diff_rejects_mismatched_api_name(monkeypatch):
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "not-x"], input=spec)
    assert result.exit_code == 2
    assert "does not match" in result.stderr


def test_automations_diff_propagates_plan_error(monkeypatch):
    def fake_diff(spec):
        raise auto_planners.PlanError("no automation with api_name 'x'")

    monkeypatch.setattr(auto_planners, "diff_automation", fake_diff)
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "x"], input=spec)
    assert result.exit_code == 1
    assert "no automation with api_name" in result.stderr


def test_plan_star_commands_are_gone():
    result = runner.invoke(cli.app, ["plan-create-field", "invoice"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# objects create — object type / pipeline flag
# ---------------------------------------------------------------------------


def _capture_object_planner(monkeypatch):
    seen = {}

    def fake(obj_dict):
        seen["obj"] = obj_dict
        return _fake_plan()

    monkeypatch.setattr(object_planners, "plan_create_object", fake)
    return seen


def test_objects_create_defaults_to_standard(monkeypatch):
    seen = _capture_object_planner(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "objects",
            "create",
            "--api-name",
            "invoice",
            "--name",
            "Invoices",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["obj"]["object_type"] == "standard"


def test_objects_create_pipeline_flag(monkeypatch):
    seen = _capture_object_planner(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "objects",
            "create",
            "--api-name",
            "deal",
            "--name",
            "Deals",
            "--pipeline",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["obj"]["object_type"] == "pipeline"


def test_objects_create_object_type_flag(monkeypatch):
    seen = _capture_object_planner(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "objects",
            "create",
            "--api-name",
            "deal",
            "--name",
            "Deals",
            "--object-type",
            "pipeline",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["obj"]["object_type"] == "pipeline"


def test_objects_create_rejects_bad_object_type(monkeypatch):
    _capture_object_planner(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "objects",
            "create",
            "--api-name",
            "x",
            "--name",
            "X",
            "--object-type",
            "bogus",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "object-type" in result.stderr


# ---------------------------------------------------------------------------
# fields create — bulk (spec-file / stdin) mode
# ---------------------------------------------------------------------------


def _capture_fields_planner(monkeypatch):
    seen = {}

    def fake(object_api_name, pairs):
        seen["object"] = object_api_name
        seen["pairs"] = pairs
        return _fake_plan()

    monkeypatch.setattr(field_planners, "plan_create_fields", fake)
    return seen


def test_fields_create_bulk_from_stdin(monkeypatch):
    seen = _capture_fields_planner(monkeypatch)
    spec = [
        {"name": "A", "api_name": "field_a", "field_type": "text", "category": "Main"},
        {
            "name": "B",
            "api_name": "field_b",
            "field_type": "integer",
            "category": "Other",
        },
    ]
    result = runner.invoke(
        cli.app,
        ["fields", "create", "invoice", "--dry-run", "--json"],
        input=json.dumps(spec),
    )
    assert result.exit_code == 0, result.output
    assert seen["object"] == "invoice"
    assert seen["pairs"] == [
        ({"name": "A", "api_name": "field_a", "field_type": "text"}, "Main"),
        ({"name": "B", "api_name": "field_b", "field_type": "integer"}, "Other"),
    ]


def test_fields_create_bulk_from_spec_file(monkeypatch, tmp_path):
    seen = _capture_fields_planner(monkeypatch)
    spec = {
        "category": "Main",
        "fields": [
            {"name": "A", "api_name": "field_a", "field_type": "text"},
        ],
    }
    spec_path = tmp_path / "fields.json"
    spec_path.write_text(json.dumps(spec))
    result = runner.invoke(
        cli.app,
        ["fields", "create", "invoice", "--spec-file", str(spec_path), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    # spec-level category applied to the field that didn't set its own.
    assert seen["pairs"] == [
        ({"name": "A", "api_name": "field_a", "field_type": "text"}, "Main")
    ]


def test_fields_create_bulk_category_flag_is_default(monkeypatch):
    seen = _capture_fields_planner(monkeypatch)
    spec = [{"name": "A", "api_name": "field_a", "field_type": "text"}]
    result = runner.invoke(
        cli.app,
        ["fields", "create", "invoice", "--category", "Fallback", "--dry-run"],
        input=json.dumps(spec),
    )
    assert result.exit_code == 0, result.output
    assert seen["pairs"][0][1] == "Fallback"


def test_fields_create_rejects_flags_and_spec_together(monkeypatch):
    _capture_fields_planner(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "fields",
            "create",
            "invoice",
            "--api-name",
            "x",
            "--spec-file",
            "/nonexistent",
        ],
    )
    # --api-name + --spec-file is contradictory; error before reading the file.
    assert result.exit_code == 2
    assert "not both" in result.stderr


def test_fields_create_reserved_name_gives_clean_spec_error():
    """A reserved field api_name surfaces as a clean one-liner, not a raw
    pydantic traceback (the real planner validates before any live call)."""
    result = runner.invoke(
        cli.app,
        [
            "fields",
            "create",
            "patients",
            "--api-name",
            "business_phone",
            "--name",
            "Business Phone",
            "--type",
            "phonenumber",
            "--category",
            "Patient Info",
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
    assert "spec error:" in result.stderr
    assert "Kizen-reserved" in result.stderr
    assert "Traceback" not in result.stderr


def test_fields_create_single_missing_flags_errors(monkeypatch):
    monkeypatch.setattr(field_planners, "plan_create_field", lambda **kw: _fake_plan())
    # No spec, no --api-name, stdin is a tty (no input) → clean usage error.
    result = runner.invoke(cli.app, ["fields", "create", "invoice"])
    assert result.exit_code == 2
    assert "single-field create needs" in result.stderr


def test_records_list_filter_spec_rendered(monkeypatch, kizen):
    seen = {}

    def fake_search(object_api_name, filters=None, search=None, limit=100):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(record_tools, "search_records", fake_search)
    result = runner.invoke(
        cli.app,
        [
            "records",
            "list",
            "policies_policy",
            "--filter",
            '{"all": [{"field": "ftext", "op": "=", "value": "abc"}]}',
        ],
    )
    assert result.exit_code == 0, result.output
    (group,) = seen["filters"]
    (clause,) = group["filters"]
    assert clause["condition"] == "="
    assert clause["value"] == "abc"


def test_records_list_filter_bad_json_exits_2(kizen):
    result = runner.invoke(
        cli.app, ["records", "list", "tax_lot", "--filter", "{not json"]
    )
    assert result.exit_code == 2
    assert "error parsing --filter JSON" in result.stderr


def test_records_list_filter_invalid_spec_exits_2(kizen):
    result = runner.invoke(
        cli.app,
        [
            "records",
            "list",
            "policies_policy",
            "--filter",
            '{"all": [{"field": "ftext", "op": "regex", "value": "x"}]}',
        ],
    )
    assert result.exit_code == 2
    assert "filter error" in result.stderr


def test_records_archive_dry_run_forwards_ids(monkeypatch):
    def fake_plan_archive(object_api_name, record_ids):
        assert object_api_name == "patients"
        assert record_ids == ["rec-1", "rec-2"]
        return plan_tools.Plan.build(
            env="testenv", summary="Archive 2 record(s)", operations=[]
        )

    monkeypatch.setattr(record_planners, "plan_archive_records", fake_plan_archive)
    result = runner.invoke(
        cli.app, ["records", "archive", "patients", "rec-1", "rec-2", "--dry-run"]
    )
    assert result.exit_code == 0, result.output


def test_records_archive_requires_at_least_one_id():
    result = runner.invoke(cli.app, ["records", "archive", "patients"])
    assert result.exit_code == 2
    assert "pass at least one record UUID" in result.stderr


def test_records_unarchive_dry_run_forwards_ids(monkeypatch):
    def fake_plan_unarchive(object_api_name, record_ids):
        assert object_api_name == "patients"
        assert record_ids == ["rec-1"]
        return plan_tools.Plan.build(
            env="testenv", summary="Unarchive 1 record(s)", operations=[]
        )

    monkeypatch.setattr(record_planners, "plan_unarchive_records", fake_plan_unarchive)
    result = runner.invoke(
        cli.app, ["records", "unarchive", "patients", "rec-1", "--dry-run"]
    )
    assert result.exit_code == 0, result.output


def test_records_unarchive_requires_at_least_one_id():
    result = runner.invoke(cli.app, ["records", "unarchive", "patients"])
    assert result.exit_code == 2
    assert "pass at least one record UUID" in result.stderr


# ---------------------------------------------------------------------------
# profiles: init, envs list, checksum refusal surfaced through a command
# ---------------------------------------------------------------------------


def test_init_stores_profile_and_pins_directory(monkeypatch, tmp_path):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)  # pin is written to cwd
    # Fourth prompt is now "Environment" (a curated name), not a free-typed URL.
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "alpha", "--skip-validation"],
        input="apikey\nAAAA\nuser1\ngo\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("alpha")
    assert stored is not None and stored.business_id == "AAAA"
    assert stored.base_url == "https://app.go.kizen.com"

    # Read the pin file directly (autouse fixture stubs find_pin to None).
    import tomllib

    pin_file = tmp_path / profiles.PIN_RELPATH
    assert pin_file.is_file()
    data = tomllib.loads(pin_file.read_text())
    assert data == {"profile": "alpha", "business_id": "AAAA"}


def test_init_environment_picker_resolves_named_host(monkeypatch, tmp_path):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "beta", "--skip-validation"],
        input="apikey\nBBBB\nuser1\nfmo\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("beta")
    assert stored is not None and stored.base_url == "https://app.fmo.kizen.com"


def test_init_environment_picker_rejects_bare_enter(monkeypatch, tmp_path):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "zeta", "--skip-validation"],
        input="apikey\nZZZZ\nuser1\n\nintegration\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("zeta")
    assert stored is not None and stored.base_url == "https://integration.kizen.dev"


def test_init_environment_picker_free_text_url(monkeypatch, tmp_path):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "gamma", "--skip-validation"],
        input="apikey\nCCCC\nuser1\nurl\nhttps://self-hosted.example.com\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("gamma")
    assert stored is not None and stored.base_url == "https://self-hosted.example.com"


def test_init_environment_picker_is_case_insensitive(monkeypatch, tmp_path):
    """Rich's `choices` matching defaults to case-sensitive, so someone typing
    their environment name as they'd naturally capitalize it (e.g. "Go") got
    stuck in an infinite "please select one of the available options" loop
    with no clue why — every retry of the same reasonable word failed."""
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "epsilon", "--skip-validation"],
        input="apikey\nEEEE\nuser1\nGo\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("epsilon")
    assert stored is not None and stored.base_url == "https://app.go.kizen.com"


def test_init_environment_picker_mistyped_name_shows_url_hint(monkeypatch, tmp_path):
    """A guess that isn't one of the curated names (e.g. a real tenant host)
    used to loop on Rich's generic "please select one of the available
    options" with no hint that "url" unlocks free-text entry. It should now
    say so directly, and still let the retry succeed."""
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        ["init", "--profile", "delta", "--skip-validation"],
        input="apikey\nDDDD\nuser1\ndzcf.example.com\nurl\nhttps://dzcf.example.com\n",
    )
    assert result.exit_code == 0, result.output
    assert 'Type "url" to enter a custom address instead' in result.output

    stored = profiles.get_profile("delta")
    assert stored is not None and stored.base_url == "https://dzcf.example.com"


def test_init_base_url_flag_accepts_named_host(monkeypatch, tmp_path):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--profile",
            "delta",
            "--skip-validation",
            "--base-url",
            "staging",
        ],
        input="apikey\nDDDD\nuser1\n",
    )
    assert result.exit_code == 0, result.output

    stored = profiles.get_profile("delta")
    assert stored is not None and stored.base_url == "https://staging.kizen.com"


def test_init_base_url_flag_rejects_unknown_name(monkeypatch, tmp_path):
    from kizen_builder import config

    config.set_profile_override(None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--profile",
            "epsilon",
            "--skip-validation",
            "--base-url",
            "not-a-real-env",
        ],
        input="apikey\nEEEE\nuser1\n",
    )
    assert result.exit_code == 2, result.output
    assert "unknown environment" in result.stderr


def test_envs_list_json_marks_pinned_profile(monkeypatch):
    from kizen_builder import profiles

    for name, bid in (("alpha", "AAAA"), ("beta", "BBBB")):
        profiles.write_profile(
            profiles.ProfileCreds(
                name, f"k-{name}", bid, f"u-{name}", "https://app.go.kizen.com"
            )
        )
    monkeypatch.setattr(
        profiles, "load_pin", lambda start=None: profiles.Pin("beta", "BBBB", "/x")
    )

    result = runner.invoke(cli.app, ["envs", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = {r["label"]: r for r in json.loads(result.stdout)}
    assert rows["beta"]["pinned"] is True
    assert rows["alpha"]["pinned"] is False
    assert rows["beta"]["source"] == "profile"


def test_command_refuses_when_pin_business_id_mismatches(monkeypatch):
    from kizen_builder import config, profiles

    config.set_profile_override(None)
    profiles.write_profile(
        profiles.ProfileCreds("alpha", "k", "AAAA", "u", "https://app.go.kizen.com")
    )
    # Directory claims a different identity than the resolved profile.
    monkeypatch.setattr(
        profiles, "load_pin", lambda start=None: profiles.Pin("alpha", "ZZZZ", "/x")
    )

    result = runner.invoke(cli.app, ["team", "search", "someone"])
    assert result.exit_code == 1
    assert "Refusing" in result.stderr


# ---------------------------------------------------------------------------
# forms & surveys — same factory-built app, smoke-test both command groups
# ---------------------------------------------------------------------------


def test_forms_list_json(monkeypatch):
    fake = [
        {
            "env": "testenv",
            "id": "abc",
            "name": "Contact Us",
            "api_name": "contact_us",
            "n_submissions": 3,
            "template_type": "modern",
            "deleted": False,
        }
    ]
    seen = {}

    def fake_list(*, base_path=None, search=None):
        seen["base_path"] = base_path
        return fake

    monkeypatch.setattr(form_tools, "list_forms", fake_list)
    result = runner.invoke(cli.app, ["forms", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == fake
    assert seen["base_path"] == "/api/forms"


def test_surveys_list_json_uses_surveys_base_path(monkeypatch):
    fake = [
        {
            "env": "testenv",
            "id": "abc",
            "name": "NPS",
            "api_name": "nps",
            "n_submissions": 1,
            "template_type": "modern",
            "deleted": False,
        }
    ]
    seen = {}

    def fake_list(*, base_path=None, search=None):
        seen["base_path"] = base_path
        return fake

    monkeypatch.setattr(form_tools, "list_forms", fake_list)
    result = runner.invoke(cli.app, ["surveys", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == fake
    assert seen["base_path"] == "/api/surveys"


def test_forms_get_table_shows_fields(monkeypatch):
    fake = {
        "env": "testenv",
        "id": "abc",
        "name": "Contact Us",
        "api_name": "contact_us",
        "description": None,
        "template_type": "modern",
        "related_object": "client_client",
        "n_submissions": 0,
        "fields": [
            {
                "id": "f1",
                "api_name": "email",
                "display_name": "Email",
                "field_type": "email",
                "is_required": True,
                "is_hidden": False,
                "order": 0,
                "options": None,
            }
        ],
        "raw": {},
    }
    monkeypatch.setattr(
        form_tools,
        "get_form",
        lambda identifier, base_path=None, include_fields=True: fake,
    )
    result = runner.invoke(cli.app, ["forms", "get", "contact_us"])
    assert result.exit_code == 0, result.output
    assert "Contact Us" in result.stdout
    assert "email" in result.stdout


def test_forms_create_dry_run(monkeypatch):
    def fake_plan_create(spec, *, base_path=None, kind=None):
        assert spec["name"] == "X"
        assert spec["related_object"] == "client_client"
        assert base_path == "/api/forms"
        assert kind == "form"
        return plan_tools.Plan.build(
            env="testenv", summary="Create form 'X'", operations=[]
        )

    monkeypatch.setattr(form_planners, "plan_create_form", fake_plan_create)
    result = runner.invoke(
        cli.app,
        [
            "forms",
            "create",
            "--name",
            "X",
            "--related-object",
            "client_client",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output


def test_forms_fields_list_is_cli_wired(monkeypatch):
    def fake_get_form(identifier, *, base_path=None, include_fields=True):
        assert identifier == "contact_us"
        assert base_path == "/api/forms"
        return {"name": "Contact Us", "fields": []}

    monkeypatch.setattr(form_tools, "get_form", fake_get_form)
    result = runner.invoke(cli.app, ["forms", "fields", "list", "contact_us"])
    assert result.exit_code == 0, result.output


def test_forms_delete_dry_run(monkeypatch):
    def fake_plan_delete(identifier, *, base_path=None, kind=None):
        assert identifier == "contact_us"
        assert base_path == "/api/forms"
        assert kind == "form"
        return plan_tools.Plan.build(
            env="testenv", summary="Delete form 'X'", operations=[]
        )

    monkeypatch.setattr(form_planners, "plan_delete_form", fake_plan_delete)
    result = runner.invoke(cli.app, ["forms", "delete", "contact_us", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_forms_duplicate_dry_run(monkeypatch):
    def fake_plan_duplicate(identifier, *, name=None, base_path=None, kind=None):
        assert identifier == "contact_us"
        assert name == "Copy"
        assert base_path == "/api/forms"
        assert kind == "form"
        return plan_tools.Plan.build(
            env="testenv", summary="Duplicate form 'X'", operations=[]
        )

    monkeypatch.setattr(form_planners, "plan_duplicate_form", fake_plan_duplicate)
    result = runner.invoke(
        cli.app, ["forms", "duplicate", "contact_us", "--name", "Copy", "--dry-run"]
    )
    assert result.exit_code == 0, result.output


def test_perms_group_renders_resolution_warnings_on_stderr(monkeypatch):
    """`describe_group`'s name resolution is best-effort and reports what it
    couldn't resolve. Those warnings go to stderr so stdout stays pure result
    data in every format; `--json` also carries them in the payload."""
    view = {
        "id": "g1",
        "name": "Sample Group",
        "user_count": 1,
        "role_count": 1,
        "summary": {},
        "blocks": [],
        "warnings": ["could not resolve object and field names (boom)"],
    }
    monkeypatch.setattr(perm_tools, "resolve_group", lambda ref: {"id": "g1"})
    monkeypatch.setattr(
        perm_tools, "describe_group", lambda gid, include_fields=False: view
    )

    result = runner.invoke(cli.app, ["permissions", "group", "Sample Group", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == view  # stdout is pure result JSON
    assert "warning: could not resolve object and field names" in result.stderr

"""The smart-connector authoring path: create → set-input → configure-flow → run.

Everything here is respx-mocked. The interesting cases aren't the happy paths
(they're thin PATCHes) but the wire quirks the CLI exists to absorb: the
three-legged S3 upload, the source-file swap refusal, name-based resolution, and
the multi-round load-step save that relationship fields require.
"""

from __future__ import annotations

import csv
import json
import time

import httpx
import pytest
import respx
from pydantic import ValidationError

from kizen_builder.api import files as files_api
from kizen_builder.api import smart_connectors as sc
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.models.spec import SmartConnectorFlowDef
from kizen_builder.tools import smart_connectors as sct
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

BASE = f"{FAKE_BASE_URL}/api/smart-connectors"
S3_URL = "https://files.example.test/"

OBJECTS = [
    {"id": "obj-orders", "name": "orders", "object_name": "Orders", "is_custom": True},
    {
        "id": "obj-lines",
        "name": "order_lines",
        "object_name": "Order Lines",
        "is_custom": True,
    },
    {
        "id": "obj-contacts",
        "name": "client_client",
        "object_name": "Contacts",
        "is_custom": False,
    },
]

# Field rows as the API really returns them: the api_name is `name`, the human
# label is `display_name`, and a live field has no `deleted` key at all.
ORDER_FIELDS = [
    {
        "id": "f-orders-name",
        "name": "name",
        "display_name": "Order Name",
        "field_type": "text",
    },
    {
        "id": "f-orders-number",
        "name": "order_number",
        "display_name": "Order Number",
        "field_type": "text",
    },
    {
        "id": "f-orders-gone",
        "name": "retired",
        "display_name": "Retired",
        "field_type": "text",
        "deleted": True,
    },
]
LINE_FIELDS = [
    {
        "id": "f-lines-name",
        "name": "name",
        "display_name": "Line Name",
        "field_type": "text",
    },
    {"id": "f-lines-sku", "name": "sku", "display_name": "SKU", "field_type": "text"},
    {
        "id": "f-lines-rel",
        "name": "order_rel",
        "display_name": "Order",
        "field_type": "relationship",
    },
]

METADATA = {
    "cadence_choices": [["300", "5 Minutes"], ["3600", "60 Minutes"]],
    "sql_versions": ["3.1.x", "4.1.x"],
}

DETAIL = {
    "id": "conn-uuid",
    "api_name": "order_import",
    "name": "Order Import",
    "connector_type": "spreadsheet",
    "status": "setup",
    "last_draft_script": {"id": "draft-1", "status": "draft", "sql_version": "4.1.x"},
    "live_script": {"id": "live-1", "status": "live"},
    "source_file": None,
    "execution_variables": [],
    "flow": {"additional_variables": [], "transformations": [], "loads": []},
    "headers": {
        "orders": [
            {"name": "order_number", "index": "A"},
            {"name": "sku", "index": "B"},
        ]
    },
}


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


def _mock_object_lookups():
    """Serve the object list + per-object field lists name resolution needs."""
    route = respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(
            200, json={"count": 3, "next": None, "results": OBJECTS}
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/obj-orders/fields").mock(
        return_value=httpx.Response(
            200, json={"count": 3, "next": None, "results": ORDER_FIELDS}
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/obj-lines/fields").mock(
        return_value=httpx.Response(
            200, json={"count": 3, "next": None, "results": LINE_FIELDS}
        )
    )
    return route


# ---------------------------------------------------------------------------
# api.files: the three-legged upload
# ---------------------------------------------------------------------------


@respx.mock
def test_upload_file_walks_presign_s3_and_success(client, tmp_path):
    src = tmp_path / "sample.csv"
    src.write_bytes(b"order_number\n1\n")

    presign = respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": S3_URL,
                "fields": {"key": "biz/smart_connector_import/obj.csv", "policy": "p"},
                "s3object_id": "s3-obj-1",
                "max_file_size": 500,
            },
        )
    )
    s3 = respx.post(S3_URL).mock(
        return_value=httpx.Response(204, headers={"etag": '"abc123"'})
    )
    success = respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-1", "name": "sample.csv"})
    )

    result = files_api.upload_file(client, src)
    assert result["id"] == "file-1"

    # Leg 1 declares the guessed content type and the source.
    q = presign.calls.last.request.url.params
    assert q["filename"] == "sample.csv"
    assert q["source"] == files_api.SMART_CONNECTOR_IMPORT
    assert q["contenttype"] == "text/csv"

    # Leg 2 goes to S3 as multipart, unauthenticated, with the file part last.
    s3_req = s3.calls.last.request
    assert "authorization" not in {k.lower() for k in s3_req.headers}
    body = s3_req.content.decode("utf-8", "replace")
    assert body.index('name="key"') < body.index('name="file"')

    # Leg 3 is form-encoded (NOT the client's default JSON) and carries the etag.
    ok_req = success.calls.last.request
    assert ok_req.headers["content-type"].startswith(
        "application/x-www-form-urlencoded"
    )
    assert b"uuid=s3-obj-1" in ok_req.content
    assert b"etag=abc123" in ok_req.content
    # is_public defaults to omitted (server default is false) — a smart
    # connector's reference file has no reason to be world-readable.
    assert b"is_public" not in ok_req.content


@respx.mock
def test_upload_file_sends_is_public_true_when_requested(client, tmp_path):
    src = tmp_path / "sample.csv"
    src.write_bytes(b"order_number\n1\n")
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "s3-obj-1"},
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e"'}))
    success = respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-1", "name": "sample.csv"})
    )

    files_api.upload_file(client, src, is_public=True)

    assert b"is_public=true" in success.calls.last.request.content


@respx.mock
def test_upload_file_rejects_oversize_before_uploading(client, tmp_path):
    src = tmp_path / "big.csv"
    src.write_bytes(b"x" * 50)
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": S3_URL,
                "fields": {"key": "k"},
                "s3object_id": "s",
                "max_file_size": 10,
            },
        )
    )
    s3 = respx.post(S3_URL).mock(return_value=httpx.Response(204))
    with pytest.raises(KizenAPIError, match="limit is 10"):
        files_api.upload_file(client, src)
    assert not s3.called


@respx.mock
def test_upload_file_needs_the_object_id_the_policy_came_with(client, tmp_path):
    src = tmp_path / "s.csv"
    src.write_bytes(b"a\n")
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(200, json={"url": S3_URL, "fields": {"key": "k"}})
    )
    with pytest.raises(KizenAPIError, match="s3object_id"):
        files_api.upload_file(client, src)


# ---------------------------------------------------------------------------
# api.smart_connectors: the authoring endpoints
# ---------------------------------------------------------------------------


@respx.mock
def test_get_file_template_always_sends_the_source_file_id(client):
    route = respx.post(f"{BASE}/c1/get-file-template").mock(
        return_value=httpx.Response(
            200, json={"user_script": "select 1", "config_metadata": {}}
        )
    )
    sc.get_file_template(client, "c1", "file-9")
    # An empty body silently returns {} server-side, so the id is never implicit.
    assert json.loads(route.calls.last.request.content) == {"source_file_id": "file-9"}


@respx.mock
def test_start_connector_flow_sends_dry_run_flag(client):
    route = respx.post(f"{BASE}/c1/start-connector-flow").mock(
        return_value=httpx.Response(200, json={"id": "exec-1"})
    )
    assert sc.start_connector_flow(client, "c1", is_dry_run=True)["id"] == "exec-1"
    assert json.loads(route.calls.last.request.content) == {"is_dry_run": True}


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def _mock_create_plan_reads(*, existing: list[dict] | None = None) -> None:
    _mock_object_lookups()
    respx.get(f"{BASE}/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json={"count": 0, "next": None, "results": existing or []}
        )
    )


@respx.mock
def test_plan_create_resolves_object_and_defaults_to_setup():
    _mock_create_plan_reads()
    plan = sct.plan_create_connector(
        name="Order Import", custom_object="orders", connector_type="spreadsheet"
    )
    assert plan["payload"] == {
        "name": "Order Import",
        "custom_object": "obj-orders",
        "connector_type": "spreadsheet",
    }
    assert plan["preview"]["custom_object"] == "orders"
    assert "set-input" in plan["next_step"]


@respx.mock
def test_plan_create_rejects_unknown_object_and_type():
    _mock_create_plan_reads()
    with pytest.raises(PlanError, match="unknown connector_type"):
        sct.plan_create_connector(
            name="X", custom_object="orders", connector_type="carrier_pigeon"
        )
    with pytest.raises(PlanError, match="not found"):
        sct.plan_create_connector(
            name="X", custom_object="nope", connector_type="spreadsheet"
        )


@respx.mock
def test_plan_create_rejects_duplicate_name_case_insensitively():
    _mock_create_plan_reads(
        existing=[{"id": "c9", "api_name": "order_import", "name": "order import"}]
    )
    with pytest.raises(PlanError, match="already exists"):
        sct.plan_create_connector(
            name="Order Import", custom_object="orders", connector_type="spreadsheet"
        )


@respx.mock
def test_plan_create_enforces_per_type_requirements():
    _mock_create_plan_reads()
    # The API's own schema marks neither required; the server 400s without them.
    with pytest.raises(PlanError, match="needs --cadence"):
        sct.plan_create_connector(
            name="S", custom_object="orders", connector_type="schedule"
        )
    with pytest.raises(PlanError, match="activity TYPE"):
        sct.plan_create_connector(
            name="A", custom_object="orders", connector_type="activity"
        )
    with pytest.raises(PlanError, match="cadence 45 isn't offered"):
        sct.plan_create_connector(
            name="S", custom_object="orders", connector_type="schedule", cadence=45
        )
    with pytest.raises(PlanError, match="sql_version"):
        sct.plan_create_connector(
            name="S",
            custom_object="orders",
            connector_type="spreadsheet",
            sql_version="9.9.x",
        )


# ---------------------------------------------------------------------------
# set-input
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_set_input_refuses_to_swap_an_attached_file(tmp_path):
    src = tmp_path / "new.csv"
    src.write_bytes(b"a\n")
    detail = {**DETAIL, "source_file": {"id": "file-old", "name": "old.csv"}}
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )

    with pytest.raises(PlanError, match="known-broken"):
        sct.plan_set_input("order_import", src)

    plan = sct.plan_set_input("order_import", src, allow_replace=True)
    assert plan["replacing"] == "old.csv"


@respx.mock
def test_apply_set_input_uploads_attaches_then_regenerates(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_bytes(b"order_number\n1\n")
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200, json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "s3-1"}
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e"'}))
    respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-new", "name": "sample.csv"})
    )
    attach = respx.patch(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.post(f"{BASE}/conn-uuid/get-file-template").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_script": "create table output.orders as select 1;",
                "config_metadata": {
                    "input_tables": [{"name": "orders.csv"}],
                    "seed_tables": [],
                },
            },
        )
    )
    # Generating the template creates a NEW draft, at a downgraded sql_version.
    respx.get(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(
            200,
            json={
                **DETAIL,
                "last_draft_script": {"id": "draft-2", "sql_version": "1.3.x"},
            },
        )
    )
    write_script = respx.patch(f"{BASE}/conn-uuid/sql-scripts/draft-2").mock(
        return_value=httpx.Response(200, json={"id": "draft-2", "sql_version": "4.1.x"})
    )

    plan = sct.plan_set_input("order_import", src)
    result = sct.apply_set_input(plan)

    assert json.loads(attach.calls.last.request.content) == {
        "source_file_id": "file-new"
    }
    assert result["regenerated"] is True
    assert result["input_tables"] == ["orders.csv"]
    # The template lands on the draft the server just made, not the stale one.
    assert result["script_id"] == "draft-2"
    assert result["new_draft"] is True
    # The generated script, its config, and the un-downgraded version all go up.
    body = json.loads(write_script.calls.last.request.content)
    assert set(body) == {"user_script", "config_metadata", "sql_version"}
    assert body["sql_version"] == "4.1.x"
    assert result["sql_version_restored"] == "4.1.x"


# The webhook template's second statement builds `output.webhooks` — a debug
# echo of the input. `webhooks` isn't a Kizen object, and leaving the statement
# in crashes sample generation.
WEBHOOK_TEMPLATE = """/* header comment */
create table output.orders engine Log() as
select timestamp, body FROM input.webhooks_raw;

create table output.webhooks engine Log() as
select timestamp, toJSONString(body) AS body FROM input.webhooks;
"""


@respx.mock
def test_apply_set_input_drops_the_phantom_output_table(tmp_path):
    src = tmp_path / "hook.csv"
    src.write_bytes(b"timestamp,employee_id,querystring,body\n")
    _mock_object_lookups()
    detail = {**DETAIL, "connector_type": "webhook"}
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200, json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "s"}
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e"'}))
    respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "f", "name": "hook.csv"})
    )
    respx.patch(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=detail))
    respx.post(f"{BASE}/conn-uuid/get-file-template").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_script": WEBHOOK_TEMPLATE,
                # BOTH input tables stay — removing the typed one also 500s,
                # even though the surviving SQL only reads the raw one.
                "config_metadata": {
                    "input_tables": [{"name": "hook.csv"}, {"name": "hook.csv"}],
                    "seed_tables": [],
                },
            },
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=detail))
    write = respx.patch(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
        return_value=httpx.Response(200, json={"id": "draft-1"})
    )

    result = sct.apply_set_input(sct.plan_set_input("order_import", src))

    assert result["dropped_output_tables"] == ["webhooks"]
    written = json.loads(write.calls.last.request.content)["user_script"]
    assert "output.webhooks" not in written
    assert "output.orders" in written  # the real load target survives
    assert (
        len(
            json.loads(write.calls.last.request.content)["config_metadata"][
                "input_tables"
            ]
        )
        == 2
    )


@respx.mock
def test_apply_set_input_explains_an_empty_template(tmp_path):
    src = tmp_path / "wrong.csv"
    src.write_bytes(b"nope\n")
    _mock_object_lookups()
    detail = {**DETAIL, "connector_type": "webhook"}
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200, json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "s"}
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e"'}))
    respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "f", "name": "wrong.csv"})
    )
    respx.patch(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=detail))
    # The endpoint no-ops to {} rather than erroring when the shape is wrong.
    respx.post(f"{BASE}/conn-uuid/get-file-template").mock(
        return_value=httpx.Response(200, json={})
    )

    plan = sct.plan_set_input("order_import", src)
    with pytest.raises(PlanError, match="timestamp, employee_id"):
        sct.apply_set_input(plan)


# ---------------------------------------------------------------------------
# generate-sample + activate
# ---------------------------------------------------------------------------


@respx.mock
def test_generate_output_sample_polls_until_the_state_settles(monkeypatch):
    # `time` is a module singleton, so patching it here reaches
    # authoring/sample.py's own `time.sleep` call.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.post(f"{BASE}/order_import/sql-scripts/draft-1/start").mock(
        return_value=httpx.Response(200, json={"id": "draft-1"})
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        side_effect=[
            httpx.Response(200, json={"id": "draft-1", "state": "in_progress"}),
            httpx.Response(200, json={"id": "draft-1", "state": "success"}),
        ]
    )
    result = sct.generate_output_sample("order_import")
    assert result["state"] == "success"
    assert result["timed_out"] is False
    assert result["scopes"] == {"orders": 2}


@respx.mock
def test_plan_set_status_reports_the_gaps_that_make_a_live_run_pointless():
    detail = {**DETAIL, "live_script": {}, "flow": {"loads": []}}
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    plan = sct.plan_set_status("order_import", "operational")
    assert plan["changed"] is True
    assert plan["has_live_script"] is False
    assert plan["load_steps"] == 0
    with pytest.raises(PlanError, match="unknown status"):
        sct.plan_set_status("order_import", "sideways")


# ---------------------------------------------------------------------------
# start-flow
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_start_flow_blocks_a_live_run_of_a_setup_connector():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    live = sct.plan_start_flow("order_import", dry_run=False)
    assert any("sit in 'queued' forever" in b for b in live["blockers"])
    assert any("no load steps" in b for b in live["blockers"])
    # A dry run doesn't care about status.
    dry = sct.plan_start_flow("order_import", dry_run=True)
    assert not any("queued" in b for b in dry["blockers"])


@respx.mock
def test_plan_start_flow_says_webhooks_are_triggered_differently():
    detail = {**DETAIL, "connector_type": "webhook", "status": "operational"}
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    plan = sct.plan_start_flow("order_import", dry_run=True)
    assert any("inbound POST" in b for b in plan["blockers"])


@respx.mock
def test_list_executions_surfaces_the_whole_executor_error():
    long_error = (
        "Code: 47. DB::Exception: There's no column 's.sku' in table 's'. " + "x" * 200
    )
    respx.get(f"{BASE}/order_import/executions").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {"id": "e1", "status": "failed", "error_details": long_error}
                ],
            },
        )
    )
    rows = sct.list_executions("order_import")
    assert rows[0]["error_details"] == long_error


# ---------------------------------------------------------------------------
# configure-flow: spec validation
# ---------------------------------------------------------------------------


def _flow_spec(**overrides):
    spec = {
        "connector": "order_import",
        "execution_variables": [
            {"name": "order_number"},
            {"name": "sku"},
        ],
        "loads": [
            {
                "custom_object": "orders",
                "matching_rules": [
                    {"field": "order_number", "variable": "order_number"}
                ],
                "field_mapping_rules": [{"field": "name", "variable": "order_number"}],
            }
        ],
    }
    spec.update(overrides)
    return spec


def test_spec_rejects_an_array_variable_with_no_delimiter():
    with pytest.raises(ValidationError, match="array_delimiter"):
        SmartConnectorFlowDef.model_validate(
            _flow_spec(execution_variables=[{"name": "tags", "is_array": True}])
        )


def test_spec_rejects_both_or_neither_variable_form():
    for mapping in (
        {"field": "name"},
        {"field": "name", "variable": "a", "variables": ["a"]},
    ):
        with pytest.raises(ValidationError, match="exactly one"):
            SmartConnectorFlowDef.model_validate(
                _flow_spec(
                    loads=[
                        {
                            "custom_object": "orders",
                            "matching_rules": [
                                {"field": "order_number", "variable": "order_number"}
                            ],
                            "field_mapping_rules": [mapping],
                        }
                    ]
                )
            )


def test_spec_rejects_a_last_rule_that_falls_through():
    with pytest.raises(ValidationError, match="no next rule"):
        SmartConnectorFlowDef.model_validate(
            _flow_spec(
                loads=[
                    {
                        "custom_object": "orders",
                        "matching_rules": [
                            {
                                "field": "order_number",
                                "variable": "order_number",
                                "multiple_match_action": "next_rule",
                            }
                        ],
                        "field_mapping_rules": [
                            {"field": "name", "variable": "order_number"}
                        ],
                    }
                ]
            )
        )


# ---------------------------------------------------------------------------
# configure-flow: planning against live state
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_configure_flow_resolves_names_and_defaults_the_scope():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    plan = sct.plan_configure_flow(_flow_spec())

    assert plan["connector"] == "conn-uuid"
    assert plan["scopes"] == {"orders": 2}
    var = plan["execution_variables"][0]
    assert var["data_source"] == "order_number"  # defaulted from name
    assert var["scope"] == "orders"  # the connector's only output table
    assert var["type"] == "data_source"
    load = plan["loads"][0]
    assert load["custom_object"] == "obj-orders"
    assert load["order"] == 0
    assert load["matching_rules"][0]["field"] == "f-orders-number"
    assert load["field_mapping_rules"][0]["field"] == "f-orders-name"
    # Variables stay by name — they have no uuid until they're saved.
    assert load["matching_rules"][0]["variable_ref"] == "order_number"


@respx.mock
def test_plan_configure_flow_needs_a_generated_sample_first():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json={**DETAIL, "headers": {}})
    )
    with pytest.raises(PlanError, match="generate-sample"):
        sct.plan_configure_flow(_flow_spec())


@respx.mock
def test_plan_configure_flow_rejects_a_column_the_output_sample_doesnt_have():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    spec = _flow_spec(
        execution_variables=[{"name": "order_number"}, {"name": "invented"}]
    )
    # The sample's columns are the contract — and a stale sample is the usual
    # reason a column the SQL clearly selects looks missing.
    with pytest.raises(PlanError, match="generate-sample"):
        sct.plan_configure_flow(spec)


@respx.mock
def test_plan_configure_flow_requires_the_objects_own_name_field():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    spec = _flow_spec(
        loads=[
            {
                "custom_object": "orders",
                "matching_rules": [
                    {"field": "order_number", "variable": "order_number"}
                ],
                "field_mapping_rules": [
                    {"field": "order_number", "variable": "order_number"}
                ],
            }
        ]
    )
    with pytest.raises(PlanError, match="'name' field"):
        sct.plan_configure_flow(spec)


@respx.mock
def test_plan_configure_flow_rejects_unknown_fields_and_variables():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    with pytest.raises(PlanError, match="field 'retired' not found"):
        sct.plan_configure_flow(
            _flow_spec(
                loads=[
                    {
                        "custom_object": "orders",
                        "matching_rules": [
                            {"field": "retired", "variable": "order_number"}
                        ],
                        "field_mapping_rules": [
                            {"field": "name", "variable": "order_number"}
                        ],
                    }
                ]
            )
        )
    with pytest.raises(PlanError, match="which nothing provides"):
        sct.plan_configure_flow(
            _flow_spec(
                loads=[
                    {
                        "custom_object": "orders",
                        "matching_rules": [
                            {"field": "order_number", "variable": "ghost"}
                        ],
                        "field_mapping_rules": [
                            {"field": "name", "variable": "order_number"}
                        ],
                    }
                ]
            )
        )


@respx.mock
def test_plan_configure_flow_rejects_a_forward_reference():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    spec = _flow_spec(
        loads=[
            {
                "custom_object": "order_lines",
                "matching_rules": [{"field": "sku", "variable": "sku"}],
                "field_mapping_rules": [
                    {"field": "name", "variable": "sku"},
                    {"field": "order_rel", "variable": "matched_order"},
                ],
            },
            {
                "custom_object": "orders",
                "matching_rules": [
                    {"field": "order_number", "variable": "order_number"}
                ],
                "field_mapping_rules": [{"field": "name", "variable": "order_number"}],
                "exposes_variable": "matched_order",
            },
        ]
    )
    with pytest.raises(PlanError, match="runs later"):
        sct.plan_configure_flow(spec)


@respx.mock
def test_plan_configure_flow_warns_about_variables_it_would_drop():
    _mock_object_lookups()
    detail = {
        **DETAIL,
        "execution_variables": [
            {"id": "v-old", "name": "legacy_column", "scope": "orders"},
            {"id": "v-num", "name": "order_number", "scope": "orders"},
        ],
    }
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    plan = sct.plan_configure_flow(_flow_spec())
    assert plan["dropped_variables"] == ["legacy_column"]


@respx.mock
def test_plan_configure_flow_warns_about_a_date_variable_with_no_output_format():
    """Kizen defaults an unset output_format to %m/%d/%Y, which a native
    ISO-only date field then rejects per row — a silent partial-success that
    doesn't surface in `executions --json`. Flag it at plan time instead."""
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    spec = _flow_spec(
        execution_variables=[
            {"name": "order_number", "data_type": "date"},
            {"name": "sku"},
        ]
    )
    plan = sct.plan_configure_flow(spec)
    assert len(plan["date_format_warnings"]) == 1
    assert "order_number" in plan["date_format_warnings"][0]
    assert "output_format" in plan["date_format_warnings"][0]


@respx.mock
def test_plan_configure_flow_no_warning_when_output_format_is_set():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    spec = _flow_spec(
        execution_variables=[
            {"name": "order_number", "data_type": "date", "output_format": "%Y-%m-%d"},
            {"name": "sku"},
        ]
    )
    plan = sct.plan_configure_flow(spec)
    assert plan["date_format_warnings"] == []


@respx.mock
def test_plan_configure_flow_needs_an_explicit_scope_when_several_exist():
    _mock_object_lookups()
    detail = {
        **DETAIL,
        "headers": {
            "orders": [{"name": "order_number"}],
            "lines": [{"name": "sku"}],
        },
    }
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    with pytest.raises(PlanError, match="needs an explicit 'scope'"):
        sct.plan_configure_flow(_flow_spec())


# ---------------------------------------------------------------------------
# configure-flow: the multi-round apply
# ---------------------------------------------------------------------------

MULTI_SPEC = {
    "connector": "order_import",
    "execution_variables": [{"name": "order_number"}, {"name": "sku"}],
    "loads": [
        {
            "custom_object": "orders",
            "matching_rules": [{"field": "order_number", "variable": "order_number"}],
            "field_mapping_rules": [{"field": "name", "variable": "order_number"}],
            "exposes_variable": "matched_order",
        },
        {
            "custom_object": "order_lines",
            "matching_rules": [{"field": "sku", "variable": "sku"}],
            "field_mapping_rules": [
                {"field": "name", "variable": "sku"},
                {"field": "order_rel", "variable": "matched_order"},
            ],
        },
    ],
}

SAVED_VARS = [
    {"id": "v-num", "name": "order_number", "scope": "orders"},
    {"id": "v-sku", "name": "sku", "scope": "orders"},
]

# What the server returns for the first load step after round 1: it now has ids,
# including the uuid of the variable carrying the record it matched/created.
LOAD_ONE_LIVE = {
    "id": "load-1",
    "custom_object": "obj-orders",
    "scope": "orders",
    "type": "csv_load",
    "order": 0,
    "matching_rules": [
        {"id": "mr-1", "order": 0, "field": "f-orders-number", "variable": "v-num"}
    ],
    "field_mapping_rules": [{"field": "f-orders-name", "variables": ["v-num"]}],
    "execution_variable": {
        "id": "v-matched",
        "name": "matched_order",
        "data_type": "uuid",
        "scope": "orders",
    },
    "stats": {"ignored": True},
}


@respx.mock
def test_apply_configure_flow_saves_related_loads_in_two_rounds():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    plan = sct.plan_configure_flow(MULTI_SPEC)
    assert plan["deferred_loads"] == ["order_lines"]

    patches = respx.patch(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    # GET after the variables PATCH, then after each load round.
    respx.get(f"{BASE}/conn-uuid").mock(
        side_effect=[
            httpx.Response(200, json={**DETAIL, "execution_variables": SAVED_VARS}),
            httpx.Response(
                200,
                json={
                    **DETAIL,
                    "execution_variables": SAVED_VARS,
                    "flow": {**DETAIL["flow"], "loads": [LOAD_ONE_LIVE]},
                },
            ),
            httpx.Response(
                200,
                json={
                    **DETAIL,
                    "execution_variables": SAVED_VARS,
                    "flow": {
                        **DETAIL["flow"],
                        "loads": [
                            LOAD_ONE_LIVE,
                            {**LOAD_ONE_LIVE, "id": "load-2", "order": 1},
                        ],
                    },
                },
            ),
        ]
    )

    result = sct.apply_configure_flow(plan)
    assert result["rounds"] == 2
    assert result["loads_saved"] == 2
    assert result["exposed_variables"] == {"matched_order": "v-matched"}

    bodies = [json.loads(c.request.content) for c in patches.calls]
    # 1: variables. 2: the first load only. 3: both, with the relationship filled in.
    assert [v["name"] for v in bodies[0]["execution_variables"]] == [
        "order_number",
        "sku",
    ]
    assert len(bodies[1]["flow"]["loads"]) == 1
    first = bodies[1]["flow"]["loads"][0]
    assert first["matching_rules"][0]["variable"] == "v-num"  # singular on the wire
    assert first["field_mapping_rules"][0]["variables"] == ["v-num"]  # plural here
    assert first["execution_variable"] == {
        "name": "matched_order",
        "data_type": "uuid",
        "scope": "orders",
    }

    second_round = bodies[2]["flow"]["loads"]
    assert len(second_round) == 2
    # The already-saved step goes back with its ids intact, so the server doesn't
    # recreate it and invalidate the uuid the next step depends on.
    assert second_round[0]["id"] == "load-1"
    assert "stats" not in second_round[0]
    rel = [
        r for r in second_round[1]["field_mapping_rules"] if r["field"] == "f-lines-rel"
    ]
    assert rel[0]["variables"] == ["v-matched"]


@respx.mock
def test_apply_configure_flow_single_round_when_nothing_is_related():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    plan = sct.plan_configure_flow(_flow_spec())
    assert plan["deferred_loads"] == []

    respx.patch(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    respx.get(f"{BASE}/conn-uuid").mock(
        side_effect=[
            httpx.Response(200, json={**DETAIL, "execution_variables": SAVED_VARS}),
            httpx.Response(
                200,
                json={
                    **DETAIL,
                    "execution_variables": SAVED_VARS,
                    "flow": {**DETAIL["flow"], "loads": [LOAD_ONE_LIVE]},
                },
            ),
        ]
    )
    result = sct.apply_configure_flow(plan)
    assert result["rounds"] == 1
    assert result["exposed_variables"] == {}


@respx.mock
def test_suggest_variables_strips_the_throwaway_ids():
    respx.post(f"{BASE}/order_import/generate-execution-variables").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "throwaway",
                    "name": "order_number",
                    "data_source": "order_number",
                    "data_type": "string",
                    "scope": "orders",
                    "is_array": False,
                    "input_format": None,
                }
            ],
        )
    )
    result = sct.suggest_execution_variables("order_import")
    assert result["count"] == 1
    # The suggestion endpoint hands back generated ids that mean nothing; the
    # spec block is what you paste into a flow spec.
    assert result["spec"]["execution_variables"] == [
        {
            "name": "order_number",
            "data_source": "order_number",
            "data_type": "string",
            "scope": "orders",
        }
    ]


@respx.mock
def test_apply_start_flow_reads_the_id_out_of_the_echoed_request():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json={**DETAIL, "status": "operational"})
    )
    respx.post(f"{BASE}/conn-uuid/start-connector-flow").mock(
        return_value=httpx.Response(
            200,
            # The endpoint echoes the queued run back; the id is `execution_id`.
            json={
                "is_dry_run": False,
                "trigger_type": "fileupload",
                "execution_id": "exec-42",
            },
        )
    )
    plan = sct.plan_start_flow("order_import", dry_run=False)
    assert sct.apply_start_flow(plan)["execution"] == "exec-42"


# ---------------------------------------------------------------------------
# webhook connectors
# ---------------------------------------------------------------------------


@respx.mock
def test_plan_create_pins_the_only_sql_version_webhooks_work_on():
    _mock_create_plan_reads()
    plan = sct.plan_create_connector(
        name="Hook", custom_object="orders", connector_type="webhook"
    )
    # Not passed by the caller: the server's default is lower, and the failure is
    # a bare 500 from sample generation with nothing naming the version.
    assert plan["payload"]["sql_version"] == sct.WEBHOOK_SQL_VERSION
    with pytest.raises(PlanError, match="500s on every lower version"):
        sct.plan_create_connector(
            name="Hook",
            custom_object="orders",
            connector_type="webhook",
            sql_version="3.1.x",
        )


def test_drop_phantom_output_tables_leaves_real_targets_alone():
    script, dropped = sct._drop_phantom_output_tables(
        "create table output.a as select 1;\ncreate table output.ghost as select 2;\n",
        {"a"},
    )
    assert dropped == ["ghost"]
    assert "output.a" in script and "ghost" not in script


@respx.mock
def test_build_webhook_sample_writes_the_shape_the_generator_demands(tmp_path):
    respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "member-1", "email": "dev@example.test", "full_name": "A Dev"}
            ],
        )
    )
    dest = tmp_path / "hook.csv"
    result = sct.build_webhook_sample(
        dest, body={"name": "X", "email": "x@example.test"}, employee="dev@example.test"
    )
    rows = list(csv.reader(dest.read_text().splitlines()))
    assert tuple(rows[0]) == sct.WEBHOOK_SAMPLE_COLUMNS
    # employee_id must be a real member uuid; a blank fails server-side.
    assert rows[1][1] == "member-1"
    assert json.loads(rows[1][3]) == {"name": "X", "email": "x@example.test"}
    assert result["body_keys"] == ["email", "name"]


@respx.mock
def test_build_webhook_sample_rejects_a_body_the_generator_would_choke_on(tmp_path):
    respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(200, json=[{"id": "m", "email": "d@example.test"}])
    )
    with pytest.raises(PlanError, match="isn't valid JSON"):
        sct.build_webhook_sample(
            tmp_path / "h.csv", body="{not json", employee="d@example.test"
        )


@respx.mock
def test_resolve_team_member_refuses_to_guess_between_matches():
    respx.get(f"{FAKE_BASE_URL}/api/team/typeahead").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "1", "email": "a@example.test", "full_name": "Dev One"},
                {"id": "2", "email": "b@example.test", "full_name": "Dev Two"},
            ],
        )
    )
    with pytest.raises(PlanError, match="matches 2 team members"):
        sct.resolve_team_member("Dev")


@respx.mock
def test_plan_send_webhook_blocks_the_wrong_connector_type_and_status():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    plan = sct.plan_send_webhook("order_import", {"a": 1})
    assert any("only webhook connectors" in b for b in plan["blockers"])
    assert any("not 'operational'" in b for b in plan["blockers"])


@respx.mock
def test_apply_send_webhook_posts_body_and_querystring():
    detail = {
        **DETAIL,
        "connector_type": "webhook",
        "status": "operational",
        "cadence": 60,
    }
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=detail)
    )
    route = respx.post(f"{BASE}/conn-uuid/webhook").mock(
        return_value=httpx.Response(201, json={"status": "accepted"})
    )
    plan = sct.plan_send_webhook(
        "order_import", {"name": "X"}, querystring={"src": "cli"}
    )
    result = sct.apply_send_webhook(plan)
    assert result["accepted"] is True
    assert result["cadence"] == 60
    request = route.calls.last.request
    assert json.loads(request.content) == {"name": "X"}
    assert request.url.params["src"] == "cli"


# ---------------------------------------------------------------------------
# kizen_data_seeds
# ---------------------------------------------------------------------------

FILTER_GROUPS = [
    {
        "id": "grp-1",
        "name": "Active Only",
        "config": {"query": [{"and": True, "filters": [{"type": "fields_v2"}]}]},
    }
]

SEED_ROW = {
    "id": "seed-1",
    "custom_object_id": "obj-lines",
    "group_id": "grp-1",
    "group": {"id": "grp-1", "name": "Active Only"},
    "custom_object": {"id": "obj-lines", "name": "order_lines"},
}

SEED_TABLE = {
    "name": "order_lines.csv",
    "database": "kizen",
    "table_name": "order_lines",
    "columns_mapping": [
        {"col": "kizen_id", "type": "str"},
        {"col": "sku", "type": "str"},
    ],
}


def _mock_filter_groups(object_id: str = "obj-lines") -> None:
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{object_id}/filter-groups").mock(
        return_value=httpx.Response(
            200, json={"count": 1, "next": None, "results": FILTER_GROUPS}
        )
    )


@respx.mock
def test_plan_add_seed_resolves_the_group_by_name_and_validates_fields():
    _mock_object_lookups()
    _mock_filter_groups()
    respx.get(f"{BASE}/metadata").mock(
        return_value=httpx.Response(
            200, json={**METADATA, "kizen_data_seeds_allowed_field_types": ["text"]}
        )
    )
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="order_lines", group="Active Only", fields=["sku"]
    )
    assert plan["payload"] == [
        {
            "custom_object_id": "obj-lines",
            "group_id": "grp-1",
            "fields_ids": ["f-lines-sku"],
        }
    ]
    assert plan["view"] == "kizen.order_lines"
    assert plan["replacing"] is False


@respx.mock
def test_plan_add_seed_resolves_contacts_by_client_client():
    """client_client (contacts) isn't a custom object — the object lookup must
    ask the server for it explicitly (custom_only=false) or it's invisible."""
    objects_route = _mock_object_lookups()
    _mock_filter_groups("obj-contacts")
    respx.get(f"{BASE}/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="client_client", group="Active Only"
    )
    assert plan["payload"] == [
        {"custom_object_id": "obj-contacts", "group_id": "grp-1"}
    ]
    assert plan["view"] == "kizen.client_client"
    assert objects_route.calls.last.request.url.params["custom_only"] == "false"


@respx.mock
def test_plan_add_seed_says_a_filter_group_is_not_a_category():
    _mock_object_lookups()
    _mock_filter_groups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    # The API's own error for a category id is a misleading "object does not exist".
    with pytest.raises(PlanError, match="not a field category"):
        sct.plan_add_seed(
            "order_import", custom_object="order_lines", group="some-category-uuid"
        )


@respx.mock
def test_plan_add_seed_rejects_an_unseedable_field_type():
    _mock_object_lookups()
    _mock_filter_groups()
    respx.get(f"{BASE}/metadata").mock(
        return_value=httpx.Response(
            200, json={**METADATA, "kizen_data_seeds_allowed_field_types": ["text"]}
        )
    )
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/obj-lines/fields").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": "f-x", "name": "attachment", "field_type": "files"}],
            },
        )
    )
    with pytest.raises(PlanError, match="can't be seeded"):
        sct.plan_add_seed(
            "order_import",
            custom_object="order_lines",
            group="Active Only",
            fields=["attachment"],
        )


@respx.mock
def test_plan_add_seed_replaces_the_existing_seed_for_the_same_object():
    _mock_object_lookups()
    _mock_filter_groups()
    respx.get(f"{BASE}/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "kizen_data_seeds": [SEED_ROW]}
        )
    )
    plan = sct.plan_add_seed(
        "order_import", custom_object="order_lines", group="Active Only"
    )
    assert plan["replacing"] is True
    # Same row id, so the seed is updated rather than swapped out from under the script.
    assert plan["payload"] == [
        {"custom_object_id": "obj-lines", "group_id": "grp-1", "id": "seed-1"}
    ]


@respx.mock
def test_plan_add_seed_preserves_another_seeds_field_restriction():
    """Regression: `fields_ids` is write-only and never comes back on a GET,
    so naively re-wiring the seeds we're *not* touching from read data drops
    any field restriction they had. The generated seed table's
    `columns_mapping` is the one place that restriction survives — it must be
    reconstructed from there, or a connector with 2+ seeded objects loses the
    field list on every seed but the one being added/replaced."""
    _mock_object_lookups()
    _mock_filter_groups("obj-orders")
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "kizen_data_seeds": [SEED_ROW]}
        )
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "draft-1", "config_metadata": {"seed_tables": [SEED_TABLE]}},
        )
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="orders", group="Active Only"
    )

    kept = next(p for p in plan["payload"] if p["custom_object_id"] == "obj-lines")
    assert kept["id"] == "seed-1"
    assert kept["fields_ids"] == ["f-lines-sku"]


@respx.mock
def test_plan_remove_seed_preserves_another_seeds_field_restriction():
    """Same regression as above, for `seeds remove`: dropping one seeded
    object must not also strip the field restriction off the seeds left
    behind."""
    _mock_object_lookups()
    SEED_ROW_2 = {
        "id": "seed-2",
        "custom_object_id": "obj-orders",
        "group_id": "grp-1",
        "group": {"id": "grp-1", "name": "Active Only"},
        "custom_object": {"id": "obj-orders", "name": "orders"},
    }
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "kizen_data_seeds": [SEED_ROW, SEED_ROW_2]}
        )
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "draft-1", "config_metadata": {"seed_tables": [SEED_TABLE]}},
        )
    )

    plan = sct.plan_remove_seed("order_import", "orders")

    assert plan["payload"] == [
        {
            "custom_object_id": "obj-lines",
            "group_id": "grp-1",
            "id": "seed-1",
            "fields_ids": ["f-lines-sku"],
        }
    ]


@respx.mock
def test_plan_remove_seed_errors_when_the_object_isnt_seeded():
    _mock_object_lookups()
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    with pytest.raises(PlanError, match="doesn't seed 'order_lines'"):
        sct.plan_remove_seed("order_import", "order_lines")


@respx.mock
def test_list_seeds_flags_a_seed_the_script_doesnt_know_about_yet():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "kizen_data_seeds": [SEED_ROW]}
        )
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200, json={"id": "draft-1", "config_metadata": {"seed_tables": []}}
        )
    )
    rows = sct.list_seeds("order_import")
    # A saved seed is inert until a template regeneration adds the view.
    assert rows[0]["in_script"] is False
    assert rows[0]["view"] == "kizen.order_lines"

    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "draft-1", "config_metadata": {"seed_tables": [SEED_TABLE]}},
        )
    )
    rows = sct.list_seeds("order_import")
    assert rows[0]["in_script"] is True
    assert rows[0]["columns"] == ["kizen_id", "sku"]


@respx.mock
def test_apply_seed_change_refreshes_the_config_without_losing_the_sql():
    patches = respx.patch(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "draft-1",
                "user_script": "-- my hand-written SQL\nselect 1;",
                "sql_version": "4.1.x",
            },
        )
    )
    respx.post(f"{BASE}/conn-uuid/get-file-template").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_script": "-- freshly generated, must NOT win\n",
                "config_metadata": {"input_tables": [], "seed_tables": [SEED_TABLE]},
            },
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "last_draft_script": {"id": "draft-9"}}
        )
    )
    write = respx.patch(f"{BASE}/conn-uuid/sql-scripts/draft-9").mock(
        return_value=httpx.Response(200, json={"id": "draft-9"})
    )

    result = sct.apply_seed_change(
        {
            "connector": "conn-uuid",
            "connector_api_name": "order_import",
            "payload": [{"custom_object_id": "obj-lines", "group_id": "grp-1"}],
            "regenerate": True,
            "script_id": "draft-1",
            "source_file_id": "file-1",
        }
    )

    assert json.loads(patches.calls[0].request.content)["kizen_data_seeds"]
    body = json.loads(write.calls.last.request.content)
    # The seed tables are new; the SQL is the one the user was iterating on.
    assert body["config_metadata"]["seed_tables"] == [SEED_TABLE]
    assert body["user_script"] == "-- my hand-written SQL\nselect 1;"
    assert body["sql_version"] == "4.1.x"
    assert result["seed_tables"] == ["order_lines"]
    assert result["kept_user_script"] is True


@respx.mock
def test_apply_seed_change_says_when_it_cant_refresh_yet():
    respx.patch(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    result = sct.apply_seed_change(
        {
            "connector": "conn-uuid",
            "connector_api_name": "order_import",
            "payload": [],
            "regenerate": True,
            "script_id": "draft-1",
            "source_file_id": None,
        }
    )
    assert result["refreshed"] is False
    assert "no reference file" in result["warning"]


# ---------------------------------------------------------------------------
# seed data export (so `run` exercises the same joins locally)
# ---------------------------------------------------------------------------


def test_flatten_field_value_collapses_kizens_rich_values():
    assert sct._flatten_field_value(None) == ""
    assert sct._flatten_field_value(True) == "Yes"
    assert sct._flatten_field_value({"id": "u", "name": "Inpatient"}) == "Inpatient"
    # No label — a relationship's id is the useful half.
    assert sct._flatten_field_value({"id": "u"}) == "u"
    assert sct._flatten_field_value([{"name": "A"}, {"name": "B"}]) == "A,B"


@respx.mock
def test_export_seed_data_writes_the_columns_the_script_expects(tmp_path, env_config):
    _mock_filter_groups()
    respx.post(f"{FAKE_BASE_URL}/api/records/obj-lines/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {
                        "id": "rec-1",
                        "fields": {
                            "f-lines-sku": {"name": "sku", "value": "SKU-1"},
                            "other": {"name": "ignored", "value": "x"},
                        },
                    }
                ],
            },
        )
    )
    with KizenClient(env_config) as client:
        exported, warnings = sct._export_seed_data(
            client,
            {"kizen_data_seeds": [SEED_ROW]},
            [SEED_TABLE],
            tmp_path,
            limit=100,
        )

    assert not warnings
    assert exported[0]["rows"] == 1
    assert exported[0]["filter_group"] == "Active Only"
    # The runtime opens the seed's `name` verbatim — it appends no extension.
    rows = list(csv.reader((tmp_path / "order_lines.csv").read_text().splitlines()))
    assert rows == [["kizen_id", "sku"], ["rec-1", "SKU-1"]]


@respx.mock
def test_export_seed_data_warns_instead_of_failing_the_pull(tmp_path, env_config):
    # An orphan seed table (no matching seed config) must not sink the pull.
    with KizenClient(env_config) as client:
        exported, warnings = sct._export_seed_data(
            client, {"kizen_data_seeds": []}, [SEED_TABLE], tmp_path, limit=None
        )
    assert exported == []
    assert "hand-author data/order_lines.csv" in warnings[0]

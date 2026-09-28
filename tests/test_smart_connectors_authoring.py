"""The smart-connector authoring path: create → set-input → configure-flow → run.

Everything here is respx-mocked. The interesting cases aren't the happy paths
(they're thin PATCHes) but the wire quirks the CLI exists to absorb: the
three-legged S3 upload, the source-file swap refusal, name-based resolution, and
the multi-round load-step save that relationship fields require.
"""

from __future__ import annotations

import csv
import io
import json
import time
import zipfile

import httpx
import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

import kizen_builder.cli as cli
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
# generate-sample + download-sample + activate
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


def _sample_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


# The draft's sample as `GET sql-scripts/{id}` returns it once `state` is
# `success` (confirmed live 2026-09-25).
SAMPLE_FILE = {
    "id": "sample-1",
    "name": "Draft_order_import_sample_output_20260925.zip",
    "content_type": "application/zip",
}
TWO_TABLE_ZIP = _sample_zip(
    {
        # A quoted newline is still one row.
        "contacts.csv": 'email,note\na@x.test,"line one\nline two"\nb@x.test,hi\n',
        "policies.csv": "number,holder,premium\nP1,a,10\n",
    }
)


def _mock_generate(state: str, sample: dict | None = SAMPLE_FILE):
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.post(f"{BASE}/order_import/sql-scripts/draft-1/start").mock(
        return_value=httpx.Response(200, json={"id": "draft-1"})
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200, json={"id": "draft-1", "state": state, "output_csv_file": sample}
        )
    )


@respx.mock
def test_generate_output_sample_reports_the_tables_the_sample_holds():
    _mock_generate("success")
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    result = sct.generate_output_sample("order_import")
    assert result["outputs"] == [
        {"table": "contacts", "rows": 2, "columns": 2},
        {"table": "policies", "rows": 1, "columns": 3},
    ]
    assert result["sample_file"] == {"id": "sample-1", "name": SAMPLE_FILE["name"]}
    # `headers` is still reported as it was, even though it disagrees.
    assert result["scopes"] == {"orders": 2}
    assert result["warnings"] == []


@respx.mock
def test_generate_output_sample_does_not_download_after_a_failed_run():
    # The failed script still carries the previous run's sample.
    _mock_generate("failed")
    download = respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download")
    result = sct.generate_output_sample("order_import")
    assert result["outputs"] is None
    assert result["sample_file"] is None
    assert not download.called


def _corrupt_deflated_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("orders.csv", "a,b\n" + "1,2\n" * 200)
    data = bytearray(buf.getvalue())
    # The first byte of the deflate stream, past the 30-byte local header and
    # the member name: zlib rejects it before any CRC check runs.
    data[30 + len("orders.csv")] ^= 0xFF
    return bytes(data)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, content=b"not a zip"),
        httpx.Response(200, content=_corrupt_deflated_zip()),
    ],
    ids=["download-error", "bad-zip", "corrupt-member"],
)
@respx.mock
def test_generate_output_sample_warns_when_the_sample_cannot_be_read(response):
    _mock_generate("success")
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=response
    )
    result = sct.generate_output_sample("order_import")
    assert result["state"] == "success"
    assert result["outputs"] is None
    assert len(result["warnings"]) == 1
    assert "output sample" in result["warnings"][0]


@respx.mock
def test_generate_output_sample_warns_when_success_has_no_sample_file():
    _mock_generate("success", sample=None)
    result = sct.generate_output_sample("order_import")
    assert result["outputs"] is None
    assert result["warnings"] == ["the script succeeded but has no output sample file."]


@respx.mock
def test_generate_sample_cli_flags_tables_that_differ_from_the_recognized_scopes():
    _mock_generate("success")
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "generate-sample", "order_import"]
    )
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "output tables: contacts (2 rows, 2 cols), policies (1 rows, 3 cols)" in text
    assert "recognized scopes are orders" in text
    assert "push --publish" in text


@respx.mock
def test_generate_sample_cli_is_quiet_when_the_tables_match():
    _mock_generate("success")
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=httpx.Response(
            200, content=_sample_zip({"orders.csv": "order_number,sku\n1,a\n"})
        )
    )
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "generate-sample", "order_import"]
    )
    assert result.exit_code == 0, result.output
    assert "orders (1 rows, 2 cols)" in result.output
    assert "recognized scopes" not in result.output


@respx.mock
def test_generate_sample_cli_exit_code_follows_the_state_not_the_download():
    _mock_generate("success")
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=httpx.Response(500)
    )
    ok = CliRunner().invoke(
        cli.app, ["smart-connectors", "generate-sample", "order_import", "--json"]
    )
    assert ok.exit_code == 0
    assert json.loads(ok.stdout)["outputs"] is None


def _mock_script(
    script_id: str,
    *,
    status: str,
    sample: dict | None = SAMPLE_FILE,
    state: str | None = None,
):
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{BASE}/order_import/sql-scripts/{script_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": script_id,
                "status": status,
                "state": state or ("success" if sample else "setup"),
                "output_csv_file": sample,
            },
        )
    )
    return respx.get(f"{FAKE_BASE_URL}/api/files/{(sample or {}).get('id')}/download")


@respx.mock
def test_download_sample_saves_the_drafts_zip_under_the_server_filename(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _mock_script("draft-1", status="draft").mock(
        return_value=httpx.Response(
            200,
            content=TWO_TABLE_ZIP,
            headers={"content-disposition": 'attachment; filename="server.zip"'},
        )
    )
    res = sct.download_sample("order_import")
    assert res["script_id"] == "draft-1"
    assert res["path"] == "server.zip"
    assert (tmp_path / "server.zip").read_bytes() == TWO_TABLE_ZIP
    assert [o["table"] for o in res["outputs"]] == ["contacts", "policies"]


@respx.mock
def test_download_sample_live_picks_the_live_script(tmp_path):
    live_sample = {**SAMPLE_FILE, "id": "sample-live", "name": "Live.zip"}
    _mock_script("live-1", status="live", sample=live_sample).mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    res = sct.download_sample("order_import", use_live=True, dest=tmp_path)
    assert res["script_id"] == "live-1"
    # No Content-Disposition: the S3Object's name is the fallback, and a
    # directory `dest` gets the file inside it.
    assert res["path"] == str(tmp_path / "Live.zip")
    assert res["bytes"] == len(TWO_TABLE_ZIP)


@respx.mock
def test_download_sample_live_refuses_when_there_is_no_live_script(tmp_path):
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json={**DETAIL, "live_script": None})
    )
    with pytest.raises(LookupError, match="has no live SQL script"):
        sct.download_sample("order_import", use_live=True, dest=tmp_path)
    assert list(tmp_path.iterdir()) == []


@respx.mock
def test_download_sample_script_overrides_the_draft_live_choice(tmp_path):
    # Only the named script's routes exist, so a connector lookup would fail.
    respx.get(f"{BASE}/order_import/sql-scripts/other-9").mock(
        return_value=httpx.Response(
            200,
            json={"id": "other-9", "state": "success", "output_csv_file": SAMPLE_FILE},
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/files/sample-1/download").mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    res = sct.download_sample(
        "order_import", use_live=True, script_id="other-9", dest=tmp_path
    )
    assert res["script_id"] == "other-9"
    assert res["warnings"] == []


@respx.mock
def test_download_sample_cli_warns_when_the_script_did_not_succeed(tmp_path):
    _mock_script("draft-1", status="draft", state="failed").mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    result = CliRunner().invoke(
        cli.app,
        ["smart-connectors", "download-sample", "order_import", "--out", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "script state is failed, so this sample may be from an earlier run" in text


@respx.mock
def test_download_sample_cli_names_the_state_when_there_is_no_sample(tmp_path):
    download = _mock_script("draft-1", status="draft", sample=None)
    result = CliRunner().invoke(
        cli.app,
        [
            "smart-connectors",
            "download-sample",
            "order_import",
            "--out",
            str(tmp_path / "s.zip"),
        ],
    )
    assert result.exit_code == 1
    text = " ".join(result.output.split())
    assert "state: setup" in text
    assert "generate-sample order_import" in text
    assert not download.called
    assert not (tmp_path / "s.zip").exists()


@respx.mock
def test_download_sample_refuses_to_overwrite_without_force(tmp_path):
    target = tmp_path / "s.zip"
    target.write_bytes(b"keep me")
    _mock_script("draft-1", status="draft").mock(
        return_value=httpx.Response(200, content=TWO_TABLE_ZIP)
    )
    args = ["smart-connectors", "download-sample", "order_import", "--out", str(target)]
    refused = CliRunner().invoke(cli.app, args)
    assert refused.exit_code == 1
    assert "--force" in refused.output
    assert target.read_bytes() == b"keep me"

    forced = CliRunner().invoke(cli.app, [*args, "--force", "--json"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.stdout)["path"] == str(target)
    assert target.read_bytes() == TWO_TABLE_ZIP


@pytest.mark.parametrize(
    ("disposition", "fallback", "expected"),
    [
        ('attachment; filename="../x.zip"', "unused.zip", ".._x.zip"),
        (None, "../x.zip", ".._x.zip"),
        (
            'attachment; filename="Orders / Returns_dry_run.xlsx"',
            "unused.xlsx",
            "Orders _ Returns_dry_run.xlsx",
        ),
        (None, "Orders / Returns_dry_run.xlsx", "Orders _ Returns_dry_run.xlsx"),
        ('attachment; filename=".."', "..", "download"),
    ],
    ids=[
        "traversal-header",
        "traversal-fallback",
        "slash-header",
        "slash-fallback",
        "nothing-usable",
    ],
)
@respx.mock
def test_save_file_keeps_the_name_inside_the_destination(
    tmp_path, monkeypatch, env_config, disposition, fallback, expected
):
    monkeypatch.chdir(tmp_path)
    headers = {"content-disposition": disposition} if disposition else {}
    respx.get(f"{FAKE_BASE_URL}/api/files/f1/download").mock(
        return_value=httpx.Response(200, content=b"data", headers=headers)
    )
    saved = sct.save_file(env_config, "f1", fallback_name=fallback)
    assert saved.name == expected
    assert [p.name for p in tmp_path.iterdir()] == [expected]
    assert not (tmp_path.parent / "x.zip").exists()


@respx.mock
def test_save_file_refuses_an_existing_path_before_downloading(tmp_path, env_config):
    target = tmp_path / "s.zip"
    target.write_bytes(b"keep me")
    download = respx.get(f"{FAKE_BASE_URL}/api/files/f1/download")
    with pytest.raises(FileExistsError, match="--force"):
        sct.save_file(env_config, "f1", target, fallback_name="s.zip")
    assert not download.called


@respx.mock
def test_save_file_keeps_the_old_file_when_a_forced_write_fails(
    tmp_path, monkeypatch, env_config
):
    target = tmp_path / "s.zip"
    target.write_bytes(b"keep me")
    respx.get(f"{FAKE_BASE_URL}/api/files/f1/download").mock(
        return_value=httpx.Response(200, content=b"new bytes")
    )

    def disk_full(self, data):
        with open(self, "wb") as f:
            f.write(data[:3])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(type(target), "write_bytes", disk_full)
    with pytest.raises(OSError, match="No space"):
        sct.save_file(env_config, "f1", target, fallback_name="s.zip", force=True)
    assert target.read_bytes() == b"keep me"
    assert [p.name for p in tmp_path.iterdir()] == ["s.zip"]


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
# executions get / download / the retired flat verbs
# ---------------------------------------------------------------------------

EID = "1d8d2633-312d-4848-a5fb-95325dcea6df"


def _s3(file_id: str, name: str) -> dict:
    return {
        "id": file_id,
        "name": name,
        "size_bytes": 7234,
        "size_formatted": "7.1KB",
        "url": f"{FAKE_BASE_URL}/api/files/{file_id}/download",
    }


# The live shape (cli-testing, 2026-09-25): three S3Objects, a nested
# started_by, and step_progress grouped by stage.
EXEC_ROW = {
    "id": EID,
    "status": "success",
    "trigger_type": "fileupload",
    "is_dry_run": True,
    "started_by": {"id": "u1", "display_name": "Pat Admin (pat@example.test)"},
    "created": "2026-07-28T12:43:05-05:00",
    "ended_at": "2026-07-28T12:43:10-05:00",
    "error_details": None,
    "final_report": _s3("f-report", "Order Import_dry_run_output_1.xlsx"),
    "sql_output_zip": _s3("f-output", "Live_order_import_sample_output_1.zip"),
    "input_file": _s3("f-input", "orders.csv"),
    "step_progress": [
        {
            "type": "smart_connector_sql_run",
            "status": "completed",
            "steps": [
                {
                    "status": "completed",
                    "scope": None,
                    "custom_object": None,
                    "valid_records": 1,
                    "invalid_records": 0,
                    "total": 1,
                }
            ],
        },
        {
            "type": "smart_connector_load_step_run",
            "status": "completed",
            "steps": [
                {
                    "status": "completed",
                    "scope": "orders",
                    "custom_object": {
                        "id": "obj-orders",
                        "name": "orders",
                        "object_name": "Orders",
                    },
                    "valid_records": 2,
                    "invalid_records": 1,
                    "total": 3,
                }
            ],
        },
    ],
}

# A failed run keeps its input file but has no report or output zip, and a
# cancelled stage can come back with no steps at all.
FAILED_ERROR = (
    "Error running connector SQL script: Code: 47. DB::Exception: There's no "
    "column 's.sku' in table 's': While processing row[field] AS [/.-]. "
    "(UNKNOWN_IDENTIFIER)"
)
FAILED_ROW = {
    **EXEC_ROW,
    "status": "failed",
    "error_details": FAILED_ERROR,
    "final_report": None,
    "sql_output_zip": None,
    "step_progress": [
        {
            "type": "smart_connector_execution_variable_eval",
            "status": "cancelled",
            "steps": [],
        }
    ],
}


def _mock_execution(row: dict) -> respx.Route:
    return respx.get(f"{BASE}/order_import/executions").mock(
        return_value=httpx.Response(
            200, json={"count": 1, "next": None, "results": [row]}
        )
    )


@respx.mock
def test_get_execution_flattens_started_by_and_keeps_the_rest_raw():
    _mock_execution(EXEC_ROW)
    row = sct.get_execution("order_import", EID)
    assert row["started_by"] == "Pat Admin (pat@example.test)"
    assert {k: v for k, v in row.items() if k != "started_by"} == {
        k: v for k, v in EXEC_ROW.items() if k != "started_by"
    }


@respx.mock
def test_executions_get_cli_shows_the_full_error_steps_and_files():
    _mock_execution(FAILED_ROW)
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "executions", "get", "order_import", EID]
    )
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    # In full, and verbatim despite the brackets.
    assert FAILED_ERROR in text
    assert "execution_variable_eval │ cancelled" in text
    assert "report: — · output: — · input: orders.csv (7.1KB)" in text


@respx.mock
def test_executions_get_cli_step_table_strips_the_type_prefix():
    _mock_execution(EXEC_ROW)
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "executions", "get", "order_import", EID]
    )
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "load_step_run │ completed │ orders │ Orders │ 2 │ 1 │ 3" in text
    assert "smart_connector_" not in text
    assert "report: Order Import_dry_run_output_1.xlsx (7.1KB)" in text


@respx.mock
def test_executions_get_cli_json_emits_the_row():
    _mock_execution(EXEC_ROW)
    result = CliRunner().invoke(
        cli.app,
        ["smart-connectors", "executions", "get", "order_import", EID, "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        **EXEC_ROW,
        "started_by": "Pat Admin (pat@example.test)",
    }


@respx.mock
def test_executions_get_cli_names_a_missing_execution():
    respx.get(f"{BASE}/order_import/executions").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "executions", "get", "order_import", EID]
    )
    assert result.exit_code == 1
    assert f"no execution {EID}" in " ".join(result.output.split())


SERVED = b"PK\x03\x04 bytes as served"


@pytest.mark.parametrize(
    ("args", "kind", "file_id", "name"),
    [
        ([], "report", "f-report", "Order Import_dry_run_output_1.xlsx"),
        (
            ["--file", "report"],
            "report",
            "f-report",
            "Order Import_dry_run_output_1.xlsx",
        ),
        (
            ["--file", "output"],
            "output",
            "f-output",
            "Live_order_import_sample_output_1.zip",
        ),
        (["--file", "input"], "input", "f-input", "orders.csv"),
    ],
    ids=["default-is-report", "report", "output", "input"],
)
@respx.mock
def test_executions_download_saves_the_chosen_file(tmp_path, args, kind, file_id, name):
    _mock_execution(EXEC_ROW)
    respx.get(f"{FAKE_BASE_URL}/api/files/{file_id}/download").mock(
        return_value=httpx.Response(200, content=SERVED)
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "smart-connectors",
            "executions",
            "download",
            "order_import",
            EID,
            *args,
            "--out",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    path = tmp_path / name
    assert json.loads(result.stdout) == {
        "path": str(path),
        "name": name,
        "bytes": len(SERVED),
        "kind": kind,
    }
    assert path.read_bytes() == SERVED


@respx.mock
def test_executions_download_prints_the_path_and_size(tmp_path):
    _mock_execution(EXEC_ROW)
    respx.get(f"{FAKE_BASE_URL}/api/files/f-report/download").mock(
        return_value=httpx.Response(200, content=b"xlsx")
    )
    target = tmp_path / "report.xlsx"
    result = CliRunner().invoke(
        cli.app,
        [
            "smart-connectors",
            "executions",
            "download",
            "order_import",
            EID,
            "--out",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"saved {target} (4 bytes, report)" in " ".join(result.output.split())


@pytest.mark.parametrize("kind", ["report", "output"])
@respx.mock
def test_executions_download_refuses_a_failed_runs_missing_file(tmp_path, kind):
    _mock_execution(FAILED_ROW)
    download = respx.get(url__regex=rf"{FAKE_BASE_URL}/api/files/.*")
    result = CliRunner().invoke(
        cli.app,
        [
            "smart-connectors",
            "executions",
            "download",
            "order_import",
            EID,
            "--file",
            kind,
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    text = " ".join(result.output.split())
    assert (
        f"execution {EID} is `failed`; failed runs have no output zip or report" in text
    )
    assert not download.called
    assert list(tmp_path.iterdir()) == []


@respx.mock
def test_executions_download_refuses_to_overwrite_without_force(tmp_path):
    target = tmp_path / "orders.csv"
    target.write_bytes(b"keep me")
    _mock_execution(EXEC_ROW)
    respx.get(f"{FAKE_BASE_URL}/api/files/f-input/download").mock(
        return_value=httpx.Response(200, content=b"new")
    )
    args = [
        "smart-connectors",
        "executions",
        "download",
        "order_import",
        EID,
        "--file",
        "input",
        "--out",
        str(target),
    ]
    refused = CliRunner().invoke(cli.app, args)
    assert refused.exit_code == 1
    assert "--force" in refused.output
    assert target.read_bytes() == b"keep me"

    forced = CliRunner().invoke(cli.app, [*args, "--force"])
    assert forced.exit_code == 0, forced.output
    assert target.read_bytes() == b"new"


def test_executions_download_rejects_an_unknown_file_kind():
    result = CliRunner().invoke(
        cli.app,
        ["smart-connectors", "executions", "download", "c", EID, "--file", "zip"],
    )
    assert result.exit_code == 2
    text = " ".join(result.output.split())
    assert "'report', 'output', 'input'" in text


@respx.mock
def test_start_flow_names_the_queued_execution_in_its_hint():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json={**DETAIL, "status": "operational"})
    )
    respx.post(f"{BASE}/conn-uuid/start-connector-flow").mock(
        return_value=httpx.Response(200, json={"execution_id": EID})
    )
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "start-flow", "order_import", "--force"]
    )
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert f"`smart-connectors executions get order_import {EID}`" in text
    assert "`smart-connectors executions list order_import --include-dry-run`" in text


@respx.mock
def test_executions_list_cli_prints_a_bracketed_error_verbatim():
    _mock_execution(FAILED_ROW)
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "executions", "list", "order_import"]
    )
    assert result.exit_code == 0, result.output
    assert "[/.-]" in "".join(result.output.split())


@pytest.mark.parametrize(
    "args",
    [
        ["order_import"],
        ["order_import", "--status", "failed", "--json"],
        ["order_import", "--help"],
    ],
    ids=["bare", "with-options", "help"],
)
def test_the_old_list_form_points_at_executions_list(args):
    result = CliRunner().invoke(cli.app, ["smart-connectors", "executions", *args])
    assert result.exit_code == 2
    assert "smart-connectors executions list order_import" in " ".join(
        result.output.split()
    )


@pytest.mark.parametrize(
    ("args", "hint"),
    [
        (["view", "c", EID], "Did you mean 'get'?"),
        (["show", "c", EID], "Did you mean 'get'?"),
        (["lst", "c"], "Did you mean 'list'?"),
        (["nope", "c", EID], "No such command 'nope'."),
    ],
    ids=["view", "show", "near-miss", "unknown"],
)
def test_other_unknown_executions_verbs_do_not_get_the_old_form_pointer(args, hint):
    result = CliRunner().invoke(cli.app, ["smart-connectors", "executions", *args])
    assert result.exit_code == 2
    text = " ".join(result.output.split())
    assert hint in text
    assert "run history" not in text


def test_the_retired_execution_sql_verb_is_gone():
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "execution-sql", "c", EID]
    )
    assert result.exit_code == 2


def test_tab_completion_after_an_old_form_connector_lists_subcommands():
    words = "kizen smart-connectors executions my_conn "
    result = CliRunner().invoke(
        cli.app,
        [],
        prog_name="kizen",
        env={
            "_KIZEN_COMPLETE": "complete_bash",
            "COMP_WORDS": words,
            "COMP_CWORD": "4",
        },
    )
    assert result.exit_code == 0, result.exception
    assert result.output.split() == ["list", "get", "download", "sql"]


@respx.mock
def test_executions_sql_prints_the_script_verbatim():
    respx.get(f"{BASE}/order_import/executions/{EID}/sql-script").mock(
        return_value=httpx.Response(200, json={"user_script": "SELECT row[field]"})
    )
    result = CliRunner().invoke(
        cli.app, ["smart-connectors", "executions", "sql", "order_import", EID]
    )
    assert result.exit_code == 0, result.output
    assert "SELECT row[field]" in result.stdout


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
    doesn't surface in `executions list --json`. Flag it at plan time instead."""
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


def _mock_record_counts(object_id: str, *, segment: int, total: int):
    """Serve `count_records`: a search with a query is the segment, without is
    the whole object."""

    def respond(request):
        query = json.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={"count": segment if query else total, "next": None, "results": []},
        )

    return respx.post(f"{FAKE_BASE_URL}/api/records/{object_id}/search").mock(
        side_effect=respond
    )


def _mock_filter_groups(object_id: str = "obj-lines") -> None:
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{object_id}/filter-groups").mock(
        return_value=httpx.Response(
            200, json={"count": 1, "next": None, "results": FILTER_GROUPS}
        )
    )
    # A default for the coverage counts; a test that cares re-mocks the route.
    _mock_record_counts(object_id, segment=7, total=7)


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


@respx.mock
def test_plan_add_seed_without_a_group_seeds_every_record():
    _mock_object_lookups()
    groups = respx.get(f"{FAKE_BASE_URL}/api/custom-objects/obj-lines/filter-groups")
    respx.get(f"{BASE}/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "kizen_data_seeds": [SEED_ROW]}
        )
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="order_lines", fields=["sku"]
    )

    # An explicit null, matching the schema (nullable + required); the row id is
    # reused because this replaces the existing order_lines seed.
    assert plan["payload"] == [
        {
            "custom_object_id": "obj-lines",
            "group_id": None,
            "fields_ids": ["f-lines-sku"],
            "id": "seed-1",
        }
    ]
    assert plan["filter_group"] == "all records"
    assert "coverage" not in plan
    assert not groups.called


@respx.mock
def test_plan_add_seed_with_a_group_carries_its_coverage():
    _mock_object_lookups()
    _mock_filter_groups()
    counts = _mock_record_counts("obj-lines", segment=5, total=7)
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="order_lines", group="Active Only"
    )

    assert plan["coverage"] == {"segment": 5, "total": 7}
    assert plan["payload"] == [{"custom_object_id": "obj-lines", "group_id": "grp-1"}]
    queries = [json.loads(c.request.content)["query"] for c in counts.calls]
    assert queries == [FILTER_GROUPS[0]["config"]["query"], []]
    assert all(c.request.url.params["page_size"] == "1" for c in counts.calls)


@respx.mock
def test_plan_add_seed_survives_a_failed_count():
    _mock_object_lookups()
    _mock_filter_groups()
    respx.post(f"{FAKE_BASE_URL}/api/records/obj-lines/search").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )

    plan = sct.plan_add_seed(
        "order_import", custom_object="order_lines", group="Active Only"
    )

    # The counts only feed a warning, so losing them costs the warning, not the plan.
    assert "coverage" not in plan
    assert plan["payload"] == [{"custom_object_id": "obj-lines", "group_id": "grp-1"}]


def _seed_add_plan(**overrides):
    return {
        "env": "test",
        "connector": "conn-uuid",
        "connector_api_name": "order_import",
        "custom_object": "order_lines",
        "filter_group": "Active Only",
        "fields": None,
        "view": "kizen.order_lines",
        "replacing": False,
        "payload": [{"custom_object_id": "obj-lines", "group_id": "grp-1"}],
        "regenerate": True,
        "script_id": "draft-1",
        "source_file_id": None,
        **overrides,
    }


def _seeds_add(monkeypatch, plan, *args):
    from typer.testing import CliRunner

    import kizen_builder.cli as cli

    planned: dict = {}

    def fake_plan(*a, **k):
        planned.update(k)
        return plan

    monkeypatch.setattr(sct, "plan_add_seed", fake_plan)
    result = CliRunner().invoke(
        cli.app,
        ["smart-connectors", "seeds", "add", "order_import", "-o", "order_lines"]
        + list(args),
    )
    return result, planned


def test_seeds_add_preview_warns_when_the_segment_leaves_records_out(monkeypatch):
    plan = _seed_add_plan(coverage={"segment": 5, "total": 7})

    result, _ = _seeds_add(monkeypatch, plan, "-g", "Active Only", "--dry-run")
    assert result.exit_code == 0
    assert "covers 5 of 7 records" in result.stdout
    assert "match-or-create connector re-creates them" in result.stdout
    assert "kizen_id only" in result.stdout

    # --json keeps the warning on stderr with the rest of the preview.
    result, _ = _seeds_add(
        monkeypatch, plan, "-g", "Active Only", "--dry-run", "--json"
    )
    assert result.exit_code == 0
    assert "covers 5 of 7 records" in result.stderr
    assert json.loads(result.stdout)["coverage"] == {"segment": 5, "total": 7}


def test_seeds_add_preview_is_quiet_when_the_segment_covers_everything(monkeypatch):
    plan = _seed_add_plan(coverage={"segment": 7, "total": 7})
    result, _ = _seeds_add(monkeypatch, plan, "-g", "Active Only", "--dry-run")
    assert result.exit_code == 0
    assert "records outside it" not in result.stdout
    assert "couldn't count" not in result.stdout


def test_seeds_add_preview_says_when_it_couldnt_check_coverage(monkeypatch):
    # A failed count leaves no `coverage`; that must not read as full coverage.
    result, _ = _seeds_add(
        monkeypatch, _seed_add_plan(), "-g", "Active Only", "--dry-run"
    )
    assert result.exit_code == 0
    assert "couldn't count the segment's records" in result.stdout


def test_seeds_add_preview_shows_all_records_for_a_null_group(monkeypatch):
    plan = _seed_add_plan(
        filter_group="all records",
        payload=[{"custom_object_id": "obj-lines", "group_id": None}],
    )
    result, planned = _seeds_add(monkeypatch, plan, "--dry-run")
    assert result.exit_code == 0
    assert planned["group"] is None
    assert "all records" in result.stdout
    assert "records outside it" not in result.stdout
    assert "couldn't count" not in result.stdout


@respx.mock
def test_list_seeds_shows_a_null_group_as_all_records():
    respx.get(f"{BASE}/order_import").mock(
        return_value=httpx.Response(
            200,
            json={
                **DETAIL,
                "kizen_data_seeds": [{**SEED_ROW, "group_id": None, "group": None}],
            },
        )
    )
    respx.get(f"{BASE}/order_import/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "draft-1", "config_metadata": {"seed_tables": [SEED_TABLE]}},
        )
    )
    rows = sct.list_seeds("order_import")
    assert rows[0]["filter_group"] == "all records"
    # The raw value stays visible in JSON/CSV.
    assert rows[0]["group_id"] is None


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


@respx.mock
def test_export_seed_data_exports_every_record_for_a_null_group(tmp_path, env_config):
    search = respx.post(f"{FAKE_BASE_URL}/api/records/obj-lines/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {
                        "id": "rec-1",
                        "fields": {"f-lines-sku": {"name": "sku", "value": "SKU-1"}},
                    }
                ],
            },
        )
    )
    with KizenClient(env_config) as client:
        exported, warnings = sct._export_seed_data(
            client,
            {"kizen_data_seeds": [{**SEED_ROW, "group_id": None, "group": None}]},
            [SEED_TABLE],
            tmp_path,
            limit=100,
        )

    # No filter group to resolve, so no filter-groups call (respx would raise).
    assert not warnings
    assert exported[0]["filter_group"] == "all records"
    assert json.loads(search.calls.last.request.content)["query"] == []
    rows = list(csv.reader((tmp_path / "order_lines.csv").read_text().splitlines()))
    assert rows == [["kizen_id", "sku"], ["rec-1", "SKU-1"]]

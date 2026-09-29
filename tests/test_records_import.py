"""`records import`: the bulk-action poller, the import planner, and the
apply walk (count → upload → uploader → progress → failure report → count).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from kizen_builder import cli
from kizen_builder.api.bulk_actions import list_bulk_actions
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.tools import bulk_actions
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.bulk_actions import wait_for_bulk_action
from kizen_builder.tools.planners.records import plan_import_records
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL, load_fixture

PATIENTS = "patients"
PATIENTS_ID = "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"
PROGRESS_ID = "prog-1"
PROGRESS_URL = f"{FAKE_BASE_URL}/api/bulk-action-progress/{PROGRESS_ID}"
S3_URL = "https://files.example.test/"


def _field_id(api_name: str) -> str:
    obj = load_fixture(f"objects/{PATIENTS}.json")
    return next(f["id"] for f in obj["fields"] if f["api_name"] == api_name)


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(bulk_actions.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# wait_for_bulk_action
# ---------------------------------------------------------------------------


@respx.mock
def test_wait_polls_through_unknown_status_to_terminal(client):
    route = respx.get(PROGRESS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"id": PROGRESS_ID, "status": "initialize"}),
            httpx.Response(200, json={"id": PROGRESS_ID, "status": "brand_new"}),
            httpx.Response(200, json={"id": PROGRESS_ID, "status": "in_progress"}),
            httpx.Response(200, json={"id": PROGRESS_ID, "status": "completed"}),
        ]
    )
    row = wait_for_bulk_action(client, PROGRESS_ID, poll_interval=0.01)
    assert route.call_count == 4
    assert row["status"] == "completed"
    assert row["timed_out"] is False
    assert row["polls"] == 4


@pytest.mark.parametrize("status", ["failed", "cancelled", "skipped"])
@respx.mock
def test_wait_stops_on_every_terminal_status(client, status):
    respx.get(PROGRESS_URL).mock(
        return_value=httpx.Response(200, json={"id": PROGRESS_ID, "status": status})
    )
    row = wait_for_bulk_action(client, PROGRESS_ID)
    assert row["status"] == status and row["timed_out"] is False


@respx.mock
def test_wait_times_out_on_a_job_that_never_finishes(client):
    respx.get(PROGRESS_URL).mock(
        return_value=httpx.Response(200, json={"id": PROGRESS_ID, "status": "queued"})
    )
    row = wait_for_bulk_action(client, PROGRESS_ID, timeout=0.05, poll_interval=0.01)
    assert row["timed_out"] is True
    assert row["status"] == "queued"


@respx.mock
def test_wait_tolerates_transient_5xx_and_network_errors(client):
    respx.get(PROGRESS_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("dropped"),
            httpx.Response(502),
            httpx.Response(200, json={"id": PROGRESS_ID, "status": "completed"}),
        ]
    )
    row = wait_for_bulk_action(client, PROGRESS_ID)
    assert row["status"] == "completed"
    assert row["polls"] == 1


@respx.mock
def test_wait_raises_after_too_many_consecutive_5xx(client):
    respx.get(PROGRESS_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(KizenAPIError) as exc:
        wait_for_bulk_action(client, PROGRESS_ID)
    assert exc.value.status_code == 500


@respx.mock
def test_wait_raises_a_4xx_at_once(client):
    route = respx.get(PROGRESS_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(KizenAPIError) as exc:
        wait_for_bulk_action(client, PROGRESS_ID)
    assert exc.value.status_code == 404
    assert route.call_count == 1


@respx.mock
def test_list_bulk_actions_filters_and_follows_pages(client):
    route = respx.get(f"{FAKE_BASE_URL}/api/bulk-action-progress").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"id": "a"}], "next": "p2"}),
            httpx.Response(200, json={"results": [{"id": "b"}], "next": None}),
        ]
    )
    rows = list_bulk_actions(
        client,
        custom_object_id="obj-1",
        action="custom_object_archive",
        completed=False,
    )
    assert [r["id"] for r in rows] == ["a", "b"]
    params = route.calls.last.request.url.params
    assert params["custom_object_id"] == "obj-1"
    assert params["action"] == "custom_object_archive"
    assert params["completed"] == "false"
    assert params["page"] == "2"
    assert "started_after" not in params


# ---------------------------------------------------------------------------
# plan_import_records
# ---------------------------------------------------------------------------


def test_plan_builds_one_op_with_uuid_keyed_mapper(patch_live_lookups):
    plan = plan_import_records(
        PATIENTS,
        [
            {"name": "Ada", "gender": "female", "encounters": "Visit 1"},
            {"name": "Bo", "mrn": 42, "deceased": True},
        ],
    )
    (op,) = plan.operations
    assert op.kind == "record_import" and op.action == "upsert"
    assert op.parent_object_uuid == PATIENTS_ID
    p = op.payload
    assert p["header"] == ["name", "gender", "encounters", "mrn", "deceased"]
    # Option labels are canonicalized; every cell is a string; gaps are blank.
    assert p["rows"] == [
        ["Ada", "Female", "Visit 1", "", ""],
        ["Bo", "", "", "42", "true"],
    ]
    body = p["body"]
    assert body["create_update_mode"] == "create_or_update"
    assert body["name_column"] == 0
    assert "kizen_id_column" not in body
    assert body["fields_for_matching"] == [
        {"key": "name", "unarchive_mode": "unarchive"}
    ]
    assert "unarchives" in op.preview["warning"]
    assert body["field_mapper"][_field_id("gender")] == {
        "csv_column": 1,
        "conflict_resolution": "overwrite_except_null",
    }
    assert body["field_mapper"][_field_id("encounters")] == {
        "csv_column": 2,
        "conflict_resolution": "overwrite_except_null",
        "field_for_matching": "name",
        "create_if_not_found": False,
    }
    assert _field_id("name") not in body["field_mapper"]
    assert p["timeout"] == 900.0


def test_plan_update_by_id_matches_on_kizen_id(patch_live_lookups):
    plan = plan_import_records(
        PATIENTS, [{"id": "rec-1", "mrn": "7"}], mode="update", resolution="overwrite"
    )
    body = plan.operations[0].payload["body"]
    assert body["create_update_mode"] == "update_only"
    assert body["kizen_id_column"] == 0
    assert body["fields_for_matching"] == [{"key": "id", "unarchive_mode": "unarchive"}]
    assert "name_column" not in body
    assert body["field_mapper"][_field_id("mrn")]["conflict_resolution"] == (
        "overwrite"
    )


def test_plan_create_mode(patch_live_lookups):
    plan = plan_import_records(PATIENTS, [{"name": "Ada"}], mode="create")
    (op,) = plan.operations
    assert op.payload["body"]["create_update_mode"] == "create_only"
    assert "warning" not in op.preview


def test_plan_reads_lookup_value_as_name(patch_live_lookups):
    plan = plan_import_records(PATIENTS, [{"lookup_value": "Ada", "mrn": "1"}])
    p = plan.operations[0].payload
    assert p["header"] == ["mrn", "name"]
    assert p["rows"] == [["1", "Ada"]]
    assert p["body"]["name_column"] == 1


def test_plan_makes_no_http_calls(patch_live_lookups):
    with respx.mock(assert_all_called=False) as mock:
        plan_import_records(PATIENTS, [{"name": "Ada"}])
    assert mock.calls.call_count == 0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"records": [{"name": "Ada", "colour": "x"}]},
            r"'colour' not found.*Available",
        ),
        ({"records": [{"name": "Ada", "gender": "Nope"}]}, r"'Nope'.*Valid: \['Male'"),
        ({"records": [{"name": "Ada", "gender": ["Male"]}]}, "list or object"),
        ({"records": [{"name": "Ada", "encounters": {"id": "r"}}]}, "list or object"),
        ({"records": [{"fields": [{"name": "name", "value": "Ada"}]}]}, "raw 'fields'"),
        ({"records": [{"mrn": "1"}]}, "no 'name'"),
        ({"records": [{"mrn": "1"}], "mode": "create"}, "no 'name'"),
        ({"records": [{"mrn": "1"}], "mode": "update"}, "neither 'id' nor 'name'"),
        ({"records": [{"id": "r1", "name": "Ada"}]}, "only works with --mode update"),
        ({"records": [{"id": "r1", "name": "Ada"}], "mode": "create"}, "--mode update"),
        (
            {"records": [{"id": "r1", "mrn": "1"}, {"mrn": "2"}], "mode": "update"},
            "#2 has no 'id'",
        ),
        (
            {"records": [{"id": "r1", "name": "Ada"}, {"id": "r2"}], "mode": "update"},
            "#2 has a blank 'name'",
        ),
        (
            {"records": [{"name": "Ada", "lookup_value": "Bo"}]},
            "both 'name' and 'lookup_value'",
        ),
        ({"records": [{"name": "Ada"}], "mode": "merge"}, "invalid mode"),
        (
            {"records": [{"name": "Ada"}], "resolution": "add_only"},
            "invalid resolution",
        ),
        ({"records": [{"name": "Ada"}], "timeout": 0}, "timeout"),
        (
            {
                "records": [{"name": "Ada", "mrn": "1"}, {"name": "Bo"}],
                "resolution": "overwrite",
            },
            r"#2 lacks \['mrn'\]",
        ),
        ({"records": []}, "no records"),
    ],
)
def test_plan_rejects(patch_live_lookups, kwargs, match):
    with pytest.raises(PlanError, match=match):
        plan_import_records(PATIENTS, **kwargs)


@pytest.mark.parametrize("obj", ["deals", "client_client"])
def test_plan_rejects_pipelines_and_contacts(patch_live_lookups, obj):
    with pytest.raises(PlanError, match="separate uploader"):
        plan_import_records(obj, [{"name": "Ada"}])


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

_REPORT = (
    "﻿Row Number,Entity Name,Record ID,Kizen URL,Failure Status,Error Messages\r\n"
    "3,Bo,rec-bo,https://x/rec-bo,Partial,Bo Co isn't a valid option value\r\n"
)


def _mock_import_walk(progress_rows, report: str | None = None):
    respx.post(f"{FAKE_BASE_URL}/api/records/{PATIENTS_ID}/search").mock(
        side_effect=[
            httpx.Response(200, json={"count": 5, "results": []}),
            httpx.Response(200, json={"count": 7, "results": []}),
        ]
    )
    presign = respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "file-1"},
        )
    )
    s3 = respx.post(S3_URL).mock(
        return_value=httpx.Response(204, headers={"etag": '"e"'})
    )
    respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-1", "name": "x.csv"})
    )
    uploader = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/uploader"
    ).mock(return_value=httpx.Response(200, json={"status_id": PROGRESS_ID}))
    progress = respx.get(PROGRESS_URL).mock(
        side_effect=[httpx.Response(200, json=r) for r in progress_rows]
    )
    download = respx.get(f"{FAKE_BASE_URL}/api/files/report-1/download").mock(
        return_value=httpx.Response(200, content=(report or "").encode("utf-8"))
    )
    return presign, s3, uploader, progress, download


@respx.mock
def test_apply_walks_upload_submit_poll_and_reads_the_failure_report(
    patch_live_lookups,
):
    presign, s3, uploader, progress, download = _mock_import_walk(
        [
            {"id": PROGRESS_ID, "status": "initialize"},
            {
                "id": PROGRESS_ID,
                "status": "completed",
                "success_count": 2,
                "failed_count": 0,
                "pending_count": 0,
                "failure_report": {"id": "report-1"},
            },
        ],
        report=_REPORT,
    )
    plan = plan_import_records(
        PATIENTS, [{"name": "Ada", "mrn": "1"}, {"name": "Bo", "mrn": "2"}]
    )
    result = plan_tools.apply_plan(plan)

    assert presign.calls.last.request.url.params["source"] == "record_import"
    assert presign.calls.last.request.url.params["contenttype"] == "text/csv"
    csv_sent = s3.calls.last.request.content.decode()
    assert "name,mrn\r\nAda,1\r\nBo,2\r\n" in csv_sent
    assert uploader.call_count == 1
    sent = json.loads(uploader.calls.last.request.content)
    assert sent["s3_object_id"] == "file-1"
    assert sent["create_update_mode"] == "create_or_update"
    assert progress.call_count == 2
    assert download.call_count == 1

    (r,) = result.results
    assert r.status == "failed"
    assert r.raw is not None
    assert r.raw["row_errors"] == [
        {
            "row": 2,
            "name": "Bo",
            "record_id": "rec-bo",
            "error": "Bo Co isn't a valid option value",
        }
    ]
    assert r.raw["status_id"] == PROGRESS_ID
    assert r.raw["failure_report_id"] == "report-1"
    assert (r.raw["count_before"], r.raw["count_after"]) == (5, 7)
    assert r.message is not None
    assert "1 row error(s): #2 Bo: Bo Co isn't a valid option value" in r.message


@respx.mock
def test_apply_clean_import_is_ok(patch_live_lookups):
    *_, download = _mock_import_walk(
        [
            {
                "id": PROGRESS_ID,
                "status": "completed",
                "success_count": 1,
                "failed_count": 0,
                "pending_count": 0,
                "failure_report": None,
            }
        ]
    )
    result = plan_tools.apply_plan(plan_import_records(PATIENTS, [{"name": "Ada"}]))
    assert result.all_ok
    assert download.call_count == 0
    assert result.results[0].message == (
        "completed: 1 succeeded, 0 failed, 0 pending; records 5 → 7"
    )


def test_plan_overwrite_accepts_uniform_rows_with_nulls(patch_live_lookups):
    plan = plan_import_records(
        PATIENTS,
        [{"name": "Ada", "mrn": "1"}, {"name": "Bo", "mrn": None}],
        resolution="overwrite",
    )
    assert plan.operations[0].payload["rows"] == [["Ada", "1"], ["Bo", ""]]


_CLEAN = {
    "status_id": PROGRESS_ID,
    "status": "completed",
    "timed_out": False,
    "success_count": 3,
    "failed_count": 0,
    "pending_count": 0,
    "row_errors": [],
    "count_before": 0,
    "count_after": 3,
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "ok"),
        ({"failed_count": 1}, "failed"),
        ({"pending_count": 2}, "failed"),
        ({"failed_count": None}, "failed"),
        ({"status": "failed"}, "failed"),
        ({"timed_out": True, "status": "in_progress"}, "failed"),
        ({"row_errors": [{"row": 1, "name": "A", "error": "x"}]}, "failed"),
    ],
)
def test_record_import_outcome(changes, expected):
    status, _ = plan_tools._record_import_outcome({**_CLEAN, **changes})
    assert status == expected


@respx.mock
def test_apply_counts_failed_rows_without_a_report(patch_live_lookups):
    _mock_import_walk(
        [
            {
                "id": PROGRESS_ID,
                "status": "completed",
                "success_count": 1,
                "failed_count": 1,
                "pending_count": 0,
                "failure_report": None,
            }
        ]
    )
    result = plan_tools.apply_plan(plan_import_records(PATIENTS, [{"name": "Ada"}]))
    assert result.results[0].status == "failed"


@respx.mock
def test_apply_failed_job_fails_the_op(patch_live_lookups):
    _mock_import_walk([{"id": PROGRESS_ID, "status": "failed"}])
    result = plan_tools.apply_plan(plan_import_records(PATIENTS, [{"name": "Ada"}]))
    assert result.results[0].status == "failed"


@respx.mock
def test_apply_timeout_fails_the_op(patch_live_lookups, monkeypatch):
    _mock_import_walk([{"id": PROGRESS_ID, "status": "in_progress"}] * 50)
    plan = plan_import_records(PATIENTS, [{"name": "Ada"}], timeout=0.001)
    result = plan_tools.apply_plan(plan)
    (r,) = result.results
    assert r.status == "failed"
    assert r.message is not None and r.message.startswith("timed out waiting on")


@respx.mock
def test_apply_without_status_id_fails_and_does_not_resubmit(patch_live_lookups):
    *_, uploader, progress, _ = _mock_import_walk([])
    uploader.mock(return_value=httpx.Response(200, json={"s3_object_id": "file-1"}))
    result = plan_tools.apply_plan(plan_import_records(PATIENTS, [{"name": "Ada"}]))
    (r,) = result.results
    assert r.status == "failed"
    assert r.message is not None and "no status_id" in r.message
    assert uploader.call_count == 1
    assert progress.call_count == 0


@respx.mock
def test_apply_uploader_5xx_is_not_retried(patch_live_lookups):
    *_, uploader, _, _ = _mock_import_walk([])
    uploader.mock(return_value=httpx.Response(502))
    result = plan_tools.apply_plan(plan_import_records(PATIENTS, [{"name": "Ada"}]))
    assert result.results[0].status == "failed"
    assert uploader.call_count == 1


@respx.mock
def test_apply_submit_timeout_keeps_the_file_id(patch_live_lookups):
    *_, uploader, _, _ = _mock_import_walk([])
    uploader.mock(side_effect=httpx.ReadTimeout("timed out"))
    (r,) = plan_tools.apply_plan(
        plan_import_records(PATIENTS, [{"name": "Ada"}])
    ).results
    assert r.status == "failed"
    assert r.message is not None and "file file-1" in r.message
    assert uploader.call_count == 1


_DONE_WITH_REPORT = {
    "id": PROGRESS_ID,
    "status": "completed",
    "success_count": 1,
    "failed_count": 0,
    "pending_count": 0,
    "failure_report": {"id": "report-1"},
}


@pytest.mark.parametrize("fails", ["report", "count_after"])
@respx.mock
def test_apply_failure_after_submit_keeps_job_and_file_ids(patch_live_lookups, fails):
    *_, download = _mock_import_walk([_DONE_WITH_REPORT], report=_REPORT)
    if fails == "report":
        download.mock(return_value=httpx.Response(500))
    else:
        respx.post(f"{FAKE_BASE_URL}/api/records/{PATIENTS_ID}/search").mock(
            side_effect=[
                httpx.Response(200, json={"count": 5, "results": []}),
                httpx.Response(503),
            ]
        )
    (r,) = plan_tools.apply_plan(
        plan_import_records(PATIENTS, [{"name": "Ada"}])
    ).results
    assert r.status == "failed"
    assert r.message is not None
    assert f"import job {PROGRESS_ID}" in r.message and "file-1" in r.message


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_import_dry_run_maps_mode(patch_live_lookups, tmp_path):
    spec = tmp_path / "rows.csv"
    spec.write_text("id,mrn\nrec-1,5\n")
    result = CliRunner().invoke(
        cli.app,
        [
            "records",
            "import",
            PATIENTS,
            "--spec-file",
            str(spec),
            "--mode",
            "update",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.stdout)["operations"]
    assert op["kind"] == "record_import"
    assert op["payload"]["body"]["create_update_mode"] == "update_only"

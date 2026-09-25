"""Tools-layer tests for smart-connectors: pull/push orchestration + run.

The pull tests stub the vendored normalizer so they don't need the optional
``connectors`` extra; the run test is skipped unless ``chdb`` is installed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools import smart_connectors as sct
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors import pull as sc_pull
from tests.conftest import FAKE_BASE_URL

BASE = f"{FAKE_BASE_URL}/api/smart-connectors"

DETAIL = {
    "id": "conn-uuid",
    "api_name": "upload_counties",
    "name": "Upload Counties",
    "connector_type": "spreadsheet",
    "status": "operational",
    "sql_parameters": {},
    "integration_secrets": [],
    "last_draft_script": {"id": "draft-1", "status": "draft"},
    "live_script": {"id": "live-1", "status": "live"},
}

DRAFT_SCRIPT = {
    "id": "draft-1",
    "status": "draft",
    "user_script": "SELECT * FROM input.records;",
    "config_metadata": {
        "input_tables": [
            {
                "name": "records.csv",
                "file_id": "file-1",
                "database": "input",
                "page_idx": 0,
                "file_path": None,
                "table_name": "records",
                "columns_mapping": [{"col": "a", "type": "str"}],
            }
        ],
        "seed_tables": [],
        "triggered": {"trigger_auth": "session", "fileupload_file_name": "records.csv"},
    },
}


@pytest.fixture
def no_normalize(monkeypatch):
    """Stub the vendored normalizer (which needs the extra) to a no-op.

    Patched on `smart_connectors.pull`, the module that both defines
    `_normalize_input` and calls it. Patching the package facade instead would
    silently miss: `pull_connector` resolves the bare name against its own
    module globals, not the re-export.
    """
    calls = []
    monkeypatch.setattr(
        sc_pull, "_normalize_input", lambda p, wd: calls.append((p, wd)) or ""
    )
    return calls


@respx.mock
def test_pull_assembles_workdir(tmp_path, no_normalize):
    respx.get(f"{BASE}/upload_counties").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{BASE}/upload_counties/sql-scripts/draft-1").mock(
        return_value=httpx.Response(200, json=DRAFT_SCRIPT)
    )
    respx.get(f"{FAKE_BASE_URL}/api/files/file-1/download").mock(
        return_value=httpx.Response(
            200,
            content=b"a\n1\n",
            headers={"content-disposition": 'inline; filename="records.csv"'},
        )
    )
    dest = tmp_path / "wd"
    res = sct.pull_connector("upload_counties", dest=str(dest))

    assert (dest / "connector.sql").read_text() == "SELECT * FROM input.records;"
    cfg = json.loads((dest / "__config.json").read_text())
    assert cfg["input_tables"][0]["table_name"] == "records"
    assert cfg["integration_secrets"] == []
    assert "sql_parameters" in cfg and "integration_secret_filenames" in cfg

    cur = json.loads((dest / "data" / "current_execution.json").read_text())
    assert cur["business_id"]  # filled from env config
    assert cur["connector_id"] == "conn-uuid"
    assert cur["trigger_auth"] == "session"

    marker = json.loads((dest / sct.MARKER_NAME).read_text())
    assert marker["connector_id"] == "conn-uuid"
    assert marker["script_id"] == "draft-1"

    assert res["inputs_downloaded"] == ["records.csv"]
    # normalizer was invoked on the downloaded file
    assert no_normalize and no_normalize[0][0].endswith("records.csv")
    assert (dest / "data" / "records.csv").read_bytes() == b"a\n1\n"


@respx.mock
def test_pull_live_selects_live_script(tmp_path, no_normalize):
    live = {**DRAFT_SCRIPT, "id": "live-1", "status": "live"}
    respx.get(f"{BASE}/upload_counties").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{BASE}/upload_counties/sql-scripts/live-1").mock(
        return_value=httpx.Response(200, json=live)
    )
    respx.get(f"{FAKE_BASE_URL}/api/files/file-1/download").mock(
        return_value=httpx.Response(200, content=b"a\n1\n")
    )
    res = sct.pull_connector(
        "upload_counties", dest=str(tmp_path / "wd"), use_live=True
    )
    assert res["script_id"] == "live-1"
    assert res["script_status"] == "live"


@respx.mock
def test_pull_refuses_nonempty_dir(tmp_path, no_normalize):
    respx.get(f"{BASE}/upload_counties").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )
    respx.get(f"{BASE}/upload_counties/sql-scripts/draft-1").mock(
        return_value=httpx.Response(200, json=DRAFT_SCRIPT)
    )
    dest = tmp_path / "wd"
    dest.mkdir()
    (dest / "something").write_text("x")
    with pytest.raises(FileExistsError):
        sct.pull_connector("upload_counties", dest=str(dest))


@respx.mock
def test_pull_warns_on_seed_and_secret(tmp_path, no_normalize):
    detail = {
        **DETAIL,
        "connector_type": "direct_api_connection",
        "integration_secrets": ["my_api"],
    }
    script = {
        **DRAFT_SCRIPT,
        "config_metadata": {
            "input_tables": [
                {"table_name": "t", "columns_mapping": [], "file_id": None}
            ],
            "seed_tables": [{"name": "seed.csv", "table_name": "seed"}],
            "triggered": {},
        },
    }
    respx.get(f"{BASE}/upload_counties").mock(
        return_value=httpx.Response(200, json=detail)
    )
    respx.get(f"{BASE}/upload_counties/sql-scripts/draft-1").mock(
        return_value=httpx.Response(200, json=script)
    )
    res = sct.pull_connector("upload_counties", dest=str(tmp_path / "wd"))
    joined = " ".join(res["warnings"])
    assert "seed table" in joined
    assert "integration secret" in joined
    assert "direct_api_connection" in joined
    assert res["inputs_downloaded"] == []


@respx.mock
def test_plan_push_detects_change(tmp_path):
    wd = tmp_path / "wd"
    (wd / "data").mkdir(parents=True)
    (wd / "connector.sql").write_text("SELECT 2;")
    (wd / sct.MARKER_NAME).write_text(
        json.dumps({"connector_id": "conn-uuid", "script_id": "draft-1"})
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200, json={"id": "draft-1", "status": "draft", "user_script": "SELECT 1;"}
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    plan = sct.plan_push(str(wd))
    assert plan["changed"] is True
    assert "SELECT 2;" in plan["diff"]
    assert plan["connector"] == "conn-uuid"
    assert plan["warning"] is None


@respx.mock
def test_plan_push_unchanged(tmp_path):
    wd = tmp_path / "wd"
    (wd / "data").mkdir(parents=True)
    (wd / "connector.sql").write_text("SELECT 1;")
    (wd / sct.MARKER_NAME).write_text(
        json.dumps({"connector_id": "conn-uuid", "script_id": "draft-1"})
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
        return_value=httpx.Response(
            200, json={"id": "draft-1", "status": "draft", "user_script": "SELECT 1;"}
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    plan = sct.plan_push(str(wd))
    assert plan["changed"] is False
    assert plan["diff"] == ""


@respx.mock
def test_plan_push_rejects_a_marker_that_went_live(tmp_path):
    """The marker's script_id was promoted live behind the CLI's back — pushing
    to it would silently no-op (200 with no applied change), so this must
    fail fast instead."""
    wd = tmp_path / "wd"
    (wd / "data").mkdir(parents=True)
    (wd / "connector.sql").write_text("SELECT 2;")
    (wd / sct.MARKER_NAME).write_text(
        json.dumps({"connector_id": "conn-uuid", "script_id": "live-1"})
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/live-1").mock(
        return_value=httpx.Response(
            200, json={"id": "live-1", "status": "live", "user_script": "SELECT 1;"}
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    with pytest.raises(PlanError, match="now 'live', not a draft"):
        sct.plan_push(str(wd))


@respx.mock
def test_plan_push_warns_when_marker_points_at_a_stray_draft(tmp_path):
    """A newer draft than the one the marker knows about has since been
    created (e.g. by get-file-template) — pushing here still writes
    somewhere, just not where `pull` would next look."""
    wd = tmp_path / "wd"
    (wd / "data").mkdir(parents=True)
    (wd / "connector.sql").write_text("SELECT 2;")
    (wd / sct.MARKER_NAME).write_text(
        json.dumps({"connector_id": "conn-uuid", "script_id": "old-draft"})
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/old-draft").mock(
        return_value=httpx.Response(
            200, json={"id": "old-draft", "status": "draft", "user_script": "SELECT 1;"}
        )
    )
    respx.get(f"{BASE}/conn-uuid").mock(return_value=httpx.Response(200, json=DETAIL))
    plan = sct.plan_push(str(wd))
    assert plan["changed"] is True
    assert "draft-1" in plan["warning"]


def _mock_publish_path(*, sample_states, forked="draft-2"):
    """PATCH → detail → start → poll → publish → detail, as a publish walks it.

    Publish forks ``forked`` as the new draft; ``draft-1`` becomes the live one.
    """
    detail = {**DETAIL, "source_file": {"id": "file-1", "name": "records.csv"}}
    after = {
        **detail,
        "status": "inactive",
        "last_draft_script": {"id": forked, "status": "draft"},
        "live_script": {"id": "draft-1", "status": "live"},
    }
    return {
        "patch": respx.patch(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
            return_value=httpx.Response(200, json={"id": "draft-1"})
        ),
        "detail": respx.get(f"{BASE}/conn-uuid").mock(
            side_effect=[
                httpx.Response(200, json=detail),
                httpx.Response(200, json=after),
            ]
        ),
        "start": respx.post(f"{BASE}/conn-uuid/sql-scripts/draft-1/start").mock(
            return_value=httpx.Response(200, json={"id": "draft-1"})
        ),
        "script": respx.get(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
            side_effect=[
                httpx.Response(200, json={"id": "draft-1", "state": st, **extra})
                for st, extra in sample_states
            ]
        ),
        "publish": respx.post(f"{BASE}/conn-uuid/sql-scripts/draft-1/publish").mock(
            return_value=httpx.Response(200, json={"id": "draft-1"})
        ),
    }


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@respx.mock
def test_apply_push_runs_the_sample_then_publishes(no_sleep):
    routes = _mock_publish_path(
        sample_states=[("queued", {}), ("in_progress", {}), ("success", {})]
    )

    result = sct.apply_push("conn-uuid", "draft-1", "SELECT 9;", publish=True)

    assert json.loads(routes["patch"].calls.last.request.content) == {
        "user_script": "SELECT 9;"
    }
    assert json.loads(routes["start"].calls.last.request.content) == {
        "source_file_id": "file-1"
    }
    order = [c.request.url.path.rsplit("/", 1)[-1] for c in respx.calls]
    assert order.index("start") < order.index("publish")
    assert routes["publish"].call_count == 1
    assert routes["script"].call_count == 3
    assert result["published"] is True
    assert result["new_draft_id"] == "draft-2"
    assert result["connector_status"] == "inactive"


@respx.mock
def test_apply_push_reports_the_publish_when_the_connector_reread_fails(no_sleep):
    routes = _mock_publish_path(sample_states=[("success", {})])
    routes["detail"].side_effect = [
        next(routes["detail"].side_effect),
        httpx.Response(500, json={"detail": "boom"}),
    ]

    result = sct.apply_push("conn-uuid", "draft-1", "SELECT 9;", publish=True)

    assert routes["publish"].called
    assert result["published"] is True
    assert result["new_draft_id"] is None
    assert "re-reading the connector failed" in result["warning"]


@respx.mock
def test_apply_push_does_not_publish_when_the_sample_fails(no_sleep):
    routes = _mock_publish_path(
        sample_states=[("failed", {"error": "Code: 47. UNKNOWN_IDENTIFIER"})]
    )

    with pytest.raises(
        PlanError, match="updated but not published.*UNKNOWN_IDENTIFIER"
    ):
        sct.apply_push("conn-uuid", "draft-1", "SELECT nope;", publish=True)

    assert routes["patch"].called and routes["start"].called
    assert not routes["publish"].called


@respx.mock
@pytest.mark.parametrize("running", ["queued", "in_progress"])
def test_apply_push_does_not_publish_when_the_sample_times_out(monkeypatch, running):
    clock = iter(range(0, 10_000, 200))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    routes = _mock_publish_path(sample_states=[])
    routes["script"].side_effect = None
    routes["script"].return_value = httpx.Response(
        200, json={"id": "draft-1", "state": running}
    )

    with pytest.raises(PlanError, match="still running"):
        sct.apply_push("conn-uuid", "draft-1", "SELECT 9;", publish=True)

    assert not routes["publish"].called


@respx.mock
def test_apply_push_without_publish_only_patches():
    patch = respx.patch(f"{BASE}/conn-uuid/sql-scripts/draft-1").mock(
        return_value=httpx.Response(200, json={"id": "draft-1"})
    )

    result = sct.apply_push("conn-uuid", "draft-1", "SELECT 9;")

    assert result == {"updated_script_id": "draft-1", "published": False}
    assert len(respx.calls) == 1 and patch.called


def _pulled_dir_publish_path(tmp_path, *, sample_states):
    """A pulled directory plus the calls ``push --dir <wd> --publish`` makes."""
    wd = tmp_path / "wd"
    (wd / "data").mkdir(parents=True)
    (wd / "connector.sql").write_text("SELECT 9;")
    marker = {
        "connector_id": "conn-uuid",
        "connector_api_name": "upload_counties",
        "connector_name": "Upload Counties",
        "script_id": "draft-1",
        "script_status": "draft",
        "env": "testenv",
        "business_id": "biz-1",
    }
    (wd / sct.MARKER_NAME).write_text(json.dumps(marker))
    routes = _mock_publish_path(sample_states=sample_states)
    # plan_push reads the detail and the script before the publish path does.
    routes["detail"].side_effect = [
        httpx.Response(200, json=DETAIL),
        *routes["detail"].side_effect,
    ]
    routes["script"].side_effect = [
        httpx.Response(
            200, json={"id": "draft-1", "status": "draft", "user_script": "SELECT 1;"}
        ),
        *routes["script"].side_effect,
    ]
    return wd, marker, routes


def _push_publish(wd):
    return CliRunner().invoke(
        cli.app, ["smart-connectors", "push", "--dir", str(wd), "--publish", "--yes"]
    )


@respx.mock
def test_push_publish_moves_the_marker_so_the_next_push_targets_the_new_draft(
    tmp_path, no_sleep
):
    wd, marker, routes = _pulled_dir_publish_path(
        tmp_path, sample_states=[("success", {})]
    )

    result = _push_publish(wd)

    assert result.exit_code == 0, result.output
    assert routes["publish"].called
    assert "script published — live runs now use it" in result.output
    assert "Connector status: inactive" in result.output
    assert json.loads((wd / sct.MARKER_NAME).read_text()) == {
        **marker,
        "script_id": "draft-2",
    }

    # The next push from the same directory reads the rewritten marker.
    respx.get(f"{BASE}/conn-uuid").mock(
        return_value=httpx.Response(
            200, json={**DETAIL, "last_draft_script": {"id": "draft-2"}}
        )
    )
    respx.get(f"{BASE}/conn-uuid/sql-scripts/draft-2").mock(
        return_value=httpx.Response(
            200, json={"id": "draft-2", "status": "draft", "user_script": "SELECT 9;"}
        )
    )
    again = sct.plan_push(str(wd))
    assert again["script_id"] == "draft-2"
    assert again["warning"] is None


@respx.mock
def test_push_publish_prints_the_sample_error_as_text_not_markup(tmp_path, no_sleep):
    wd, marker, routes = _pulled_dir_publish_path(
        tmp_path, sample_states=[("failed", {"error": "bad [/red] :smile:"})]
    )

    result = _push_publish(wd)

    assert result.exit_code == 1
    assert "bad [/red] :smile:" in result.output
    assert not routes["publish"].called
    assert json.loads((wd / sct.MARKER_NAME).read_text()) == marker


def test_advance_marker_leaves_a_marker_for_another_script_alone(tmp_path):
    (tmp_path / sct.MARKER_NAME).write_text(json.dumps({"script_id": "other"}))

    assert not sct.advance_marker(tmp_path, from_script_id="draft-1", to_script_id="d2")
    assert json.loads((tmp_path / sct.MARKER_NAME).read_text()) == {
        "script_id": "other"
    }


def test_events_requires_uuid(monkeypatch):
    with pytest.raises(LookupError):
        sct.list_events("not-a-uuid")


def test_run_missing_workdir(tmp_path):
    with pytest.raises(FileNotFoundError):
        sct.run_connector(str(tmp_path))


def test_run_executes_sql_when_chdb_present(tmp_path):
    pytest.importorskip("chdb")
    wd = tmp_path / "wd"
    data = wd / "data"
    data.mkdir(parents=True)
    (data / "records.csv").write_text("id,val\n1,hello\n2,world\n")
    (data / "current_execution.json").write_text("{}")
    (wd / "connector.sql").write_text(
        "CREATE TABLE output.out ENGINE = Log AS SELECT * FROM input.records;"
    )
    (wd / "__config.json").write_text(
        json.dumps(
            {
                "input_tables": [
                    {
                        "name": "records.csv",
                        "database": "input",
                        "page_idx": 0,
                        "table_name": "records",
                        "columns_mapping": [
                            {"col": "id", "type": "str"},
                            {"col": "val", "type": "str"},
                        ],
                    }
                ],
                "seed_tables": [],
                "integration_secrets": [],
                "sql_parameters": {},
                "integration_secret_filenames": [],
            }
        )
    )
    meta = sct.run_connector(str(wd))
    assert meta["stats"]["num_rows"] == 2
    assert any(f["file_name"] == "out.csv" for f in meta["output_files"])
    assert Path(meta["output_files"][0]["file_path"]).exists()

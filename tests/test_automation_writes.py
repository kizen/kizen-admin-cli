"""Automation writes that bypass `apply_plan`: the step-patch GET→PUT loop,
`roundtrip`, and the one-field patches (`activate`/`deactivate`/`move`).

respx serves `two_code_steps.raw.json` (revision 2, inactive, at the root
folder) as the live automation. Its detail GET answers with revision 3 once
the PUT has landed, as the server would. Every test pins the requests made
and the PUT body, so a change to `last_revision`, the request order, or the
id drop on inserted steps fails here offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools.automations import (
    build_wire_step,
    patch_steps,
    roundtrip_automation,
)
from kizen_builder.tools.steps import find_step, insert_step, remove_step
from kizen_builder.translate import live_to_payload
from tests.conftest import (
    FAKE_BASE_URL,
    load_fixture,
    mock_automation_lookup,
    request_json,
)

runner = CliRunner()

API_NAME = "test_two_code_steps"
AUTO_PATH = "/api/automation2/automations"
FOLDERS_PATH = "/api/automation2/folders"
ROOT_FOLDER = {"id": "44b300c0-ce2c-4c49-a427-44a25f85bfad", "name": "<business_root>"}
INTAKE_FOLDER = {"id": "6f0e8a52-2c1d-4b7e-9a3f-0d5c8e1b7a64", "name": "Intake"}


@pytest.fixture
def live() -> Iterator[SimpleNamespace]:
    raw = load_fixture("automations/two_code_steps.raw.json")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        detail = mock_automation_lookup(raw, mock)
        detail_path = f"{AUTO_PATH}/{raw['id']}"
        put = mock.put(f"{FAKE_BASE_URL}{detail_path}").respond(
            json={**raw, "revision": 3}
        )
        detail.mock(
            side_effect=lambda request: httpx.Response(
                200, json={**raw, "revision": 3} if put.called else raw
            )
        )
        mock.get(f"{FAKE_BASE_URL}{FOLDERS_PATH}").respond(
            json={"results": [ROOT_FOLDER, INTAKE_FOLDER], "next": None}
        )
        move = mock.post(f"{FAKE_BASE_URL}{FOLDERS_PATH}/move").respond(200)
        yield SimpleNamespace(
            raw=raw,
            put=put,
            move=move,
            # The read every patch makes, then the PUT and its re-read. An
            # extra detail GET before the PUT means last_revision was dropped
            # and update_automation re-fetched it.
            read=[("GET", AUTO_PATH), ("GET", detail_path)],
            write=[("PUT", detail_path), ("GET", detail_path)],
            calls=lambda: [(c.request.method, c.request.url.path) for c in mock.calls],
        )


def _s00_id(live: SimpleNamespace) -> str:
    return live.raw["steps"][0]["id"]


def _invoke(*args: str) -> Any:
    return runner.invoke(cli.app, ["automations", *args])


# ---------------------------------------------------------------------------
# patch_steps / roundtrip_automation (tools layer)
# ---------------------------------------------------------------------------


def test_patch_steps_puts_mutated_steps_with_the_read_revision(live) -> None:
    result = patch_steps(
        API_NAME, lambda payload, raw: remove_step(payload, "s01_code_step"), True
    )

    assert live.calls() == live.read + live.write
    body = request_json(live.put)
    assert body["last_revision"] == 2
    assert [s["key"] for s in body["steps"]] == ["s00_code_step"]
    assert result["executed"]
    assert (result["revision_before"], result["revision_after"]) == (2, 3)


def test_patch_steps_refuses_to_put_an_invalid_graph(live) -> None:
    def dangle(payload: dict[str, Any], raw: dict[str, Any]) -> None:
        find_step(payload, "s01_code_step")["parent_key"] = "nope"

    result = patch_steps(API_NAME, dangle, execute=True)

    assert any("dangling parent_key 'nope'" in p for p in result["validation_problems"])
    assert not result["executed"]
    assert not live.put.called


def test_patch_steps_without_execute_returns_the_mutated_payload(live) -> None:
    result = patch_steps(
        API_NAME, lambda payload, raw: remove_step(payload, "s01_code_step")
    )

    assert not live.put.called
    assert not result["executed"]
    assert [s["key"] for s in result["payload"]["steps"]] == ["s00_code_step"]


def test_inserted_step_drops_a_copied_id(live) -> None:
    payload = live_to_payload(live.raw)
    spec = {"step_type": "stop_execution", "id": _s00_id(live)}
    assert "id" not in build_wire_step(spec, payload, None)

    def insert(payload: dict[str, Any], raw: dict[str, Any]) -> Any:
        wire = build_wire_step(spec, payload, None)
        return insert_step(payload, wire, parent_key="s01_code_step")

    assert patch_steps(API_NAME, insert, execute=True)["executed"]
    steps = request_json(live.put)["steps"]
    assert len(steps) == 3
    assert [s.get("id") for s in steps].count(_s00_id(live)) == 1


def test_roundtrip_without_execute_makes_no_put(live) -> None:
    result = roundtrip_automation(API_NAME)

    assert not live.put.called
    assert not result["executed"]


def test_roundtrip_puts_the_translated_payload_unchanged(live) -> None:
    result = roundtrip_automation(API_NAME, execute=True)

    assert live.calls() == live.read + live.write
    body = request_json(live.put)
    assert body == live_to_payload(live.raw)
    assert body["last_revision"] == 2
    assert (result["revision_before"], result["revision_after"]) == (2, 3)
    assert result["drift"] == []


# ---------------------------------------------------------------------------
# kizen automations steps add / edit / remove
# ---------------------------------------------------------------------------


def test_steps_remove_dry_run_makes_no_put(live) -> None:
    result = _invoke("steps", "remove", API_NAME, "s01_code_step", "--dry-run")

    assert result.exit_code == 0, result.output
    assert not live.put.called


def test_steps_remove_yes_puts_once(live) -> None:
    result = _invoke("steps", "remove", API_NAME, "s01_code_step", "--yes")

    assert result.exit_code == 0, result.output
    body = request_json(live.put)
    assert body["last_revision"] == 2
    assert [s["key"] for s in body["steps"]] == ["s00_code_step"]


def test_steps_remove_without_a_terminal_or_yes_exits_2(live) -> None:
    result = _invoke("steps", "remove", API_NAME, "s01_code_step")

    assert result.exit_code == 2, result.output
    assert not live.put.called


def test_steps_edit_keeps_the_block_and_changes_the_description(live, tmp_path) -> None:
    block = find_step(live_to_payload(live.raw), "s01_code_step")["action_code_step"]
    spec = tmp_path / "patch.json"
    spec.write_text(
        json.dumps({"user_description": "Edited", "action_code_step": block})
    )

    result = _invoke(
        "steps", "edit", API_NAME, "s01_code_step", "--yes", "--spec-file", str(spec)
    )

    assert result.exit_code == 0, result.output
    s01 = find_step(request_json(live.put), "s01_code_step")
    assert s01["user_description"] == "Edited"
    assert s01["action_code_step"] == block


def test_steps_edit_to_a_dangling_parent_exits_1(live, tmp_path) -> None:
    spec = tmp_path / "patch.json"
    spec.write_text(json.dumps({"parent_key": "nope"}))

    result = _invoke(
        "steps", "edit", API_NAME, "s01_code_step", "--yes", "--spec-file", str(spec)
    )

    assert result.exit_code == 1, result.output
    assert not live.put.called


def test_steps_add_drops_a_copied_id(live, tmp_path) -> None:
    spec = tmp_path / "step.json"
    spec.write_text(json.dumps({"step_type": "stop_execution", "id": _s00_id(live)}))

    result = _invoke(
        "steps",
        "add",
        API_NAME,
        "--parent",
        "s01_code_step",
        "--yes",
        "--spec-file",
        str(spec),
    )

    assert result.exit_code == 0, result.output
    (new,) = [
        s for s in request_json(live.put)["steps"] if s["type"] == "stop_execution"
    ]
    assert "id" not in new
    assert new["parent_key"] == "s01_code_step"


# ---------------------------------------------------------------------------
# kizen automations activate / deactivate / move
# ---------------------------------------------------------------------------


def test_activate_puts_active_with_the_read_revision(live) -> None:
    result = _invoke("activate", API_NAME, "--yes")

    assert result.exit_code == 0, result.output
    assert live.calls() == live.read + live.read + live.write  # preview, then apply
    body = request_json(live.put)
    assert body["active"] is True
    assert body["last_revision"] == 2
    assert "folder_id" not in body
    assert "done — active: False → True" in result.output


def test_activate_puts_a_schedule_activity_automation() -> None:
    raw = load_fixture("automations/schedule_activity_target_variable.raw.json")
    with respx.mock(assert_all_mocked=True) as mock:
        mock_automation_lookup(raw, mock)
        put = mock.put(f"{FAKE_BASE_URL}{AUTO_PATH}/{raw['id']}").respond(json=raw)

        result = _invoke("activate", raw["api_name"], "--yes")

    assert result.exit_code == 0, result.output
    body = request_json(put)
    assert body["active"] is True
    assert "schedule_activity" in {s["type"] for s in body["steps"]}


def test_activate_dry_run_makes_no_put(live) -> None:
    result = _invoke("activate", API_NAME, "--dry-run")

    assert result.exit_code == 0, result.output
    assert not live.put.called


def test_deactivate_an_inactive_automation_is_a_no_op(live) -> None:
    result = _invoke("deactivate", API_NAME, "--yes")

    assert result.exit_code == 0, result.output
    assert not live.put.called


def _move_body(live: SimpleNamespace, folder: dict[str, str]) -> dict[str, Any]:
    return {
        "automation_ids": [live.raw["id"]],
        "folder_ids": [],
        "new_parent_folder_id": folder["id"],
    }


def test_move_posts_to_folders_move_without_a_put(live) -> None:
    result = _invoke("move", API_NAME, "--folder", "Intake", "--yes", "--json")

    assert result.exit_code == 0, result.output
    assert request_json(live.move) == _move_body(live, INTAKE_FOLDER)
    assert not live.put.called
    out = json.loads(result.output)
    assert (out["revision_before"], out["revision_after"]) == (2, 2)
    assert out["after"] == INTAKE_FOLDER


def test_move_prints_done_not_put(live) -> None:
    result = _invoke("move", API_NAME, "--folder", "Intake", "--yes")

    assert result.exit_code == 0, result.output
    assert "done — folder: <business_root> → Intake" in result.output
    assert "PUT" not in result.output


def test_move_without_a_folder_targets_the_business_root(live) -> None:
    # Already at the root would be a no-op. The detail GET reads live.raw on
    # each call, so this moves the served automation to Intake first.
    live.raw["folder"] = INTAKE_FOLDER

    result = _invoke("move", API_NAME, "--yes")

    assert result.exit_code == 0, result.output
    assert request_json(live.move) == _move_body(live, ROOT_FOLDER)
    assert not live.put.called


def test_move_to_the_folder_it_is_in_posts_nothing(live) -> None:
    result = _invoke("move", API_NAME, "--folder", ROOT_FOLDER["id"], "--yes")

    assert result.exit_code == 0, result.output
    assert "no-op" in result.output
    assert not live.move.called


def test_move_to_an_unknown_folder_exits_1(live) -> None:
    result = _invoke("move", API_NAME, "--folder", "Nowhere", "--yes")

    assert result.exit_code == 1, result.output
    assert "folder 'Nowhere' not found" in result.output
    assert not live.move.called
    assert not live.put.called


def test_list_refuses_a_folder_name_two_folders_share() -> None:
    twin = {**INTAKE_FOLDER, "id": "9b1d4c2e-7a8f-4e3b-8c5d-1f2a3b4c5d6e"}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        mock.get(f"{FAKE_BASE_URL}{FOLDERS_PATH}").respond(
            json={"results": [ROOT_FOLDER, INTAKE_FOLDER, twin], "next": None}
        )
        listing = mock.get(f"{FAKE_BASE_URL}{AUTO_PATH}").respond(
            json={"results": [], "next": None}
        )
        result = _invoke("list", "--folder", "Intake")

    assert result.exit_code == 1, result.output
    assert "matches 2 folders" in result.output
    assert not listing.called

"""Automation runs (executions): endpoint paths, read→display field mapping,
and the endpoint and body of each execution-control verb.

Regression coverage for two bugs fixed in the `runs` command regroup:

1. The single-run detail endpoint was called with a trailing slash
   (`/automation-execution/{id}/`), which 404s with an HTML page; the API
   serves it at `/automation-execution/{id}` (no slash).
2. Both detail and history mapped fields that don't exist on the response
   (`started_at`/`finished_at`, and a flat `type`/`description` on history
   rows), so timestamps and step types/descriptions always came back blank.
   The real fields are `created`/`updated`, `execution_time_ms`, and a nested
   `step`/`trigger` ActionLight.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx

from kizen_builder.api.client import KizenAPIError
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools.automations import (
    get_execution,
    get_execution_history,
    list_executions,
    wait_for_execution,
)
from tests.conftest import FAKE_BASE_URL, load_fixture, request_json

EXEC_ID = "a5fa0b69-cf86-4849-81b2-1521cffe19a4"
EXEC_BASE = f"{FAKE_BASE_URL}/api/automation2/automation-execution"


def _execution(status: str, **extra: Any) -> dict[str, Any]:
    """A minimal raw execution GET response with the given status."""
    return {
        "id": EXEC_ID,
        "status": status,
        "automation": {"api_name": "form_submission"},
        "automation_id": "b1c2d3e4-0000-4000-8000-000000000001",
        "record": {"id": "51612b78-723e-451b-aede-f8037d2523d4"},
        "created": "2026-03-18T10:29:31.364611-05:00",
        "updated": "2026-03-18T10:30:03.444480-05:00",
        **extra,
    }


@respx.mock
def test_get_execution_uses_no_trailing_slash_and_maps_timestamps():
    detail = load_fixture("executions/detail_form_submission.json")
    # The route is registered WITHOUT a trailing slash. A request to the old
    # trailing-slash path would not match this mock (and 404s in production).
    route = respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        return_value=httpx.Response(200, json=detail)
    )

    result = get_execution(EXEC_ID)

    assert route.called
    assert result["status"] == "completed"
    assert result["automation_api_name"] == "form_submission"
    assert result["record_id"] == "51612b78-723e-451b-aede-f8037d2523d4"
    # created → started_at, updated → finished_at (no started_at/finished_at
    # exists on the response).
    assert result["started_at"] == detail["created"]
    assert result["finished_at"] == detail["updated"]


@respx.mock
def test_get_execution_history_maps_nested_step_and_duration():
    history = load_fixture("executions/history_form_submission.json")
    respx.get(f"{EXEC_BASE}/{EXEC_ID}/history").mock(
        return_value=httpx.Response(200, json=history)
    )

    rows = get_execution_history(EXEC_ID)

    assert len(rows) == 3

    trigger_row, init_row, code_row = rows

    # A trigger firing: type/description come from the nested ActionLight.
    assert trigger_row["kind"] == "trigger"
    assert trigger_row["type"] == "manual"
    assert trigger_row["description"] == "Manual"

    # A completed step surfaces its duration and start/finish timestamps.
    assert init_row["kind"] == "step"
    assert init_row["type"] == "initialize_variable"
    assert init_row["duration_ms"] == 25
    assert init_row["started_at"] == "2026-03-18T10:29:31.500000-05:00"
    assert init_row["finished_at"] == "2026-03-18T10:29:31.525000-05:00"

    # A failed step falls back to error_description when error is null.
    assert code_row["status"] == "failed"
    assert code_row["error"] == "NameError: name 'foo' is not defined"


@respx.mock
def test_list_executions_maps_start_time_not_created():
    """The list item shape uses start_time/completed (AutomationExecutionList),
    not the created/updated of the single-run detail — mapping started_at from
    `created` silently produced blanks."""
    auto_id = "b1c2d3e4-0000-4000-8000-000000000001"
    respx.get(f"{FAKE_BASE_URL}/api/automation2/automations").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": auto_id, "api_name": "form_submission"}],
                "next": None,
            },
        )
    )
    respx.get(EXEC_BASE).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": EXEC_ID,
                        "status": "completed",
                        "start_time": "2026-03-18T10:29:31.364611-05:00",
                        "completed": "2026-03-18T10:30:03.444480-05:00",
                        "created": None,
                        "record": {"id": "51612b78-723e-451b-aede-f8037d2523d4"},
                        "automation": {"api_name": "form_submission"},
                        "debug_mode": "off",
                    }
                ],
                "next": None,
            },
        )
    )

    rows = list_executions("form_submission")

    assert len(rows) == 1
    row = rows[0]
    assert row["started_at"] == "2026-03-18T10:29:31.364611-05:00"
    assert row["finished_at"] == "2026-03-18T10:30:03.444480-05:00"
    assert row["record_id"] == "51612b78-723e-451b-aede-f8037d2523d4"


@respx.mock
def test_get_execution_history_maps_row_id():
    """`runs debug-step --history` documents its --history value as coming
    from `runs view`, which can only be true if the mapped row carries the
    raw row's id (previously dropped)."""
    history = load_fixture("executions/history_form_submission.json")
    respx.get(f"{EXEC_BASE}/{EXEC_ID}/history").mock(
        return_value=httpx.Response(200, json=history)
    )

    rows = get_execution_history(EXEC_ID)

    assert [r["id"] for r in rows] == [
        "e0000000-0000-4000-8000-000000000001",
        "e0000000-0000-4000-8000-000000000002",
        "e0000000-0000-4000-8000-000000000003",
    ]


# ---------------------------------------------------------------------------
# wait_for_execution — the sample.py-shaped poll loop.
# ---------------------------------------------------------------------------


@respx.mock
def test_wait_for_execution_polls_until_terminal(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    route = respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("active")),
            httpx.Response(200, json=_execution("active")),
            httpx.Response(200, json=_execution("completed")),
        ]
    )

    result = wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)

    assert route.call_count == 3
    assert result["status"] == "completed"
    assert result["timed_out"] is False
    assert result["polls"] == 3


@respx.mock
def test_wait_for_execution_unrecognized_status_keeps_polling(monkeypatch):
    """The regression test for the "declared stalled but actually fine"
    failure mode: an unfamiliar status (e.g. a value added server-side after
    this repo's allowlist was written) must not end the wait."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("queued")),
            httpx.Response(200, json=_execution("completed")),
        ]
    )

    result = wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)

    assert result["status"] == "completed"
    assert result["timed_out"] is False


@respx.mock
def test_wait_for_execution_times_out_without_declaring_failure(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # deadline = monotonic() [call 1] + timeout; the loop's own deadline
    # check [call 2] then sees a value already past it.
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        return_value=httpx.Response(200, json=_execution("active"))
    )

    result = wait_for_execution(EXEC_ID, timeout=5.0, poll_interval=1.0)

    assert result["timed_out"] is True
    assert result["status"] == "active"
    assert result["polls"] == 1


@respx.mock
def test_wait_for_execution_paused_by_failure_is_terminal_not_timed_out():
    """`paused_by_failure` is a distinct string from `paused` and must end the
    wait as its own outcome — not be polled to the deadline and reported as
    a timeout."""
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        return_value=httpx.Response(
            200,
            json=_execution(
                "paused_by_failure",
                paused_on_step={
                    "id": "52ced4b6-0000-4000-8000-000000000009",
                    "type": "create_related_entity",
                    "branching_step": False,
                    "label": "Action: Create Related Entity",
                },
            ),
        )
    )

    result = wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)

    assert result["status"] == "paused_by_failure"
    assert result["timed_out"] is False
    assert result["paused_on_step"]["type"] == "create_related_entity"


def test_wait_for_execution_rejects_negative_timeout():
    with pytest.raises(ValueError, match="timeout"):
        wait_for_execution(EXEC_ID, timeout=-1.0, poll_interval=1.0)


@pytest.mark.parametrize("poll_interval", [-1.0, 0.0])
def test_wait_for_execution_rejects_non_positive_poll_interval(poll_interval):
    with pytest.raises(ValueError, match="poll_interval"):
        wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=poll_interval)


@respx.mock
def test_wait_for_execution_survives_a_transient_poll_error(monkeypatch):
    """A dropped connection or a 5xx mid-poll must not abort the wait — that
    would recreate this item's own "declared stalled but actually fine" bug
    one layer down, at the exit-code level."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    route = respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("active")),
            httpx.Response(500, json={"detail": "temporarily unavailable"}),
            httpx.Response(200, json=_execution("completed")),
        ]
    )

    result = wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)

    assert route.call_count == 3
    assert result["status"] == "completed"
    assert result["timed_out"] is False


@respx.mock
def test_wait_for_execution_gives_up_after_too_many_consecutive_errors(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("active")),
            httpx.Response(500, json={"detail": "down"}),
            httpx.Response(500, json={"detail": "down"}),
            httpx.Response(500, json={"detail": "down"}),
            httpx.Response(500, json={"detail": "down"}),
        ]
    )

    with pytest.raises(KizenAPIError):
        wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)


@respx.mock
def test_wait_for_execution_does_not_retry_a_4xx(monkeypatch):
    """A wrong execution_id is not a transient failure — retrying it for up
    to 900s would just be a slower way to fail."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("active")),
            httpx.Response(404, json={"detail": "not found"}),
        ]
    )

    with pytest.raises(KizenAPIError):
        wait_for_execution(EXEC_ID, timeout=60.0, poll_interval=1.0)


@respx.mock
def test_wait_for_execution_on_poll_runs_once_per_poll(monkeypatch):
    """BCLI-021's one sanctioned extension: `on_poll`, when given, is called
    once per successful poll (including the first, before the loop even
    waits), with the same summary shape plus `polls`. Absent, behaviour is
    identical to before (covered by every other test in this file, which
    don't pass it)."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
        side_effect=[
            httpx.Response(200, json=_execution("active")),
            httpx.Response(200, json=_execution("active")),
            httpx.Response(200, json=_execution("completed")),
        ]
    )
    seen = []

    result = wait_for_execution(
        EXEC_ID, timeout=60.0, poll_interval=1.0, on_poll=seen.append
    )

    assert [s["status"] for s in seen] == ["active", "active", "completed"]
    assert [s["polls"] for s in seen] == [1, 2, 3]
    assert all(s["execution_id"] == EXEC_ID for s in seen)
    assert result["status"] == "completed"
    assert result["polls"] == 3


def test_get_execution_omits_paused_on_step_when_absent():
    """A normal (non-paused) execution's summary shape is unchanged — no
    `paused_on_step` key at all, not `paused_on_step: None`."""
    from kizen_builder.tools.automations import _summarize_execution

    summary = _summarize_execution("testenv", _execution("completed"))
    assert "paused_on_step" not in summary


# ---------------------------------------------------------------------------
# Execution control: the endpoint and exact body each verb POSTs.
# ---------------------------------------------------------------------------

STEP_ID = "2ed0252a-8408-4444-9da3-4ac4bbb0e228"
HISTORY_ID = "9c4d2e1f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def _actionable_execution(status: str) -> dict[str, Any]:
    """An execution GET carrying every field `execution_action_body` reads."""
    return _execution(
        status,
        client_id=None,
        record_id="51612b78-723e-451b-aede-f8037d2523d4",
        trigger_history_id="7e1f0c2a-3b4d-4e5f-8a9b-0c1d2e3f4a5b",
        debug_mode=True,
    )


@pytest.mark.parametrize(
    ("verb", "suffix"),
    [
        ("pause_execution", "/pause"),
        ("resume_execution", "/play"),
        ("cancel_execution", "/cancel"),
        ("debug_sendit", "/debug-sendit"),
    ],
)
def test_status_action_posts_the_execution_echo(verb, suffix):
    before = _actionable_execution("active")
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
            side_effect=[
                httpx.Response(200, json=before),
                httpx.Response(200, json=_actionable_execution("paused")),
            ]
        )
        post = mock.post(f"{EXEC_BASE}/{EXEC_ID}{suffix}").respond(json=None)

        result = getattr(auto_tools, verb)(EXEC_ID)
        calls = [(c.request.method, c.request.url.path) for c in mock.calls]

    assert calls == [
        ("GET", f"/api/automation2/automation-execution/{EXEC_ID}"),
        ("POST", f"/api/automation2/automation-execution/{EXEC_ID}{suffix}"),
        ("GET", f"/api/automation2/automation-execution/{EXEC_ID}"),
    ]
    keys = (
        "id",
        "automation_id",
        "client_id",
        "record_id",
        "status",
        "trigger_history_id",
        "debug_mode",
    )
    assert request_json(post) == {k: before[k] for k in keys}
    assert (result["status_before"], result["status_after"]) == ("active", "paused")


@pytest.mark.parametrize(
    ("branch", "body"),
    [
        (None, {"skip_step_id": STEP_ID}),
        ("yes", {"skip_step_id": STEP_ID, "continue_with_branch": "yes"}),
    ],
    ids=["no-branch", "branch"],
)
def test_skip_and_resume_posts_the_step_and_optional_branch(branch, body):
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(f"{EXEC_BASE}/{EXEC_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_execution("paused_by_failure")),
                httpx.Response(200, json=_execution("active")),
            ]
        )
        post = mock.post(f"{EXEC_BASE}/{EXEC_ID}/skip-and-resume").respond(json=None)

        result = auto_tools.skip_and_resume_execution(EXEC_ID, STEP_ID, branch)

    assert request_json(post) == body
    assert (result["status_before"], result["status_after"]) == (
        "paused_by_failure",
        "active",
    )


@pytest.mark.parametrize(
    ("verb", "suffix"),
    [("debug_rerun", "/debug-rerun"), ("debug_restart", "/debug-restart")],
)
def test_debug_rerun_and_restart_post_the_step_id(verb, suffix):
    with respx.mock(assert_all_mocked=True) as mock:
        post = mock.post(f"{EXEC_BASE}/{EXEC_ID}{suffix}").respond(json={})

        getattr(auto_tools, verb)(EXEC_ID, STEP_ID)
        assert len(mock.calls) == 1

    assert request_json(post) == {"step_id": STEP_ID}


@pytest.mark.parametrize(
    ("branch", "body"),
    [
        (None, {"action": "skip", "history_id": HISTORY_ID}),
        (
            "no",
            {"action": "skip", "history_id": HISTORY_ID, "continue_with_branch": "no"},
        ),
    ],
    ids=["no-branch", "branch"],
)
def test_debug_step_posts_the_action_and_optional_branch(branch, body):
    with respx.mock(assert_all_mocked=True) as mock:
        post = mock.post(f"{EXEC_BASE}/{EXEC_ID}/debug-step").respond(json={})

        auto_tools.debug_step(EXEC_ID, "skip", HISTORY_ID, branch)

    assert request_json(post) == body

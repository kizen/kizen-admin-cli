"""`automations start`: variable-override payload, name validation, and the
client_id-vs-record_id routing for contact automations.

The override wire shape is StartAutomationRequest.variable_overrides — a list
of VariableOverrideRequest ``{variable_name, value}`` where value is a string
the server coerces by the variable's data_type (confirmed against the public
OpenAPI schema and a live start).
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from kizen_builder.tools.automations import start_and_wait, start_automation
from tests.conftest import FAKE_BASE_URL, mock_automation_lookup

AUTO_ID = "1800a96e-fb22-4ec3-a912-ca1795744b7a"
AUTOS = f"{FAKE_BASE_URL}/api/automation2/automations"


def _mock_automation(
    *,
    api_name: str,
    object_name: str | None = None,
    variables: list[dict] | None = None,
    active: bool = True,
):
    """Mock the list + detail lookups start_automation performs."""
    mock_automation_lookup(
        {
            "id": AUTO_ID,
            "api_name": api_name,
            "revision": 1,
            "active": active,
            "custom_object": {"name": object_name} if object_name else None,
            "variables": variables or [],
            "triggers": [],
            "steps": [],
        }
    )


def _start_route(api_name: str):
    return respx.post(f"{AUTOS}/{api_name}/start").mock(
        return_value=httpx.Response(200, json={"execution": {"id": "exec-123"}})
    )


@respx.mock
def test_variables_become_variable_overrides_with_string_values():
    _mock_automation(
        api_name="form_submission",
        object_name="cd_activity",
        variables=[{"name": "org_match"}, {"name": "llm_notes"}],
    )
    route = _start_route("form_submission")

    result = start_automation(
        "form_submission",
        "rec-1",
        variables={"org_match": True, "llm_notes": "seed text"},
    )

    body = json.loads(route.calls[-1].request.content)
    assert body["record_id"] == "rec-1"
    assert "client_id" not in body
    assert body["variable_overrides"] == [
        {"variable_name": "org_match", "value": "true"},  # bool → "true"
        {"variable_name": "llm_notes", "value": "seed text"},
    ]
    assert result["execution_id"] == "exec-123"


@respx.mock
def test_unknown_variable_name_is_rejected_before_start():
    _mock_automation(
        api_name="form_submission",
        object_name="cd_activity",
        variables=[{"name": "org_match"}],
    )
    route = _start_route("form_submission")

    with pytest.raises(LookupError) as exc:
        start_automation("form_submission", "rec-1", variables={"typo": "x"})

    assert "typo" in str(exc.value)
    assert "org_match" in str(exc.value)  # lists the declared names
    assert not route.called  # never fired the automation


@respx.mock
def test_contact_automation_routes_id_to_client_id():
    _mock_automation(api_name="contact_flow", object_name="client_client", variables=[])
    route = _start_route("contact_flow")

    result = start_automation("contact_flow", "contact-9")

    body = json.loads(route.calls[-1].request.content)
    assert body == {"client_id": "contact-9"}  # NOT record_id
    assert result["client_id"] == "contact-9"
    assert result["record_id"] is None


@respx.mock
def test_no_variables_sends_bare_record_id():
    _mock_automation(
        api_name="form_submission", object_name="cd_activity", variables=[]
    )
    route = _start_route("form_submission")

    start_automation("form_submission", "rec-1")

    body = json.loads(route.calls[-1].request.content)
    assert body == {"record_id": "rec-1"}  # no empty variable_overrides key


@respx.mock
def test_global_automation_starts_without_record():
    _mock_automation(api_name="nightly_sync")
    route = _start_route("nightly_sync")

    result = start_automation("nightly_sync")  # no record

    body = json.loads(route.calls[-1].request.content)
    assert body == {}  # neither record_id nor client_id
    assert result["execution_id"] == "exec-123"
    assert result["record_id"] is None and result["client_id"] is None


@respx.mock
def test_record_based_automation_without_record_errors():
    _mock_automation(
        api_name="form_submission", object_name="cd_activity", variables=[]
    )
    route = _start_route("form_submission")

    with pytest.raises(LookupError, match="record-based"):
        start_automation("form_submission")  # missing required record

    assert not route.called


@respx.mock
def test_inactive_automation_errors_before_start():
    """An inactive automation is caught locally, not sent (the API 400s on it)."""
    _mock_automation(api_name="global_test", active=False)
    route = _start_route("global_test")

    with pytest.raises(LookupError, match="inactive"):
        start_automation("global_test")

    assert not route.called


# ---------------------------------------------------------------------------
# start_and_wait — BCLI-021's composition of start_automation() +
# wait_for_execution(), over the real (mocked) HTTP wire rather than the
# tool-layer mocks test_cli.py uses, so the merge of the two dicts is
# exercised against what each endpoint actually returns.
# ---------------------------------------------------------------------------

EXEC_BASE = f"{FAKE_BASE_URL}/api/automation2/automation-execution"


@respx.mock
def test_start_and_wait_false_returns_exactly_start_automations_result():
    """wait=False: no status GET at all, and the return is exactly what
    start_automation() itself returns — no new keys."""
    _mock_automation(
        api_name="form_submission", object_name="cd_activity", variables=[]
    )
    _start_route("form_submission")
    exec_route = respx.get(f"{EXEC_BASE}/exec-123").mock(
        return_value=httpx.Response(200, json={"id": "exec-123", "status": "active"})
    )

    direct = start_automation("form_submission", "rec-1")
    started = start_and_wait("form_submission", "rec-1", wait=False)

    assert started == direct
    assert not exec_route.called


@respx.mock
def test_start_and_wait_merges_start_and_wait_results(monkeypatch):
    """wait=True: start_automation()'s execution_id feeds wait_for_execution(),
    and the merged dict keeps start's own keys (raw = the /start response,
    not the execution GET) plus status/timed_out/polls from the wait."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    _mock_automation(
        api_name="form_submission", object_name="cd_activity", variables=[]
    )
    _start_route("form_submission")
    respx.get(f"{EXEC_BASE}/exec-123").mock(
        side_effect=[
            httpx.Response(200, json={"id": "exec-123", "status": "active"}),
            httpx.Response(200, json={"id": "exec-123", "status": "completed"}),
        ]
    )

    result = start_and_wait("form_submission", "rec-1", timeout=60.0, poll_interval=1.0)

    assert result["execution_id"] == "exec-123"
    assert result["record_id"] == "rec-1"
    assert result["raw"] == {"execution": {"id": "exec-123"}}  # start's raw, unchanged
    assert result["status"] == "completed"
    assert result["timed_out"] is False
    assert result["polls"] == 2


@respx.mock
def test_start_and_wait_passes_on_poll_through_to_wait_for_execution(monkeypatch):
    """The on_poll callback BCLI-021's CLI layer needs for streaming reaches
    wait_for_execution() unchanged — start_and_wait doesn't swallow it."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    _mock_automation(
        api_name="form_submission", object_name="cd_activity", variables=[]
    )
    _start_route("form_submission")
    respx.get(f"{EXEC_BASE}/exec-123").mock(
        return_value=httpx.Response(200, json={"id": "exec-123", "status": "completed"})
    )
    seen = []

    start_and_wait("form_submission", "rec-1", on_poll=seen.append)

    assert len(seen) == 1
    assert seen[0]["status"] == "completed"

"""Live round-trip coverage for **every wired automation step and trigger type**.

Automations are the payload surface this CLI gets most wrong most expensively:
the write dialect is undocumented, the schema describes the read dialect under
different key names, and several wire keys are *silently ignored* rather than
rejected — so a wrong one is data loss with a 200 next to it. The offline
golden tests in ``tests/test_automation_payloads.py`` pin the payloads as
literals; nothing there can notice Kizen changing its mind.

This file closes that gap for the whole registry rather than a representative
sample. Two invariants make "the whole registry" a maintained property instead
of a one-time sweep:

* every type in ``_STEP_BUILDERS`` (plus the configless ``branch`` /
  ``merge_branches``) / ``_TRIGGER_BUILDERS`` is named in
  :data:`COVERED_STEP_TYPES` / :data:`COVERED_TRIGGER_TYPES` (asserted at the
  bottom, so wiring a 25th step type fails until someone covers it);
* each themed automation asserts that the payload it actually sent carries
  exactly the types its group claims, so the sets above cannot drift into
  wishful thinking.

Step types are grouped into a handful of themed automations rather than one
create per type — same coverage, a fraction of the live API load and the
debris. Everything created registers its deleter the moment the POST returns;
see ``Scratch`` in ``conftest.py``.

Nothing here is ever *started*, and only one automation is even active — the
target a ``start_automation`` step is required to point at, which carries
nothing but the auto-prepended manual trigger and so never fires on its own.
Everything else is created ``active: false`` and only read back, so defining a
``call_llm`` step stores config without invoking a model.
"""

from __future__ import annotations

from typing import Any

import pytest

from kizen_builder.models.spec.automations_triggers import trigger_config_field
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
)
from tests.drift.conftest import (
    _create_drift_object,
    create_field_on,
    debris_api_name,
    debris_name,
)

pytestmark = pytest.mark.drift


# ---------------------------------------------------------------------------
# What each themed automation is responsible for
# ---------------------------------------------------------------------------
#
# Each frozenset is asserted against the step types the corresponding fixture
# really sent, so these are a description of live behaviour rather than a
# wish-list. `COVERED_STEP_TYPES` is their union and is reconciled against
# `_STEP_BUILDERS` at the bottom of the file.

#: `drift_automation` (in conftest.py) — the branching baseline.
BRANCHING_STEPS = frozenset({"condition", "stop_execution"})

CONTROL_FLOW_STEPS = frozenset(
    {
        "initialize_variable",
        "search_records",
        "delay",
        "code_step",
        "goal",
        "condition",
        "start_automation",
        "stop_execution",
        "go_to_automation_step",
    }
)

DATA_STEPS = frozenset(
    {
        "initialize_variable",
        "update_variable",
        "math_operator",
        "change_field_value",
        "archive_record",
    }
)

RELATED_STEPS = frozenset({"create_related_entity", "modify_related_entities"})

MESSAGING_STEPS = frozenset(
    {
        "assign_team_member",
        "notify_member_via_text",
        "notify_member_via_email",
        "send_related_contact_email",
        "send_related_contact_text",
    }
)

AI_STEPS = frozenset({"call_llm", "file_content_extraction", "audio_transcription"})

ACTIVITY_STEPS = frozenset({"schedule_activity"})

BRANCH_GROUP_STEPS = frozenset({"condition", "merge_branches", "branch", "delay"})

LOOP_STEPS = frozenset(
    {
        "initialize_variable",
        "loop",
        "delay",
        "condition",
        "merge_branches",
        "exit_loop",
        "next_or_finish",
    }
)

#: Needs an installed plugin that ships steps; skipped when there is none.
PLUGIN_STEPS = frozenset({"plugin_code_step"})

COVERED_STEP_TYPES = (
    BRANCHING_STEPS
    | BRANCH_GROUP_STEPS
    | LOOP_STEPS
    | CONTROL_FLOW_STEPS
    | DATA_STEPS
    | RELATED_STEPS
    | MESSAGING_STEPS
    | AI_STEPS
    | ACTIVITY_STEPS
    | PLUGIN_STEPS
)

#: Nine of the eleven wired trigger types ride on one record-based automation.
RECORD_TRIGGER_TYPES = frozenset(
    {
        "manual",
        "new_entity_created",
        "activity_logged",
        "on_or_around_date",
        "webhook",
        "field_updated",
        "scheduled_activity_overdue",
        "form_submitted",
        "survey_submitted",
    }
)

#: `schedule` is global-only, so it needs an automation of its own.
GLOBAL_TRIGGER_TYPES = frozenset({"manual", "schedule"})

#: `email_received` is Contact-only, so it needs one too.
CONTACT_TRIGGER_TYPES = frozenset({"manual", "email_received"})

COVERED_TRIGGER_TYPES = (
    RECORD_TRIGGER_TYPES | GLOBAL_TRIGGER_TYPES | CONTACT_TRIGGER_TYPES
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_automation(
    drift_client, scratch, spec_dict: dict[str, Any]
) -> dict[str, Any]:
    """Build one automation with the real planner payload, POST it, track it.

    Returns the sent payload alongside the live read, so a test can assert on
    both dialects of the same automation.
    """
    from kizen_builder.api import automations as auto_api
    from kizen_builder.models.spec import AutomationDef
    from kizen_builder.tools.planners.automations import (
        LiveContext,
        _build_automation_payload,
    )

    spec = AutomationDef.model_validate(spec_dict)
    payload = _build_automation_payload(spec, LiveContext())
    created = auto_api.create_automation(drift_client, payload)
    scratch.track(
        "automation",
        created["id"],
        lambda: auto_api.delete_automation(drift_client, created["id"]),
    )
    return {
        "sent": payload,
        "live": auto_api.get_automation(drift_client, created["id"]),
        "uuid": created["id"],
        "api_name": spec.api_name,
    }


def _replace_automation(
    drift_client, record: dict[str, Any], spec_dict: dict[str, Any]
) -> dict[str, Any]:
    """PUT a new full body over an existing drift automation, and re-read.

    Needed for steps whose config points at an automation-scoped resource
    (the ``notify_member_via_*`` / ``send_related_contact_*`` message
    resources), which cannot exist before the automation they belong to.
    """
    from kizen_builder.api import automations as auto_api
    from kizen_builder.models.spec import AutomationDef
    from kizen_builder.tools.planners.automations import (
        LiveContext,
        _build_automation_payload,
    )

    spec = AutomationDef.model_validate(spec_dict)
    payload = _build_automation_payload(spec, LiveContext())
    auto_api.update_automation(
        drift_client,
        record["uuid"],
        payload,
        last_revision=record["live"]["revision"],
    )
    return {
        **record,
        "sent": payload,
        "live": auto_api.get_automation(drift_client, record["uuid"]),
    }


def _sent_step_types(record: dict[str, Any]) -> set[str]:
    """Step types in the *write* dialect — the wire key is `type`, not `step_type`."""
    return {s["type"] for s in record["sent"]["steps"]}


def _live_steps_by_type(record: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for s in record["live"]["steps"]:
        out.setdefault(s["step_type"], []).append(s)
    return out


def _base_spec(what: str, drift_object) -> dict[str, Any]:
    return {
        "api_name": debris_api_name(what),
        "name": debris_name(f"automation {what}"),
        "type": "record_based",
        "target_object": drift_object["api_name"],
        "active": False,
        "triggers": [],
        "steps": [],
    }


# ---------------------------------------------------------------------------
# Support fixtures — the live things the step configs point at
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_step_fields(drift_client, scratch, drift_object) -> dict[str, Any]:
    """One field of each type the step configs below need to reference.

    Created here rather than reused from ``test_roundtrip_drift.py``'s field
    tests: those are tests, not fixtures, so nothing guarantees they ran (or
    ran first).
    """
    specs = {
        "text": ("Drift Step Text", "text"),
        "number": ("Drift Step Number", "integer"),
        "longtext": ("Drift Step Summary", "longtext"),
        "files": ("Drift Step Attachment", "files"),
        "date": ("Drift Step Due Date", "date"),
    }
    fields = {
        role: create_field_on(
            drift_client,
            scratch,
            drift_object,
            {
                "name": display,
                "api_name": debris_api_name(f"step_{role}"),
                "field_type": field_type,
            },
        )
        for role, (display, field_type) in specs.items()
    }
    # A relationship to Contacts, for the two send_related_contact_* steps.
    # Those target contacts specifically, and both HTTP **500** (not 400)
    # without a `send_to_contact_field` — confirmed live 2026-08-06 — so a
    # real contacts hop is the minimum setup for either of them.
    fields["contact"] = create_field_on(
        drift_client,
        scratch,
        drift_object,
        {
            "name": "Drift Step Contact",
            "api_name": debris_api_name("step_contact"),
            "field_type": "relationship",
            "relation": {
                "target_object": "client_client",
                "relation_type": "many_to_one",
                "related_name": "Drift Step Records",
            },
        },
    )
    return fields


@pytest.fixture(scope="session")
def drift_activity_type(drift_client, scratch, drift_object) -> dict[str, Any]:
    """A throwaway activity type — referenced by two triggers and one step."""
    from kizen_builder.api import activities as act_api
    from kizen_builder.tools.planners.activities import plan_create_activity

    plan = plan_create_activity(
        {
            "name": debris_name("activity"),
            "api_name": debris_api_name("activity"),
            "association_mode": "all_objects_associated",
        }
    )
    created = act_api.create_activity(drift_client, plan.operations[0].payload)
    scratch.track(
        "activity type",
        created["id"],
        lambda: act_api.delete_activity(drift_client, created["id"]),
    )
    return created


def _create_form_like(
    drift_client, scratch, drift_object, *, kind: str
) -> dict[str, Any]:
    from kizen_builder.api import forms as forms_api
    from kizen_builder.tools.planners.forms import plan_create_form

    base_path = "/api/forms" if kind == "form" else "/api/surveys"
    plan = plan_create_form(
        {
            "name": debris_name(kind),
            "api_name": debris_api_name(kind),
            "related_object": drift_object["api_name"],
            "template_type": "modern",
        },
        base_path=base_path,
        kind=kind,
    )
    created = forms_api.create_form(drift_client, base_path, plan.operations[0].payload)
    scratch.track(
        kind,
        created["id"],
        lambda: forms_api.delete_form(drift_client, base_path, created["id"]),
    )
    return created


@pytest.fixture(scope="session")
def drift_form(drift_client, scratch, drift_object) -> dict[str, Any]:
    """A throwaway form — the only way to exercise the `form_submitted` trigger."""
    return _create_form_like(drift_client, scratch, drift_object, kind="form")


@pytest.fixture(scope="session")
def drift_survey(drift_client, scratch, drift_object) -> dict[str, Any]:
    """A throwaway survey — same, for `survey_submitted`."""
    return _create_form_like(drift_client, scratch, drift_object, kind="survey")


@pytest.fixture(scope="session")
def drift_team_member_id(drift_config) -> str:
    """The authenticated user's own team-member id. Read-only; creates nothing."""
    from kizen_builder.tools.team import search_team

    members = search_team("", limit=25)
    assert members, "the drift environment has no team members to assign to"
    return members[0]["id"]


@pytest.fixture(scope="session")
def drift_llm_models(drift_config) -> dict[str, dict[str, Any]]:
    """Live LLM catalogue, keyed by capability.

    `model_name` is unvalidated client-side and a wrong one 400s the same way
    a missing `business_plugin_app_id` does, so the ids come from the
    environment's own catalogue (``GET /api/automation2/automations/metadata``
    via ``list_llm_models``) rather than a literal. Native ``kizen/*`` models
    are preferred because they need no ``business_plugin_app_id`` at all.
    """
    from kizen_builder.tools.automations import list_llm_models

    rows = [r for r in list_llm_models() if not r.get("is_deprecated")]
    assert rows, "the automations metadata endpoint returned no LLM models"

    def pick(capability: str) -> dict[str, Any]:
        usable = [r for r in rows if r.get(capability)]
        assert usable, (
            f"no live model advertises {capability}; either the catalogue shape "
            "changed or this business has no such model enabled"
        )
        native = [r for r in usable if not r["business_plugin_app_id"]]
        return (native or usable)[0]

    return {
        "call": pick("supports_call"),
        "extraction": pick("supports_extraction"),
        "transcription": pick("supports_transcription"),
    }


def _llm_block(model: dict[str, Any], **extra: Any) -> dict[str, Any]:
    block = {"model_name": model["model_value"], **extra}
    if model["business_plugin_app_id"]:
        block["business_plugin_app_id"] = model["business_plugin_app_id"]
    return block


# ---------------------------------------------------------------------------
# The branching baseline (fixture lives in conftest.py — see the note there)
# ---------------------------------------------------------------------------


def test_automation_write_dialect_is_accepted(drift_automation):
    """The write envelope the CLI sends is *not* what the schema documents.

    Sent per step: ``key`` / ``parent_key`` / ``parent_yes_no`` /
    ``parent_condition`` / ``type`` / ``prefix`` / ``goal_type``.
    ``WriteStepRequest`` instead documents ``step_type`` as required and knows
    none of those. This test is the standing evidence that the CLI's dialect —
    not the schema's — is the one the API accepts.
    """
    steps = drift_automation["sent"]["steps"]
    assert {s["key"] for s in steps} == {"check", "stop_yes", "stop_no"}
    for s in steps:
        assert "type" in s and "step_type" not in s
        assert {
            "key",
            "parent_key",
            "parent_yes_no",
            "parent_condition",
            "prefix",
        } <= set(s)
    # It was accepted: the fixture would have raised on a 4xx/5xx.
    assert drift_automation["uuid"]


def test_automation_branch_linkage_survives_the_roundtrip(drift_automation):
    """Write side is ``parent_key`` + ``parent_yes_no``; read side is
    ``parent_step_id`` + ``parent_condition``. Both branches must come back
    attached to the condition step."""
    live = drift_automation["live"]
    steps = live["steps"]
    assert len(steps) == 3, [s.get("step_type") for s in steps]

    by_type: dict[str, list[dict[str, Any]]] = {}
    for s in steps:
        by_type.setdefault(s["step_type"], []).append(s)
    assert sorted(by_type) == ["condition", "stop_execution"]

    condition = by_type["condition"][0]
    assert condition["parent_step_id"] is None
    assert condition["step_condition"]["type"] == "custom_filter"
    # The 500-on-write fields must not come back populated either.
    assert not condition["step_condition"].get("yes_step_ids")
    assert not condition["step_condition"].get("no_step_ids")
    # condition steps are forced to notify_pause by the planner
    assert condition["action_on_failure"] == "notify_pause"

    stops = by_type["stop_execution"]
    assert {s["parent_step_id"] for s in stops} == {condition["id"]}
    assert sorted(s["parent_condition"] for s in stops) == ["no", "yes"]


def test_automation_read_shape_keys_the_planner_depends_on(drift_automation):
    """`plan_update_automation` rebuilds a PUT body from the read response, so
    these keys are load-bearing (see the normalization notes in
    ``docs/specs/automation.md``)."""
    live = drift_automation["live"]
    for key in (
        "id",
        "api_name",
        "revision",
        "type",
        "steps",
        "triggers",
        "custom_object",
    ):
        assert key in live, f"read response lost `{key}`"
    assert isinstance(live["revision"], int)
    # A manual trigger is auto-prepended by the planner alongside the declared one.
    trigger_types = sorted(t["trigger_type"] for t in live["triggers"])
    assert trigger_types == ["manual", "new_entity_created"], trigger_types


# ---------------------------------------------------------------------------
# A field compared to an array variable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_variable_condition(drift_client, scratch) -> dict[str, Any]:
    """A dropdown "is any of" a string-array variable, built by the planner,
    on an object of its own."""
    obj = _create_drift_object(drift_client, scratch, "array_variable_object")
    field = create_field_on(
        drift_client,
        scratch,
        obj,
        {
            "name": "Drift Array Variable Status",
            "api_name": debris_api_name("array_variable_status"),
            "field_type": "dropdown",
            "options": ["Open", "Closed"],
        },
    )
    spec = _base_spec("array_variable", obj)
    spec["steps"] = [
        {
            "key": "allowed",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "sources": [],
                "variable": {
                    "name": "allowed_statuses",
                    "data_type": "string",
                    "is_array": True,
                },
                "array_aggregation_mode": "add_all",
            },
        },
        {
            "key": "check",
            "step_type": "condition",
            "order": 1,
            "parent_key": "allowed",
            "step_condition": {
                "type": "custom_filter",
                "filter_config": {
                    "all": [
                        {
                            "field": field["name"],
                            "op": "is_any_of",
                            "variable": "allowed_statuses",
                        }
                    ]
                },
            },
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_variable_comparison_survives_the_roundtrip(drift_variable_condition):
    """The server stores the clause as sent: `value` is the variable's name,
    `value_type` says so, and the UI's `view_model` comes back intact."""

    def clause(steps, kind_key):
        (step,) = [s for s in steps if s[kind_key] == "condition"]
        (only,) = step["step_condition"]["filter_config"]["query"][0]["filters"]
        return only

    sent = clause(drift_variable_condition["sent"]["steps"], "type")
    live = clause(drift_variable_condition["live"]["steps"], "step_type")
    assert sent["value"] == "allowed_statuses"
    assert sent["value_type"] == "variable"
    assert {k: live[k] for k in sent} == sent


# ---------------------------------------------------------------------------
# Triggers — all ten on one automation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_triggers(
    drift_client,
    scratch,
    drift_object,
    drift_step_fields,
    drift_activity_type,
    drift_form,
    drift_survey,
) -> dict[str, Any]:
    """One automation carrying nine of the eleven wired trigger types.

    Directly analogous to the offline ``kitchen_sink_triggers.raw.json``
    capture, which is the evidence that many triggers on one automation is a
    shape the product itself produces. ``manual`` is declared explicitly here
    rather than left to the planner's auto-prepend (covered separately by
    ``test_automation_read_shape_keys_the_planner_depends_on``), so the
    ordering stays under this spec's control.

    ``schedule`` can't ride along: a record-based automation rejects it
    outright (*"Trigger type 'schedule' is only available for global agentic
    workflows"*, confirmed live 2026-08-06), so it gets its own global
    automation in :func:`drift_schedule_trigger`. ``email_received`` is
    Contact-only, so it gets :func:`drift_email_received_trigger`.
    """
    activity_id = drift_activity_type["id"]
    spec = _base_spec("trigs", drift_object)
    spec["triggers"] = [
        {"trigger_type": "manual", "order": 0, "trigger_manual": {}},
        {
            "trigger_type": "new_entity_created",
            "order": 1,
            "trigger_new_entity_created": {"action": "create_and_unarchive"},
        },
        {
            "trigger_type": "activity_logged",
            "order": 2,
            "trigger_activity_logged": {"activity_type_id": activity_id},
        },
        {
            "trigger_type": "scheduled_activity_overdue",
            "order": 3,
            "trigger_scheduled_activity_overdue": {"activity": {"id": activity_id}},
        },
        {
            "trigger_type": "on_or_around_date",
            "order": 4,
            "trigger_on_or_around_date": {
                "field_id": drift_step_fields["date"]["id"],
                "date_offset": "days_before",
                "date_offset_days": 3,
                "time": "09:15",
                "period": "AM",
                "every_year": False,
            },
        },
        {
            "trigger_type": "field_updated",
            "order": 5,
            "trigger_field_updated": {
                # field_ref is required by the model; field_id is the wire key
                # and wins when both are present.
                "field_ref": f"{drift_object['api_name']}.{drift_step_fields['text']['name']}",
                "field_id": drift_step_fields["text"]["id"],
                "fire_on_create": False,
                "from_match_mode": "any_including_blank",
                "to_match_mode": "any_excluding_blank",
            },
        },
        {
            "trigger_type": "webhook",
            "order": 6,
            "trigger_webhook": {
                "name": debris_api_name("hook"),
                "http_method": "POST",
                "content_type": "application/json",
                "sample_post_body": '{"key": "value"}',
                "extract_raw_body_content": True,
                "extract_url_query_string": True,
                "extractors": [
                    {"value_name": "key", "value_json_path": "$['key']", "order": 0}
                ],
            },
        },
        {
            "trigger_type": "form_submitted",
            "order": 7,
            "trigger_form_submitted": {"form_id": drift_form["id"]},
        },
        {
            "trigger_type": "survey_submitted",
            "order": 8,
            "trigger_survey_submitted": {"survey_id": drift_survey["id"]},
        },
    ]
    spec["steps"] = [
        {
            "key": "stop",
            "step_type": "stop_execution",
            "order": 0,
            "parent_key": None,
            "action_stop_execution": {"action": "stop_and_complete"},
        }
    ]
    return _create_automation(drift_client, scratch, spec)


@pytest.fixture(scope="session")
def drift_schedule_trigger(drift_client, scratch) -> dict[str, Any]:
    """The `schedule` trigger, on the only kind of automation that takes it.

    Global automations have no target_object, which is exactly why `schedule`
    is their only time-based option — `on_or_around_date` needs a date field
    to hang off.
    """
    spec = {
        "api_name": debris_api_name("sched"),
        "name": debris_name("automation sched"),
        "type": "global",
        "active": False,
        "triggers": [
            {"trigger_type": "manual", "order": 0, "trigger_manual": {}},
            {
                "trigger_type": "schedule",
                "order": 1,
                "trigger_schedule": {
                    "rrule": "DTSTART:20260801T120000Z\nRRULE:FREQ=DAILY;INTERVAL=1",
                    "is_advanced": False,
                },
            },
        ],
        "steps": [
            {
                "key": "stop",
                "step_type": "stop_execution",
                "order": 0,
                "parent_key": None,
                "action_stop_execution": {"action": "stop_and_complete"},
            }
        ],
    }
    return _create_automation(drift_client, scratch, spec)


@pytest.fixture(scope="session")
def drift_email_received_trigger(drift_client, scratch) -> dict[str, Any]:
    """`email_received`, as a trigger and as a goal's wait-until trigger.

    A custom-object automation rejects it ("only client custom object is
    allowed", confirmed live 2026-10-14), so it runs on Contact.
    """
    spec = {
        "api_name": debris_api_name("email"),
        "name": debris_name("automation email"),
        "type": "record_based",
        "target_object": "client_client",
        "active": False,
        "triggers": [
            {"trigger_type": "manual", "order": 0, "trigger_manual": {}},
            {
                "trigger_type": "email_received",
                "order": 1,
                "trigger_email_received_from_contact": {"receiving_inbox": "any"},
            },
        ],
        "steps": [
            {
                "key": "goal",
                "step_type": "goal",
                "order": 0,
                "parent_key": None,
                "step_goal": {
                    "wait_type": "never",
                    "triggers": [{"trigger_type": "email_received"}],
                },
            }
        ],
    }
    return _create_automation(drift_client, scratch, spec)


def test_every_wired_trigger_type_is_accepted(
    drift_triggers, drift_schedule_trigger, drift_email_received_trigger
):
    """The write half: every wired type goes out and the POST succeeds."""
    sent = {t["type"] for t in drift_triggers["sent"]["triggers"]}
    assert sent == set(RECORD_TRIGGER_TYPES), sorted(
        sent.symmetric_difference(RECORD_TRIGGER_TYPES)
    )
    # Declaring `manual` ourselves suppresses the planner's auto-prepend, so
    # the count is exactly what the spec asked for.
    assert len(drift_triggers["sent"]["triggers"]) == len(RECORD_TRIGGER_TYPES)

    scheduled = {t["type"] for t in drift_schedule_trigger["sent"]["triggers"]}
    assert scheduled == set(GLOBAL_TRIGGER_TYPES)
    contact = {t["type"] for t in drift_email_received_trigger["sent"]["triggers"]}
    assert contact == set(CONTACT_TRIGGER_TYPES)
    assert sent | scheduled | contact == set(COVERED_TRIGGER_TYPES)


def test_schedule_trigger_is_flat_in_both_dialects(drift_schedule_trigger):
    """Nothing to unwrap: `{rrule, is_advanced}` goes out and comes back
    identical, unlike every other reference-bearing trigger."""
    sent = next(
        t for t in drift_schedule_trigger["sent"]["triggers"] if t["type"] == "schedule"
    )["trigger_schedule"]
    live = next(
        t
        for t in drift_schedule_trigger["live"]["triggers"]
        if t["trigger_type"] == "schedule"
    )["trigger_schedule"]
    assert sent == {
        "rrule": "DTSTART:20260801T120000Z\nRRULE:FREQ=DAILY;INTERVAL=1",
        "is_advanced": False,
    }
    assert live["rrule"] == sent["rrule"]
    assert live["is_advanced"] is False


def test_email_received_reads_back_as_sent(drift_email_received_trigger):
    """The config key is `trigger_email_received_from_contact` in both
    dialects, and reads add an empty `selected_integrated_inboxes`."""
    record = drift_email_received_trigger
    sent = next(t for t in record["sent"]["triggers"] if t["type"] == "email_received")
    live = next(
        t for t in record["live"]["triggers"] if t["trigger_type"] == "email_received"
    )
    expected = {"receiving_inbox": "any", "selected_integrated_inboxes": []}
    assert sent["trigger_email_received_from_contact"] == {"receiving_inbox": "any"}
    assert live["trigger_email_received_from_contact"] == expected

    (goal,) = _live_steps_by_type(record)["goal"]
    (nested,) = goal["step_goal"]["triggers"]
    assert nested["trigger_type"] == "email_received"
    assert nested["trigger_email_received_from_contact"] == expected


def test_email_received_puts_back_unchanged(drift_client, drift_email_received_trigger):
    """What every re-PUT verb (`activate`, `steps edit`, `roundtrip
    --execute`) does to the trigger and the goal: translate, PUT, re-read."""
    from kizen_builder.api import automations as auto_api
    from kizen_builder.translate import live_to_payload, semantic_diff, validate_payload

    before = drift_email_received_trigger["live"]
    payload = live_to_payload(before)
    assert validate_payload(payload) == []
    auto_api.update_automation(
        drift_client,
        drift_email_received_trigger["uuid"],
        payload,
        last_revision=payload.get("last_revision"),
    )
    after = auto_api.get_automation(drift_client, drift_email_received_trigger["uuid"])
    assert list(semantic_diff(before, after)) == []


def test_every_wired_trigger_type_survives_the_roundtrip(drift_triggers):
    """The read half: same set comes back, keyed the way ``LiveContext`` and
    the read→write translator expect (``trigger_type``, plus a
    ``trigger_<type>`` config block)."""
    live = drift_triggers["live"]["triggers"]
    assert {t["trigger_type"] for t in live} == set(RECORD_TRIGGER_TYPES)
    for trigger in live:
        ttype = trigger["trigger_type"]
        if ttype == "manual":
            # No config block: the manual trigger carries no configuration at
            # all (recorded in KNOWN_UNDOCUMENTED_BLOCKS for the same reason).
            continue
        assert trigger_config_field(ttype) in trigger, (
            f"live read of a '{ttype}' trigger has no {trigger_config_field(ttype)} block; "
            "the planner's read→write translation keys off exactly that name"
        )


def test_trigger_reference_fields_round_trip_as_expanded_objects(
    drift_triggers, drift_activity_type, drift_step_fields, drift_form, drift_survey
):
    """Writes take bare ids; reads expand them. Both halves are load-bearing —
    the translator unwraps the read shape back to the write one, so a change
    in either direction breaks `plan_update_automation`."""
    sent = {t["type"]: t for t in drift_triggers["sent"]["triggers"]}
    live = {t["trigger_type"]: t for t in drift_triggers["live"]["triggers"]}

    # Written flat, read expanded.
    assert sent["activity_logged"]["trigger_activity_logged"] == {
        "activity_type_id": drift_activity_type["id"]
    }
    assert (
        live["activity_logged"]["trigger_activity_logged"]["activity_type"]["id"]
        == drift_activity_type["id"]
    )
    # scheduled_activity_overdue is the one trigger whose write shape is an
    # {"id": ...} association rather than a bare `*_id` scalar.
    assert sent["scheduled_activity_overdue"]["trigger_scheduled_activity_overdue"][
        "activity"
    ] == {"id": drift_activity_type["id"]}
    assert (
        live["scheduled_activity_overdue"]["trigger_scheduled_activity_overdue"][
            "activity"
        ]["id"]
        == drift_activity_type["id"]
    )

    assert (
        sent["field_updated"]["trigger_field_updated"]["field_id"]
        == drift_step_fields["text"]["id"]
    )
    assert (
        live["field_updated"]["trigger_field_updated"]["field"]["id"]
        == drift_step_fields["text"]["id"]
    )
    assert (
        sent["on_or_around_date"]["trigger_on_or_around_date"]["field_id"]
        == drift_step_fields["date"]["id"]
    )
    # The planner lowercases the period; the live API rejects "AM".
    assert sent["on_or_around_date"]["trigger_on_or_around_date"]["period"] == "am"
    assert live["on_or_around_date"]["trigger_on_or_around_date"]["period"] == "am"


def test_form_and_survey_trigger_wire_shapes_are_accepted(
    drift_triggers, drift_form, drift_survey
):
    """`form_submitted`/`survey_submitted` were built to the bare-scalar-id
    convention as a *guess* — no live capture of either existed. This is the
    confirmation; if it starts failing, the guess was wrong and the builders
    in ``planners/automations.py`` need the real shape."""
    sent = {t["type"]: t for t in drift_triggers["sent"]["triggers"]}
    live = {t["trigger_type"]: t for t in drift_triggers["live"]["triggers"]}
    assert sent["form_submitted"]["trigger_form_submitted"] == {
        "form_id": drift_form["id"]
    }
    assert sent["survey_submitted"]["trigger_survey_submitted"] == {
        "survey_id": drift_survey["id"]
    }
    assert "trigger_form_submitted" in live["form_submitted"]
    assert "trigger_survey_submitted" in live["survey_submitted"]


# ---------------------------------------------------------------------------
# Steps: control flow
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_startable_automation(drift_client, scratch, drift_object) -> dict[str, Any]:
    """An **active** automation for a `start_automation` step to point at.

    Confirmed live 2026-08-06: `automation_ids` rejects an *inactive*
    automation with ``{'automation_ids': {'0': 'Automation not found'}}`` —
    a message that reads like a bad UUID rather than a state requirement. It
    also rejects a `global` automation from a record-based caller, and
    rejects an empty list. So the target has to be a live, active,
    same-object automation.

    Safe to leave active: its only trigger is the auto-prepended `manual`
    one, which never fires on its own.
    """
    spec = _base_spec("target", drift_object)
    spec["active"] = True
    spec["steps"] = [
        {
            "key": "stop",
            "step_type": "stop_execution",
            "order": 0,
            "parent_key": None,
            "action_stop_execution": {"action": "stop_and_complete"},
        }
    ]
    record = _create_automation(drift_client, scratch, spec)
    assert record["live"]["active"] is True, (
        "the target automation did not come back active; a start_automation "
        "step cannot reference an inactive one"
    )
    return record


@pytest.fixture(scope="session")
def drift_code_step_automation(drift_client, scratch, drift_object) -> dict[str, Any]:
    """An **active** automation with a `code_step` first (no `delay` ahead of
    it), for `test_wait_for_execution_and_history_row_shapes_against_a_live_run`
    to reach a real `code_step`'s `detailed_log` inside a short poll budget.

    `drift_control_flow` also has a `code_step`, but it sits behind a 1-day
    `delay` step and so can never complete inside any test's budget — a
    run started against it would only ever observe `stop_execution`'s
    `detailed_log` shape, not the one this item calls its central risk.
    """
    spec = _base_spec("codelog", drift_object)
    spec["active"] = True
    spec["steps"] = [
        {
            "key": "log",
            "step_type": "code_step",
            "order": 0,
            "parent_key": None,
            "action_code_step": {
                "script": 'outputs.log("drift-wait-for-execution")',
                "runtime": "python-3-13",
            },
        },
        {
            "key": "stop",
            "step_type": "stop_execution",
            "order": 1,
            "parent_key": "log",
            "action_stop_execution": {"action": "stop_and_complete"},
        },
    ]
    record = _create_automation(drift_client, scratch, spec)
    assert record["live"]["active"] is True, (
        "the code_step target automation did not come back active"
    )
    return record


@pytest.fixture(scope="session")
def drift_control_flow(
    drift_client,
    scratch,
    drift_object,
    drift_step_fields,
    drift_activity_type,
    drift_startable_automation,
) -> dict[str, Any]:
    """Graph-shaped steps: variables at the root, then a branch and a merge.

    The two ``initialize_variable`` steps sit on the un-branched root chain on
    purpose — the server rejects a new-variable declaration anywhere else (see
    automation.md's root-chain rule), and this is the only automated check that
    the rule still holds.
    """
    spec = _base_spec("ctrl", drift_object)
    spec["steps"] = [
        {
            "key": "init_note",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "variable": {"name": "drift_note", "data_type": "string"},
                # `source_subtype` must name the variable's own data type —
                # omitting it 400s even though the schema marks only
                # `source_type` required.
                "sources": [
                    {
                        "source_type": "static",
                        "source_subtype": "string",
                        "value": "drift",
                    }
                ],
                "is_required": False,
            },
        },
        {
            "key": "init_found",
            "step_type": "initialize_variable",
            "order": 1,
            "parent_key": "init_note",
            "action_initialize_variable": {
                "variable": {
                    "name": "drift_found",
                    "data_type": "entity",
                    "data_subtype": drift_object["uuid"],
                    "is_array": True,
                },
                "sources": [],
                # Required (non-null) for an array variable.
                "array_aggregation_mode": "add_all",
            },
        },
        {
            "key": "search",
            "step_type": "search_records",
            "order": 2,
            "parent_key": "init_found",
            "action_search_records": {
                "custom_object": drift_object["api_name"],
                "filter_type": "all_records",
                "destination_variable": "drift_found",
                "destination_variable_resolution": "overwrite",
            },
        },
        {
            "key": "wait",
            "step_type": "delay",
            "order": 3,
            "parent_key": "search",
            "step_delay": {"days": 1, "value_origin": "static"},
        },
        {
            "key": "code",
            "step_type": "code_step",
            "order": 4,
            "parent_key": "wait",
            "action_code_step": {
                "script": "outputs.drift_note = str(inputs.drift_note or '')",
                "runtime": "python-3-13",
                "inputs": [
                    {
                        "name": "drift_note",
                        "input_type": "variable",
                        "variable": {"name": "drift_note"},
                    }
                ],
                "outputs": [
                    {
                        "name": "drift_note",
                        "output_type": "field",
                        "field_id": drift_step_fields["text"]["id"],
                    }
                ],
            },
        },
        {
            "key": "goal",
            "step_type": "goal",
            "order": 5,
            "parent_key": "code",
            "step_goal": {
                "wait_type": "delay",
                "delay_type": "minutes",
                "delay_amount": 5,
                # Not every trigger type is legal inside a goal —
                # `new_entity_created` 400s with "Trigger type … is not
                # allowed for goal step". `activity_logged` is, and matches
                # the shape in the offline kitchen-sink capture.
                "triggers": [
                    {
                        "trigger_type": "activity_logged",
                        "order": 0,
                        "trigger_activity_logged": {
                            "activity_type_id": drift_activity_type["id"]
                        },
                    }
                ],
            },
        },
        {
            "key": "check",
            "step_type": "condition",
            "order": 6,
            "parent_key": "goal",
            "step_condition": {
                "type": "custom_filter",
                "filter_config": {"all": [{"field": "name", "op": "not_blank"}]},
            },
        },
        {
            "key": "kick",
            "step_type": "start_automation",
            "order": 7,
            "parent_key": "check",
            "parent_branch": "yes",
            "action_start_automation": {
                "record_source": "this_record",
                "automation_ids": [drift_startable_automation["uuid"]],
            },
        },
        {
            "key": "stop",
            "step_type": "stop_execution",
            "order": 8,
            "parent_key": "check",
            "parent_branch": "no",
            "action_stop_execution": {"action": "stop_and_complete"},
        },
        {
            # Converging the YES branch back onto the NO branch's tail.
            "key": "merge",
            "step_type": "go_to_automation_step",
            "order": 9,
            "parent_key": "kick",
            "action_go_to_automation_step": {"step_key": "stop"},
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_control_flow_steps_are_accepted(drift_control_flow):
    assert _sent_step_types(drift_control_flow) == set(CONTROL_FLOW_STEPS)
    assert set(_live_steps_by_type(drift_control_flow)) == set(CONTROL_FLOW_STEPS)


def test_initialize_variable_declares_the_automation_variable_set(drift_control_flow):
    """Variables are automation-level state the planner reconstructs from the
    read (`_merge_server_state`), keyed by name — the ids it strips are
    reassigned on every write."""
    variables = {v["name"]: v for v in drift_control_flow["live"]["variables"]}
    assert {"drift_note", "drift_found"} <= set(variables)
    assert variables["drift_note"]["data_type"] == "string"
    assert variables["drift_found"]["is_array"] is True
    assert all(v.get("id") for v in variables.values()), (
        "the server stopped assigning variable ids; `_merge_server_state` "
        "strips them on write precisely because it does"
    )


def test_search_records_reference_fields_are_objects_not_bare_scalars(
    drift_control_flow, drift_object
):
    """The documented exception to this codebase's bare-scalar convention:
    `custom_object`/`destination_variable` are `{"id"|"name": ...}` objects on
    write, and bare strings 500. Asserted on the payload the API just
    accepted."""
    sent = next(
        s for s in drift_control_flow["sent"]["steps"] if s["type"] == "search_records"
    )["action_search_records"]
    assert sent["custom_object"] == {"id": drift_object["uuid"]}
    assert sent["destination_variable"] == {"name": "drift_found"}
    assert sent["filter_groups"] == []


def test_go_to_automation_step_resolves_its_target_key(drift_control_flow):
    """`step_key` is a client-supplied key on write; the read resolves it to
    the target step's server-assigned UUID."""
    live = _live_steps_by_type(drift_control_flow)
    (goto,) = live["go_to_automation_step"]
    (stop,) = live["stop_execution"]
    block = goto["action_go_to_automation_step"]
    target = block.get("step")
    target_id = target.get("id") if isinstance(target, dict) else target
    assert target_id == stop["id"], (
        "go_to_automation_step lost its target on the round-trip: "
        f"{block!r} does not point at the stop_execution step {stop['id']}"
    )


def test_start_automation_write_dialect_is_the_one_that_sticks(
    drift_control_flow, drift_startable_automation
):
    """`automations`/`relationship_fields` (the read dialect) are *silently
    ignored* on write — the step would lose its target with a 200. The planner
    always emits `automation_ids`; this proves the target survived."""
    sent = next(
        s
        for s in drift_control_flow["sent"]["steps"]
        if s["type"] == "start_automation"
    )["action_start_automation"]
    assert sent["automation_ids"] == [drift_startable_automation["uuid"]]
    assert "automations" not in sent

    (live,) = _live_steps_by_type(drift_control_flow)["start_automation"]
    linked = live["action_start_automation"]["automations"]
    assert [a["id"] for a in linked] == [drift_startable_automation["uuid"]], (
        "start_automation was accepted but came back with no target — the "
        "write dialect changed and the failure mode is silent"
    )


def test_goal_step_embeds_triggers_in_the_trigger_dialect(drift_control_flow):
    """A goal's wait-until conditions are full triggers, built by the same
    ``_TRIGGER_BUILDERS`` the top-level ones use."""
    sent = next(s for s in drift_control_flow["sent"]["steps"] if s["type"] == "goal")[
        "step_goal"
    ]
    (trigger,) = sent["triggers"]
    assert trigger["type"] == "activity_logged"
    assert trigger["prefix"] == "trigger"
    assert "activity_type_id" in trigger["trigger_activity_logged"]

    (live,) = _live_steps_by_type(drift_control_flow)["goal"]
    assert [t["trigger_type"] for t in live["step_goal"]["triggers"]] == [
        "activity_logged"
    ]


# ---------------------------------------------------------------------------
# Branch groups: a merged condition, then parallel lanes under a branch step
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_branch_groups(drift_client, scratch, drift_object) -> dict[str, Any]:
    def step(key: str, parent: str | None, step_type: str, **kw: Any) -> dict:
        return {"key": key, "parent_key": parent, "step_type": step_type, **kw}

    def wait(key: str, parent: str, **kw: Any) -> dict:
        delay = {"days": 1, "value_origin": "static"}
        return step(key, parent, "delay", step_delay=delay, **kw)

    spec = _base_spec("groups", drift_object)
    spec["steps"] = [
        step(
            "check",
            None,
            "condition",
            is_branch_group_initiator=True,
            step_condition={
                "type": "custom_filter",
                "filter_config": {"all": [{"field": "name", "op": "not_blank"}]},
            },
        ),
        wait("wait_yes", "check", parent_branch="yes"),
        wait("wait_no", "check", parent_branch="no"),
        step("merge", "check", "merge_branches"),
        step("lanes", "merge", "branch"),
        wait("lane_a", "lanes"),
        wait("lane_b", "lanes"),
    ]
    for order, s in enumerate(spec["steps"]):
        s["order"] = order
    return _create_automation(drift_client, scratch, spec)


def test_branch_group_steps_are_accepted(drift_branch_groups):
    assert _sent_step_types(drift_branch_groups) == set(BRANCH_GROUP_STEPS)
    assert set(_live_steps_by_type(drift_branch_groups)) == set(BRANCH_GROUP_STEPS)


def test_branch_group_linkage_survives_the_roundtrip(drift_branch_groups):
    """The merge hangs off its initiator with no lane, and what follows the
    group hangs off the merge."""
    live = _live_steps_by_type(drift_branch_groups)
    (check,) = live["condition"]
    (merge,) = live["merge_branches"]
    (lanes,) = live["branch"]
    assert check["is_branch_group_initiator"] is True
    assert merge["parent_step_id"] == check["id"]
    assert not merge["parent_condition"]
    assert lanes["parent_step_id"] == merge["id"]
    assert lanes["is_branch_group_initiator"] is False
    delays = [(s["parent_step_id"], s["parent_condition"] or "") for s in live["delay"]]
    expected = [(check["id"], "yes"), (check["id"], "no")] + [(lanes["id"], "")] * 2
    assert sorted(delays) == sorted(expected)


def _put_back_unchanged(drift_client, record: dict[str, Any]) -> None:
    """What `roundtrip --execute` does: translate the read, PUT it, re-read."""
    from kizen_builder.api import automations as auto_api
    from kizen_builder.translate import live_to_payload, semantic_diff, validate_payload

    before = auto_api.get_automation(drift_client, record["uuid"])
    payload = live_to_payload(before)
    assert validate_payload(payload) == []
    auto_api.update_automation(
        drift_client,
        record["uuid"],
        payload,
        last_revision=payload.get("last_revision"),
    )
    after = auto_api.get_automation(drift_client, record["uuid"])
    assert list(semantic_diff(before, after)) == []


def test_branch_groups_put_back_unchanged(drift_client, drift_branch_groups):
    _put_back_unchanged(drift_client, drift_branch_groups)


# ---------------------------------------------------------------------------
# Loops: a merged condition with an exit_loop in the body, a second loop after
# the first, and a step after that
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_loops(drift_client, scratch, drift_object) -> dict[str, Any]:
    def step(key: str, parent: str | None, step_type: str, **kw: Any) -> dict:
        return {"key": key, "parent_key": parent, "step_type": step_type, **kw}

    def var(key: str, parent: str | None, is_array: bool) -> dict:
        block: dict[str, Any] = {
            "sources": [],
            "variable": {"name": key, "data_type": "string", "is_array": is_array},
        }
        if is_array:
            block["array_aggregation_mode"] = "add_all"
            block["sources"] = [
                {"source_type": "static", "source_subtype": "string", "value": ["a"]}
            ]
        return step(
            key, parent, "initialize_variable", action_initialize_variable=block
        )

    def loop(key: str, parent: str, array: str) -> dict:
        block = {"array_variable": array, "item_variable": f"{array}_item"}
        return step(
            key, parent, "loop", is_branch_group_initiator=True, step_loop=block
        )

    def wait(key: str, parent: str, **kw: Any) -> dict:
        delay = {"days": 1, "value_origin": "static"}
        return step(key, parent, "delay", step_delay=delay, **kw)

    spec = _base_spec("loops", drift_object)
    spec["steps"] = [
        var("arr0", None, True),
        var("arr0_item", "arr0", False),
        var("arr1", "arr0_item", True),
        var("arr1_item", "arr1", False),
        loop("each", "arr1_item", "arr0"),
        step(
            "check",
            "each",
            "condition",
            is_branch_group_initiator=True,
            step_condition={
                "type": "custom_filter",
                "filter_config": {"all": [{"field": "name", "op": "not_blank"}]},
            },
        ),
        wait("wait_yes", "check", parent_branch="yes"),
        step("leave", "check", "exit_loop", parent_branch="no"),
        step("merge", "check", "merge_branches"),
        step("next", "each", "next_or_finish"),
        loop("each_again", "next", "arr1"),
        wait("body", "each_again"),
        step("next_again", "each_again", "next_or_finish"),
        wait("after", "next_again"),
    ]
    for order, s in enumerate(spec["steps"]):
        s["order"] = order
    return _create_automation(drift_client, scratch, spec)


def test_loop_steps_are_accepted(drift_loops):
    assert _sent_step_types(drift_loops) == set(LOOP_STEPS)
    assert set(_live_steps_by_type(drift_loops)) == set(LOOP_STEPS)


def test_loop_linkage_survives_the_roundtrip(drift_loops):
    """The body hangs off the loop, its next_or_finish closes it, and what
    follows the loop hangs off the next_or_finish."""
    live = _live_steps_by_type(drift_loops)
    each, each_again = sorted(live["loop"], key=lambda s: s["order"])
    nxt, nxt_again = sorted(live["next_or_finish"], key=lambda s: s["order"])
    (check,) = live["condition"]
    (leave,) = live["exit_loop"]
    assert each["is_branch_group_initiator"] is True
    assert each["step_loop"]["array_variable"]["name"] == "arr0"
    assert each["step_loop"]["item_variable"]["name"] == "arr0_item"
    assert check["parent_step_id"] == each["id"]
    assert (leave["parent_step_id"], leave["parent_condition"]) == (check["id"], "no")
    assert nxt["parent_step_id"] == each["id"]
    assert each_again["parent_step_id"] == nxt["id"]
    assert nxt_again["parent_step_id"] == each_again["id"]
    after = max(live["delay"], key=lambda s: s["order"])
    assert after["parent_step_id"] == nxt_again["id"]


def test_loops_put_back_unchanged(drift_client, drift_loops):
    _put_back_unchanged(drift_client, drift_loops)


# ---------------------------------------------------------------------------
# Steps: data & variables
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_data_steps(
    drift_client, scratch, drift_object, drift_step_fields
) -> dict[str, Any]:
    spec = _base_spec("data", drift_object)
    spec["steps"] = [
        {
            "key": "init_num",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "variable": {"name": "drift_count", "data_type": "number"},
                "sources": [
                    {
                        "source_type": "static",
                        "source_subtype": "number",
                        "value": "1",
                    }
                ],
            },
        },
        {
            "key": "bump",
            "step_type": "update_variable",
            "order": 1,
            "parent_key": "init_num",
            "action_update_variable": {
                # The spec model takes the variable *definition*; the planner
                # reduces it to the bare name string the wire wants.
                "variable": {"name": "drift_count", "data_type": "number"},
                "sources": [
                    {
                        "source_type": "static",
                        "source_subtype": "number",
                        "value": "2",
                    }
                ],
            },
        },
        {
            "key": "math",
            "step_type": "math_operator",
            "order": 2,
            "parent_key": "bump",
            "action_math_operator": {
                "type": "simple_builder",
                "subtype": "addition",
                "variable": {"name": "drift_count"},
                "simple_builder_arguments": [
                    {
                        "argument_type": "constant",
                        "operation_type": "regular",
                        "constant": 4,
                    },
                    {
                        "argument_type": "variable",
                        "operation_type": "regular",
                        "variable": {"name": "drift_count"},
                    },
                ],
            },
        },
        {
            "key": "set_text",
            "step_type": "change_field_value",
            "order": 3,
            "parent_key": "math",
            "action_change_field_value": {
                "field_to_modify": drift_step_fields["text"]["id"],
                "specific_field_value": "set by the drift suite",
            },
        },
        {
            "key": "archive",
            "step_type": "archive_record",
            "order": 4,
            "parent_key": "set_text",
            "action_archive_record": {"record_source": "this_record"},
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_data_steps_are_accepted(drift_data_steps):
    assert _sent_step_types(drift_data_steps) == set(DATA_STEPS)
    assert set(_live_steps_by_type(drift_data_steps)) == set(DATA_STEPS)


def test_change_field_value_wraps_a_flat_spec_into_actions(
    drift_data_steps, drift_step_fields
):
    """The spec's flat single-change form becomes the wire's `actions: [...]`,
    and `specific_field_value` stays a bare scalar (not `{value: ...}`)."""
    sent = next(
        s
        for s in drift_data_steps["sent"]["steps"]
        if s["type"] == "change_field_value"
    )["action_change_field_value"]
    (action,) = sent["actions"]
    assert action["field_to_modify"] == drift_step_fields["text"]["id"]
    assert action["specific_field_value"] == "set by the drift suite"
    assert action["change_type"] == "specific_value"

    (live,) = _live_steps_by_type(drift_data_steps)["change_field_value"]
    (live_action,) = live["action_change_field_value"]["actions"]
    assert live_action["field_to_modify"]["id"] == drift_step_fields["text"]["id"]


def test_variable_references_are_names_on_write_and_definitions_on_read(
    drift_data_steps,
):
    """`update_variable.variable` is a bare name string on write (a definition
    dict is rejected) while `initialize_variable.variable` is the definition —
    an asymmetry the planner has to keep straight."""
    sent = {s["type"]: s for s in drift_data_steps["sent"]["steps"]}
    assert sent["update_variable"]["action_update_variable"]["variable"] == (
        "drift_count"
    )
    init_var = sent["initialize_variable"]["action_initialize_variable"]["variable"]
    assert init_var["name"] == "drift_count"
    assert "id" not in init_var, "a variable id inside initialize_variable is a 500"
    # math_operator takes the third spelling: a {"name": ...} object.
    assert sent["math_operator"]["action_math_operator"]["variable"] == {
        "name": "drift_count"
    }


@pytest.fixture(scope="session")
def drift_array_variable_steps(drift_client, scratch, drift_object) -> dict[str, Any]:
    """Its own automation because `value_resolution` 400s on a scalar
    variable, and the data-steps automation's variable is a number."""
    source = {
        "source_type": "static",
        "source_subtype": "string",
        "value": ["a"],
        "array_separator": "comma",
    }
    spec = _base_spec("arrayvar", drift_object)
    spec["steps"] = [
        {
            "key": "init_tags",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "variable": {
                    "name": "drift_tags",
                    "data_type": "string",
                    "is_array": True,
                },
                "sources": [source],
                "array_aggregation_mode": "add_all",
                "deduplicate_array": False,
            },
        },
        {
            "key": "append_tags",
            "step_type": "update_variable",
            "order": 1,
            "parent_key": "init_tags",
            "action_update_variable": {
                "variable": {"name": "drift_tags", "data_type": "string"},
                "sources": [{**source, "value": ["b"]}],
                "array_aggregation_mode": "add_all",
                "deduplicate_array": True,
                "value_resolution": "add_only",
            },
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_update_variable_value_resolution_reads_back(drift_array_variable_steps):
    (live,) = _live_steps_by_type(drift_array_variable_steps)["update_variable"]
    block = live["action_update_variable"]
    assert block["value_resolution"] == "add_only"
    assert block["array_aggregation_mode"] == "add_all"
    assert block["deduplicate_array"] is True


def test_archive_record_uses_the_id_suffixed_write_keys(drift_data_steps):
    """`relationship_fields` (read) is silently ignored on write; the planner
    always emits `relationship_field_ids`."""
    sent = next(
        s for s in drift_data_steps["sent"]["steps"] if s["type"] == "archive_record"
    )["action_archive_record"]
    assert sent == {"record_source": "this_record", "relationship_field_ids": []}


# ---------------------------------------------------------------------------
# Steps: related entities
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_related_steps(
    drift_client, scratch, drift_object, drift_related_object
) -> dict[str, Any]:
    hop_id = drift_related_object["hop_field"]["id"]
    spec = _base_spec("rel", drift_object)
    spec["steps"] = [
        {
            "key": "create_related",
            "step_type": "create_related_entity",
            "order": 0,
            "parent_key": None,
            "action_create_related_entity": {
                "target_custom_object": drift_related_object["api_name"],
                "new_entity_name": debris_name("related record"),
                "new_entity_name_html": f"<p>{debris_name('related record')}</p>",
                "new_entity_owner_type": "assign_from_context_record",
                "context_entity_field": hop_id,
            },
        },
        {
            "key": "modify_related",
            "step_type": "modify_related_entities",
            "order": 1,
            "parent_key": "create_related",
            "action_modify_related_entities": {
                "object_to_modify": drift_related_object["api_name"],
                "automation_target_relationship_fields": [hop_id],
                "fields_to_modify": [
                    {
                        "field_to_modify": drift_related_object["text_field"]["id"],
                        "value_type": "specific_value",
                        "specific_field_value": "set by the drift suite",
                    }
                ],
            },
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_related_entity_steps_are_accepted(drift_related_steps):
    assert _sent_step_types(drift_related_steps) == set(RELATED_STEPS)
    assert set(_live_steps_by_type(drift_related_steps)) == set(RELATED_STEPS)


def test_modify_related_entities_hop_and_value_type_dialect(
    drift_related_steps, drift_related_object
):
    """Two dialect traps in one step: the hop list is the real wire key (not
    the `relationship_field_ref` alias), and items use `value_type` where a
    standalone `change_field_value` uses `change_type` — the wrong one 400s
    with an unrelated-sounding "specific_field_value should be passed"."""
    sent = next(
        s
        for s in drift_related_steps["sent"]["steps"]
        if s["type"] == "modify_related_entities"
    )["action_modify_related_entities"]
    assert sent["object_to_modify"] == drift_related_object["uuid"]
    assert sent["automation_target_relationship_fields"] == [
        drift_related_object["hop_field"]["id"]
    ]
    (item,) = sent["fields_to_modify"]
    assert item["value_type"] == "specific_value"
    assert "change_type" not in item


def test_related_entity_steps_put_back_unchanged(drift_client, drift_related_steps):
    """The modify step's read carries `record_source: related_object_field`
    and `record_source_variables: []`, and the translator now sends both."""
    _put_back_unchanged(drift_client, drift_related_steps)


def test_create_related_entity_reduces_references_to_bare_ids(
    drift_related_steps, drift_related_object
):
    """`target_custom_object` resolves an api_name to a bare UUID, and the
    read expands it back into a full object."""
    sent = next(
        s
        for s in drift_related_steps["sent"]["steps"]
        if s["type"] == "create_related_entity"
    )["action_create_related_entity"]
    assert sent["target_custom_object"] == drift_related_object["uuid"]
    assert sent["context_entity_field"] == drift_related_object["hop_field"]["id"]

    (live,) = _live_steps_by_type(drift_related_steps)["create_related_entity"]
    block = live["action_create_related_entity"]
    assert block["target_custom_object"]["id"] == drift_related_object["uuid"]


# ---------------------------------------------------------------------------
# Steps: messaging & assignment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_messaging(
    drift_client, scratch, drift_object, drift_step_fields, drift_team_member_id
) -> dict[str, Any]:
    """Built in two passes, because the message resources cannot exist first.

    ``notify_member_via_email`` and both ``send_related_contact_*`` steps
    reference an ``AutomationMessage``, and those are scoped to *an*
    automation — there is nothing to scope them to until the automation
    exists. So: POST the two inline-content steps, create the messages against
    the new automation, then PUT the full step set. That is the same order a
    human building this in the UI is forced into, and it exercises the PUT
    path (with ``last_revision``) as a side effect.
    """
    from kizen_builder.api import messages as msg_api
    from kizen_builder.tools.messages import has_compiled_content

    inline_steps = [
        {
            "key": "assign",
            "step_type": "assign_team_member",
            "order": 0,
            "parent_key": None,
            "action_assign_team_member": {
                # This step's `type` enum is its own — `owner`/`employees`
                # are legal on other selectors and 400 here — and
                # `team_member` takes the *singular* employee_id.
                "type": "team_member",
                "employee_id": drift_team_member_id,
            },
        },
        {
            "key": "text_member",
            "step_type": "notify_member_via_text",
            "order": 1,
            "parent_key": "assign",
            "action_notify_member_via_text": {
                "team_member": {"type": "owner"},
                "content": "Drift check for {{ entity_record.name }}.",
            },
        },
    ]
    spec = _base_spec("msg", drift_object)
    spec["steps"] = list(inline_steps)
    record = _create_automation(drift_client, scratch, spec)

    templates = [
        t for t in msg_api.list_templates(drift_client) if t.get("type") == "email"
    ]
    if not templates:
        pytest.skip(
            "the drift environment has no email templates; a "
            "notify_member_via_email step's message resource must be seeded "
            "from one (see api/messages.py)"
        )
    details = (msg_api.get_template(drift_client, t["id"]) for t in templates)
    template = next((d for d in details if has_compiled_content(d)), None)
    if template is None:
        pytest.skip(
            f"the drift environment's {len(templates)} email template(s) all "
            "have blank `content`; a message seeded from one 400s on create"
        )
    notify_message = msg_api.create_automation_message_from_template(
        drift_client, record["uuid"], template
    )
    contact_message = msg_api.create_automation_message_from_template(
        drift_client, record["uuid"], template
    )
    # No text templates exist in a stock environment, so this one is authored
    # from raw content. There is no api/messages.py helper for that on purpose
    # — a raw message reads as "unselected" in the builder UI's picker — but
    # it is a perfectly valid target for the step's `text: {id}` association.
    text_message = drift_client.post(
        f"/api/messages/automations/automation/{record['uuid']}",
        json={
            "name": debris_name("text message"),
            "type": "text",
            "subject": "",
            "content": "Drift check.",
            "html_content": "<p>Drift check.</p>",
            "from_name_type": "default",
            "sender_type": "last_team_member",
        },
    )
    # The messages are scoped to the automation and go with it on delete; no
    # separate scratch entry (there is no message-delete endpoint wired).

    spec["steps"] = inline_steps + [
        {
            "key": "email_member",
            "step_type": "notify_member_via_email",
            "order": 2,
            "parent_key": "text_member",
            "action_notify_member_via_email": {
                "team_member": {"type": "owner"},
                "email_template_id": notify_message["id"],
            },
        },
        {
            "key": "email_contact",
            "step_type": "send_related_contact_email",
            "order": 3,
            "parent_key": "email_member",
            "action_send_related_contact_email": {
                "send_to_contact_field": drift_step_fields["contact"]["id"],
                "email": {"id": contact_message["id"]},
                "send_from_owner": False,
            },
        },
        {
            "key": "text_contact",
            "step_type": "send_related_contact_text",
            "order": 4,
            "parent_key": "email_contact",
            "action_send_related_contact_text": {
                "send_to_contact_field": drift_step_fields["contact"]["id"],
                "text": {"id": text_message["id"]},
            },
        },
    ]
    record = _replace_automation(drift_client, record, spec)
    return {
        **record,
        "notify_message_id": notify_message["id"],
        "contact_message_id": contact_message["id"],
        "text_message_id": text_message["id"],
    }


def test_messaging_steps_are_accepted(drift_messaging):
    assert _sent_step_types(drift_messaging) == set(MESSAGING_STEPS)
    assert set(_live_steps_by_type(drift_messaging)) == set(MESSAGING_STEPS)


def test_team_member_selectors_use_the_id_suffixed_dialect(
    drift_messaging, drift_team_member_id
):
    """Reads expand `employee`/`role`/`field`; writes take `employee_id` /
    `employee_ids` / `role_id` / `field_id`. A live 400 is what originally
    caught this.

    Also standing evidence for ``assign_team_member``'s own six-value ``type``
    enum: ``team_member`` is legal here and ``owner`` is not, which is the
    exact inverse of the ``team_member`` selector on the notify step below.
    """
    sent = {s["type"]: s for s in drift_messaging["sent"]["steps"]}
    assign = sent["assign_team_member"]["action_assign_team_member"]
    assert assign == {"type": "team_member", "employee_id": drift_team_member_id}
    assert sent["notify_member_via_text"]["action_notify_member_via_text"][
        "team_member"
    ] == {"type": "owner"}

    (live,) = _live_steps_by_type(drift_messaging)["assign_team_member"]
    assert live["action_assign_team_member"]["employee"]["id"] == drift_team_member_id


def test_notify_member_via_text_keeps_content_and_html_content_in_sync(
    drift_messaging,
):
    """Plain `content` only still runs, but the builder UI's rich-text editor
    renders blank without `html_content` — so the planner derives it, with
    merge-field tokens wrapped in the UI's own span markup."""
    sent = next(
        s
        for s in drift_messaging["sent"]["steps"]
        if s["type"] == "notify_member_via_text"
    )["action_notify_member_via_text"]
    assert sent["content"] == "Drift check for {{ entity_record.name }}."
    assert 'class="kzn-merge-field"' in sent["html_content"]
    assert 'data-merge-field-relationship="entity_record.name"' in sent["html_content"]

    (live,) = _live_steps_by_type(drift_messaging)["notify_member_via_text"]
    assert live["action_notify_member_via_text"]["content"] == sent["content"]


def test_message_backed_steps_reference_their_automation_scoped_resource(
    drift_messaging,
):
    """Three steps, three different spellings of "point at a message":
    `notify_member_via_email` takes a bare `id`, while the two
    `send_related_contact_*` steps take an `{"id": ...}` association."""
    sent = {s["type"]: s for s in drift_messaging["sent"]["steps"]}
    assert (
        sent["notify_member_via_email"]["action_notify_member_via_email"]["id"]
        == (drift_messaging["notify_message_id"])
    )
    assert sent["send_related_contact_email"]["action_send_related_contact_email"][
        "email"
    ] == {"id": drift_messaging["contact_message_id"]}
    assert sent["send_related_contact_text"]["action_send_related_contact_text"][
        "text"
    ] == {"id": drift_messaging["text_message_id"]}

    # Reads expand the association into the whole message resource, but the id
    # is preserved — the server does not clone the message onto the step.
    live = _live_steps_by_type(drift_messaging)
    (email_contact,) = live["send_related_contact_email"]
    assert (
        email_contact["action_send_related_contact_email"]["email"]["id"]
        == drift_messaging["contact_message_id"]
    )
    (text_contact,) = live["send_related_contact_text"]
    assert (
        text_contact["action_send_related_contact_text"]["text"]["id"]
        == drift_messaging["text_message_id"]
    )
    (notify,) = live["notify_member_via_email"]
    assert (
        notify["action_notify_member_via_email"]["id"]
        == drift_messaging["notify_message_id"]
    )


# ---------------------------------------------------------------------------
# Steps: AI & file
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_ai_steps(
    drift_client, scratch, drift_object, drift_step_fields, drift_llm_models
) -> dict[str, Any]:
    """`call_llm`, `file_content_extraction` and `audio_transcription`.

    Creating these only *stores* config — the automation stays inactive and is
    never started, so no model is invoked and nothing is billed.
    """
    summary_id = drift_step_fields["longtext"]["id"]
    attachment_id = drift_step_fields["files"]["id"]
    spec = _base_spec("ai", drift_object)
    spec["steps"] = [
        {
            "key": "llm",
            "step_type": "call_llm",
            "order": 0,
            "parent_key": None,
            "action_call_llm": _llm_block(
                drift_llm_models["call"],
                prompt="Summarize {{ custom_objects.name }} in one sentence.",
                destinations=[{"field": summary_id}],
            ),
        },
        {
            "key": "extract",
            "step_type": "file_content_extraction",
            "order": 1,
            "parent_key": "llm",
            "action_file_content_extraction": _llm_block(
                drift_llm_models["extraction"],
                prompt="Extract a one-line summary of the attached document.",
                input_field=attachment_id,
                data_type="image",
                destinations=[{"field": summary_id}],
            ),
        },
        {
            "key": "transcribe",
            "step_type": "audio_transcription",
            "order": 2,
            "parent_key": "extract",
            "action_audio_transcription": _llm_block(
                drift_llm_models["transcription"],
                prompt="Transcribe the attached recording.",
                input_field=attachment_id,
                data_type="audio",
                destinations=[{"field": summary_id}],
            ),
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_ai_steps_are_accepted(drift_ai_steps):
    assert _sent_step_types(drift_ai_steps) == set(AI_STEPS)
    assert set(_live_steps_by_type(drift_ai_steps)) == set(AI_STEPS)


def test_call_llm_block_is_action_call_llm(drift_ai_steps):
    """The API accepts, and the read returns, ``action_call_llm``. Until
    Kizen's 2026-10-13 release ``GET /api/docs/schema`` spelled the property
    ``action_llm_call``, so this pins the spelling the CLI sends."""
    sent = next(s for s in drift_ai_steps["sent"]["steps"] if s["type"] == "call_llm")
    assert "action_call_llm" in sent
    assert "action_llm_call" not in sent
    (live,) = _live_steps_by_type(drift_ai_steps)["call_llm"]
    assert "action_call_llm" in live, (
        "the read response no longer uses `action_call_llm`; the schema "
        "spelled it `action_llm_call` until the 2026-10-13 release, so check "
        "which spelling the write and read now use"
    )


def test_llm_prompt_and_html_prompt_are_kept_in_sync(drift_ai_steps):
    """Same quirk as `notify_member_via_text`: a plain prompt runs fine but
    the rich-text editor renders blank without `html_prompt`. `custom_objects`
    is call_llm's literal merge-field namespace token for target_object."""
    sent = next(s for s in drift_ai_steps["sent"]["steps"] if s["type"] == "call_llm")[
        "action_call_llm"
    ]
    assert 'data-merge-field-relationship="custom_objects.name"' in sent["html_prompt"]


def test_extraction_steps_take_a_bare_input_field_uuid(
    drift_ai_steps, drift_step_fields
):
    """`file_content_extraction`/`audio_transcription` share one builder; the
    input key is `input_field` (bare UUID), not the `field`/`field_id` spelling
    used almost everywhere else."""
    sent = {s["type"]: s for s in drift_ai_steps["sent"]["steps"]}
    for step_type in ("file_content_extraction", "audio_transcription"):
        block = sent[step_type][f"action_{step_type}"]
        assert block["input_field"] == drift_step_fields["files"]["id"], step_type
        (dest,) = block["destinations"]
        assert dest["field"] == drift_step_fields["longtext"]["id"]
        # Defaults the planner fills in rather than leaving to the server.
        assert dest["conflict_resolution"] == "overwrite_except_null"
        assert dest["confidence_threshold"] == 0.7


@pytest.fixture(scope="session")
def drift_extraction_sources(
    drift_client,
    scratch,
    drift_object,
    drift_related_object,
    drift_step_fields,
    drift_llm_models,
) -> dict[str, Any]:
    """The related-record hop must be single-valued, so this creates a
    `many_to_one` field on `drift_object`. `drift_related_object`'s
    `hop_field` is the to-many inverse, which Kizen rejects."""
    hop = create_field_on(
        drift_client,
        scratch,
        drift_object,
        {
            "name": "Drift Source Record",
            "api_name": debris_api_name("source_hop"),
            "field_type": "relationship",
            "relation": {
                "target_object": drift_related_object["api_name"],
                "relation_type": "many_to_one",
                "related_name": "Drift Source Children",
            },
        },
    )
    related_files = create_field_on(
        drift_client,
        scratch,
        drift_related_object,
        {
            "name": "Drift Related Attachment",
            "api_name": debris_api_name("rel_files"),
            "field_type": "files",
        },
    )
    related = {
        "type": "related_object_field",
        "relationship_field": hop["id"],
        "field": related_files["id"],
    }
    destinations = [{"field": drift_step_fields["longtext"]["id"]}]
    spec = _base_spec("extraction_sources", drift_object)
    spec["steps"] = [
        {
            "key": "init",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "variable": {"name": "drift_file", "data_type": "string"},
                "sources": [],
            },
        },
        {
            "key": "extract_related",
            "step_type": "file_content_extraction",
            "order": 1,
            "parent_key": "init",
            "action_file_content_extraction": _llm_block(
                drift_llm_models["extraction"],
                prompt="Summarize the related record's document.",
                input_value_source=related,
                data_type="image",
                destinations=destinations,
            ),
        },
        {
            "key": "transcribe_related",
            "step_type": "audio_transcription",
            "order": 2,
            "parent_key": "extract_related",
            "action_audio_transcription": _llm_block(
                drift_llm_models["transcription"],
                prompt="Transcribe the related record's recording.",
                input_value_source=related,
                data_type="audio",
                destinations=destinations,
            ),
        },
        {
            "key": "extract_variable",
            "step_type": "file_content_extraction",
            "order": 3,
            "parent_key": "transcribe_related",
            "action_file_content_extraction": _llm_block(
                drift_llm_models["extraction"],
                prompt="Summarize the file in the variable.",
                input_value_source={
                    "type": "automation_variable",
                    "variable": "drift_file",
                },
                data_type="image",
                destinations=destinations,
            ),
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_extraction_input_sources_are_accepted(drift_extraction_sources):
    sent = [
        s[f"action_{s['type']}"]
        for s in drift_extraction_sources["sent"]["steps"]
        if s["type"] != "initialize_variable"
    ]
    live = [
        s[f"action_{s['step_type']}"]
        for s in sorted(
            drift_extraction_sources["live"]["steps"], key=lambda s: s["order"]
        )
        if s["step_type"] != "initialize_variable"
    ]
    assert len(live) == len(sent) == 3
    for wrote, read in zip(sent, live, strict=True):
        assert "input_field" not in wrote
        assert read["input_value_source"]["type"] == wrote["input_value_source"]["type"]
        assert read["input_field"] is None


def test_extraction_input_sources_put_back_unchanged(
    drift_client, drift_extraction_sources
):
    """What every read-modify-write verb does: translate the read, PUT it,
    re-read. Before input_value_source was read, this PUT carried no input
    and Kizen 400'd it."""
    from kizen_builder.api import automations as auto_api
    from kizen_builder.translate import live_to_payload, semantic_diff, validate_payload

    before = drift_extraction_sources["live"]
    payload = live_to_payload(before)
    assert validate_payload(payload) == []
    auto_api.update_automation(
        drift_client,
        drift_extraction_sources["uuid"],
        payload,
        last_revision=payload.get("last_revision"),
    )
    after = auto_api.get_automation(drift_client, drift_extraction_sources["uuid"])
    assert list(semantic_diff(before, after)) == []


@pytest.fixture(scope="session")
def drift_ai_mcp_step(
    drift_client, scratch, drift_object, drift_step_fields, drift_llm_models
) -> dict[str, Any]:
    """A `call_llm` step with an MCP server and a run-as account, named the
    way a spec names them: the first row of each of the step's own pickers.
    Skips when either picker is empty. Stored config only, as above."""
    from kizen_builder.api import automations as auto_api

    servers = auto_api.list_mcp_step_options(drift_client)
    accounts = auto_api.list_run_tools_as_options(drift_client)
    if not servers or not accounts:
        pytest.skip(
            "the drift environment has no MCP server or no service account a "
            "Call LLM step can use"
        )
    spec = _base_spec("ai_mcp", drift_object)
    spec["steps"] = [
        {
            "key": "llm_tools",
            "step_type": "call_llm",
            "order": 0,
            "parent_key": None,
            "action_call_llm": _llm_block(
                drift_llm_models["call"],
                prompt="Summarize this record using the available tools.",
                destinations=[{"field": drift_step_fields["longtext"]["id"]}],
                mcp_server=servers[0]["api_name"],
                run_tools_as=accounts[0]["id"],
                max_sequential_turns=10,
            ),
        },
    ]
    record = _create_automation(drift_client, scratch, spec)
    return {**record, "server": servers[0], "account": accounts[0]}


def test_call_llm_mcp_server_and_account_read_back(drift_ai_mcp_step):
    """The wire takes `mcp_server_id`/`run_tools_as_id`; the read expands
    both to objects. A spec names the server by api_name."""
    (live,) = _live_steps_by_type(drift_ai_mcp_step)["call_llm"]
    block = live["action_call_llm"]
    assert block["mcp_server"]["id"] == drift_ai_mcp_step["server"]["id"]
    assert block["run_tools_as"]["id"] == drift_ai_mcp_step["account"]["id"]
    assert block["max_sequential_turns"] == 10
    assert block["tool_failure_policy"] == "never_fail"


def test_call_llm_mcp_step_puts_back_unchanged(drift_client, drift_ai_mcp_step):
    """What every read-modify-write verb does: translate, PUT, re-read."""
    from kizen_builder.api import automations as auto_api
    from kizen_builder.translate import live_to_payload, semantic_diff, validate_payload

    before = drift_ai_mcp_step["live"]
    payload = live_to_payload(before)
    assert validate_payload(payload) == []
    auto_api.update_automation(
        drift_client,
        drift_ai_mcp_step["uuid"],
        payload,
        last_revision=payload.get("last_revision"),
    )
    after = auto_api.get_automation(drift_client, drift_ai_mcp_step["uuid"])
    assert list(semantic_diff(before, after)) == []


def test_input_field_reads_back_mirrored_as_a_context_source(
    drift_ai_steps, drift_step_fields
):
    """A step written with only `input_field` reads back with a matching
    `context_entity_field` source as well. The extraction builder collapses
    that mirror back to `input_field`; if Kizen stops mirroring, the collapse
    rule needs revisiting."""
    attachment_id = drift_step_fields["files"]["id"]
    live = _live_steps_by_type(drift_ai_steps)
    for step_type in ("file_content_extraction", "audio_transcription"):
        (step,) = live[step_type]
        block = step[f"action_{step_type}"]
        source = block["input_value_source"]
        assert source["type"] == "context_entity_field", step_type
        assert source["field"]["id"] == attachment_id, step_type
        assert block["input_field"]["id"] == attachment_id, step_type


# ---------------------------------------------------------------------------
# Steps: plugin
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_plugin_steps(
    drift_client, scratch, drift_object, drift_step_fields
) -> dict[str, Any]:
    """Two `plugin_code_step`s running the first step of the first installed
    plugin that ships one, read from `GET /external-integrations/bootstrap`
    (each install's `automation_action_configs`). An environment with no
    such plugin skips the type rather than naming one here.

    The server checks inputs and outputs against the step's manifest, and a
    missing required one is a 400 that names it, so every required parameter
    is bound: in the first step the first input and output to a field and
    the rest statically or to a variable, in the second all to a variable.
    """
    bootstrap = drift_client.get("/api/external-integrations/bootstrap")
    found = next(
        (
            (api_name, app["automation_action_configs"][0])
            for api_name, app in sorted(bootstrap.items())
            if isinstance(app, dict) and app.get("automation_action_configs")
        ),
        None,
    )
    if found is None:
        pytest.skip("no installed plugin in the drift environment ships a step")
    plugin_app_api_name, manifest = found
    text_id = drift_step_fields["text"]["id"]
    var = "drift_plugin_out"

    def bind(param: dict[str, Any], kind: str, as_input: bool) -> dict[str, Any]:
        typed = {"input_type" if as_input else "output_type": kind}
        source = {
            "field": {"field_id": text_id},
            "variable": {"variable_name": var},
            "static_value": {"static_value": "x"},
        }[kind]
        return {
            "name": param["name"],
            **typed,
            **source,
            "data_type": param.get("data_type") or "string",
        }

    required_in = [p for p in manifest.get("inputs") or [] if p.get("required")]
    required_out = [p for p in manifest.get("outputs") or [] if p.get("required")]

    def block(first: str, rest_in: str) -> dict[str, Any]:
        return {
            "plugin_app_api_name": plugin_app_api_name,
            "action_step_api_name": manifest["action_step_api_name"],
            "inputs": [
                bind(p, first if i == 0 else rest_in, True)
                for i, p in enumerate(required_in)
            ],
            "outputs": [
                bind(p, first if i == 0 else "variable", False)
                for i, p in enumerate(required_out)
            ],
        }

    spec = _base_spec("plugin", drift_object)
    spec["steps"] = [
        {
            "key": "init",
            "step_type": "initialize_variable",
            "order": 0,
            "parent_key": None,
            "action_initialize_variable": {
                "variable": {"name": var, "data_type": "string"},
                "sources": [],
            },
        },
        {
            "key": "with_fields",
            "step_type": "plugin_code_step",
            "order": 1,
            "parent_key": "init",
            "action_plugin_code_step": block("field", "static_value"),
        },
        {
            "key": "with_variable",
            "step_type": "plugin_code_step",
            "order": 2,
            "parent_key": "with_fields",
            "action_plugin_code_step": block("variable", "variable"),
        },
    ]
    return _create_automation(drift_client, scratch, spec)


def test_plugin_steps_are_accepted(drift_plugin_steps):
    assert _sent_step_types(drift_plugin_steps) >= set(PLUGIN_STEPS)
    assert set(_live_steps_by_type(drift_plugin_steps)) >= set(PLUGIN_STEPS)


def test_plugin_steps_read_back_as_sent(drift_plugin_steps):
    """The read expands fields, variables and secrets; translated back, each
    plugin block is the one the planner sent."""
    from kizen_builder.translate import live_to_payload

    def blocks(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            s["action_plugin_code_step"]
            for s in sorted(steps, key=lambda s: s["order"])
            if s["type"] == "plugin_code_step"
        ]

    sent = blocks(drift_plugin_steps["sent"]["steps"])
    read = blocks(live_to_payload(drift_plugin_steps["live"])["steps"])
    assert len(sent) == 2
    for s_block, r_block in zip(sent, read, strict=True):
        for key in ("plugin_app_api_name", "action_step_api_name", "secrets"):
            assert r_block[key] == s_block[key], key
        for side in ("inputs", "outputs"):
            for s_item, r_item in zip(s_block[side], r_block[side], strict=True):
                assert {k: r_item.get(k) for k in s_item} == s_item


# ---------------------------------------------------------------------------
# Steps: activity scheduling
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_activity_steps(
    drift_client, scratch, drift_object, drift_activity_type
) -> dict[str, Any]:
    spec = _base_spec("act", drift_object)
    spec["steps"] = [
        {
            "key": "schedule",
            "step_type": "schedule_activity",
            "order": 0,
            "parent_key": None,
            "action_schedule_activity": {
                "activity_type_id": drift_activity_type["id"],
                "schedule": {"type": "immediately"},
                "assigned_to": {"assignment_type": "owner"},
                "note": "<p>Scheduled by the kizen-builder drift suite.</p>",
            },
        }
    ]
    return _create_automation(drift_client, scratch, spec)


def test_activity_steps_are_accepted(drift_activity_steps):
    assert _sent_step_types(drift_activity_steps) == set(ACTIVITY_STEPS)
    assert set(_live_steps_by_type(drift_activity_steps)) == set(ACTIVITY_STEPS)


def test_schedule_activity_wire_keys(drift_activity_steps, drift_activity_type):
    """`activity_type_id` (not `activity_type`), and `assigned_to` keyed by
    `assignment_type` (not the `type` the two other selectors use)."""
    sent = next(
        s
        for s in drift_activity_steps["sent"]["steps"]
        if s["type"] == "schedule_activity"
    )["action_schedule_activity"]
    assert sent["activity_type_id"] == drift_activity_type["id"]
    assert sent["assigned_to"] == {"assignment_type": "owner"}
    assert sent["schedule"] == {"type": "immediately"}

    (live,) = _live_steps_by_type(drift_activity_steps)["schedule_activity"]
    assert (
        live["action_schedule_activity"]["activity_type"]["id"]
        == drift_activity_type["id"]
    )


def test_schedule_activity_auto_adds_its_own_context_record_association(
    drift_activity_steps, drift_object
):
    """The server fills `association_configs` in itself, and the entry for the
    automation's own target_object comes back as `context_record` even though
    the spec sent an empty list. If this stops happening, automation.md's
    association_configs section is wrong. (The activity type here is `all_objects_associated`, so the server
    also emits a `none` entry per other object in the business — hence
    filtering rather than comparing the whole list.)"""
    sent = next(
        s
        for s in drift_activity_steps["sent"]["steps"]
        if s["type"] == "schedule_activity"
    )["action_schedule_activity"]
    assert sent["association_configs"] == []

    (live,) = _live_steps_by_type(drift_activity_steps)["schedule_activity"]
    configs = live["action_schedule_activity"].get("association_configs") or []
    context = [c for c in configs if c["association_source"] == "context_record"]
    assert len(context) == 1, [c["association_source"] for c in configs]
    assert context[0]["custom_object"]["id"] == drift_object["uuid"]


# ---------------------------------------------------------------------------
# Watching a run — wait_for_execution + detailed_log wire shapes (BCLI-012)
# ---------------------------------------------------------------------------


def test_wait_for_execution_and_history_row_shapes_against_a_live_run(
    drift_client, scratch, drift_object, drift_code_step_automation
):
    """Starts the `code_step`-first drift automation and waits on it with
    ``wait_for_execution``, then records the live wire shapes this item's
    design leans on: the status vocabulary actually returned, whether a
    history row (and its nested step/trigger) carries its own ``id``, and —
    the item's central open risk — a real `code_step`'s ``detailed_log`` key
    shape.

    The contract this test proves is the wire shape and the status
    vocabulary, not an SLA, so a run that hasn't reached a terminal status
    inside the budget below skips with an explicit message rather than
    failing.
    """
    from kizen_builder.api import automations as auto_api
    from kizen_builder.api import records as records_api
    from kizen_builder.tools.automations import (
        _TERMINAL_EXECUTION_STATUSES,
        wait_for_execution,
    )
    from kizen_builder.tools.planners.records import plan_create_records

    # Every other record-creation call site in this suite goes through
    # plan_create_records because the live object requires `name`; a bare
    # create_record(..., []) is rejected with "name: This field is required".
    plan = plan_create_records(
        drift_object["api_name"], [{"name": debris_name("wait-for-execution")}]
    )
    (op,) = plan.operations
    record = records_api.create_record(
        drift_client, drift_object["api_name"], op.payload["fields"]
    )
    scratch.track(
        "record",
        record["id"],
        lambda: records_api.delete_record(
            drift_client, drift_object["api_name"], record["id"]
        ),
    )

    resp = auto_api.start_automation(
        drift_client,
        drift_code_step_automation["api_name"],
        record_id=record["id"],
    )
    execution = resp.get("execution")
    execution_id = execution.get("id") if isinstance(execution, dict) else execution
    assert execution_id, f"start_automation returned no execution id: {resp!r}"

    result = wait_for_execution(execution_id, timeout=30.0, poll_interval=2.0)
    if result["timed_out"]:
        pytest.skip(
            f"execution {execution_id} had not reached a terminal status "
            f"inside this test's 30s budget (last observed status: "
            f"{result['status']!r}) — the wire shapes below are unverified "
            "this run, not broken. Re-run to try again."
        )
    assert result["status"] in _TERMINAL_EXECUTION_STATUSES

    history = auto_api.get_execution_history(drift_client, execution_id)
    assert history, "expected at least one history row (the manual trigger)"
    row = history[0]
    # The finding this test exists to pin: does a raw row carry `id` (needed
    # for `runs debug-step --history`)? Hard-asserted, not just type-checked,
    # so a false guess fails loudly instead of passing vacuously.
    assert "id" in row, f"history row has no top-level id: {sorted(row)}"
    # The nested step/trigger object does NOT carry its own `id` — confirmed
    # live 2026-08-13, exactly `{type, description, deleted}` on every row.
    # (A separate raw `step_id` field does exist on the row but is not mapped
    # through by get_execution_history(); out of scope here.) Asserting its
    # absence, not its presence, is what actually matches the wire.
    nested = row.get("step") or row.get("trigger") or {}
    assert isinstance(nested, dict), f"unexpected step/trigger shape: {nested!r}"
    assert "id" not in nested, (
        f"nested step/trigger now carries an id: {sorted(nested)}"
    )

    code_row = next(
        (r for r in history if (r.get("step") or {}).get("type") == "code_step"),
        None,
    )
    assert code_row is not None, (
        "no code_step history row found: "
        f"{[(r.get('step') or {}).get('type') for r in history]}"
    )
    detailed_log = code_row.get("detailed_log")
    # The item's central risk: a real code_step's detailed_log has at least
    # `logs` (the script called outputs.log(...) above) and, per the field
    # report, may also carry inputs/values/duration/http_requests — recorded
    # via the message rather than hardcoded, since the full key set was never
    # confirmed from this path.
    assert isinstance(detailed_log, dict) and "logs" in detailed_log, (
        f"unexpected code_step detailed_log shape: {detailed_log!r}"
    )
    assert {"status", "created", "updated"} <= set(row), sorted(row)


# ---------------------------------------------------------------------------
# Starting and following a run in one command (BCLI-021) — composes the
# wait_for_execution() proven above, plus start_automation() and the CLI's
# streaming callback. Reuses drift_code_step_automation (not a second live
# fixture) so this test — unlike an earlier version of it that pointed at
# drift_startable_automation's manual-trigger/stop_execution chain — actually
# streams a real code_step's detailed_log, not just an empty trigger row.
# ---------------------------------------------------------------------------


def test_start_and_wait_streams_a_live_run_without_crashing(
    drift_client, scratch, drift_object, drift_code_step_automation
):
    """Starts `drift_code_step_automation` via the new `start_and_wait()`, and
    drives the real `cli.automations._RunStream` callback (`show_logs=True`)
    against whatever history-row shape the live API actually returns —
    including the `code_step` row's real `detailed_log`, which
    `test_wait_for_execution_and_history_row_shapes_against_a_live_run` above
    already pins the shape of (`{"logs": ...}`).

    This is the composition's own live proof, not a second copy of that
    wire-shape assertion: it checks that BCLI-021's added code —
    `start_and_wait()`'s merge and `_RunStream`'s dedupe/render loop —
    doesn't raise when pointed at a real run, and that streaming reaches a
    real `code_step`'s log content, not just a trigger's empty one. The
    contract is the composition wiring, not an SLA, so a run still active at
    the end of this test's short budget skips rather than fails, matching
    BCLI-012's own skip rule.

    Owner-confirmed 2026-08-13: a `code_step`'s `detailed_log` is populated
    only once the step finishes (the underlying runner releases it on
    completion, not incrementally) — so "streaming" a code_step's log means
    printing it the moment its row is first seen already-complete, not
    watching it grow mid-run. This test's assertion matches that: it checks
    the log printed once the row appears, not partial output during
    execution.
    """
    from kizen_builder.api import records as records_api
    from kizen_builder.cli.automations import _RunStream
    from kizen_builder.tools.automations import (
        _TERMINAL_EXECUTION_STATUSES,
        start_and_wait,
    )
    from kizen_builder.tools.planners.records import plan_create_records

    plan = plan_create_records(
        drift_object["api_name"], [{"name": debris_name("start-and-wait")}]
    )
    (op,) = plan.operations
    record = records_api.create_record(
        drift_client, drift_object["api_name"], op.payload["fields"]
    )
    scratch.track(
        "record",
        record["id"],
        lambda: records_api.delete_record(
            drift_client, drift_object["api_name"], record["id"]
        ),
    )

    stream = _RunStream(show_logs=True)
    result = start_and_wait(
        drift_code_step_automation["api_name"],
        record["id"],
        timeout=30.0,
        poll_interval=2.0,
        on_poll=stream,
    )

    assert result.get("execution_id"), f"start_and_wait returned no id: {result!r}"
    if result.get("timed_out"):
        pytest.skip(
            f"execution {result['execution_id']} had not reached a terminal "
            f"status inside this test's 30s budget (last observed status: "
            f"{result.get('status')!r}) — the composition wiring is "
            "unverified this run, not broken. Re-run to try again."
        )
    assert result["status"] in _TERMINAL_EXECUTION_STATUSES


# ---------------------------------------------------------------------------
# Read keys: every one a live read carries is classified
# ---------------------------------------------------------------------------

#: Every themed automation above; together they carry every wired type.
THEMED_AUTOMATIONS = (
    "drift_automation",
    "drift_variable_condition",
    "drift_triggers",
    "drift_schedule_trigger",
    "drift_email_received_trigger",
    "drift_startable_automation",
    "drift_code_step_automation",
    "drift_control_flow",
    "drift_branch_groups",
    "drift_loops",
    "drift_data_steps",
    "drift_array_variable_steps",
    "drift_related_steps",
    "drift_messaging",
    "drift_ai_steps",
    "drift_ai_mcp_step",
    "drift_extraction_sources",
    "drift_plugin_steps",
    "drift_activity_steps",
)


@pytest.mark.parametrize("fixture", THEMED_AUTOMATIONS)
def test_live_read_keys_are_all_classified(request, fixture):
    """`translate` refuses a read carrying a set key its builder doesn't
    model, so an unclassified key here — even a null one — is an automation
    the CLI will one day refuse to show or re-save."""
    from tests import builder_keys

    record = request.getfixturevalue(fixture)
    assert builder_keys.unclassified_read_keys(record["live"]) == set()


# ---------------------------------------------------------------------------
# Coverage completeness — the invariant that keeps this file honest
# ---------------------------------------------------------------------------


def test_every_wired_step_type_has_roundtrip_coverage():
    """``_STEP_BUILDERS`` plus the configless types is the authoritative gate
    for what a spec may use. Wiring a new step type without adding it to a
    themed automation above fails here rather than shipping untested."""
    wired = set(_STEP_BUILDERS) | _CONFIGLESS_STEP_TYPES
    assert wired == set(COVERED_STEP_TYPES), (
        "uncovered wired step types: "
        f"{sorted(wired - COVERED_STEP_TYPES)}; "
        "covered types that are no longer wired: "
        f"{sorted(COVERED_STEP_TYPES - wired)}"
    )


def test_every_wired_trigger_type_has_roundtrip_coverage():
    wired = set(_TRIGGER_BUILDERS)
    assert wired == set(COVERED_TRIGGER_TYPES), (
        "uncovered wired trigger types: "
        f"{sorted(wired - COVERED_TRIGGER_TYPES)}; "
        "covered types that are no longer wired: "
        f"{sorted(COVERED_TRIGGER_TYPES - wired)}"
    )

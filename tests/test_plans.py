"""Plan serialization round-trip and apply_plan execution semantics."""

from __future__ import annotations

import httpx
import respx

from kizen_builder.cli._mutations import _enrich_known_choice_failures
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.plans import Plan, PlanOperation
from tests.conftest import FAKE_BASE_URL


def _field_op(**overrides) -> PlanOperation:
    base = {
        "action": "create",
        "kind": "field",
        "key": "invoice.total",
        "preview": {"env": "testenv", "api_name": "total"},
        "payload": {"name": "total", "display_name": "Total", "field_type": "money"},
        "parent_object_uuid": "11111111-1111-4111-8111-111111111111",
    }
    base.update(overrides)
    return PlanOperation(**base)


def _automation_op(**overrides) -> PlanOperation:
    base = {
        "action": "create",
        "kind": "automation",
        "key": "onboarding_flow",
        "preview": {"env": "testenv"},
        "payload": {"name": "onboarding flow"},
    }
    base.update(overrides)
    return PlanOperation(**base)


def test_plan_json_round_trip():
    plan = Plan.build(env="testenv", summary="test", operations=[_field_op()])
    text = plan_tools.plan_to_json(plan)
    restored = plan_tools.plan_from_json(text)
    assert restored.id == plan.id
    assert restored.env == plan.env
    assert restored.operations[0].payload == plan.operations[0].payload
    assert (
        restored.operations[0].parent_object_uuid
        == plan.operations[0].parent_object_uuid
    )


@respx.mock
def test_apply_create_field_posts_and_records_uuid():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/11111111-1111-4111-8111-111111111111/fields"
    ).mock(return_value=httpx.Response(200, json={"id": "new-field-uuid"}))
    plan = Plan.build(env="testenv", summary="test", operations=[_field_op()])

    result = plan_tools.apply_plan(plan)

    assert route.call_count == 1
    (r,) = result.results
    assert r.status == "ok"
    assert r.server_uuid == "new-field-uuid"
    assert result.all_ok


@respx.mock
def test_apply_failure_is_recorded_not_raised():
    respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/11111111-1111-4111-8111-111111111111/fields"
    ).mock(return_value=httpx.Response(400, json={"detail": "nope"}))
    plan = Plan.build(env="testenv", summary="test", operations=[_field_op()])

    result = plan_tools.apply_plan(plan)

    (r,) = result.results
    assert r.status == "failed"
    assert "nope" in (r.message or "")
    assert not result.all_ok


@respx.mock
def test_known_enum_choices_enrich_a_matching_failure():
    """BCLI-015: a real 400 whose message matches DRF's "not a valid choice"
    shape, for a field this repo has recorded knowledge of, gets that
    knowledge appended once `_enrich_known_choice_failures` (the exact call
    `cli/_mutations.py::_run_mutation` makes on every failed automation op)
    runs over the result."""
    body = {
        "step_assign_owner": {
            "action_create_related_entity": {
                "new_entity_owner_type": ['"bogus" is not a valid choice.'],
            },
        },
    }
    respx.post(f"{FAKE_BASE_URL}/api/automation2/automations").mock(
        return_value=httpx.Response(400, json=body)
    )
    plan = Plan.build(env="testenv", summary="test", operations=[_automation_op()])

    result = plan_tools.apply_plan(plan)
    _enrich_known_choice_failures(result)

    (r,) = result.results
    assert r.status == "failed"
    assert "assign_from_context_record" in (r.message or "")
    assert "newly_assigned_owner" in (r.message or "")


@respx.mock
def test_known_enum_choices_enrich_every_match_in_a_multi_field_failure():
    """A single 400 can reject several fields at once (seen live: a create's
    body failed on `new_entity_name`, `new_entity_name_html`, and
    `new_entity_owner_type` together). If more than one rejected field has a
    registry entry, every match must be appended, not just the first found."""
    body = {
        "step_assign_owner": {
            "action_create_related_entity": {
                "new_entity_owner_type": ['"bogus" is not a valid choice.'],
            },
        },
        "step_notify": {
            "action_notify_member_via_text": {
                "team_member": {"type": ['"manager" is not a valid choice.']},
            },
        },
    }
    respx.post(f"{FAKE_BASE_URL}/api/automation2/automations").mock(
        return_value=httpx.Response(400, json=body)
    )
    plan = Plan.build(env="testenv", summary="test", operations=[_automation_op()])

    result = plan_tools.apply_plan(plan)
    _enrich_known_choice_failures(result)

    (r,) = result.results
    assert "assign_from_context_record" in (r.message or "")
    assert "newly_assigned_owner" in (r.message or "")
    assert "employee" in (r.message or "")


@respx.mock
def test_known_enum_choices_leaves_an_unregistered_field_unchanged():
    """A 400 for a field with no registry entry passes through completely
    unchanged — the failure mode for "we don't know this one" is silence,
    not a crash or a misleading guess."""
    body = {
        "step_send_message": {
            "action_send_email": {
                "subject": ["This field is required."],
            },
        },
    }
    respx.post(f"{FAKE_BASE_URL}/api/automation2/automations").mock(
        return_value=httpx.Response(400, json=body)
    )
    plan = Plan.build(env="testenv", summary="test", operations=[_automation_op()])

    result = plan_tools.apply_plan(plan)
    original_message = result.results[0].message

    _enrich_known_choice_failures(result)  # must not raise

    (r,) = result.results
    assert r.status == "failed"
    assert r.message == original_message


@respx.mock
def test_apply_internal_error_is_recorded_not_raised():
    """A non-API failure mid-op (here: a field op with no parent uuid, which
    _execute rejects as a planning bug) is recorded as failed, not raised."""
    plan = Plan.build(
        env="testenv",
        summary="t",
        operations=[_field_op(key="invoice.total", parent_object_uuid=None)],
    )

    result = plan_tools.apply_plan(plan)  # must not raise

    (r,) = result.results
    assert r.status == "failed"
    assert "PlanError" in (r.message or "")
    assert not result.all_ok


@respx.mock
def test_apply_continues_after_mid_batch_internal_failure():
    """An internal failure on one op doesn't abort the batch: independent ops
    before and after it still run, and the report is complete."""
    p1 = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    p3 = "cccccccc-3333-4333-8333-cccccccccccc"
    route1 = respx.post(f"{FAKE_BASE_URL}/api/custom-objects/{p1}/fields").mock(
        return_value=httpx.Response(200, json={"id": "field-1"})
    )
    route3 = respx.post(f"{FAKE_BASE_URL}/api/custom-objects/{p3}/fields").mock(
        return_value=httpx.Response(200, json={"id": "field-3"})
    )
    ops = [
        _field_op(key="obj_a.f1", parent_object_uuid=p1),
        _field_op(key="obj_b.f2", parent_object_uuid=None),  # internal failure
        _field_op(key="obj_c.f3", parent_object_uuid=p3),
    ]

    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=ops)
    )

    assert route1.call_count == 1
    assert route3.call_count == 1
    assert [r.status for r in result.results] == ["ok", "failed", "ok"]
    assert not result.all_ok


@respx.mock
def test_apply_deferred_parent_resolved_from_earlier_op():
    obj_route = respx.post(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json={"id": "created-obj-uuid"})
    )
    field_route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/created-obj-uuid/fields"
    ).mock(return_value=httpx.Response(200, json={"id": "created-field-uuid"}))

    ops = [
        PlanOperation(
            action="create",
            kind="object",
            key="invoice",
            preview={},
            payload={"name": "invoice", "object_name": "Invoices"},
        ),
        _field_op(parent_object_uuid=None, deferred_parent_object_key="invoice"),
    ]
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=ops)
    )

    assert obj_route.call_count == 1
    assert field_route.call_count == 1
    assert [r.status for r in result.results] == ["ok", "ok"]


@respx.mock
def test_apply_child_ops_skipped_when_parent_fails():
    respx.post(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(400, json={"detail": "invalid object"})
    )
    ops = [
        PlanOperation(
            action="create",
            kind="object",
            key="invoice",
            preview={},
            payload={"name": "invoice"},
        ),
        _field_op(parent_object_uuid=None, deferred_parent_object_key="invoice"),
        # prefix-cascade path: no deferred key, but key is namespaced under the object
        _field_op(
            key="invoice.subtotal",
            parent_object_uuid=None,
            payload={"name": "subtotal", "field_type": "money"},
        ),
    ]
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=ops)
    )

    statuses = {r.key: r.status for r in result.results}
    assert statuses["invoice"] == "failed"
    assert statuses["invoice.total"] == "skipped"
    assert statuses["invoice.subtotal"] == "skipped"


@respx.mock
def test_apply_automation_update_puts_previewed_payload_verbatim():
    """The plan builder assembles the full PUT body (incl. last_revision);
    apply must send it as-is — no refetch, no server-side reassembly."""
    import json

    payload = {"name": "X", "api_name": "x", "steps": [], "last_revision": 7}
    route = respx.put(f"{FAKE_BASE_URL}/api/automation2/automations/auto-uuid").mock(
        return_value=httpx.Response(200, json={"id": "auto-uuid"})
    )
    # No GET route registered: a revision refetch would blow up the test.
    op = PlanOperation(
        action="update",
        kind="automation",
        key="x",
        preview={},
        payload=payload,
        existing_uuid="auto-uuid",
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )

    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == payload
    assert result.all_ok


@respx.mock
def test_apply_skip_ops_never_hit_the_api():
    plan = Plan.build(
        env="testenv",
        summary="t",
        operations=[
            _field_op(
                action="skip",
                payload={},
                existing_uuid="22222222-2222-4222-8222-222222222222",
            )
        ],
    )
    result = plan_tools.apply_plan(plan)  # no respx routes: any call would error
    (r,) = result.results
    assert r.status == "skipped"
    assert result.all_ok


def _permission_setting_op(**overrides) -> PlanOperation:
    base = {
        "action": "update",
        "kind": "permission_setting",
        "key": "Group.setting[0]",
        "preview": {},
        "payload": {
            "mode": "object_update",
            "body": {"custom_object": {"id": "obj-uuid"}, "permission_level": 2},
            # False: no live entry at plan time — the genuine-defect case.
            # The "present but normalized" test below overrides this.
            "control_present": False,
        },
        "parent_object_uuid": "group-uuid",
    }
    base.update(overrides)
    return PlanOperation(**base)


@respx.mock
def test_apply_permission_setting_fails_when_control_was_absent():
    """object-update silently corrects the level it's given (e.g. a
    freshly-inserted object always lands at "none") and reports it in the
    response body instead of a 4xx — apply_plan must not report `ok` for a
    write that didn't do what was asked. `control_present=False` (no live
    entry at plan time) is what makes this the genuine-defect case, not a
    legal server normalization."""
    respx.patch(f"{FAKE_BASE_URL}/api/permission-group/group-uuid/object-update").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "all_records",
                "permission_level": 0,
                "details": {
                    "message": "Permission level was automatically corrected by rule."
                },
            },
        )
    )
    plan = Plan.build(env="testenv", summary="t", operations=[_permission_setting_op()])

    result = plan_tools.apply_plan(plan)

    (r,) = result.results
    assert r.status == "failed"
    assert "permission_level=0, requested 2" in r.message
    assert "automatically corrected by rule" in r.message
    assert not result.all_ok


@respx.mock
def test_apply_permission_setting_adjusted_when_control_was_present():
    """A live probe found the server clamping a legal,
    in-range write on a control the group already carried (Companies'
    `associated_records`, normalized up to satisfy `associated_records >=
    all_records` after an earlier op in the same plan raised `all_records`).
    That's the server doing exactly what this item's design delegates to it
    — reported as "adjusted", not "failed", and must not flip the exit code."""
    respx.patch(f"{FAKE_BASE_URL}/api/permission-group/group-uuid/object-update").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "associated_records",
                "permission_level": 3,
                "details": {
                    "message": "Permission level was automatically corrected by rule."
                },
            },
        )
    )
    op = _permission_setting_op(
        payload={
            "mode": "object_update",
            "body": {"custom_object": {"id": "obj-uuid"}, "permission_level": 1},
            "control_present": True,
        }
    )
    plan = Plan.build(env="testenv", summary="t", operations=[op])

    result = plan_tools.apply_plan(plan)

    (r,) = result.results
    assert r.status == "adjusted"
    assert r.message == (
        "requested view, server normalized to remove "
        "(Permission level was automatically corrected by rule.)"
    )
    assert result.all_ok


@respx.mock
def test_apply_permission_setting_ok_when_level_matches():
    respx.patch(f"{FAKE_BASE_URL}/api/permission-group/group-uuid/object-update").mock(
        return_value=httpx.Response(
            200, json={"key": "all_records", "permission_level": 2}
        )
    )
    plan = Plan.build(env="testenv", summary="t", operations=[_permission_setting_op()])

    result = plan_tools.apply_plan(plan)

    (r,) = result.results
    assert r.status == "ok"
    assert r.message is None
    assert result.all_ok

"""Live round-trip coverage for ``tools/planners/records.py``.

Records are data, not schema, but the same "does the payload still work"
concern applies: ``_resolve_fields``/``_resolve_option`` translate authored
values (option labels, bare ids) into wire shapes against a live field
snapshot, and ``_unwrap_bulk_field_value`` carries a documented live quirk
(``bulk-change-field-value`` wants a bare scalar, not ``{"id": ...}``) that a
schema diff alone cannot catch — the OpenAPI spec types ``field_value`` as
``object`` and would happily validate the wrapped form that live 400s on.

Every planner here (`plan_create_records`, `plan_update_records`,
`plan_upsert_records`, `plan_set_field`, `plan_archive_records`) is exercised
end to end: the plan's payload is applied via the real ``api/records.py``
functions (the same ones ``apply_plan`` calls), then read back with
``get_record`` to confirm the shape the planner assumes still holds.

This file builds its own throwaway custom object and dropdown field rather
than depending on the shared ``drift_object`` fixture other files in this
directory use — deliberately, so it has no assumption about fixture-sharing
state across files and runs correctly whether invoked with the rest of the
suite or in isolation (e.g. ``pytest -m drift -k records``). It only depends
on ``drift_client``/``scratch``, which every file here shares via
``conftest.py`` regardless.

``plan_upsert_records`` branches server-side on whether ``lookup_value``
matches an existing record, so both the create-branch and the update-branch
get their own test — one passing path would leave the other entirely
unverified.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.drift.conftest import debris_api_name, debris_name

pytestmark = pytest.mark.drift


# ---------------------------------------------------------------------------
# This file's own throwaway object + field
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def records_object(drift_client, scratch) -> dict[str, Any]:
    """A throwaway standard custom object dedicated to this file's tests.

    Built by the same real planner payload as the shared ``drift_object`` in
    ``test_roundtrip_drift.py``, but not shared with it — see module
    docstring for why.
    """
    from kizen_builder.api import custom_objects as co_api
    from kizen_builder.models.spec import ObjectDef
    from kizen_builder.tools.planners.objects import _build_object_payload

    spec = ObjectDef(
        name=debris_name("records-object"),
        api_name=debris_api_name("records_object"),
        object_type="standard",
        description="Created by the kizen-builder drift suite. Safe to delete.",
    )
    payload = _build_object_payload(spec)
    created = co_api.create_object(drift_client, payload)
    scratch.track(
        "custom object",
        created["id"],
        lambda: co_api.delete_object(drift_client, created["id"]),
    )
    live = co_api.get_object(drift_client, created["id"])
    return {
        "uuid": created["id"],
        # Kizen derives the api_name server-side and returns it as `name`.
        "api_name": live["name"],
        "categories": co_api.list_categories(drift_client, created["id"]),
    }


@pytest.fixture(scope="session")
def records_dropdown_field(drift_client, scratch, records_object) -> dict[str, Any]:
    """A dropdown field (options Low/High) on ``records_object``, for
    exercising option-label resolution in the records planners."""
    from kizen_builder.api import custom_objects as co_api
    from kizen_builder.tools.planners.fields import plan_create_field

    category = records_object["categories"][0]["name"]
    plan = plan_create_field(
        records_object["api_name"],
        {
            "name": "Drift Risk Level",
            "api_name": debris_api_name("dropdown"),
            "field_type": "dropdown",
            "options": ["Low", "High"],
        },
        category=category,
    )
    payload = plan.operations[0].payload
    created = co_api.create_field(drift_client, records_object["uuid"], payload)
    scratch.track(
        "field",
        created["id"],
        lambda: co_api.delete_field(
            drift_client, records_object["uuid"], created["id"]
        ),
    )
    return {
        "api_name": created["name"],
        "id": created["id"],
        "options": created["options"],
    }


def _option_id(dropdown_field: dict[str, Any], label: str) -> str:
    return next(o["id"] for o in dropdown_field["options"] if o["name"] == label)


def _create_record(
    drift_client, scratch, object_api_name: str, fields_mapping: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan + apply a single-record create, tracking cleanup immediately.

    Returns ``(op.payload, created)`` so callers can assert the wire shape
    the planner built as well as the live response.
    """
    from kizen_builder.api import records as records_api
    from kizen_builder.tools.planners.records import plan_create_records

    plan = plan_create_records(object_api_name, [fields_mapping])
    (op,) = plan.operations
    created = records_api.create_record(
        drift_client, object_api_name, op.payload["fields"]
    )
    scratch.track(
        "record",
        created["id"],
        lambda: records_api.delete_record(drift_client, object_api_name, created["id"]),
    )
    return op.payload, created


def _poll_field_value(
    drift_client,
    object_api_name: str,
    record_id: str,
    field_api_name: str,
    predicate,
    *,
    attempts: int = 5,
    delay: float = 1.0,
):
    """Re-fetch a record's field value until ``predicate`` holds.

    A ``bulk-change-field-value`` write that 200s is not always visible on the
    very next ``GET`` — observed locally with up to ~1.5s of lag. Retries
    rather than sleeping a fixed amount so the common case (already visible)
    doesn't pay a tax, and fails with the last-seen value if it never shows up.
    """
    import time

    from kizen_builder.api import records as records_api

    value = None
    for _ in range(attempts):
        live = records_api.get_record(drift_client, object_api_name, record_id)
        value = records_api.field_value(live, field_api_name)
        if predicate(value):
            return value
        time.sleep(delay)
    raise AssertionError(f"field never reached expected value; last seen: {value!r}")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_record_resolves_dropdown_option_and_roundtrips(
    drift_client, scratch, records_object, records_dropdown_field
):
    """``plan_create_records`` resolves an option label to ``{"id": <uuid>}``
    on the way out; the live read-back carries the same option id."""
    from kizen_builder.api import records as records_api

    object_api_name = records_object["api_name"]
    name_value = debris_name("record create")
    low_id = _option_id(records_dropdown_field, "Low")

    payload, created = _create_record(
        drift_client,
        scratch,
        object_api_name,
        {"name": name_value, records_dropdown_field["api_name"]: "Low"},
    )

    sent = {f["name"]: f["value"] for f in payload["fields"]}
    assert sent["name"] == name_value
    assert sent[records_dropdown_field["api_name"]] == {"id": low_id}
    assert created["id"]

    live = records_api.get_record(drift_client, object_api_name, created["id"])
    assert records_api.field_value(live, "name") == name_value
    live_dropdown = records_api.field_value(live, records_dropdown_field["api_name"])
    assert isinstance(live_dropdown, dict), live_dropdown
    assert live_dropdown["id"] == low_id


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_record_changes_field_and_roundtrips(
    drift_client, scratch, records_object, records_dropdown_field
):
    """``plan_update_records`` targets an existing record by id; the new
    value sticks on read-back."""
    from kizen_builder.api import records as records_api
    from kizen_builder.tools.planners.records import plan_update_records

    object_api_name = records_object["api_name"]
    _, created = _create_record(
        drift_client,
        scratch,
        object_api_name,
        {
            "name": debris_name("record update"),
            records_dropdown_field["api_name"]: "Low",
        },
    )
    record_id = created["id"]
    high_id = _option_id(records_dropdown_field, "High")

    plan = plan_update_records(
        object_api_name, [{"id": record_id, records_dropdown_field["api_name"]: "High"}]
    )
    (op,) = plan.operations
    assert op.action == "update" and op.existing_uuid == record_id
    sent_value = next(
        f["value"]
        for f in op.payload["fields"]
        if f["name"] == records_dropdown_field["api_name"]
    )
    assert sent_value == {"id": high_id}

    records_api.update_record(
        drift_client, object_api_name, record_id, op.payload["fields"]
    )

    live = records_api.get_record(drift_client, object_api_name, record_id)
    live_dropdown = records_api.field_value(live, records_dropdown_field["api_name"])
    assert live_dropdown["id"] == high_id


# ---------------------------------------------------------------------------
# Upsert — both branches: no match (creates), match (updates)
# ---------------------------------------------------------------------------


def test_upsert_creates_when_lookup_value_has_no_match(
    drift_client, scratch, records_object, records_dropdown_field
):
    """``lookup_value`` naming nothing live yet: upsert creates a new record."""
    from kizen_builder.api import records as records_api
    from kizen_builder.tools.planners.records import plan_upsert_records

    object_api_name = records_object["api_name"]
    lookup_value = debris_name("record upsert create")
    low_id = _option_id(records_dropdown_field, "Low")

    plan = plan_upsert_records(
        object_api_name,
        [{"lookup_value": lookup_value, records_dropdown_field["api_name"]: "Low"}],
    )
    (op,) = plan.operations
    assert op.action == "upsert"
    assert op.payload["lookup_value"] == lookup_value

    result = records_api.upsert_record(
        drift_client,
        object_api_name,
        op.payload["lookup_value"],
        op.payload["fields"],
    )
    scratch.track(
        "record",
        result["id"],
        lambda: records_api.delete_record(drift_client, object_api_name, result["id"]),
    )

    live = records_api.get_record(drift_client, object_api_name, result["id"])
    assert records_api.field_value(live, "name") == lookup_value
    live_dropdown = records_api.field_value(live, records_dropdown_field["api_name"])
    assert live_dropdown["id"] == low_id


def test_upsert_updates_when_lookup_value_matches_existing(
    drift_client, scratch, records_object, records_dropdown_field
):
    """The same ``lookup_value`` on a second upsert updates the existing
    record in place rather than creating a duplicate."""
    from kizen_builder.api import records as records_api
    from kizen_builder.tools.planners.records import plan_upsert_records

    object_api_name = records_object["api_name"]
    lookup_value = debris_name("record upsert update")
    high_id = _option_id(records_dropdown_field, "High")

    _, created = _create_record(
        drift_client,
        scratch,
        object_api_name,
        {"name": lookup_value, records_dropdown_field["api_name"]: "Low"},
    )

    plan = plan_upsert_records(
        object_api_name,
        [{"lookup_value": lookup_value, records_dropdown_field["api_name"]: "High"}],
    )
    (op,) = plan.operations

    result = records_api.upsert_record(
        drift_client,
        object_api_name,
        op.payload["lookup_value"],
        op.payload["fields"],
    )
    # Must be the *same* record — this is the branch a schema diff can't see:
    # the endpoint matched on lookup_value and updated rather than creating.
    assert result["id"] == created["id"]

    live = records_api.get_record(drift_client, object_api_name, created["id"])
    live_dropdown = records_api.field_value(live, records_dropdown_field["api_name"])
    assert live_dropdown["id"] == high_id


# ---------------------------------------------------------------------------
# Bulk field set — the bare-scalar quirk
# ---------------------------------------------------------------------------


def test_set_field_sends_bare_option_id_not_wrapped(
    drift_client, scratch, records_object, records_dropdown_field
):
    """``bulk-change-field-value``'s ``field_value`` wants the bare option
    UUID string for a select field, not ``{"id": ...}`` — the opposite of what
    a record's own ``fields`` list takes. ``_unwrap_bulk_field_value`` exists
    for exactly this; assert both the outgoing wire shape and the live effect.

    The live effect isn't necessarily visible on the very next read: a POST
    that 200s can still be followed by a ``GET`` reflecting the pre-change
    value for a brief window (observed locally up to ~1.5s) before the write
    propagates. ``_poll_field_value`` below retries rather than asserting on
    the first read, so this test pins the *value* the field settles on
    without being flaky about *when* that happens. Undocumented in
    ``docs/specs/records.md`` today — worth a line if this keeps reproducing.
    """
    from kizen_builder.api import custom_objects as co_api
    from kizen_builder.tools.planners.records import plan_set_field

    object_api_name = records_object["api_name"]
    high_id = _option_id(records_dropdown_field, "High")

    _, rec_a = _create_record(
        drift_client,
        scratch,
        object_api_name,
        {
            "name": debris_name("record bulk a"),
            records_dropdown_field["api_name"]: "Low",
        },
    )
    _, rec_b = _create_record(
        drift_client,
        scratch,
        object_api_name,
        {
            "name": debris_name("record bulk b"),
            records_dropdown_field["api_name"]: "Low",
        },
    )
    record_ids = [rec_a["id"], rec_b["id"]]

    plan = plan_set_field(
        object_api_name, record_ids, records_dropdown_field["api_name"], "High"
    )
    (op,) = plan.operations
    assert op.kind == "record_bulk_field_value"
    # The quirk, asserted explicitly: a bare id string, never a dict.
    assert op.payload["field_value"] == high_id
    assert not isinstance(op.payload["field_value"], dict)
    assert op.payload["field_id"] == records_dropdown_field["id"]
    assert op.parent_object_uuid == records_object["uuid"]

    co_api.bulk_change_field_value(drift_client, op.parent_object_uuid, op.payload)

    for rid in record_ids:
        live_dropdown = _poll_field_value(
            drift_client,
            object_api_name,
            rid,
            records_dropdown_field["api_name"],
            lambda v: isinstance(v, dict) and v.get("id") == high_id,
        )
        assert live_dropdown["id"] == high_id


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------


def _poll_search_membership(
    drift_client,
    object_api_name: str,
    record_id: str,
    search: str,
    *,
    present: bool,
    attempts: int = 6,
    delay: float = 1.5,
):
    """Poll ``search_records`` until ``record_id``'s membership matches
    ``present``.

    ``bulk-archive-entity-record`` answers ``{"async": true}`` — a 200 does
    not mean the record is out of search yet. Retries the same way
    ``_poll_field_value`` does rather than asserting on the first read.
    """
    import time

    from kizen_builder.api import records as records_api

    ids: set[str] = set()
    for _ in range(attempts):
        ids = {
            r["id"]
            for r in records_api.search_records(
                drift_client, object_api_name, search=search
            )
        }
        if (record_id in ids) == present:
            return ids
        time.sleep(delay)
    raise AssertionError(
        f"record {record_id} membership in search never reached present={present}; "
        f"last seen ids={ids}"
    )


def _archive_progress_rows(drift_client, object_uuid: str) -> list[dict[str, Any]]:
    resp = drift_client.get(
        "/api/bulk-action-progress",
        params={
            "custom_object_id": object_uuid,
            "action": "custom_object_archive",
            "page_size": 100,
        },
    )
    return resp.get("results", []) if isinstance(resp, dict) else resp


def test_archive_records_one_request_then_unarchive_restores_them(
    drift_client, scratch, records_object
):
    """``plan_archive_records`` archives two records in one
    ``bulk-archive-entity-record`` call with email off. A 200 proves nothing
    by itself (the response is ``{"async": true}``), so this checks both
    records leave search, the server wrote exactly one progress row flagged
    ``send_email_notification: false``, and both come back via
    ``plan_unarchive_records``.
    """
    from kizen_builder.api import records as records_api
    from kizen_builder.api.client import KizenAPIError
    from kizen_builder.tools.planners.records import (
        plan_archive_records,
        plan_create_records,
        plan_unarchive_records,
    )
    from kizen_builder.tools.plans import apply_plan

    object_api_name = records_object["api_name"]
    object_uuid = records_object["uuid"]
    names = [debris_name("record archive a"), debris_name("record archive b")]
    create_plan = plan_create_records(object_api_name, [{"name": n} for n in names])
    record_ids = [
        records_api.create_record(drift_client, object_api_name, op.payload["fields"])[
            "id"
        ]
        for op in create_plan.operations
    ]

    for rid in record_ids:

        def _delete_if_still_present(rid: str = rid) -> None:
            try:
                records_api.delete_record(drift_client, object_api_name, rid)
            except KizenAPIError as exc:
                if exc.status_code != 404:
                    raise

        scratch.track("record", rid, _delete_if_still_present)

    rows_before = {r["id"] for r in _archive_progress_rows(drift_client, object_uuid)}

    archive_plan = plan_archive_records(object_api_name, record_ids)
    (archive_op,) = archive_plan.operations
    assert archive_op.parent_object_uuid == object_uuid
    result = apply_plan(archive_plan)
    assert result.all_ok, result.results
    assert result.results[0].raw["number_archived"] == 2

    for rid, name in zip(record_ids, names, strict=True):
        _poll_search_membership(drift_client, object_api_name, rid, name, present=False)
        with pytest.raises(KizenAPIError) as excinfo:
            records_api.get_record(drift_client, object_api_name, rid)
        assert excinfo.value.status_code == 404

    new_rows = [
        r
        for r in _archive_progress_rows(drift_client, object_uuid)
        if r["id"] not in rows_before
    ]
    assert len(new_rows) == 1, new_rows
    assert new_rows[0]["send_email_notification"] is False
    assert new_rows[0]["success_count"] == 2

    unarchive_result = apply_plan(plan_unarchive_records(object_api_name, record_ids))
    assert unarchive_result.all_ok, unarchive_result.results
    for rid, name in zip(record_ids, names, strict=True):
        assert records_api.get_record(drift_client, object_api_name, rid)["id"] == rid
        _poll_search_membership(drift_client, object_api_name, rid, name, present=True)

"""Records CRUD + field delete/options: plan builders and apply dispatch.

Value resolution (option label → uuid, checkbox/number coercion,
relationship refs, raw-fields escape hatch) is pinned here, plus the
apply_plan wiring for the new ``record`` / ``field_option`` kinds and the
``delete`` action.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools import records as record_tools
from kizen_builder.tools.planners.fields import (
    plan_add_field_options,
    plan_delete_field,
    plan_remove_field_option,
)
from kizen_builder.tools.planners.records import (
    ARCHIVE_CHUNK,
    plan_archive_records,
    plan_create_records,
    plan_set_field,
    plan_unarchive_records,
    plan_update_records,
    plan_upsert_records,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL, load_fixture

PATIENTS = "patients"
PATIENTS_ID = "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"


def _gender_option_id(name: str) -> str:
    obj = load_fixture(f"objects/{PATIENTS}.json")
    field = next(f for f in obj["fields"] if f["api_name"] == "gender")
    return next(o["id"] for o in field["options"] if o["name"] == name)


def _gender_field_id() -> str:
    obj = load_fixture(f"objects/{PATIENTS}.json")
    return next(f["id"] for f in obj["fields"] if f["api_name"] == "gender")


# ---------------------------------------------------------------------------
# records — get by name
# ---------------------------------------------------------------------------

_NAME_SEARCH = f"{FAKE_BASE_URL}/api/records/{PATIENTS}/search"


@respx.mock
def test_get_by_name_returns_exact_match():
    respx.post(_NAME_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "r1", "name": "Ada Lovelace"},
                    {"id": "r2", "name": "Ada Lovelace Jr"},  # substring, not exact
                ],
                "next": None,
            },
        )
    )
    rec = record_tools.get_record_by_name(PATIENTS, "ada lovelace")  # case-insensitive
    assert rec["id"] == "r1"
    assert rec["object_api_name"] == PATIENTS


@respx.mock
def test_get_by_name_no_match_raises_lookup():
    respx.post(_NAME_SEARCH).mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )
    with pytest.raises(LookupError):
        record_tools.get_record_by_name(PATIENTS, "Nobody")


@respx.mock
def test_get_by_name_ambiguous_raises_value_error():
    respx.post(_NAME_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "r1", "name": "Sam"},
                    {"id": "r2", "name": "Sam"},
                ],
                "next": None,
            },
        )
    )
    with pytest.raises(ValueError, match="r1|r2"):
        record_tools.get_record_by_name(PATIENTS, "Sam")


# ---------------------------------------------------------------------------
# records — value resolution
# ---------------------------------------------------------------------------


def test_create_resolves_dropdown_label_to_option_id(patch_live_lookups):
    plan = plan_create_records(PATIENTS, [{"name": "Ada", "gender": "Female"}])
    (op,) = plan.operations
    assert op.action == "create" and op.kind == "record"
    assert op.parent_object_uuid == PATIENTS
    fields = {f["name"]: f["value"] for f in op.payload["fields"]}
    assert fields["name"] == "Ada"
    assert fields["gender"] == {"id": _gender_option_id("Female")}


def test_create_unknown_label_falls_back_to_name(patch_live_lookups):
    plan = plan_create_records(PATIENTS, [{"gender": "Nonbinary"}])
    (op,) = plan.operations
    assert op.payload["fields"][0]["value"] == {"name": "Nonbinary"}


def test_create_unknown_field_errors(patch_live_lookups):
    with pytest.raises(PlanError, match="not_a_field"):
        plan_create_records(PATIENTS, [{"not_a_field": "x"}])


def test_checkbox_and_relationship_coercion(patch_live_lookups):
    obj = load_fixture(f"objects/{PATIENTS}.json")
    checkbox = next(
        f["api_name"] for f in obj["fields"] if f["field_type"] == "checkbox"
    )
    rel = next(
        f["api_name"] for f in obj["fields"] if f["field_type"] == "relationship"
    )
    plan = plan_create_records(PATIENTS, [{checkbox: "true", rel: "rec-uuid-1"}])
    (op,) = plan.operations
    vals = {f["name"]: f["value"] for f in op.payload["fields"]}
    assert vals[checkbox] is True
    assert vals[rel] == {"id": "rec-uuid-1"}


def test_radio_option_resolves_to_id(patch_live_lookups):
    obj = load_fixture(f"objects/{PATIENTS}.json")
    radio = next(f for f in obj["fields"] if f["field_type"] == "radio")
    option_id = next(o["id"] for o in radio["options"] if o["name"] == "Email")
    plan = plan_create_records(PATIENTS, [{radio["api_name"]: "Email"}])
    (op,) = plan.operations
    assert op.payload["fields"][0]["value"] == {"id": option_id}


def test_rating_field_coerces_to_int(patch_live_lookups):
    obj = load_fixture(f"objects/{PATIENTS}.json")
    rating = next(f for f in obj["fields"] if f["field_type"] == "rating")
    plan = plan_create_records(PATIENTS, [{rating["api_name"]: "3"}])
    (op,) = plan.operations
    assert op.payload["fields"][0]["value"] == 3


def test_raw_fields_passthrough(patch_live_lookups):
    raw = [{"name": "gender", "value": {"id": "explicit"}}]
    plan = plan_create_records(PATIENTS, [{"fields": raw}])
    (op,) = plan.operations
    assert op.payload["fields"] == raw


def test_update_requires_id(patch_live_lookups):
    with pytest.raises(PlanError, match="no 'id'"):
        plan_update_records(PATIENTS, [{"gender": "Male"}])


def test_update_builds_patch_op(patch_live_lookups):
    plan = plan_update_records(PATIENTS, [{"id": "rec-9", "gender": "Male"}])
    (op,) = plan.operations
    assert op.action == "update" and op.existing_uuid == "rec-9"
    assert op.payload["fields"][0]["value"] == {"id": _gender_option_id("Male")}


def test_upsert_requires_lookup_value(patch_live_lookups):
    with pytest.raises(PlanError, match="no 'lookup_value'"):
        plan_upsert_records(PATIENTS, [{"gender": "Male"}])


def test_upsert_builds_op(patch_live_lookups):
    plan = plan_upsert_records(PATIENTS, [{"lookup_value": "Ada", "gender": "Female"}])
    (op,) = plan.operations
    assert op.action == "upsert" and op.kind == "record"
    assert op.parent_object_uuid == PATIENTS
    assert op.payload["lookup_value"] == "Ada"
    assert op.payload["fields"][0]["value"] == {"id": _gender_option_id("Female")}
    assert "oncreate_unarchive" not in op.payload
    assert "onupdate_archived_conflict" not in op.payload


def test_upsert_passes_through_conflict_options(patch_live_lookups):
    plan = plan_upsert_records(
        PATIENTS,
        [{"lookup_value": "Ada", "gender": "Female"}],
        oncreate_unarchive="overwrite",
        onupdate_archived_conflict="overwrite",
    )
    (op,) = plan.operations
    assert op.payload["oncreate_unarchive"] == "overwrite"
    assert op.payload["onupdate_archived_conflict"] == "overwrite"


# ---------------------------------------------------------------------------
# records — archive / unarchive
#
# `DELETE /api/records/{o}/{id}` reaches the same archived, restorable state
# (confirmed live 2026-09-28), so `records archive` is the one CLI path.
# ---------------------------------------------------------------------------


def test_archive_records_plan_one_op_for_many_ids(patch_live_lookups):
    plan = plan_archive_records(PATIENTS, ["a", "b", "a"])
    (op,) = plan.operations
    assert (op.action, op.kind) == ("update", "record_archive")
    assert op.payload == {"record_ids": ["a", "b"], "send_email_notification": False}
    assert op.key == f"{PATIENTS}#a..b"
    # bulk-archive-entity-record lives under /api/custom-objects and takes
    # the object's UUID, not its api_name.
    assert op.parent_object_uuid == PATIENTS_ID
    assert op.preview["record_count"] == 2
    assert "records unarchive" in op.preview["warning"]
    assert plan.summary == f"Archive 2 record(s) from {PATIENTS} in 1 request(s)"


def test_archive_records_plan_chunks_at_boundary(patch_live_lookups):
    ids = [f"r{i}" for i in range(ARCHIVE_CHUNK + 1)]
    plan = plan_archive_records(PATIENTS, ids)
    assert [len(op.payload["record_ids"]) for op in plan.operations] == [
        ARCHIVE_CHUNK,
        1,
    ]
    assert all(op.payload["send_email_notification"] is False for op in plan.operations)
    assert plan.operations[1].key == f"{PATIENTS}#r{ARCHIVE_CHUNK}"
    assert plan.summary.endswith("in 2 request(s)")


def test_archive_records_rejects_empty_ids(patch_live_lookups):
    with pytest.raises(PlanError, match="no record ids"):
        plan_archive_records(PATIENTS, [])


def test_unarchive_records_plan(patch_live_lookups):
    plan = plan_unarchive_records(PATIENTS, ["a", "b"])
    assert [op.action for op in plan.operations] == ["update", "update"]
    assert [op.kind for op in plan.operations] == [
        "record_unarchive",
        "record_unarchive",
    ]
    assert [op.existing_uuid for op in plan.operations] == ["a", "b"]
    assert all(op.parent_object_uuid == PATIENTS for op in plan.operations)


def test_unarchive_records_rejects_empty_ids(patch_live_lookups):
    with pytest.raises(PlanError, match="no record ids"):
        plan_unarchive_records(PATIENTS, [])


# ---------------------------------------------------------------------------
# records — bulk set-field
#
# bulk-change-field-value's ``field_value`` takes the bare wire scalar, not
# the ``{"id": ...}`` dict a regular record write uses — confirmed live
# 2026-07-20 (a wrapped/dict value 400s with "Not a valid string").
# ---------------------------------------------------------------------------


def test_set_field_unwraps_option_id_for_radio(patch_live_lookups):
    obj = load_fixture(f"objects/{PATIENTS}.json")
    radio = next(f for f in obj["fields"] if f["field_type"] == "radio")
    option_id = next(o["id"] for o in radio["options"] if o["name"] == "Email")
    plan = plan_set_field(PATIENTS, ["rec-1", "rec-2"], radio["api_name"], "Email")
    (op,) = plan.operations
    assert op.kind == "record_bulk_field_value"
    assert op.payload["field_id"] == radio["id"]
    assert op.payload["field_value"] == option_id  # bare id, not {"id": ...}
    assert op.payload["record_ids"] == ["rec-1", "rec-2"]
    assert op.payload["field_resolution"] == "overwrite"


def test_set_field_passes_bare_scalar_for_text(patch_live_lookups):
    plan = plan_set_field(PATIENTS, ["rec-1"], "name", "New Name")
    (op,) = plan.operations
    assert op.payload["field_value"] == "New Name"


def test_set_field_rejects_empty_record_ids(patch_live_lookups):
    with pytest.raises(PlanError, match="no record ids"):
        plan_set_field(PATIENTS, [], "name", "x")


def test_set_field_rejects_unknown_resolution(patch_live_lookups):
    with pytest.raises(PlanError, match="field_resolution"):
        plan_set_field(PATIENTS, ["rec-1"], "name", "x", field_resolution="bogus")


@respx.mock
def test_apply_set_field_posts_bulk_change_field_value(patch_live_lookups):
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/bulk-change-field-value"
    ).mock(return_value=httpx.Response(200, json={"field_id": "x"}))
    plan = plan_set_field(PATIENTS, ["rec-1"], "name", "New Name")
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


# ---------------------------------------------------------------------------
# records — apply dispatch
# ---------------------------------------------------------------------------


@respx.mock
def test_apply_create_record_posts_add(patch_live_lookups):
    route = respx.post(f"{FAKE_BASE_URL}/api/records/{PATIENTS}/add").mock(
        return_value=httpx.Response(200, json={"id": "new-rec"})
    )
    plan = plan_create_records(PATIENTS, [{"name": "Ada"}])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.results[0].status == "ok"
    assert result.results[0].server_uuid == "new-rec"


@respx.mock
def test_apply_update_record_patches(patch_live_lookups):
    route = respx.patch(f"{FAKE_BASE_URL}/api/records/{PATIENTS}/rec-9").mock(
        return_value=httpx.Response(200, json={"id": "rec-9"})
    )
    plan = plan_update_records(PATIENTS, [{"id": "rec-9", "name": "Ada"}])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_upsert_record_posts_upsert(patch_live_lookups):
    route = respx.post(f"{FAKE_BASE_URL}/api/records/{PATIENTS}/upsert").mock(
        return_value=httpx.Response(200, json={"id": "rec-9", "action": "updated"})
    )
    plan = plan_upsert_records(PATIENTS, [{"lookup_value": "Ada", "name": "Ada"}])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    r = result.results[0]
    assert r.status == "ok" and r.action == "upsert"
    assert r.server_uuid == "rec-9"
    assert r.message == "updated"


@respx.mock
def test_apply_archive_records_posts_one_batched_request(patch_live_lookups):
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/bulk-archive-entity-record"
    ).mock(return_value=httpx.Response(200, json={"number_archived": 3, "async": True}))
    plan = plan_archive_records(PATIENTS, ["rec-1", "rec-2", "rec-3"])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {
        "record_ids": ["rec-1", "rec-2", "rec-3"],
        "send_email_notification": False,
    }
    (r,) = result.results
    assert r.status == "ok" and r.action == "update"
    assert r.message == "3 id(s) accepted"


@respx.mock
def test_apply_archive_failed_chunk_does_not_stop_later_chunks(patch_live_lookups):
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/bulk-archive-entity-record"
    ).mock(
        side_effect=[
            httpx.Response(400, json={"detail": "bad"}),
            httpx.Response(200, json={"number_archived": 1, "async": True}),
        ]
    )
    ids = [f"r{i}" for i in range(ARCHIVE_CHUNK + 1)]
    result = plan_tools.apply_plan(plan_archive_records(PATIENTS, ids))
    assert route.call_count == 2
    assert [r.status for r in result.results] == ["failed", "ok"]
    assert result.results[0].key == f"{PATIENTS}#r0..r{ARCHIVE_CHUNK - 1}"


@respx.mock
def test_apply_rejects_record_delete_from_an_old_saved_plan(patch_live_lookups):
    op = plan_tools.PlanOperation(
        action="delete",
        kind="record",
        key=f"{PATIENTS}#rec-9",
        existing_uuid="rec-9",
        parent_object_uuid=PATIENTS,
    )
    result = plan_tools.apply_plan(plan_tools.Plan.build("testenv", "x", [op]))
    (r,) = result.results
    assert r.status == "failed" and "unsupported record action" in (r.message or "")


@respx.mock
def test_apply_unarchive_record_patches(patch_live_lookups):
    route = respx.patch(f"{FAKE_BASE_URL}/api/records/{PATIENTS}/rec-9/unarchive").mock(
        return_value=httpx.Response(200, json={"id": "rec-9"})
    )
    plan = plan_unarchive_records(PATIENTS, ["rec-9"])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


# ---------------------------------------------------------------------------
# fields — delete + options
# ---------------------------------------------------------------------------


def test_plan_delete_field(patch_live_lookups):
    plan = plan_delete_field(PATIENTS, "gender")
    (op,) = plan.operations
    assert op.action == "delete" and op.kind == "field"
    assert op.existing_uuid == _gender_field_id()
    assert op.parent_object_uuid == PATIENTS_ID


def test_plan_add_options_skips_existing(patch_live_lookups):
    plan = plan_add_field_options(PATIENTS, "gender", ["Female", "Nonbinary"])
    actions = {op.preview["option"]: op.action for op in plan.operations}
    assert actions == {"Female": "skip", "Nonbinary": "create"}


def test_plan_add_options_rejects_non_option_field(patch_live_lookups):
    with pytest.raises(PlanError, match="no options"):
        plan_add_field_options(PATIENTS, "name", ["X"])


def test_plan_remove_option_without_remap(patch_live_lookups):
    plan = plan_remove_field_option(PATIENTS, "gender", "Other")
    (op,) = plan.operations
    assert op.action == "delete" and op.kind == "field_option"
    assert op.existing_uuid == _gender_option_id("Other")
    assert "remap_to" not in op.payload
    assert op.payload["field_id"] == _gender_field_id()


def test_plan_remove_option_with_remap(patch_live_lookups):
    plan = plan_remove_field_option(PATIENTS, "gender", "Other", remap_to="Unknown")
    (op,) = plan.operations
    assert op.payload["remap_to"] == _gender_option_id("Unknown")


@respx.mock
def test_apply_add_option_posts(patch_live_lookups):
    fid = _gender_field_id()
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/fields/{fid}/options"
    ).mock(return_value=httpx.Response(201, json={"id": "opt-new"}))
    plan = plan_add_field_options(PATIENTS, "gender", ["Nonbinary"])
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_remove_option_deletes(patch_live_lookups):
    fid = _gender_field_id()
    oid = _gender_option_id("Other")
    route = respx.delete(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/fields/{fid}/options/{oid}"
    ).mock(return_value=httpx.Response(204))
    plan = plan_remove_field_option(PATIENTS, "gender", "Other")
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_remove_option_with_remap_posts_replace(patch_live_lookups):
    fid = _gender_field_id()
    oid = _gender_option_id("Other")
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/fields/{fid}/options/{oid}/replace"
    ).mock(return_value=httpx.Response(200, json={"id": "opt-keep"}))
    plan = plan_remove_field_option(PATIENTS, "gender", "Other", remap_to="Unknown")
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_delete_field_deletes(patch_live_lookups):
    fid = _gender_field_id()
    route = respx.delete(
        f"{FAKE_BASE_URL}/api/custom-objects/{PATIENTS_ID}/fields/{fid}"
    ).mock(return_value=httpx.Response(204))
    plan = plan_delete_field(PATIENTS, "gender")
    result = plan_tools.apply_plan(plan)
    assert route.call_count == 1
    assert result.all_ok

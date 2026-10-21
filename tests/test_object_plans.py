"""The six custom-object planner entry points: object and field-category
create/update/delete.

These reach live state through `get_object` / `list_objects`, which the
planner imports from `tools.objects`. Those are monkeypatched on the planner
module directly — the same live-lookup seam `patch_live_lookups` (conftest.py)
uses, and the layer whose HTTP chain is already covered by
`tests/test_objects_tool.py`. Tests that only need realistic shape use the
`patch_live_lookups` fixture and its `tests/fixtures/objects/` data; tests
asserting a *diff* patch in their own `raw` block, since the CLI-derived
fixtures carry `raw: None`.

`tests/test_object_payloads.py` already covers `_build_object_payload` and
`_build_pipeline_stages`; nothing here re-asserts those.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from kizen_builder.models.spec import FieldCategory, ObjectDef
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners import objects as object_planners
from kizen_builder.tools.planners.records import ARCHIVE_CHUNK
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

OBJ_ID = "00000000-0000-4000-8000-0000000002a0"
CAT_ID = "00000000-0000-4000-8000-0000000002a1"
CAT_ID_2 = "00000000-0000-4000-8000-0000000002a2"

OBJECT_DETAIL = {
    "id": OBJ_ID,
    "api_name": "policies",
    "display_name": "Policies",
    "entity_name": "Policy",
    "object_type": "standard",
    "is_custom": True,
    "categories": [
        {"id": CAT_ID, "name": "Details", "order": 0},
        {"id": CAT_ID_2, "name": "Billing", "order": 1},
    ],
    "fields": [],
    "raw": {
        "object_name": "Policies",
        "entity_name": "Policy",
        "description": "",
        "default_on_activities": True,
    },
}


@pytest.fixture
def live(monkeypatch):
    """Serve get_object/list_objects from a controlled in-test object."""

    def _install(detail=None, listing=None, record_ids=()):
        obj = OBJECT_DETAIL if detail is None else detail
        monkeypatch.setattr(
            object_planners, "active_record_ids", lambda api_name: list(record_ids)
        )

        def fake_get_object(api_name):
            if api_name != obj["api_name"]:
                raise LookupError(f"object '{api_name}' not found (no fixture)")
            return obj

        monkeypatch.setattr(object_planners, "get_object", fake_get_object)
        monkeypatch.setattr(
            object_planners,
            "list_objects",
            lambda: listing if listing is not None else [],
        )
        return obj

    return _install


# ---------------------------------------------------------------------------
# plan_create_object
# ---------------------------------------------------------------------------


def test_plan_create_object_emits_one_create_op(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Policies", "api_name": "policies", "entity_name": "Policy"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "object"
    assert op.key == "policies"
    assert op.preview["object_name"] == "Policies"
    assert op.preview["entity_name"] == "Policy"
    assert op.payload["object_name"] == "Policies"
    assert op.payload["object_type"] == "standard"


def test_plan_create_object_accepts_a_model_as_well_as_a_dict(live):
    live()
    plan = object_planners.plan_create_object(
        ObjectDef.model_validate({"name": "Policies", "api_name": "policies"})
    )
    (op,) = plan.operations
    assert op.payload["object_name"] == "Policies"


def test_plan_create_object_entity_name_defaults_to_the_display_name(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Policies", "api_name": "policies"}
    )
    (op,) = plan.operations
    assert op.preview["entity_name"] == "Policies"


def test_plan_create_object_raises_when_the_display_name_is_taken(live):
    # Kizen 400s a duplicate object_name (confirmed live 2026-10-14).
    live(listing=[{"id": OBJ_ID, "api_name": "policies", "display_name": "Policies"}])
    with pytest.raises(PlanError, match="already exists"):
        object_planners.plan_create_object({"name": "Policies"})


def test_plan_create_object_keys_on_the_derived_api_name(live):
    live()
    plan = object_planners.plan_create_object({"name": "Service Tickets"})
    (op,) = plan.operations
    assert op.key == "service_tickets"
    assert op.preview["api_name (derived)"] == "service_tickets"
    assert "warning" not in op.preview


def test_plan_create_object_refuses_an_api_name_kizen_wont_derive(live):
    live()
    with pytest.raises(PlanError, match=r"becomes 'policies', not 'policy'") as e:
        object_planners.plan_create_object({"name": "Policies", "api_name": "policy"})
    assert '--name "Policy"' in str(e.value)


@pytest.mark.parametrize(
    "api_name, name",
    [
        ("employee_m7SZCzg3", "Employee"),  # suffixed, no name derives it
        ("client_client", "Contacts"),  # "Client Client" would be suffixed
    ],
)
def test_plan_create_object_reports_a_live_api_name_as_existing(live, api_name, name):
    live(listing=[{"id": OBJ_ID, "api_name": api_name, "display_name": name}])
    with pytest.raises(PlanError, match=f"'{api_name}' already exists") as e:
        object_planners.plan_create_object({"name": name, "api_name": api_name})
    assert "becomes" not in str(e.value)
    assert "--name" not in str(e.value)


def test_plan_create_object_mismatch_with_no_possible_name_says_so(live):
    live()
    with pytest.raises(PlanError, match="No display name produces 'Policy_X'"):
        object_planners.plan_create_object({"name": "Policies", "api_name": "Policy_X"})


def test_plan_create_object_warns_when_the_derived_slug_is_taken(live):
    # Another object's display name slugs the same way: Kizen suffixes ours.
    live(listing=[{"id": OBJ_ID, "api_name": "policies", "display_name": "Policies!"}])
    plan = object_planners.plan_create_object({"name": "Policies"})
    (op,) = plan.operations
    assert op.key == "policies"
    assert "random suffix" in op.preview["warning"]


def test_plan_create_object_ignores_deleted_objects(live):
    live(
        listing=[
            {
                "id": OBJ_ID,
                "api_name": "policies",
                "display_name": "Policies",
                "deleted": True,
            }
        ]
    )
    (op,) = object_planners.plan_create_object({"name": "Policies"}).operations
    assert "warning" not in op.preview


def test_plan_create_object_pipeline_type_carries_stages(live):
    live()
    plan = object_planners.plan_create_object(
        {"name": "Deals", "api_name": "deals", "object_type": "pipeline"}
    )
    (op,) = plan.operations
    assert op.payload["pipeline"]["stages"][0]["name"] == "Open"


# ---------------------------------------------------------------------------
# plan_update_object
# ---------------------------------------------------------------------------


def test_plan_update_object_sends_only_changed_keys(live):
    live()
    plan = object_planners.plan_update_object(
        "policies", {"object_name": "Renamed", "entity_name": "Policy"}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.existing_uuid == OBJ_ID
    # entity_name already matches live state, so it is not resent
    assert op.payload == {"object_name": "Renamed"}
    assert op.preview["diff"] == {"object_name": "Policies → Renamed"}


def test_plan_update_object_can_change_every_supported_key(live):
    live()
    plan = object_planners.plan_update_object(
        "policies",
        {
            "object_name": "Renamed",
            "entity_name": "Contract",
            "description": "now set",
            "default_on_activities": False,
        },
    )
    (op,) = plan.operations
    assert op.payload == {
        "object_name": "Renamed",
        "entity_name": "Contract",
        "description": "now set",
        "default_on_activities": False,
    }


def test_plan_update_object_treats_a_null_description_as_empty_string(live):
    """The API returns `description: null` for an unset description but the
    spec carries `""`, so setting it to `""` must not register as a change."""
    live(detail=dict(OBJECT_DETAIL, raw=dict(OBJECT_DETAIL["raw"], description=None)))
    plan = object_planners.plan_update_object("policies", {"description": ""})
    (op,) = plan.operations
    assert op.action == "skip"


def test_plan_update_object_no_changes_skips(live):
    live()
    plan = object_planners.plan_update_object("policies", {"object_name": "Policies"})
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.payload == {}
    assert op.preview["diff"] == "no changes"
    assert "No changes" in plan.summary


def test_plan_update_object_ignores_unknown_keys(live):
    live()
    plan = object_planners.plan_update_object("policies", {"not_a_key": "x"})
    (op,) = plan.operations
    assert op.action == "skip"


def test_plan_update_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_update_object("nope", {"object_name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_object
# ---------------------------------------------------------------------------


def test_plan_delete_object_with_no_records_is_one_delete_op(live):
    live()
    plan = object_planners.plan_delete_object("policies")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "object"
    assert op.existing_uuid == OBJ_ID
    assert op.payload == {}
    assert plan.summary == "Delete object 'policies'"
    assert "archives the object and its data" not in op.preview["warning"]
    assert "no active records" in op.preview["warning"]


def test_plan_delete_object_archives_active_records_first(live):
    live(record_ids=["r1", "r2", "r3"])
    plan = object_planners.plan_delete_object("policies")
    archive, delete = plan.operations
    assert (archive.kind, archive.action) == ("record_archive", "update")
    assert archive.key == "policies#r1..r3"
    assert archive.parent_object_uuid == OBJ_ID
    assert archive.payload == {
        "record_ids": ["r1", "r2", "r3"],
        "send_email_notification": False,
    }
    assert (delete.kind, delete.action, delete.key) == ("object", "delete", "policies")
    assert delete.payload == {
        "wait_for_archive_seconds": object_planners.ARCHIVE_SETTLE_TIMEOUT
    }
    assert plan.summary == (
        "Archive 3 record(s) from policies in 1 request(s), "
        "then delete object 'policies'"
    )
    warning = delete.preview["warning"]
    assert "refuses to delete an object with active records" in warning
    assert "archives its 3 active record(s) first, with no email" in warning
    assert "restore" not in warning


def test_plan_delete_object_chunks_the_archive(live):
    ids = [f"r{i}" for i in range(ARCHIVE_CHUNK + 1)]
    live(record_ids=ids)
    plan = object_planners.plan_delete_object("policies")
    first, second, delete = plan.operations
    assert first.payload["record_ids"] == ids[:ARCHIVE_CHUNK]
    assert second.key == f"policies#r{ARCHIVE_CHUNK}"
    assert all(op.payload["send_email_notification"] is False for op in (first, second))
    assert delete.kind == "object"
    assert plan.summary.startswith(
        f"Archive {ARCHIVE_CHUNK + 1} record(s) from policies in 2 request(s)"
    )


def test_plan_delete_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_delete_object("nope")


def test_plan_delete_object_refuses_a_builtin_before_listing_records(live, monkeypatch):
    live(detail={**OBJECT_DETAIL, "api_name": "client_client", "is_custom": False})

    def never(api_name):
        raise AssertionError("listed records of a built-in object")

    monkeypatch.setattr(object_planners, "active_record_ids", never)
    with pytest.raises(PlanError) as exc:
        object_planners.plan_delete_object("client_client")
    assert str(exc.value) == (
        "'client_client' is a built-in Kizen object. objects delete only "
        "deletes custom objects, so nothing was planned and no records were "
        "touched"
    )


def test_plan_delete_object_archives_exactly_one_chunk_in_one_request(live):
    live(record_ids=[f"r{i}" for i in range(ARCHIVE_CHUNK)])
    archive, delete = object_planners.plan_delete_object("policies").operations
    assert len(archive.payload["record_ids"]) == ARCHIVE_CHUNK
    assert delete.kind == "object"


# ---------------------------------------------------------------------------
# plan_create_category
# ---------------------------------------------------------------------------


def test_plan_create_category_is_scoped_to_its_parent_object(live):
    live()
    plan = object_planners.plan_create_category(
        "policies", {"name": "Claims", "api_name": "claims"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.kind == "category"
    assert op.parent_object_uuid == OBJ_ID
    assert op.payload == {"name": "Claims"}
    assert op.preview["object"] == "policies"


def test_plan_create_category_accepts_a_model(live):
    live()
    plan = object_planners.plan_create_category(
        "policies",
        FieldCategory.model_validate({"name": "Claims", "api_name": "claims"}),
    )
    (op,) = plan.operations
    assert op.payload == {"name": "Claims"}


def test_plan_create_category_raises_when_the_name_is_taken(live):
    live()
    with pytest.raises(PlanError, match="category 'Details' already exists"):
        object_planners.plan_create_category(
            "policies", {"name": "Details", "api_name": "details"}
        )


def test_plan_create_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_create_category(
            "nope", {"name": "Claims", "api_name": "claims"}
        )


# ---------------------------------------------------------------------------
# plan_update_category
# ---------------------------------------------------------------------------


def test_plan_update_category_renames(live):
    live()
    plan = object_planners.plan_update_category(
        "policies", "Details", {"name": "Overview"}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"name": "Overview"}
    assert op.existing_uuid == CAT_ID
    assert op.parent_object_uuid == OBJ_ID
    assert op.preview["diff"] == {"name": "Details → Overview"}


def test_plan_update_category_same_name_skips(live):
    live()
    plan = object_planners.plan_update_category(
        "policies", "Details", {"name": "Details"}
    )
    (op,) = plan.operations
    assert op.action == "skip"
    assert op.preview["diff"] == "no changes"
    assert "No changes" in plan.summary


def test_plan_update_category_unknown_category_lists_available(live):
    live()
    with pytest.raises(PlanError, match=r"not found on 'policies'"):
        object_planners.plan_update_category("policies", "Nope", {"name": "x"})


def test_plan_update_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_update_category("nope", "Details", {"name": "x"})


# ---------------------------------------------------------------------------
# plan_delete_category
# ---------------------------------------------------------------------------


def test_plan_delete_category(live):
    live()
    plan = object_planners.plan_delete_category("policies", "Billing")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.kind == "category"
    assert op.existing_uuid == CAT_ID_2
    assert op.parent_object_uuid == OBJ_ID
    assert op.key == "policies.Billing"


def test_plan_delete_category_unknown_category_lists_available(live):
    live()
    with pytest.raises(PlanError, match=r"not found on 'policies'"):
        object_planners.plan_delete_category("policies", "Nope")


def test_plan_delete_category_object_not_found_raises_plan_error(live):
    live()
    with pytest.raises(PlanError, match="object 'nope' not found"):
        object_planners.plan_delete_category("nope", "Details")


# ---------------------------------------------------------------------------
# against the real captured object fixtures
# ---------------------------------------------------------------------------


def test_plan_create_category_against_a_captured_object_fixture(patch_live_lookups):
    """Same path, but driven by real captured API output rather than an
    in-test dict — guards against the hand-written fixtures above drifting
    from the shape the API actually returns."""
    plan = object_planners.plan_create_category(
        "patients", {"name": "New Group", "api_name": "new_group"}
    )
    (op,) = plan.operations
    assert op.action == "create"
    assert op.parent_object_uuid == "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"
    assert op.payload == {"name": "New Group"}


def test_plan_delete_category_against_a_captured_object_fixture(patch_live_lookups):
    plan = object_planners.plan_delete_category("patients", "Identifiers")
    (op,) = plan.operations
    assert op.action == "delete"
    assert op.existing_uuid == "e523181d-9331-4328-b09f-829d8f2a367f"


# ---------------------------------------------------------------------------
# objects delete — apply
# ---------------------------------------------------------------------------

_ARCHIVE_URL = f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/bulk-archive-entity-record"
_SEARCH_URL = f"{FAKE_BASE_URL}/api/records/policies/search"
_OBJECT_URL = f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}"


def _count(n):
    return httpx.Response(200, json={"count": n, "next": None, "results": []})


@respx.mock
def test_apply_delete_object_archives_then_waits_then_deletes(live):
    live(record_ids=["r1", "r2", "r3"])
    archive = respx.post(_ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json={"number_archived": 3, "async": True})
    )
    count = respx.post(_SEARCH_URL).mock(return_value=_count(0))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert result.all_ok
    assert json.loads(archive.calls.last.request.content) == {
        "record_ids": ["r1", "r2", "r3"],
        "send_email_notification": False,
    }
    assert [(c.request.method, c.request.url.path) for c in respx.calls] == [
        ("POST", f"/api/custom-objects/{OBJ_ID}/bulk-archive-entity-record"),
        ("POST", "/api/records/policies/search"),
        ("DELETE", f"/api/custom-objects/{OBJ_ID}"),
    ]
    assert count.calls.last.request.url.params["page_size"] == "1"
    assert delete.call_count == 1


@respx.mock
def test_apply_delete_object_polls_until_the_archive_clears(live, monkeypatch):
    live(record_ids=["r1", "r2", "r3"])
    sleeps = []
    monkeypatch.setattr(plan_tools.time, "sleep", sleeps.append)
    respx.post(_ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json={"number_archived": 3, "async": True})
    )
    count = respx.post(_SEARCH_URL).mock(side_effect=[_count(3), _count(0)])
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert result.all_ok
    assert count.call_count == 2
    assert sleeps == [2]
    assert delete.call_count == 1


@respx.mock
def test_apply_delete_object_times_out_without_deleting(live):
    live(record_ids=["r1", "r2", "r3"])
    plan = object_planners.plan_delete_object("policies")
    plan.operations[-1].payload["wait_for_archive_seconds"] = 0
    respx.post(_ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json={"number_archived": 3, "async": True})
    )
    respx.post(_SEARCH_URL).mock(return_value=_count(3))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(plan)

    assert delete.call_count == 0
    archived, deleted = result.results
    assert archived.status == "ok"
    assert deleted.status == "failed"
    assert deleted.message == (
        "HTTP 0: 3 record(s) still active after 0s; the archive may still be "
        "running. Re-run kizen objects delete policies"
    )


@respx.mock
def test_apply_delete_object_is_skipped_when_an_archive_chunk_fails(live):
    live(record_ids=["r1", "r2", "r3"])
    respx.post(_ARCHIVE_URL).mock(
        return_value=httpx.Response(400, json={"detail": "bad"})
    )
    count = respx.post(_SEARCH_URL).mock(return_value=_count(0))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert count.call_count == 0
    assert delete.call_count == 0
    archived, deleted = result.results
    assert archived.status == "failed"
    assert deleted.status == "skipped"
    assert deleted.message == "blocked by failed op 'policies#r1..r3'"


@respx.mock
def test_apply_delete_object_is_skipped_when_a_later_chunk_fails(live):
    live(record_ids=[f"r{i}" for i in range(ARCHIVE_CHUNK + 1)])
    respx.post(_ARCHIVE_URL).mock(
        side_effect=[
            httpx.Response(200, json={"number_archived": ARCHIVE_CHUNK}),
            httpx.Response(500),
        ]
    )
    count = respx.post(_SEARCH_URL).mock(return_value=_count(0))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert [r.status for r in result.results] == ["ok", "failed", "skipped"]
    assert result.results[-1].message == (
        f"blocked by failed op 'policies#r{ARCHIVE_CHUNK}'"
    )
    assert count.call_count == 0
    assert delete.call_count == 0


@respx.mock
def test_apply_delete_object_rides_out_a_transient_count_error(live, monkeypatch):
    live(record_ids=["r1"])
    monkeypatch.setattr(plan_tools.time, "sleep", lambda _s: None)
    respx.post(_ARCHIVE_URL).mock(return_value=httpx.Response(200, json={}))
    respx.post(_SEARCH_URL).mock(side_effect=[httpx.Response(503), _count(0)])
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert result.all_ok
    assert delete.call_count == 1


@respx.mock
def test_apply_delete_object_count_failure_says_how_to_finish(live, monkeypatch):
    live(record_ids=["r1"])
    monkeypatch.setattr(plan_tools.time, "sleep", lambda _s: None)
    respx.post(_ARCHIVE_URL).mock(return_value=httpx.Response(200, json={}))
    respx.post(_SEARCH_URL).mock(return_value=httpx.Response(503))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(object_planners.plan_delete_object("policies"))

    assert delete.call_count == 0
    deleted = result.results[-1]
    assert deleted.status == "failed"
    assert deleted.message.endswith(
        "the records are already archived, so the object was not deleted. "
        "Re-run kizen objects delete policies to finish"
    )


@respx.mock
def test_apply_delete_object_from_an_old_saved_plan_does_not_poll(live):
    op = plan_tools.PlanOperation(
        action="delete", kind="object", key="policies", existing_uuid=OBJ_ID
    )
    count = respx.post(_SEARCH_URL).mock(return_value=_count(0))
    delete = respx.delete(_OBJECT_URL).mock(return_value=httpx.Response(200, json={}))

    result = plan_tools.apply_plan(plan_tools.Plan.build("testenv", "x", [op]))

    assert result.all_ok
    assert count.call_count == 0
    assert delete.call_count == 1

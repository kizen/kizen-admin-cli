"""Golden tests for the saved-view plan builders — filter groups, quick
filters, and column templates, the three per-object resources under
``/api/custom-objects/{object_pk}/{filter-groups,quick-filters,columns}``
(see ``kizen docs show saved-views``). Modeled on
``test_dashboard_layout_payloads.py`` (same shared-sharing-block shape) and
``test_form_payloads.py`` (spec-in / payload-out golden style).

Quirks pinned here, from ``docs/specs/saved-views.md``'s wire-format section:

  * ``owner`` is omitted from the payload entirely when unset, never sent as
    ``null`` — the API 500s on ``owner: null`` for quick filters/columns.
  * A filter group's ``config`` / quick filter's ``filters`` render through
    the same ``{"and", "query": [...], "invalid"}`` shape as an automation
    condition step's ``filter_config`` (the DSL is shared across six
    surfaces — see ``docs/filters.md``); a raw ``{"query": [...]}`` dict is
    accepted too and merely normalized.
  * ``sharing_settings`` defaults via ``default_sharing_settings()`` when
    unset, and a template's ``{id, display_name}`` shape is normalized to
    bare UUIDs on write — the same helper dashboards uses.
  * Update ops diff against live state field-by-field and emit ``skip`` (not
    a no-op PATCH) when nothing changed.
  * ``owner``'s *read* shape expands to ``{id, display_name}``; writes take
    a bare id (``_unwrap_owner``).
  * apply-to-* dispatch: quick filters take roles/users; columns take
    roles/users/permission_groups. Filter groups have no apply endpoint.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder import filtering
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners import saved_views as sv_planners
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from tests.conftest import FAKE_BASE_URL, load_fixture

# patients object id, from tests/fixtures/objects/patients.json — reused
# rather than inventing a new fixture, same object the automation-payload
# filter-DSL tests already exercise.
OBJ_ID = "ceed733b-9dd9-4bf9-8c52-8ba1ac41da45"
MRN_FIELD_ID = "db489b3e-a880-41f7-90b9-312b8cfdd02b"

ADMIN_ID = "00000000-0000-4000-8000-0000000000ad"
FG_ID = "00000000-0000-4000-8000-000000000fb1"
QF_ID = "00000000-0000-4000-8000-000000000qf1"
CT_ID = "00000000-0000-4000-8000-000000000ct1"


def _default_sharing() -> dict:
    return {
        "private": False,
        "all_team_members": 1,
        "roles": {"view": [], "edit": [], "admin": [ADMIN_ID]},
        "team_members": {"view": [], "edit": [], "admin": []},
    }


@pytest.fixture
def no_existing_views(monkeypatch):
    """Baseline for create-path tests: 'patients' resolves to OBJ_ID, no
    saved view with the target name exists yet, and sharing defaults without
    a live admin-role lookup."""
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda api_name: OBJ_ID)
    monkeypatch.setattr(sv_planners, "list_saved_views", lambda *a, **k: [])
    monkeypatch.setattr(sv_planners, "default_sharing_settings", _default_sharing)


@pytest.fixture
def filter_schema():
    """Serve filtering-DSL schema lookups from the patients fixture.

    Same pattern as test_automation_payloads.py's ``patients_filter_schema``
    — module-local, not added to conftest.py per the task's no-shared-fixture
    rule.
    """
    patients = load_fixture("objects/patients.json")

    class _Schema:
        def custom_object(self, api_name):
            return {"id": patients["id"], "name": api_name}

        def get_field(self, obj_id, name):
            for f in patients["fields"]:
                if name in (f["api_name"], f["id"]):
                    return {
                        "name": f["api_name"],
                        "id": f["id"],
                        "field_type": f["field_type"],
                        "is_default": False,
                        "options": f["options"] or [],
                    }
            return None

    filtering.set_default_client(_Schema())
    yield
    filtering.set_default_client(None)


def _existing(kind_key: str, **overrides) -> dict:
    """A find_saved_view()-shaped detail record, defaults matching a
    freshly-created resource of the given wire key ('config'/'filters'/
    'configuration_json')."""
    base = {
        "id": FG_ID,
        "name": "Big deals",
        kind_key: {},
        "owner": None,
        "sharing_settings": _default_sharing(),
    }
    if kind_key == "config":
        base["hidden"] = False
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Filter groups — create
# ---------------------------------------------------------------------------


def test_create_filter_group_minimal_payload(no_existing_views):
    plan = sv_planners.plan_create_filter_group("patients", {"name": "Big deals"})
    (op,) = plan.operations
    assert op.kind == "filter_group" and op.action == "create"
    assert op.key == "patients.Big deals"
    assert op.parent_object_uuid == OBJ_ID
    assert op.payload == {
        "name": "Big deals",
        "config": {},
        "hidden": False,
        "sharing_settings": _default_sharing(),
    }
    assert "owner" not in op.payload


def test_create_filter_group_includes_owner_when_set(no_existing_views):
    plan = sv_planners.plan_create_filter_group(
        "patients", {"name": "X", "owner": "user-1"}
    )
    (op,) = plan.operations
    assert op.payload["owner"] == "user-1"


def test_create_filter_group_renders_filter_spec_via_dsl(
    no_existing_views, filter_schema
):
    plan = sv_planners.plan_create_filter_group(
        "patients",
        {
            "name": "MRN filter",
            "config": {"all": [{"field": "mrn", "op": "=", "value": "A-1"}]},
        },
    )
    (op,) = plan.operations
    cfg = op.payload["config"]
    assert cfg["invalid"] is False
    assert cfg["query"][0]["id"] == "query-0"
    (clause,) = cfg["query"][0]["filters"]
    assert clause["field"] == f'"custom"::{MRN_FIELD_ID}'
    assert clause["value"] == "A-1"


def test_create_filter_group_compiles_a_stage_time_op(no_existing_views):
    from tests.filter_stub import StubClient

    filtering.set_default_client(StubClient())
    try:
        plan = sv_planners.plan_create_filter_group(
            "patients",
            {
                "name": "Stuck in Stage 1",
                "config": {
                    "all": [
                        {"field": "stage", "op": "=", "value": "Stage 1"},
                        {
                            "field": "stage",
                            "op": "time_in_stage",
                            "value": "Stage 1",
                            "more_than": 7,
                        },
                    ]
                },
            },
        )
    finally:
        filtering.set_default_client(None)
    (op,) = plan.operations
    _, clause = op.payload["config"]["query"][0]["filters"]
    assert clause == {
        "type": "fields_v2",
        "subtype": "non_custom",
        "field": "stage",
        "overwrite": {"type": "stage"},
        "condition": "time_in_stage",
        "value": "a82570ce-c4e4-4548-bbb7-8b77f94bbb9a",
        "comparison_condition": ">",
        "comparison_value": "7",
        "comparison_type": "days",
    }


def test_create_filter_group_invalid_filter_spec_raises(
    no_existing_views, filter_schema
):
    with pytest.raises(PlanError, match="invalid filter spec"):
        sv_planners.plan_create_filter_group(
            "patients", {"name": "Bad", "config": {"all": []}}
        )


def test_create_filter_group_raw_filter_config_normalized(no_existing_views):
    raw = {
        "query": [
            {"filters": [{"field": "name", "condition": "=", "value": "x"}]},
        ]
    }
    plan = sv_planners.plan_create_filter_group(
        "patients", {"name": "Raw", "config": raw}
    )
    (op,) = plan.operations
    cfg = op.payload["config"]
    assert cfg["query"][0]["id"] == "query-0"


def test_create_filter_group_invalid_raw_config_raises(no_existing_views):
    raw = {"query": [{"filters": [{"condition": "is_blank", "value": None}]}]}
    with pytest.raises(PlanError, match="invalid filter config"):
        sv_planners.plan_create_filter_group("patients", {"name": "Bad", "config": raw})


def test_create_filter_group_object_not_found(monkeypatch):
    def _raise(_api_name):
        raise LookupError("no such object")

    monkeypatch.setattr(sv_planners, "resolve_object_id", _raise)
    with pytest.raises(PlanError, match="not found"):
        sv_planners.plan_create_filter_group("bogus", {"name": "X"})


def test_create_filter_group_collision_raises(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners,
        "list_saved_views",
        lambda *a, **k: [{"id": FG_ID, "name": "Big deals"}],
    )
    with pytest.raises(PlanError, match="already exists"):
        sv_planners.plan_create_filter_group("patients", {"name": "Big deals"})


def test_create_filter_group_normalizes_template_sharing(no_existing_views):
    sharing = {
        "private": False,
        "all_team_members": 1,
        "roles": {
            "view": [],
            "edit": [],
            "admin": [{"id": ADMIN_ID, "display_name": "Admin"}],
        },
        "team_members": {"view": [], "edit": [], "admin": []},
    }
    plan = sv_planners.plan_create_filter_group(
        "patients", {"name": "X", "sharing_settings": sharing}
    )
    (op,) = plan.operations
    assert op.payload["sharing_settings"]["roles"]["admin"] == [ADMIN_ID]


def test_create_filter_group_hidden_flag(no_existing_views):
    plan = sv_planners.plan_create_filter_group(
        "patients", {"name": "Hidden one", "hidden": True}
    )
    (op,) = plan.operations
    assert op.payload["hidden"] is True
    assert op.preview["hidden"] is True


# ---------------------------------------------------------------------------
# Filter groups — update
# ---------------------------------------------------------------------------


def test_update_filter_group_diffs_name(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    plan = sv_planners.plan_update_filter_group(
        "patients", "Big deals", {"name": "Bigger deals"}
    )
    (op,) = plan.operations
    assert op.action == "update"
    assert op.payload == {"name": "Bigger deals"}
    assert op.existing_uuid == FG_ID
    assert op.parent_object_uuid == OBJ_ID


def test_update_filter_group_diffs_config(monkeypatch, filter_schema):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    changes = {"config": {"all": [{"field": "mrn", "op": "=", "value": "A-2"}]}}
    plan = sv_planners.plan_update_filter_group("patients", "Big deals", changes)
    (op,) = plan.operations
    assert op.action == "update"
    assert "config" in op.payload
    assert op.payload["config"]["query"][0]["filters"][0]["value"] == "A-2"


def test_update_filter_group_diffs_hidden(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    plan = sv_planners.plan_update_filter_group(
        "patients", "Big deals", {"hidden": True}
    )
    (op,) = plan.operations
    assert op.payload == {"hidden": True}


def test_update_filter_group_diffs_owner_unwraps_read_shape(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    existing = _existing("config", owner={"id": "user-1", "display_name": "Bob"})
    monkeypatch.setattr(sv_planners, "find_saved_view", lambda *a, **k: existing)

    # Same owner (unwrapped) -> no change.
    plan = sv_planners.plan_update_filter_group(
        "patients", "Big deals", {"owner": "user-1"}
    )
    (op,) = plan.operations
    assert op.action == "skip"

    # Different owner -> diffed and included, as the bare id.
    plan2 = sv_planners.plan_update_filter_group(
        "patients", "Big deals", {"owner": "user-2"}
    )
    (op2,) = plan2.operations
    assert op2.payload == {"owner": "user-2"}


def test_update_filter_group_diffs_sharing_settings(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    changes = {
        "sharing_settings": {
            "private": True,
            "all_team_members": 0,
            "roles": {"view": [], "edit": [], "admin": [ADMIN_ID]},
            "team_members": {"view": [], "edit": [], "admin": []},
        }
    }
    plan = sv_planners.plan_update_filter_group("patients", "Big deals", changes)
    (op,) = plan.operations
    assert op.payload["sharing_settings"]["private"] is True


def test_update_filter_group_no_changes_is_skip(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    plan = sv_planners.plan_update_filter_group(
        "patients", "Big deals", {"name": "Big deals", "hidden": False}
    )
    (op,) = plan.operations
    assert op.action == "skip"
    assert plan.summary.startswith("No changes")


def test_update_filter_group_not_found_raises(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)

    def _raise(*a, **k):
        raise LookupError("no saved view named 'Nope' on 'patients'")

    monkeypatch.setattr(sv_planners, "find_saved_view", _raise)
    with pytest.raises(PlanError, match="no saved view named"):
        sv_planners.plan_update_filter_group("patients", "Nope", {"name": "Y"})


# ---------------------------------------------------------------------------
# Filter groups — delete
# ---------------------------------------------------------------------------


def test_delete_filter_group(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("config")
    )
    plan = sv_planners.plan_delete_filter_group("patients", "Big deals")
    (op,) = plan.operations
    assert op.action == "delete" and op.kind == "filter_group"
    assert op.existing_uuid == FG_ID
    assert op.parent_object_uuid == OBJ_ID


# ---------------------------------------------------------------------------
# Quick filters — create/update/delete (same shape, "filters" key)
# ---------------------------------------------------------------------------


def test_create_quick_filter_minimal_payload(no_existing_views):
    plan = sv_planners.plan_create_quick_filter("patients", {"name": "Open"})
    (op,) = plan.operations
    assert op.kind == "quick_filter" and op.action == "create"
    assert op.payload == {
        "name": "Open",
        "filters": {},
        "sharing_settings": _default_sharing(),
    }
    assert "owner" not in op.payload


def test_create_quick_filter_renders_filter_spec_via_dsl(
    no_existing_views, filter_schema
):
    plan = sv_planners.plan_create_quick_filter(
        "patients",
        {
            "name": "MRN chip",
            "filters": {"all": [{"field": "mrn", "op": "=", "value": "A-1"}]},
        },
    )
    (op,) = plan.operations
    fc = op.payload["filters"]
    assert fc["invalid"] is False
    (clause,) = fc["query"][0]["filters"]
    assert clause["field"] == f'"custom"::{MRN_FIELD_ID}'


def test_create_quick_filter_collision_raises(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners,
        "list_saved_views",
        lambda *a, **k: [{"id": QF_ID, "name": "Open"}],
    )
    with pytest.raises(PlanError, match="already exists"):
        sv_planners.plan_create_quick_filter("patients", {"name": "Open"})


def test_update_quick_filter_diffs_filters(monkeypatch, filter_schema):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("filters")
    )
    changes = {"filters": {"all": [{"field": "mrn", "op": "=", "value": "A-2"}]}}
    plan = sv_planners.plan_update_quick_filter("patients", "Open", changes)
    (op,) = plan.operations
    assert op.action == "update"
    assert "filters" in op.payload


def test_update_quick_filter_no_changes_is_skip(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("filters")
    )
    plan = sv_planners.plan_update_quick_filter(
        "patients", "Open", {"name": "Big deals"}
    )
    (op,) = plan.operations
    assert op.action == "skip"


def test_update_quick_filter_not_found_raises(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)

    def _raise(*a, **k):
        raise LookupError("no saved view named 'Nope' on 'patients'")

    monkeypatch.setattr(sv_planners, "find_saved_view", _raise)
    with pytest.raises(PlanError, match="no saved view named"):
        sv_planners.plan_update_quick_filter("patients", "Nope", {"name": "Y"})


def test_delete_quick_filter(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("filters")
    )
    plan = sv_planners.plan_delete_quick_filter("patients", "Open")
    (op,) = plan.operations
    assert op.action == "delete" and op.kind == "quick_filter"
    assert op.existing_uuid == FG_ID


# ---------------------------------------------------------------------------
# Quick filters — apply-to-roles / apply-to-users
# ---------------------------------------------------------------------------


def test_apply_quick_filter_requires_a_target():
    with pytest.raises(PlanError, match="at least one"):
        sv_planners.plan_apply_quick_filter("patients", "Open", None, None)


def test_apply_quick_filter_roles_and_users(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("filters", id=QF_ID)
    )
    plan = sv_planners.plan_apply_quick_filter(
        "patients", "Open", role_ids=["r1"], user_ids=["u1"]
    )
    roles_op, users_op = plan.operations
    assert roles_op.payload == {"target": "roles", "ids": ["r1"]}
    assert roles_op.existing_uuid == QF_ID
    assert roles_op.key.endswith(".roles")
    assert users_op.payload == {"target": "users", "ids": ["u1"]}
    assert users_op.key.endswith(".users")


def test_apply_quick_filter_roles_only(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("filters", id=QF_ID)
    )
    plan = sv_planners.plan_apply_quick_filter("patients", "Open", role_ids=["r1"])
    (op,) = plan.operations
    assert op.payload["target"] == "roles"


# ---------------------------------------------------------------------------
# Column templates — create/update/delete
# ---------------------------------------------------------------------------


def test_create_column_template_minimal_payload(no_existing_views):
    plan = sv_planners.plan_create_column_template(
        "patients", {"name": "Ops columns", "configuration_json": {"cols": ["a"]}}
    )
    (op,) = plan.operations
    assert op.kind == "column_template" and op.action == "create"
    assert op.payload == {
        "name": "Ops columns",
        "configuration_json": {"cols": ["a"]},
        "sharing_settings": _default_sharing(),
    }
    assert "owner" not in op.payload


def test_create_column_template_configuration_json_is_opaque_passthrough(
    no_existing_views,
):
    """No filter DSL applies here — whatever blob is given passes through
    byte-for-byte."""
    blob = {"columns": [{"field": "name", "width": 120}], "order": ["name"]}
    plan = sv_planners.plan_create_column_template(
        "patients", {"name": "X", "configuration_json": blob}
    )
    (op,) = plan.operations
    assert op.payload["configuration_json"] == blob


def test_create_column_template_collision_raises(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners,
        "list_saved_views",
        lambda *a, **k: [{"id": CT_ID, "name": "Ops columns"}],
    )
    with pytest.raises(PlanError, match="already exists"):
        sv_planners.plan_create_column_template("patients", {"name": "Ops columns"})


def test_update_column_template_diffs_configuration_json(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("configuration_json")
    )
    changes = {"configuration_json": {"cols": ["b"]}}
    plan = sv_planners.plan_update_column_template("patients", "Big deals", changes)
    (op,) = plan.operations
    assert op.payload == {"configuration_json": {"cols": ["b"]}}


def test_update_column_template_no_changes_is_skip(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("configuration_json")
    )
    plan = sv_planners.plan_update_column_template(
        "patients", "Big deals", {"name": "Big deals"}
    )
    (op,) = plan.operations
    assert op.action == "skip"


def test_delete_column_template(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners, "find_saved_view", lambda *a, **k: _existing("configuration_json")
    )
    plan = sv_planners.plan_delete_column_template("patients", "Big deals")
    (op,) = plan.operations
    assert op.action == "delete" and op.kind == "column_template"


# ---------------------------------------------------------------------------
# Column templates — apply-to-roles / apply-to-users / apply-to-permission-groups
# ---------------------------------------------------------------------------


def test_apply_column_template_requires_a_target():
    with pytest.raises(PlanError, match="at least one"):
        sv_planners.plan_apply_column_template("patients", "Ops columns")


def test_apply_column_template_all_three_targets(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners,
        "find_saved_view",
        lambda *a, **k: _existing("configuration_json", id=CT_ID),
    )
    plan = sv_planners.plan_apply_column_template(
        "patients",
        "Ops columns",
        role_ids=["r1"],
        user_ids=["u1"],
        permission_group_ids=["g1"],
    )
    roles_op, users_op, groups_op = plan.operations
    assert roles_op.payload == {"target": "roles", "ids": ["r1"]}
    assert users_op.payload == {"target": "users", "ids": ["u1"]}
    assert groups_op.payload == {"target": "permission_groups", "ids": ["g1"]}
    assert groups_op.key.endswith(".permission_groups")
    for op in plan.operations:
        assert op.existing_uuid == CT_ID


def test_apply_column_template_permission_groups_only(monkeypatch):
    monkeypatch.setattr(sv_planners, "resolve_object_id", lambda a: OBJ_ID)
    monkeypatch.setattr(
        sv_planners,
        "find_saved_view",
        lambda *a, **k: _existing("configuration_json", id=CT_ID),
    )
    plan = sv_planners.plan_apply_column_template(
        "patients", "Ops columns", permission_group_ids=["g1"]
    )
    (op,) = plan.operations
    assert op.payload["target"] == "permission_groups"


# ---------------------------------------------------------------------------
# Apply dispatch (respx) — plans.py routes each op kind to the right endpoint
# ---------------------------------------------------------------------------


@respx.mock
def test_apply_create_filter_group_posts_to_collection():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/filter-groups"
    ).mock(return_value=httpx.Response(201, json={"id": FG_ID}))
    op = PlanOperation(
        action="create",
        kind="filter_group",
        key="patients.Big deals",
        payload={"name": "Big deals", "config": {}, "hidden": False},
        parent_object_uuid=OBJ_ID,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )
    assert route.call_count == 1
    assert result.all_ok
    assert result.results[0].server_uuid == FG_ID


@respx.mock
def test_apply_update_quick_filter_patches_item():
    route = respx.patch(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/quick-filters/{QF_ID}"
    ).mock(return_value=httpx.Response(200, json={"id": QF_ID}))
    op = PlanOperation(
        action="update",
        kind="quick_filter",
        key="patients.Open",
        payload={"name": "Open now"},
        existing_uuid=QF_ID,
        parent_object_uuid=OBJ_ID,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_delete_column_template():
    route = respx.delete(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/columns/{CT_ID}"
    ).mock(return_value=httpx.Response(204))
    op = PlanOperation(
        action="delete",
        kind="column_template",
        key="patients.Ops columns",
        existing_uuid=CT_ID,
        parent_object_uuid=OBJ_ID,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_apply_quick_filter_to_roles():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/quick-filters/{QF_ID}/apply-to-roles"
    ).mock(return_value=httpx.Response(200, json={}))
    op = PlanOperation(
        action="apply",
        kind="quick_filter",
        key="patients.Open.roles",
        payload={"target": "roles", "ids": ["r1", "r2"]},
        existing_uuid=QF_ID,
        parent_object_uuid=OBJ_ID,
    )
    plan_tools.apply_plan(Plan.build(env="testenv", summary="t", operations=[op]))
    assert route.call_count == 1
    assert route.calls[0].request.content == b'{"role_ids":["r1","r2"]}'


@respx.mock
def test_apply_column_template_to_permission_groups():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/columns/{CT_ID}"
        "/apply-to-permission-groups"
    ).mock(return_value=httpx.Response(200, json={}))
    op = PlanOperation(
        action="apply",
        kind="column_template",
        key="patients.Ops columns.permission_groups",
        payload={"target": "permission_groups", "ids": ["g1"]},
        existing_uuid=CT_ID,
        parent_object_uuid=OBJ_ID,
    )
    plan_tools.apply_plan(Plan.build(env="testenv", summary="t", operations=[op]))
    assert route.call_count == 1
    assert route.calls[0].request.content == b'{"permission_group_ids":["g1"]}'


@respx.mock
def test_apply_saved_view_missing_parent_object_uuid_fails_cleanly():
    """A malformed op (planning bug, not a live-API condition) is recorded as
    a failed OperationResult rather than raising — apply_plan's contract."""
    op = PlanOperation(
        action="create",
        kind="filter_group",
        key="patients.Bad",
        payload={"name": "Bad"},
        parent_object_uuid=None,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )
    (r,) = result.results
    assert r.status == "failed"
    assert "no parent object id" in r.message


@respx.mock
def test_apply_saved_view_unknown_apply_target_fails_cleanly():
    op = PlanOperation(
        action="apply",
        kind="quick_filter",
        key="patients.Open.bogus",
        payload={"target": "bogus", "ids": ["x"]},
        existing_uuid=QF_ID,
        parent_object_uuid=OBJ_ID,
    )
    result = plan_tools.apply_plan(
        Plan.build(env="testenv", summary="t", operations=[op])
    )
    (r,) = result.results
    assert r.status == "failed"
    assert "unknown target" in r.message

"""A filter spec in a dashlet's `config.filters` compiles at plan time, in
`dashboards create` and `update`; wire filters, as a live read returns them,
pass through untouched and never reach the schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from kizen_builder import filtering
from kizen_builder.tools.planners import dashboards as dash_planners
from kizen_builder.tools.plans import PlanError
from tests.filter_stub import POLICIES_OBJ_ID, StubClient

DASH_ID = "5abc2e37-33d6-4f9c-9c70-d5fd74a2bea5"
DL_ID = "45580bf3-1111-4111-8111-111111111111"
MINE = {"all": [{"field": "owner", "op": "is_me"}]}
OWNER_IS_ME = {
    "type": "fields",
    "subtype": "non_custom",
    "field": "owner",
    "condition": "=",
    "value": "is_me",
}
# A dashlet's filters as `dashboards get --raw` returns them.
WIRE = {
    "custom_filters": {
        "and": True,
        "query": [{"id": "query-0", "and": True, "filters": [OWNER_IS_ME]}],
    }
}


class _NoSchema:
    def __getattr__(self, name):
        raise AssertionError(f"wire filters must not touch the schema ({name})")


@pytest.fixture
def schema():
    filtering.set_default_client(StubClient())
    yield
    filtering.set_default_client(None)


@pytest.fixture
def no_schema():
    filtering.set_default_client(_NoSchema())
    yield
    filtering.set_default_client(None)


@pytest.fixture
def no_dashboards(monkeypatch):
    monkeypatch.setattr(dash_planners, "list_dashboards", lambda: [])
    monkeypatch.setattr(dash_planners, "default_sharing_settings", lambda: {})


@pytest.fixture
def live_homepage(monkeypatch):
    detail = {
        "id": DASH_ID,
        "raw": {"id": DASH_ID, "type": "homepage", "dashlets": [{"id": DL_ID}]},
    }
    monkeypatch.setattr(dash_planners, "get_dashboard_detail", lambda d: detail)


def _homepage(*dashlets: dict) -> dict:
    return {
        "api_name": "rep_home",
        "name": "Rep Home",
        "type": "homepage",
        "dashlets": list(dashlets),
    }


def _dashlet(filters, *, custom_object=POLICIES_OBJ_ID, **config) -> dict:
    return {
        "name": "My deals",
        "custom_object": custom_object,
        "config": {"entity_type": "custom_object", **config, "filters": filters},
    }


def _query(op) -> list:
    return op.payload["config"]["filters"]["custom_filters"]["query"][0]["filters"]


def test_create_compiles_a_spec_against_the_dashlet_custom_object(
    schema, no_dashboards
):
    plan = dash_planners.plan_create_dashboard(_homepage(_dashlet(MINE)))
    _, dashlet = plan.operations
    assert _query(dashlet) == [OWNER_IS_ME]
    assert dashlet.payload["config"]["entity_type"] == "custom_object"


def test_create_falls_back_to_the_config_object_id(schema, no_dashboards):
    dl = _dashlet(MINE, custom_object=None, object_id=POLICIES_OBJ_ID)
    _, dashlet = dash_planners.plan_create_dashboard(_homepage(dl)).operations
    assert _query(dashlet) == [OWNER_IS_ME]


def test_a_spec_with_no_object_to_resolve_against_is_a_plan_error(
    schema, no_dashboards
):
    with pytest.raises(PlanError) as e:
        dash_planners.plan_create_dashboard(
            _homepage(_dashlet(MINE, custom_object=None))
        )
    assert str(e.value) == (
        "dashlet 'My deals': a filter spec needs the dashlet's custom_object "
        "to resolve field names against"
    )


def test_a_spec_on_a_non_custom_object_dashlet_is_a_plan_error(
    no_schema, no_dashboards
):
    dl = _dashlet(MINE)
    dl["config"]["entity_type"] = "pipeline"
    with pytest.raises(PlanError) as e:
        dash_planners.plan_create_dashboard(_homepage(dl))
    assert str(e.value) == (
        "dashlet 'My deals': entity_type 'pipeline' has no record filter. A "
        "filter spec works on table_of_records, field_breakdown, field_sum, "
        "field_range_breakdown and pivot_table."
    )


def test_a_bad_field_names_the_dashlet_and_the_dsl_message(schema, no_dashboards):
    bad = {"all": [{"field": "nope", "op": "is_me"}]}
    with pytest.raises(
        PlanError,
        match="^dashlet 'My deals': invalid filter spec: Unknown field 'nope'",
    ):
        dash_planners.plan_create_dashboard(_homepage(_dashlet(bad)))


def test_update_compiles_specs_on_existing_and_new_dashlets(schema, live_homepage):
    spec = _homepage({**_dashlet(MINE), "id": DL_ID}, _dashlet(MINE))
    _, existing, new = dash_planners.plan_update_dashboard(DASH_ID, spec).operations
    assert (existing.action, new.action) == ("update", "create")
    assert _query(existing) == [OWNER_IS_ME]
    assert _query(new) == [OWNER_IS_ME]


@pytest.mark.parametrize(
    "filters",
    [
        WIRE,
        {"custom_filters": {"and": True, "query": []}},
        {"in_group_ids": ["a0000000-0000-4000-8000-000000000001"]},
    ],
)
def test_update_passes_wire_filters_through_byte_identical(
    no_schema, live_homepage, filters
):
    read_back = {**_dashlet(copy.deepcopy(filters)), "id": DL_ID}
    sent = json.dumps(read_back["config"])
    _, op = dash_planners.plan_update_dashboard(
        DASH_ID, _homepage(read_back)
    ).operations
    assert json.dumps(op.payload["config"]) == sent


def test_create_passes_wire_filters_through(no_schema, no_dashboards):
    _, op = dash_planners.plan_create_dashboard(_homepage(_dashlet(WIRE))).operations
    assert op.payload["config"]["filters"] == WIRE


# ---------------------------------------------------------------------------
# The doc's rep_home example plans offline
# ---------------------------------------------------------------------------

DEALS_ID = "fb000000-0000-4000-8000-0000000000d0"
DEAL_FIELDS = {
    "name": ("text", "fb000000-0000-4000-8000-000000000010", True, []),
    "owner": ("team_selector", "fb000000-0000-4000-8000-000000000011", True, []),
    "health": (
        "dropdown",
        "fb000000-0000-4000-8000-000000000003",
        False,
        [
            {"id": "fb000000-0000-4000-8000-000000000100", "name": "On Track"},
            {"id": "fb000000-0000-4000-8000-000000000101", "name": "Needs Attention"},
            {"id": "fb000000-0000-4000-8000-000000000102", "name": "At Risk"},
        ],
    ),
}


class _Deals:
    def custom_object(self, ref):
        return {"id": DEALS_ID, "api_name": "deals", "name": "deals"}

    def get_field(self, obj_id, name):
        if name not in DEAL_FIELDS:
            return None
        field_type, field_id, is_default, options = DEAL_FIELDS[name]
        return {
            "name": name,
            "id": field_id,
            "field_type": field_type,
            "is_default": is_default,
            "options": options,
        }


def test_rep_home_example_plans_with_each_dashlet_filtered_to_the_viewer(
    no_dashboards,
):
    path = (
        Path(__file__).parent / "fixtures/examples/first_build/rep_home.dashboard.json"
    )
    text = path.read_text()
    for token, value in {
        "<deals_object_uuid>": DEALS_ID,
        "<deals.name_field_uuid>": DEAL_FIELDS["name"][1],
        "<deals.health_field_uuid>": DEAL_FIELDS["health"][1],
    }.items():
        text = text.replace(f'"{token}"', json.dumps(value))
    assert "<" not in text

    filtering.set_default_client(_Deals())
    try:
        board, *dashlets = dash_planners.plan_create_dashboard(
            json.loads(text)
        ).operations
    finally:
        filtering.set_default_client(None)

    assert board.payload["type"] == "homepage"
    assert [_query(d) for d in dashlets] == [
        [OWNER_IS_ME],
        [OWNER_IS_ME],
        [
            OWNER_IS_ME,
            {
                "type": "fields_v2",
                "subtype": "custom",
                "field": f'"custom"::{DEAL_FIELDS["health"][1]}',
                "condition": "=",
                "value": "fb000000-0000-4000-8000-000000000102",
            },
        ],
    ]

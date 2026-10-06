"""Tests for the dashlet config builders added to cover more chart types.

Pins wire-format rules confirmed live 2026-07-20 against real
"Kitchen Sink Homepage" / "Email Marketing Hub" / "Activity Dashboard"
dashboards (see `kizen docs show reference`, "Dashboards / Homepages API"):
  * a dashlet with no filter must send NO_FILTER, not {} (400s);
  * pipeline/activity/email metric dashlets share one config envelope per
    entity_type, varying only report_type/chart_type/metric_type/frequency;
  * dashboard_type=generic_dashboard and dashboard_type=homepage are distinct
    list queries that must be merged to see everything;
  * entity_type=custom_object dashlets 400 on a generic_dashboard, so the
    planner must catch that before apply.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from kizen_builder.tools import dashboards as dash_tools
from kizen_builder.tools.dashboards import (
    NO_FILTER,
    activity_metric_config,
    email_metric_config,
    field_range_breakdown_config,
    field_sum_config,
    html_button_log_activity,
    html_button_url,
    html_dashlet_config,
    html_raw_block,
    html_text_block,
    marketing_metric_config,
    pipeline_metric_config,
    scheduled_activities_calendar_config,
    scheduled_activities_config,
    table_of_records_config,
)
from kizen_builder.tools.planners import dashboards as dash_planners
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL

OBJECT_ID = "18a93b82-9925-4fd8-9c18-7d291412f0fa"
FIELD_ID = "ce136267-283e-4c3f-9d24-7184c66de6d8"
PIPELINE_ID = "8c3f0149-6915-4370-940f-2e61848d6842"
ACTIVITY_ID = "0c01c7a5-bc95-4d49-8d93-8a33e9ab3950"


# ---------------------------------------------------------------------------
# NO_FILTER default
# ---------------------------------------------------------------------------


def test_no_filter_default_is_not_empty_dict():
    cfg = table_of_records_config(OBJECT_ID, columns=[])
    assert cfg["filters"] == NO_FILTER
    assert cfg["filters"] != {}


def test_explicit_filter_overrides_default():
    custom = {"custom_filters": {"and": True, "query": [{"field": "x"}]}}
    cfg = field_sum_config(OBJECT_ID, FIELD_ID, filters=custom)
    assert cfg["filters"] == custom


# ---------------------------------------------------------------------------
# field_range_breakdown_config
# ---------------------------------------------------------------------------


def test_field_range_breakdown_config_shape():
    cfg = field_range_breakdown_config(
        OBJECT_ID,
        FIELD_ID,
        buckets=[{"min": 0, "max": 10, "label": "Low"}],
    )
    assert cfg["chart_type"] == "bar"
    assert cfg["metric_type"] == "fields_range_breakdown"
    assert cfg["report_type"] == "field_metrics"
    bucket = cfg["metric_type_extra_info"]["fields_range_breakdown"]["buckets"][0]
    assert bucket["min"] == {"value": 0, "operator": ">="}
    assert bucket["max"] == {"value": 10, "operator": "<="}
    assert bucket["bucket_label"] == "Low"
    assert "id" in bucket


# ---------------------------------------------------------------------------
# pipeline_metric_config
# ---------------------------------------------------------------------------


def test_pipeline_metric_config_line_requires_frequency_key():
    cfg = pipeline_metric_config(PIPELINE_ID, "records_won", "line", frequency="month")
    assert cfg["entity_type"] == "pipeline"
    assert cfg["frequency"] == "month"
    assert cfg["inverse"] is False
    assert cfg["pipeline_level_of_detail"] == "sum_of_stages"


def test_pipeline_metric_config_horizontal_bar_omits_frequency_and_inverse():
    cfg = pipeline_metric_config(
        PIPELINE_ID, "opportunity_conversion", "horizontal_bar"
    )
    assert "frequency" not in cfg
    assert "inverse" not in cfg


def test_pipeline_metric_config_records_to_include_optional():
    cfg = pipeline_metric_config(
        PIPELINE_ID,
        "pipeline_values_over_time",
        "line",
        metric_type="records_value",
        frequency="day",
        records_to_include="open",
    )
    assert cfg["records_to_include"] == "open"


# ---------------------------------------------------------------------------
# activity_metric_config / scheduled_activities_config
# ---------------------------------------------------------------------------


def test_activity_metric_config_shape():
    cfg = activity_metric_config(ACTIVITY_ID, frequency="week")
    assert cfg["entity_type"] == "activity"
    assert cfg["object_id"] == ACTIVITY_ID
    assert cfg["report_type"] == "records_added"
    assert cfg["frequency"] == "week"


def test_scheduled_activities_config_chart_type_is_empty_string():
    cfg = scheduled_activities_config(ACTIVITY_ID, time_period="month")
    assert cfg["chart_type"] == ""
    assert cfg["report_type"] == "scheduled_activities"
    assert cfg["fe_extra_info"]["scheduled_activities_config"]["time_period"] == "month"


def test_scheduled_activities_calendar_config_is_a_distinct_report_type():
    cfg = scheduled_activities_calendar_config(
        ACTIVITY_ID, showing_only_working_days=False
    )
    assert cfg["report_type"] == "scheduled_activities_calendar"
    assert cfg["chart_type"] == "calendar"
    assert cfg["showing_only_working_days"] is False
    assert "fe_extra_info" not in cfg


# ---------------------------------------------------------------------------
# email_metric_config
# ---------------------------------------------------------------------------


def test_email_metric_config_is_minimal_shape():
    cfg = email_metric_config("email_sent", "numeric", historical=True)
    assert cfg == {
        "stages": [],
        "inverse": False,
        "chart_type": "numeric",
        "historical": True,
        "entity_type": "email",
        "metric_type": "records_number",
        "report_type": "email_sent",
    }


def test_email_metric_config_line_includes_frequency():
    cfg = email_metric_config("email_interaction_stats", "line", frequency="week")
    assert cfg["frequency"] == "week"


# ---------------------------------------------------------------------------
# marketing_metric_config
# ---------------------------------------------------------------------------


def test_marketing_metric_config_uses_plural_object_ids():
    cfg = marketing_metric_config(
        [OBJECT_ID], "leads_added", "numeric", historical=True
    )
    assert cfg["object_ids"] == [OBJECT_ID]
    assert "object_id" not in cfg
    assert cfg["historical"] is True


def test_marketing_metric_config_line_requires_frequency_no_historical():
    cfg = marketing_metric_config(
        [OBJECT_ID], "lead_source_breakdown_over_time", "line", frequency="week"
    )
    assert cfg["frequency"] == "week"
    assert "historical" not in cfg
    assert cfg["lead_sources"] == []


def test_marketing_metric_config_by_source_gets_lead_sources_key():
    cfg = marketing_metric_config([OBJECT_ID], "leads_added_by_source", "donut")
    assert cfg["lead_sources"] == []


def test_marketing_metric_config_plain_leads_added_has_no_lead_sources_key():
    cfg = marketing_metric_config(
        [OBJECT_ID], "leads_added", "numeric", historical=False
    )
    assert "lead_sources" not in cfg


# ---------------------------------------------------------------------------
# html_dashlet_config
# ---------------------------------------------------------------------------


def test_html_dashlet_config_minimal_tree():
    cfg = html_dashlet_config("<p>hello</p>")
    assert cfg["chart_type"] == "html"
    assert cfg["entity_type"] == "static_content"
    assert cfg["report_type"] == "html"
    content = cfg["content"]
    assert content["ROOT"]["type"]["resolved_name"] == "Root"
    text_nodes = [n for n in content.values() if n["type"]["resolved_name"] == "Text"]
    assert len(text_nodes) == 1
    assert text_nodes[0]["custom"]["text"] == "<p>hello</p>"


def test_html_dashlet_config_multi_row_buttons_and_transparent_background():
    rows = [
        [html_text_block("<p>merge field row</p>")],
        [
            html_button_url("Visit", "https://example.com"),
            html_button_log_activity("Log It", ACTIVITY_ID),
            html_text_block("<p>third column</p>"),
        ],
    ]
    cfg = html_dashlet_config(rows, background_color="transparent")
    content = cfg["content"]

    assert content["ROOT"]["props"]["background_color"] == "rgba(0,0,0,0)"

    rows_nodes = [n for n in content.values() if n["type"]["resolved_name"] == "Row"]
    assert len(rows_nodes) == 2
    one_col_row = next(r for r in rows_nodes if r["props"]["columns"] == [1.0])
    three_col_row = next(r for r in rows_nodes if len(r["props"]["columns"]) == 3)
    assert len(one_col_row["linked_nodes"]) == 1
    assert len(three_col_row["linked_nodes"]) == 3
    assert three_col_row["props"]["columns"] == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    buttons = [n for n in content.values() if n["type"]["resolved_name"] == "Button"]
    assert len(buttons) == 2
    url_button = next(b for b in buttons if b["props"]["action"] == "url")
    assert url_button["props"]["url"] == "https://example.com"
    log_button = next(b for b in buttons if b["props"]["action"] == "log-activity")
    assert log_button["props"]["activity_id"] == ACTIVITY_ID
    assert "url" not in log_button["props"] or log_button["props"]["url"] == ""


def test_html_dashlet_config_raw_html_block_is_distinct_from_text():
    rows = [[html_text_block("<p>rich</p>"), html_raw_block("<div>raw</div>")]]
    cfg = html_dashlet_config(rows)
    content = cfg["content"]

    text_node = next(
        n for n in content.values() if n["type"]["resolved_name"] == "Text"
    )
    html_node = next(
        n for n in content.values() if n["type"]["resolved_name"] == "HTMLBlock"
    )
    assert text_node["custom"]["text"] == "<p>rich</p>"
    assert "html_content" not in text_node["props"]
    assert html_node["props"]["html_content"] == "<div>raw</div>"
    assert html_node["custom"] == {}


# ---------------------------------------------------------------------------
# list_dashboards() merges generic_dashboard + homepage
# ---------------------------------------------------------------------------


def test_list_dashboards_merges_generic_and_homepage(monkeypatch):
    calls = []

    def fake_list_dashboards(
        client, dashboard_type="generic_dashboard", custom_object_id=None
    ):
        calls.append(dashboard_type)
        if dashboard_type == "generic_dashboard":
            return [{"id": "1", "api_name": "a_dashboard"}]
        if dashboard_type == "homepage":
            return [{"id": "2", "api_name": "a_homepage"}]
        return []

    monkeypatch.setattr(dash_tools.dash_api, "list_dashboards", fake_list_dashboards)
    monkeypatch.setattr(dash_tools, "load_env_config", lambda: object())
    monkeypatch.setattr(dash_tools, "KizenClient", lambda config: _NullClientCtx())

    results = dash_tools.list_dashboards()
    api_names = {r["api_name"] for r in results}
    assert api_names == {"a_dashboard", "a_homepage"}
    assert set(calls) == {"generic_dashboard", "homepage"}


class _NullClientCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# admin_role_id reads every page of roles
# ---------------------------------------------------------------------------


@respx.mock
def test_admin_role_id_finds_admin_on_page_two():
    admin_id = "00000000-0000-4000-8000-000000000199"
    respx.get(f"{FAKE_BASE_URL}/api/role").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [{"id": "r1", "name": "Sales Rep"}],
                    "next": f"{FAKE_BASE_URL}/api/role?page=2&page_size=200",
                },
            ),
            httpx.Response(
                200, json={"results": [{"id": admin_id, "name": "Admin"}], "next": None}
            ),
        ]
    )

    assert dash_tools.admin_role_id() == admin_id


# ---------------------------------------------------------------------------
# planner guard: entity_type=custom_object dashlets need type=homepage
# ---------------------------------------------------------------------------


@pytest.fixture
def no_dashboards(monkeypatch):
    monkeypatch.setattr(dash_planners, "list_dashboards", lambda: [])
    monkeypatch.setattr(
        dash_planners,
        "default_sharing_settings",
        lambda: {
            "private": False,
            "all_team_members": 1,
            "roles": {"view": [], "edit": [], "admin": []},
            "team_members": {"view": [], "edit": [], "admin": []},
        },
    )


def test_custom_object_dashlet_on_generic_dashboard_errors(no_dashboards):
    spec = {
        "api_name": "d1",
        "name": "D1",
        "type": "generic_dashboard",
        "dashlets": [
            {
                "name": "Sum",
                "layout": {},
                "config": field_sum_config(OBJECT_ID, FIELD_ID),
            }
        ],
    }
    with pytest.raises(PlanError, match="entity_type=custom_object"):
        dash_planners.plan_create_dashboard(spec)


def test_custom_object_dashlet_on_homepage_is_fine(no_dashboards):
    spec = {
        "api_name": "d1",
        "name": "D1",
        "type": "homepage",
        "dashlets": [
            {
                "name": "Sum",
                "layout": {},
                "config": field_sum_config(OBJECT_ID, FIELD_ID),
            }
        ],
    }
    plan = dash_planners.plan_create_dashboard(spec)
    assert len(plan.operations) == 2


def test_pipeline_dashlet_on_generic_dashboard_is_fine(no_dashboards):
    spec = {
        "api_name": "d1",
        "name": "D1",
        "type": "generic_dashboard",
        "dashlets": [
            {
                "name": "Won",
                "layout": {},
                "config": pipeline_metric_config(
                    PIPELINE_ID, "records_won", "line", frequency="month"
                ),
            }
        ],
    }
    plan = dash_planners.plan_create_dashboard(spec)
    assert len(plan.operations) == 2

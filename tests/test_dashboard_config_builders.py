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


# Expected values transcribed from the Kizen web app source (react-app),
# 2026-11-11, snake_cased as a dashlet's content carries them. A new block
# uses the homepage creators (PageBuilder/presets/creator-elements.js:49-57).

# packages/page-builder/nodes/containers/ElementContainer.tsx:14-37
HTML_ELEMENT_CONTAINER = {
    "container_background_color": "inherit",
    "container_background_image_name": "",
    "container_background_position_x": "0%",
    "container_background_position_y": "0%",
    "container_background_size": "auto",
    "container_background_repeat": "repeat",
    "container_border_color": "#4A5660",
    "container_border_style": "solid",
    "container_border_width": "0",
    "container_border_radius": True,
    "container_border_top_left_radius": "4",
    "container_border_top_right_radius": "4",
    "container_border_bottom_left_radius": "4",
    "container_border_bottom_right_radius": "4",
    "container_margin_top": "0",
    "container_margin_right": "0",
    "container_margin_bottom": "0",
    "container_margin_left": "0",
    "container_padding_top": "0",
    "container_padding_right": "0",
    "container_padding_bottom": "0",
    "container_padding_left": "0",
}
HTML_PADDING_10 = {
    "container_padding_top": "10",
    "container_padding_right": "10",
    "container_padding_bottom": "10",
    "container_padding_left": "10",
}
# gen/defaults.ts:25-50, 74-94, 178-187 (DefaultHomepageProps.Section/Row)
HTML_SECTION = {
    **HTML_ELEMENT_CONTAINER,
    "container_background_color": "rgba(0,0,0,0)",
    "container_border_color": "rgba(74,86,96,1)",
    "container_border_radius": False,
    **HTML_PADDING_10,
    "max_width": "1372",
    "width": "100",
    "alignment": "center",
}


def _html_only(content: dict, resolved_name: str) -> list[dict]:
    return [n for n in content.values() if n["type"]["resolved_name"] == resolved_name]


def test_html_dashlet_root_matches_a_new_block():
    # gen/defaults.ts:165-177 (DefaultHomepageProps.Root) plus the column
    # count the builder writes on open (HomepageBuilder.tsx:101,
    # PageBuilderCanvas.jsx:73-85).
    root = html_dashlet_config("<p>x</p>")["content"]["ROOT"]["props"]
    assert root == {
        "background_color": "rgba(255,255,255,1)",
        "color": "rgba(74,86,96,1)",
        "font_family": "Arial",
        "font_size": "14",
        "link_color": "rgba(82,142,249,1)",
        "line_height": "1.25",
        "height": 335,
        "max_width": 1372,
        "has_shadow": True,
        "tablet_break": "768",
        "mobile_break": "414",
        "columns": 6,
    }


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("transparent", "rgba(0,0,0,0)"),
        ("#123456", "#123456"),
        ("", ""),
    ],
)
def test_html_dashlet_background_color_given_is_sent(given, sent):
    root = html_dashlet_config("<p>x</p>", background_color=given)["content"]["ROOT"]
    assert root["props"]["background_color"] == sent


def test_html_dashlet_nodes_match_the_block_builder_creation_defaults():
    content = html_dashlet_config(
        [
            [html_text_block("<p>t</p>"), html_raw_block("<div>r</div>")],
            [
                html_button_url("Go", "https://x"),
                html_button_log_activity("Log", ACTIVITY_ID),
            ],
        ]
    )["content"]

    (section,) = _html_only(content, "Section")
    assert section["props"] == HTML_SECTION
    for row_node in _html_only(content, "Row"):
        assert row_node["props"] == {"columns": [0.5, 0.5], **HTML_SECTION}
    # TextContainer.tsx:19-25 — no margins
    (text,) = _html_only(content, "Text")
    assert text["props"] == {**HTML_ELEMENT_CONTAINER, **HTML_PADDING_10}
    # HTMLBlockContainer.tsx:15-22
    (raw,) = _html_only(content, "HTMLBlock")
    assert raw["props"] == {
        **HTML_ELEMENT_CONTAINER,
        **HTML_PADDING_10,
        "html_content": "<div>r</div>",
    }
    # Button/props.ts:10-42 (DEFAULT_STATIC_PAGE_BUTTON_PROPS) + ButtonContainer.tsx:22-28
    button_base = {
        "color": "#4BC7B4",
        "text_color": "#FFFFFF",
        "border_size": "0",
        "border_radius": "8",
        "border_color": "#2E373E",
        "padding_top": "10",
        "padding_right": "20",
        "padding_bottom": "10",
        "padding_left": "20",
        "font_size": "12",
        "text_styles": [],
        "alignment": "center",
        "font_family": "Arial",
        **HTML_ELEMENT_CONTAINER,
        "container_padding_top": "10",
        "container_padding_right": "0",
        "container_padding_bottom": "10",
        "container_padding_left": "0",
        "enable_recaptcha": False,
    }
    buttons = {b["props"]["action"]: b["props"] for b in _html_only(content, "Button")}
    assert buttons["url"] == {
        **button_base,
        "label": "Go",
        "action": "url",
        "url": "https://x",
    }
    assert buttons["log-activity"] == {
        **button_base,
        "label": "Log",
        "action": "log-activity",
        "url": "",
        "activity_id": ACTIVITY_ID,
    }


@pytest.mark.parametrize("color", ["#ABCDEF", ""])
def test_html_dashlet_button_color_given_is_sent(color):
    content = html_dashlet_config(
        [
            [
                html_button_url("Go", "https://x", color=color),
                html_button_log_activity("Log", ACTIVITY_ID, color=color),
            ]
        ]
    )["content"]
    assert [b["props"]["color"] for b in _html_only(content, "Button")] == [
        color,
        color,
    ]


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


def test_default_sharing_settings_is_the_ui_private_default(monkeypatch):
    """One helper, one default: private, no access for all team members, the
    Admin role as admin — what the UI's create and save dialogs start from."""
    admin_id = "937c92bb-d5dd-4990-bcfc-437757e28070"
    monkeypatch.setattr(dash_tools, "admin_role_id", lambda: admin_id)
    assert dash_tools.default_sharing_settings() == {
        "private": True,
        "all_team_members": 0,
        "roles": {"view": [], "edit": [], "admin": [admin_id]},
        "team_members": {"view": [], "edit": [], "admin": []},
    }


# ---------------------------------------------------------------------------
# No CLI styling: builders carry only the keys the dashlet needs
# ---------------------------------------------------------------------------


def _every_builder_output() -> dict[str, dict]:
    return {
        "table_of_records": table_of_records_config(
            OBJECT_ID, [dash_tools.col(FIELD_ID, "Status")]
        ),
        "field_breakdown": dash_tools.field_breakdown_config(OBJECT_ID, FIELD_ID),
        "field_sum": field_sum_config(OBJECT_ID, FIELD_ID),
        "pivot_table": dash_tools.pivot_table_config(
            OBJECT_ID,
            [{"id": FIELD_ID, "label": "Row"}],
            {"id": FIELD_ID, "label": "C"},
        ),
        "field_range_breakdown": field_range_breakdown_config(
            OBJECT_ID, FIELD_ID, [{"min": 0, "max": 10}]
        ),
        "pipeline_metric": pipeline_metric_config(
            PIPELINE_ID, "records_won", "numeric"
        ),
        "activity_metric": activity_metric_config(ACTIVITY_ID, frequency="week"),
        "scheduled_activities": scheduled_activities_config(ACTIVITY_ID),
        "scheduled_activities_calendar": scheduled_activities_calendar_config(
            ACTIVITY_ID
        ),
        "email_metric": email_metric_config("email_sent", "numeric"),
        "marketing_metric": marketing_metric_config(
            [OBJECT_ID], "leads_added", "numeric"
        ),
        "html": html_dashlet_config("<p>Hi</p>"),
    }


@pytest.mark.parametrize("name", list(_every_builder_output()))
def test_no_builder_stamps_dashlet_style_config(name):
    cfg = _every_builder_output()[name]
    assert "dashlet_style_config" not in (cfg.get("fe_extra_info") or {})
    assert "dashlet_style_config" not in str(cfg)


@pytest.mark.parametrize(
    "build",
    [
        lambda: dash_tools.field_breakdown_config(OBJECT_ID, FIELD_ID),
        lambda: field_sum_config(OBJECT_ID, FIELD_ID),
        lambda: field_range_breakdown_config(
            OBJECT_ID, FIELD_ID, [{"min": 0, "max": 1}]
        ),
    ],
    ids=["field_breakdown", "field_sum", "field_range_breakdown"],
)
def test_builders_with_nothing_for_fe_extra_info_omit_it(build):
    # Only table_of_records needs the key (webapp CustomObjectTableOfRecords
    # ConfigValidator); these carried it solely for the dropped style block.
    assert "fe_extra_info" not in build()


def test_fe_extra_info_keeps_its_real_data():
    assert table_of_records_config(OBJECT_ID, [dash_tools.col(FIELD_ID, "S")])[
        "fe_extra_info"
    ] == {"columns": [dash_tools.col(FIELD_ID, "S")]}
    assert set(
        dash_tools.pivot_table_config(
            OBJECT_ID, [{"id": FIELD_ID, "label": "R"}], {"id": FIELD_ID, "label": "C"}
        )["fe_extra_info"]
    ) == {"pivot_table_config"}
    assert set(
        pipeline_metric_config(PIPELINE_ID, "records_won", "numeric")["fe_extra_info"]
    ) == {"date_filter"}
    assert set(activity_metric_config(ACTIVITY_ID)["fe_extra_info"]) == {"date_filter"}
    assert set(scheduled_activities_config(ACTIVITY_ID)["fe_extra_info"]) == {
        "date_filter",
        "scheduled_activities_config",
    }


def test_layout_takes_no_default_geometry():
    with pytest.raises(TypeError):
        dash_tools.layout(0, 0)  # type: ignore[call-arg]
    cell = dash_tools.layout(0, 3, 6, 2)
    assert {k: cell[k] for k in ("x", "y", "w", "h")} == {
        "x": 0,
        "y": 3,
        "w": 6,
        "h": 2,
    }
    assert cell["i"]


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

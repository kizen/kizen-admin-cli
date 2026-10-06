"""Tools for creating and managing Kizen dashboards and dashlets.

Config builders, by entity_type:

  entity_type=custom_object
    - table_of_records         (report_type=table_of_records, chart_type=table)
    - field_breakdown          (report_type=field_metrics,    chart_type=donut)
    - field_sum                (report_type=field_metrics,    chart_type=numeric)
    - field_range_breakdown    (report_type=field_metrics,    chart_type=bar)
    - pivot_table              (report_type=pivot_table,       chart_type=table)
  entity_type=pipeline (pipeline_metric_config; report_type selects the metric)
    - records_added, records_won, records_lost, records_dq   (chart_type=line|numeric)
    - pipeline_values_over_time, stage_values_over_time (chart_type=line only)
    - opportunity_conversion   (chart_type=horizontal_bar only)
    - leaderboard              (chart_type=leaderboard only)
  entity_type=activity
    - records_added                  (activity_metric_config; chart_type=line|numeric)
    - scheduled_activities           (scheduled_activities_config; chart_type
                                       is server-unvalidated, calendar_view flag)
    - scheduled_activities_calendar  (scheduled_activities_calendar_config;
                                       chart_type=calendar, a distinct report_type)
  entity_type=email (email_metric_config)
    - email_sent, email_delivery, email_opt_out, email_complaint (numeric)
    - email_sent, email_delivery, email_interaction_stats (line)
  entity_type=marketing (marketing_metric_config; object_ids is plural)
    - leads_added                       (chart_type=numeric|line)
    - leads_added_by_source             (chart_type=bar|donut|line, needs lead_sources)
    - lead_source_breakdown_over_time   (chart_type=line only, needs lead_sources)
  entity_type=static_content
    - html                     (html_dashlet_config; craft.js content tree —
                                 Section > Row(s) > Cell(s) > Text/Button
                                 blocks; images/dividers/multi-Section still
                                 NOT modeled — see its docstring)
  entity_type=plugin: report_type/chart_type are both fixed to "plugin"; the
    config also needs plugin_api_name + block_api_name of an installed
    plugin — not modeled here, no installed plugin was available to test
    against live.

The full ``entity_type`` enum, confirmed live 2026-07-20 straight from the
API's own validation error: ``["activity", "custom_object", "email",
"marketing", "pipeline", "static_content", "plugin"]``. Notably there is no
"goal" or "funnel" entity_type, report_type, or chart_type anywhere in this
enum or any of the per-report_type chart_type choices above (each was
enumerated the same way, by triggering the 400 and reading its choices list)
— despite being commonly assumed CRM dashlet types, they don't appear to
exist as distinct configurable dashlets in this API version. The closest
analog to a "funnel" is opportunity_conversion (chart_type=horizontal_bar).

Layout helpers generate the grid layout dict expected by the dashboard renderer.

Dashlet filters come from the filtering DSL (kizen_builder.filtering) — build
an expression and wrap it with ``as_custom_filters``::

    from kizen_builder.filtering import Field, filter_context, as_custom_filters

    with filter_context("document_sets"):
        filters = as_custom_filters(Field("status") == "Complete")

A dashlet with no filter must still send the "no filter" shape, not an empty
``{}`` — ``{}`` 400s with "You must send only one type of filters:
in_group_ids, not_in_group_ids or custom_filters" (confirmed live
2026-07-20). ``NO_FILTER`` is that shape; every config builder here defaults
to it.

Usage pattern:
    from kizen_builder.tools.dashboards import (
        create_homepage, add_dashlet,
        table_of_records_config, field_breakdown_config,
        col, layout,
    )
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from kizen_builder.api import dashboards as dash_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config

# ---------------------------------------------------------------------------
# Default style — matches the Billing Dashboard palette
# ---------------------------------------------------------------------------

_CHART_THEME = [
    {"dark": "#32c055", "light": "#ADE6BB", "xlight": "#e6f7eb"},
    {"sky": "#4090F7", "dark": "#085bee", "light": "#9CBDF8", "xlight": "#e1ebfd"},
    {"dark": "#f3a800", "light": "#FADC99", "xlight": "#FEF5E2"},
    {"dark": "#4a21d1", "light": "#B7A6ED", "xlight": "#e9e4fa"},
    {"dark": "#38b3b8", "light": "#9BD9DB"},
    {"dark": "#f55f04", "light": "#FAAF81"},
    {"dark": "#dc1422", "light": "#ED8990", "xlight": "#FEE2E5"},
    {"dark": "#c132b1", "light": "#E098D8"},
    {"dark": "#ae764a", "light": "#D6BAA4"},
    {"light": "#d8dde1"},
]

DEFAULT_STYLE_SETTINGS: dict[str, Any] = {
    "boldTitle": False,
    "chartTheme": _CHART_THEME,
    "dropShadow": True,
    "fontFamily": "Proxima Nova",
    "borderColor": "rgba(0,0,0,0)",
    "italicTitle": False,
    "borderWeight": 0,
    "titleAllCaps": True,
    "titleFontSize": "12",
    "titleLineHeight": 2.5,
    "topLeftBorderRadius": 8,
    "titleBackgroundColor": "rgba(244,248,252,1)",
    "topRightBorderRadius": 8,
    "bottomLeftBorderRadius": 8,
    "bottomRightBorderRadius": 8,
}

_DASHLET_STYLE_CONFIG: dict[str, Any] = {
    "bold_title": False,
    "chart_theme": _CHART_THEME,
    "drop_shadow": True,
    "font_family": "Proxima Nova",
    "border_color": "rgba(0,0,0,0)",
    "italic_title": False,
    "border_weight": 0,
    "title_all_caps": True,
    "title_font_size": "12",
    "title_line_height": 2.5,
    "title_background_color": "rgba(244,248,252,1)",
    "top_left_border_radius": 8,
    "top_right_border_radius": 8,
    "bottom_left_border_radius": 8,
    "bottom_right_border_radius": 8,
}

NO_FILTER: dict[str, Any] = {"custom_filters": {"and": True, "query": []}}
"""The wire shape for "no filter" on a dashlet. An empty ``{}`` 400s."""


def _default_date_filter(
    selected_index: int = 6, end: str | None = None
) -> dict[str, Any]:
    """The ``fe_extra_info.date_filter`` block most metric dashlets carry.

    ``start`` is a fixed epoch the UI always sends; ``end`` defaults to today
    (M/D/YYYY, no zero-padding, matching the observed wire format) and
    ``selected_index: 6`` ("All time") is what every live example used.
    """
    if end is None:
        now = datetime.now()
        end = f"{now.month}/{now.day}/{now.year}"
    return {
        "end": end,
        "start": "Sat Jan 01 2000 00:00:00 GMT-0500 (Eastern Standard Time)",
        "selected_index": selected_index,
    }


# NOTE: the bespoke filter_* helpers that used to live here were retired in
# favor of kizen_builder.filtering (see module docstring). One behavioral
# difference: they emitted `is_not_blank` for has-a-value checks (confirmed
# working in dashlets), while the DSL emits `is_blank` with value false (the
# form the records-search UI sends). If a dashlet rejects the DSL form, see
# git history / `kizen docs show reference` for the legacy shape.


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------


def layout(x: int, y: int, w: int = 12, h: int = 3) -> dict:
    """Grid layout for a dashlet on the 12-column dashboard grid.

    ``w`` is a column span (1–12); ``w=12`` is full width, ``w=6`` half. ``h``
    is a row span — real dashboards run small: numeric tiles ~2, donut/bar/
    line/table ~3, calendars ~4–5, HTML banners ~2–3 (see the sizing table in
    `kizen docs show dashboard`). Defaults are a sensible full-width row.
    """
    cell_id = str(uuid.uuid4())
    return {"h": h, "i": cell_id, "w": w, "x": x, "y": y}


# ---------------------------------------------------------------------------
# Column helper (for table_of_records)
# ---------------------------------------------------------------------------


def col(field_id: str, label: str, display_name: str | None = None) -> dict:
    """One column descriptor for a table_of_records dashlet."""
    return {"id": field_id, "label": label, "display_name": display_name or label}


# ---------------------------------------------------------------------------
# Dashlet config builders
# ---------------------------------------------------------------------------
#
# entity_type=custom_object dashlets (every builder below through
# field_range_breakdown_config) can ONLY be added to a ``type: "homepage"``
# dashboard — a ``generic_dashboard`` 400s with "Cannot create Custom Object
# Dashlet for generic Dashboards" even though the dashlet's own config is
# otherwise valid. Confirmed live 2026-07-20 (also true of the pre-existing
# table_of_records/field_breakdown/field_sum/pivot_table builders, not just
# the new ones — this was never actually exercised against a real
# generic_dashboard before). pipeline/activity/email/static_content dashlets
# further down this file have no such restriction.

_BASE_CONFIG: dict[str, Any] = {
    "entity_type": "custom_object",
    "activity_ids": [],
    "include_roles": [],
    "include_employees": [],
    "include_employees_with_roles": [],
}


def table_of_records_config(
    object_id: str,
    columns: list[dict],
    filters: dict | None = None,
    column_widths: dict[str, str] | None = None,
) -> dict:
    """Config for a filterable list of records with chosen columns.

    Args:
        object_id: UUID of the custom object to query.
        columns: List of col() dicts defining which fields appear as columns.
        filters: Output of filtering.as_custom_filters(), or None for no filter.
        column_widths: Optional {field_uuid: "200px"} map.
    """
    fe: dict[str, Any] = {
        "columns": columns,
        "dashlet_style_config": _DASHLET_STYLE_CONFIG,
    }
    if column_widths:
        fe["column_widths"] = column_widths

    return {
        **_BASE_CONFIG,
        "chart_type": "table",
        "metric_type": "records_number",
        "report_type": "table_of_records",
        "filters": filters or NO_FILTER,
        "fe_extra_info": fe,
    }


def field_breakdown_config(
    object_id: str,
    field_id: str,
    filters: dict | None = None,
    include_summary: bool = True,
) -> dict:
    """Config for a donut chart breaking down records by a field's values.

    Args:
        object_id: UUID of the custom object.
        field_id: UUID of the field to group by (dropdown, status, yesnomaybe).
        filters: Optional filtering.as_custom_filters() output to pre-filter the record set.
        include_summary: Show total count in the centre of the donut.
    """
    return {
        **_BASE_CONFIG,
        "field": field_id,
        "object_id": object_id,
        "chart_type": "donut",
        "metric_type": "fields_value_breakdown",
        "report_type": "field_metrics",
        "filters": filters or NO_FILTER,
        "metric_type_extra_info": {
            "include_summary": include_summary,
            "summary_explanation": "",
            "fields_value_breakdown": {
                "type": "custom_values",
                "buckets": [],
                "consider_null_values": False,
            },
        },
        "fe_extra_info": {"dashlet_style_config": _DASHLET_STYLE_CONFIG},
    }


def field_sum_config(
    object_id: str,
    field_id: str,
    filters: dict | None = None,
) -> dict:
    """Config for a numeric widget showing the sum of a numeric field.

    Args:
        object_id: UUID of the custom object.
        field_id: UUID of the numeric/money/decimal field to sum.
        filters: Optional filtering.as_custom_filters() output to restrict which records are summed.
    """
    return {
        **_BASE_CONFIG,
        "field": field_id,
        "object_id": object_id,
        "chart_type": "numeric",
        "metric_type": "fields_value_sum",
        "report_type": "field_metrics",
        "filters": filters or NO_FILTER,
        "fe_extra_info": {"dashlet_style_config": _DASHLET_STYLE_CONFIG},
    }


def pivot_table_config(
    object_id: str,
    row_fields: list[dict],
    column_field: dict,
    filters: dict | None = None,
    col_width: int = 210,
) -> dict:
    """Config for a pivot table cross-tabulating records.

    Args:
        object_id: UUID of the custom object.
        row_fields: List of {"id": field_uuid, "label": str} for the row axis.
        column_field: {"id": field_uuid, "label": str} for the column axis.
        filters: Optional filtering.as_custom_filters() output.
        col_width: Pixel width for each column (default 210).
    """
    return {
        **_BASE_CONFIG,
        "object_id": object_id,
        "chart_type": "table",
        "metric_type": "records_number",
        "report_type": "pivot_table",
        "aggregation": {
            "rows": row_fields,
            "columns": column_field,
            "field_to_aggregate": None,
        },
        "filters": filters or NO_FILTER,
        "fe_extra_info": {
            "pivot_table_config": {"cols": [col_width] * (len(row_fields) + 1)},
            "dashlet_style_config": _DASHLET_STYLE_CONFIG,
        },
    }


def field_range_breakdown_config(
    object_id: str,
    field_id: str,
    buckets: list[dict],
    filters: dict | None = None,
    include_summary: bool = False,
) -> dict:
    """Config for a bar chart bucketing a numeric field's values into ranges.

    The histogram sibling of ``field_breakdown_config`` (donut, discrete
    values) — this buckets a *numeric* field into custom min/max ranges.
    Confirmed live 2026-07-20 (Kitchen Sink Homepage).

    Args:
        object_id: UUID of the custom object.
        field_id: UUID of the numeric field to bucket.
        buckets: List of range dicts, each
            ``{"min": number, "max": number, "label": str,
            "min_op": ">=", "max_op": "<="}`` — ``min_op``/``max_op`` default
            to ``">="``/``"<="`` if omitted (the observed live shape). A
            bucket ``id`` is assigned automatically.
        filters: Output of filtering.as_custom_filters(), or None for no filter.
        include_summary: Show total count in the chart's summary area.
    """
    wire_buckets = [
        {
            "id": str(uuid.uuid4()),
            "min": {"value": b["min"], "operator": b.get("min_op", ">=")},
            "max": {"value": b["max"], "operator": b.get("max_op", "<=")},
            "bucket_label": b.get("label", "[default]"),
        }
        for b in buckets
    ]
    return {
        **_BASE_CONFIG,
        "field": field_id,
        "object_id": object_id,
        "chart_type": "bar",
        "metric_type": "fields_range_breakdown",
        "report_type": "field_metrics",
        "filters": filters or NO_FILTER,
        "metric_type_extra_info": {
            "include_summary": include_summary,
            "field_to_analyze": field_id,
            "summary_explanation": "",
            "fields_range_breakdown": {
                "type": "custom_range",
                "buckets": wire_buckets,
            },
        },
        "fe_extra_info": {"dashlet_style_config": _DASHLET_STYLE_CONFIG},
    }


# ---------------------------------------------------------------------------
# Pipeline metric dashlets (entity_type=pipeline)
# ---------------------------------------------------------------------------
#
# One config shape covers every pipeline metric report_type seen live —
# records_added/records_won/records_lost (numeric or line), pipeline/stage
# values over time (line), opportunity_conversion (horizontal_bar), and
# leaderboard (leaderboard). They differ only in report_type/chart_type/
# metric_type plus a couple of optional flags. Confirmed live 2026-07-20
# against the Kitchen Sink Homepage.


def pipeline_metric_config(
    object_id: str,
    report_type: str,
    chart_type: str,
    metric_type: str = "records_number",
    frequency: str | None = None,
    historical: bool = False,
    inverse: bool = False,
    pipeline_level_of_detail: str = "sum_of_stages",
    percentage_change_over_time: bool = True,
    records_to_include: str | None = None,
    stages: list[str] | None = None,
    include_roles: list[str] | None = None,
    include_employees: list[str] | None = None,
    include_employees_with_roles: list[str] | None = None,
) -> dict:
    """Config for a pipeline-metric dashlet (entity_type=pipeline).

    Args:
        object_id: UUID of the pipeline (a pipeline-type custom object).
        report_type: the full server-validated list (confirmed live
            2026-07-20): "records_added", "records_won", "records_lost",
            "records_dq" (disqualified), "pipeline_values_over_time",
            "stage_values_over_time", "opportunity_conversion", "leaderboard".
        chart_type: confirmed per-report_type choices — "numeric"/"line" for
            records_added/records_won/records_lost/records_dq; "line" only
            for pipeline_values_over_time/stage_values_over_time;
            "horizontal_bar" only for opportunity_conversion; "leaderboard"
            only for leaderboard. Omit ``frequency`` for "numeric"/
            "horizontal_bar"/"leaderboard" (no time bucketing); set it for
            "line".
        metric_type: "records_number", "records_value", or
            "records_weighted_value".
        frequency: "day", "week", or "month" — required when chart_type="line".
            ("quarter" was seen live on the unrelated sales_projection
            report_type, entity_type=custom_object, but 400s here — confirmed
            live 2026-07-20 the API only accepts day/week/month for pipeline
            metrics.)
        historical: Show the all-time total instead of a period comparison
            (numeric dashlets only).
        inverse: Treat a decrease as the "good" direction (e.g. records_lost).
        pipeline_level_of_detail: "sum_of_stages" or "stages_breakdown".
        percentage_change_over_time: Show a period-over-period delta.
        records_to_include: e.g. "open" — only observed on
            pipeline_values_over_time; omit for the server default.
        stages: Restrict to specific pipeline stage UUIDs; omit for all stages.
    """
    fe_extra: dict[str, Any] = {
        "date_filter": _default_date_filter(),
        "dashlet_style_config": _DASHLET_STYLE_CONFIG,
    }
    config: dict[str, Any] = {
        "stages": stages or [],
        "object_id": object_id,
        "chart_type": chart_type,
        "historical": historical,
        "entity_type": "pipeline",
        "metric_type": metric_type,
        "report_type": report_type,
        "activity_ids": [],
        "fe_extra_info": fe_extra,
        "include_roles": include_roles or [],
        "include_employees": include_employees or [],
        "pipeline_level_of_detail": pipeline_level_of_detail,
        "percentage_change_over_time": percentage_change_over_time,
        "include_employees_with_roles": include_employees_with_roles or [],
    }
    if frequency is not None:
        config["frequency"] = frequency
    if chart_type in ("numeric", "line"):
        config["inverse"] = inverse
    if records_to_include is not None:
        config["records_to_include"] = records_to_include
    return config


# ---------------------------------------------------------------------------
# Activity dashlets (entity_type=activity)
# ---------------------------------------------------------------------------


def activity_metric_config(
    object_id: str,
    report_type: str = "records_added",
    chart_type: str = "line",
    metric_type: str = "records_number",
    frequency: str | None = None,
    historical: bool = False,
    inverse: bool = False,
    include_roles: list[str] | None = None,
    include_employees: list[str] | None = None,
    include_employees_with_roles: list[str] | None = None,
) -> dict:
    """Config for an activity-metric dashlet (entity_type=activity).

    Same shape as ``pipeline_metric_config`` but ``object_id`` is an
    *activity type* UUID (from ``kizen activities list``), not a custom
    object. Confirmed live 2026-07-20 for report_type="records_added".

    Args:
        object_id: UUID of the activity type to count.
        report_type: "records_added" is the only one confirmed live so far.
        chart_type: "line" or "numeric".
        frequency: "day", "week", or "month" — required for chart_type="line"
            ("quarter" 400s here; confirmed live 2026-07-20).
    """
    config: dict[str, Any] = {
        "stages": [],
        "inverse": inverse,
        "object_id": object_id,
        "chart_type": chart_type,
        "historical": historical,
        "entity_type": "activity",
        "metric_type": metric_type,
        "report_type": report_type,
        "activity_ids": [],
        "fe_extra_info": {
            "date_filter": _default_date_filter(),
            "dashlet_style_config": _DASHLET_STYLE_CONFIG,
        },
        "include_roles": include_roles or [],
        "include_employees": include_employees or [],
        "include_employees_with_roles": include_employees_with_roles or [],
    }
    if frequency is not None:
        config["frequency"] = frequency
    return config


def scheduled_activities_config(
    object_id: str,
    time_period: str = "week",
    calendar_view: bool = True,
    allow_external_calendars: bool = True,
    showing_only_working_days: bool = True,
    include_roles: list[str] | None = None,
    include_employees: list[str] | None = None,
) -> dict:
    """Config for a scheduled-activities calendar/list dashlet.

    Confirmed live 2026-07-20 (Kitchen Sink Homepage's "Activity Dashboard"
    style calendar dashlet). ``chart_type`` is deliberately the empty string
    on the wire — the UI doesn't chart this, it renders a calendar/list.

    Args:
        object_id: UUID of the activity type to show scheduled instances of.
        time_period: "day", "week", or "month" — the calendar's default view.
        calendar_view: True for calendar layout, False for a flat list.
        allow_external_calendars: Whether external calendar overlay is enabled.
        showing_only_working_days: Hide weekends in the calendar view.
    """
    return {
        "stages": [],
        "object_id": object_id,
        "chart_type": "",
        "historical": False,
        "entity_type": "activity",
        "metric_type": "records_number",
        "report_type": "scheduled_activities",
        "activity_ids": [],
        "fe_extra_info": {
            "date_filter": _default_date_filter(),
            "dashlet_style_config": _DASHLET_STYLE_CONFIG,
            "scheduled_activities_config": {
                "time_period": time_period,
                "calendar_view": calendar_view,
                "allow_external_calendars": allow_external_calendars,
                "showing_only_working_days": showing_only_working_days,
            },
        },
        "include_roles": include_roles or [],
        "include_employees": include_employees or [],
        "include_employees_with_roles": [],
    }


def scheduled_activities_calendar_config(
    object_id: str,
    showing_only_working_days: bool = True,
    include_roles: list[str] | None = None,
    include_employees: list[str] | None = None,
) -> dict:
    """Config for the dedicated calendar-view scheduled-activities dashlet.

    A DIFFERENT report_type from ``scheduled_activities_config`` above —
    discovered live 2026-07-20 via the API's own validation error (*"'report_type'
    property in 'config' must have one of the following choices:
    ['records_added', 'scheduled_activities', 'scheduled_activities_calendar']"*).
    This one requires ``chart_type="calendar"`` and a top-level
    ``showing_only_working_days`` — no nested ``scheduled_activities_config``
    block, no ``fe_extra_info`` at all (both are optional; the server applies
    its own defaults). Not yet seen alongside a live UI screenshot, so its
    exact rendering difference from ``scheduled_activities_config`` (which is
    also calendar-capable via its own ``calendar_view`` flag) isn't confirmed
    — both round-trip live either way.

    Args:
        object_id: UUID of the activity type to show scheduled instances of.
        showing_only_working_days: Hide weekends in the calendar view.
    """
    return {
        "object_id": object_id,
        "chart_type": "calendar",
        "entity_type": "activity",
        "metric_type": "records_number",
        "report_type": "scheduled_activities_calendar",
        "activity_ids": [],
        "showing_only_working_days": showing_only_working_days,
        "include_roles": include_roles or [],
        "include_employees": include_employees or [],
    }


# ---------------------------------------------------------------------------
# Email dashlets (entity_type=email)
# ---------------------------------------------------------------------------


def email_metric_config(
    report_type: str,
    chart_type: str,
    metric_type: str = "records_number",
    frequency: str | None = None,
    historical: bool = True,
    inverse: bool = False,
) -> dict:
    """Config for an email-metric dashlet (entity_type=email, no object_id).

    Confirmed live against a real "Email Marketing Hub" dashboard. Notably
    this shape carries none of the ``fe_extra_info``/``include_roles``/etc.
    machinery the other entity_types do — it's the plainest dashlet config
    observed, and the live dashboard renders fine with just these keys.

    Args:
        report_type: "email_sent", "email_delivery", "email_opt_out",
            "email_complaint", or "email_interaction_stats".
        chart_type: "numeric" or "line".
        frequency: "day", "week", or "month" — required for chart_type="line"
            ("quarter" 400s here; confirmed live 2026-07-20).
        historical: Show the all-time total instead of a period comparison
            (numeric dashlets only; line dashlets observed with False).
        inverse: Treat a decrease as the "good" direction (e.g. opt-outs,
            complaints).
    """
    config: dict[str, Any] = {
        "stages": [],
        "inverse": inverse,
        "chart_type": chart_type,
        "historical": historical,
        "entity_type": "email",
        "metric_type": metric_type,
        "report_type": report_type,
    }
    if frequency is not None:
        config["frequency"] = frequency
    return config


# ---------------------------------------------------------------------------
# Marketing/lead dashlets (entity_type=marketing)
# ---------------------------------------------------------------------------


def marketing_metric_config(
    object_ids: list[str],
    report_type: str,
    chart_type: str,
    metric_type: str = "records_number",
    frequency: str | None = None,
    historical: bool = False,
    lead_sources: list[dict] | None = None,
) -> dict:
    """Config for a lead-metric dashlet (entity_type=marketing).

    Discovered live 2026-07-20 via the API's own validation errors (this
    entity_type isn't exercised by any live example on hand — every
    key/choice here was confirmed by iterating on 400 responses, then
    creating and reading back a real dashlet of each report_type).

    Args:
        object_ids: List of custom-object UUIDs to count leads from — note
            the plural key (``object_ids``, not ``object_id``); at least one
            is required or the API 400s with *"At least one Custom Object
            must be passed in 'object_ids' property."*
        report_type: "leads_added" (chart_type "numeric" or "line"),
            "leads_added_by_source" (chart_type "bar", "donut", or "line"),
            or "lead_source_breakdown_over_time" (chart_type "line" only).
        chart_type: See report_type above for the valid choices per type.
        frequency: "day"/"week"/"month" — required for chart_type="line".
        historical: Show the all-time total instead of a period comparison —
            required (True or False) for chart_type="numeric".
        lead_sources: List of "Lead Source" dicts to filter/break down by —
            required for "leads_added_by_source"/"lead_source_breakdown_over_time"
            (400s without the key at all), but an empty list is accepted
            (confirmed live) and reads as "all sources"; the dict shape for a
            specific source isn't confirmed — no live example had one set.
    """
    config: dict[str, Any] = {
        "chart_type": chart_type,
        "object_ids": object_ids,
        "entity_type": "marketing",
        "metric_type": metric_type,
        "report_type": report_type,
    }
    if frequency is not None:
        config["frequency"] = frequency
    if chart_type == "numeric":
        config["historical"] = historical
    if report_type in ("leads_added_by_source", "lead_source_breakdown_over_time"):
        config["lead_sources"] = lead_sources or []
    return config


# ---------------------------------------------------------------------------
# Static-content / HTML dashlets (entity_type=static_content)
# ---------------------------------------------------------------------------


_CONTAINER_DEFAULTS: dict[str, Any] = {
    "container_margin_top": "0",
    "container_margin_left": "0",
    "container_padding_top": "10",
    "container_border_color": "rgba(74,86,96,1)",
    "container_border_style": "solid",
    "container_border_width": "0",
    "container_margin_right": "0",
    "container_padding_left": "10",
    "container_border_radius": False,
    "container_margin_bottom": "0",
    "container_padding_right": "10",
    "container_padding_bottom": "10",
    "container_background_size": "auto",
    "container_background_color": "rgba(0,0,0,0)",
    "container_background_repeat": "repeat",
    "container_background_image_name": "",
    "container_background_position_x": "0%",
    "container_background_position_y": "0%",
    "container_border_top_left_radius": "4",
    "container_border_top_right_radius": "4",
    "container_border_bottom_left_radius": "4",
    "container_border_bottom_right_radius": "4",
}


def _html_text_node(parent: str, html: str) -> tuple[str, dict]:
    node_id = uuid.uuid4().hex[:24]
    return node_id, {
        "type": {"resolved_name": "Text"},
        "nodes": [],
        "props": {
            **_CONTAINER_DEFAULTS,
            "container_margin_top": "10",
            "container_margin_left": "10",
            "container_margin_right": "10",
            "container_margin_bottom": "10",
        },
        "custom": {"text": html},
        "hidden": False,
        "parent": parent,
        "is_canvas": False,
        "display_name": "Text",
        "linked_nodes": {},
    }


def _html_button_node(
    parent: str,
    label: str,
    *,
    action: str,
    url: str = "",
    activity_id: str | None = None,
    color: str = "rgba(74,33,209,1)",
) -> tuple[str, dict]:
    """A "Button" node — either a link (``action="url"``) or a
    log-an-activity button (``action="log-activity"``, ``activity_id``
    required — from ``kizen activities list``). Prop shape confirmed live
    2026-07-20 from the Kitchen Sink Homepage's static-content dashlet.
    """
    node_id = uuid.uuid4().hex[:24]
    props: dict[str, Any] = {
        "url": url,
        "color": color,
        "label": label,
        "action": action,
        "alignment": "center",
        "font_size": "16",
        "text_color": "#FFFFFF",
        "border_size": "2",
        "font_family": "Helvetica Neue",
        "padding_top": "10",
        "text_styles": [],
        "border_color": "#000000",
        "padding_left": "18",
        "border_radius": "20",
        "padding_right": "20",
        "padding_bottom": "10",
        "enable_recaptcha": False,
        "container_margin_top": "0",
        "open_link_in_new_tab": True,
        "container_margin_left": "0",
        "container_padding_top": "10",
        "container_border_color": "#4A5660",
        "container_border_style": "solid",
        "container_border_width": "0",
        "container_margin_right": "0",
        "container_padding_left": "0",
        "container_border_radius": True,
        "container_margin_bottom": "0",
        "container_padding_right": "0",
        "container_padding_bottom": "10",
        "container_background_size": "auto",
        "container_background_color": "inherit",
        "container_background_repeat": "repeat",
        "container_background_image_name": "",
        "container_background_position_x": "0%",
        "container_background_position_y": "0%",
        "container_border_top_left_radius": "4",
        "container_border_top_right_radius": "4",
        "container_border_bottom_left_radius": "4",
        "container_border_bottom_right_radius": "4",
    }
    if activity_id is not None:
        props["activity_id"] = activity_id
    return node_id, {
        "type": {"resolved_name": "Button"},
        "nodes": [],
        "props": props,
        "custom": {},
        "hidden": False,
        "parent": parent,
        "is_canvas": False,
        "display_name": "v0e",
        "linked_nodes": {},
    }


def _html_raw_node(parent: str, html: str) -> tuple[str, dict]:
    """An "HTMLBlock" node — a *different* node type from Text, for raw
    markup a WYSIWYG editor wouldn't produce (custom CSS, tables, etc).
    Confirmed live 2026-07-21 for the forms/surveys page-builder
    (``tools/form_ui.py::html_block``, ``resolvedName: "HTMLBlock"``,
    content in ``props.htmlContent``) — this is the same snake_case
    translation dashboards use for every other node type here, not yet
    independently confirmed live for dashlets specifically.
    """
    node_id = uuid.uuid4().hex[:24]
    return node_id, {
        "type": {"resolved_name": "HTMLBlock"},
        "nodes": [],
        "props": {**_CONTAINER_DEFAULTS, "html_content": html},
        "custom": {},
        "hidden": False,
        "parent": parent,
        "is_canvas": False,
        "display_name": "HTMLBlock",
        "linked_nodes": {},
    }


def html_text_block(html: str) -> dict:
    """A "text" block for ``html_dashlet_config``'s ``rows`` — rich-text HTML.

    For a real merge field (not just literal ``{{ ... }}`` text), wrap it in
    the exact span markup the UI resolves, confirmed live 2026-07-20 —
    confirmed namespaces are ``entity_record``/``team_member``/``business``
    (see `kizen docs show reference` "Merge fields in message content")::

        html_text_block(
            'Hi <span class="kzn-merge-field" '
            'data-merge-field-fallback-label="Team Member Email" '
            'data-merge-field-relationship="team_member.email">'
            '{{ team_member.email }}</span>'
        )
    """
    return {"type": "text", "html": html}


def html_button_url(label: str, url: str) -> dict:
    """A button block linking to an external URL, for ``html_dashlet_config``'s ``rows``."""
    return {"type": "button_url", "label": label, "url": url}


def html_button_log_activity(label: str, activity_id: str) -> dict:
    """A button block that logs an activity, for ``html_dashlet_config``'s
    ``rows``. ``activity_id`` is an activity type UUID (``kizen activities list``)."""
    return {"type": "button_log_activity", "label": label, "activity_id": activity_id}


def html_raw_block(html: str) -> dict:
    """A raw-HTML block ("HTMLBlock" node, distinct from the rich-text
    ``html_text_block``/"Text" node) for ``html_dashlet_config``'s ``rows``.
    See :func:`_html_raw_node` for the confirmed-elsewhere shape this
    mirrors — not yet independently round-tripped against a live dashlet."""
    return {"type": "html", "html": html}


def html_dashlet_config(
    rows: str | list[list[dict]],
    background_color: str = "transparent",
) -> dict:
    """Config for a static-content/HTML dashlet built from Section > Row(s) > Cell(s).

    The live "html" report_type stores a full craft.js-style node graph
    (Sections > Rows > Cells > blocks like Text/Image/Button/Divider/
    HTMLBlock) — the Kitchen Sink Homepage's own example is ~1500 lines. This
    builder reproduces one Section containing one Row per entry in ``rows``,
    each Row split into equal-width columns holding the blocks given —
    confirmed live 2026-07-20, including a URL button, a log-activity button,
    and a text block with a working merge field, side by side. Multi-Section
    layouts, images, and dividers are still NOT modeled — copy a live example
    via ``dashboards get <id> --raw`` and edit it directly for those.

    Args:
        rows: Either a single HTML string (shorthand for one Section, one
            Row, one column, one Text block — the pre-2026-07-20 signature),
            or a list of rows, each row a list of blocks (one per column,
            equal width) built with ``html_text_block``/``html_button_url``/
            ``html_button_log_activity``.
        background_color: The content block's overall background —
            ``"transparent"`` (the default) or an ``rgba(...)``/``#hex`` string.
    """
    if isinstance(rows, str):
        rows = [[html_text_block(rows)]]

    root_id = "ROOT"
    section_id = uuid.uuid4().hex[:24]
    content: dict[str, Any] = {
        "ROOT": {
            "type": {"resolved_name": "Root"},
            "nodes": [section_id],
            "props": {
                "color": "rgba(74,86,96,1)",
                "height": 450,
                "columns": 5,
                "font_size": "14",
                "max_width": 1372,
                "has_shadow": True,
                "link_color": "rgba(82,142,249,1)",
                "font_family": "Arial",
                "line_height": "1.25",
                "mobile_break": "414",
                "tablet_break": "768",
                "background_color": (
                    "rgba(0,0,0,0)"
                    if background_color == "transparent"
                    else background_color
                ),
            },
            "custom": {},
            "hidden": False,
            "parent": None,
            "is_canvas": True,
            "display_name": "Root",
            "linked_nodes": {},
        },
        section_id: {
            "type": {"resolved_name": "Section"},
            "nodes": [],
            "props": {
                "width": "100",
                "alignment": "center",
                "max_width": "1372",
                **_CONTAINER_DEFAULTS,
                "container_background_color": "rgba(0,0,0,0)",
            },
            "custom": {},
            "hidden": False,
            "parent": root_id,
            "is_canvas": True,
            "display_name": "Section",
            "linked_nodes": {},
        },
    }

    row_ids: list[str] = []
    for row_blocks in rows:
        row_id = uuid.uuid4().hex[:24]
        row_ids.append(row_id)
        n = len(row_blocks)
        share = 1.0 / n if n else 1.0
        linked_nodes: dict[str, str] = {}
        cell_ids: list[str] = []
        for col_index, block in enumerate(row_blocks, start=1):
            cell_id = uuid.uuid4().hex[:24]
            cell_ids.append(cell_id)
            linked_nodes[f"column-{col_index}"] = cell_id

            btype = block["type"]
            if btype == "text":
                block_id, block_node = _html_text_node(cell_id, block["html"])
            elif btype == "button_url":
                block_id, block_node = _html_button_node(
                    cell_id, block["label"], action="url", url=block["url"]
                )
            elif btype == "button_log_activity":
                block_id, block_node = _html_button_node(
                    cell_id,
                    block["label"],
                    action="log-activity",
                    activity_id=block["activity_id"],
                )
            elif btype == "html":
                block_id, block_node = _html_raw_node(cell_id, block["html"])
            else:
                raise ValueError(f"unknown html dashlet block type: {btype!r}")

            content[cell_id] = {
                "type": {"resolved_name": "Cell"},
                "nodes": [block_id],
                "props": {},
                "custom": {},
                "hidden": False,
                "parent": row_id,
                "is_canvas": True,
                "display_name": "Cell",
                "linked_nodes": {},
            }
            content[block_id] = block_node

        content[row_id] = {
            "type": {"resolved_name": "Row"},
            "nodes": [],
            "props": {
                "width": "100",
                "columns": [share] * n,
                "alignment": "center",
                "max_width": "1372",
                **_CONTAINER_DEFAULTS,
                "container_background_color": "rgba(0,0,0,0)",
            },
            "custom": {},
            "hidden": False,
            "parent": section_id,
            "is_canvas": False,
            "display_name": "Row",
            "linked_nodes": linked_nodes,
        }

    content[section_id]["nodes"] = row_ids

    return {
        "content": content,
        "chart_type": "html",
        "entity_type": "static_content",
        "report_type": "html",
    }


# ---------------------------------------------------------------------------
# High-level tools
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sharing settings
# ---------------------------------------------------------------------------
#
# Dashboards require the built-in "Admin" role to hold admin-level access, or
# the create/update 400s ("The 'Admin' role must have Admin-level access to
# this Dashboard."). The Admin role's UUID is env-specific, so it's resolved
# live rather than hardcoded. On the wire, roles.* / team_members.* are arrays
# of bare UUIDs (read responses expand them to {id, display_name} — see
# normalize_sharing_settings).


def admin_role_id() -> str | None:
    """Return the UUID of the built-in 'Admin' role in this env, or None."""
    from kizen_builder.api import permissions as perm_api

    config = load_env_config()
    with KizenClient(config) as client:
        roles = perm_api.list_roles(client)
    admin = next((r for r in roles if r.get("name") == "Admin"), None)
    return admin.get("id") if admin else None


def default_sharing_settings() -> dict[str, Any]:
    """Sharing block visible to all team members, with Admin role as admin.

    Resolves the Admin role UUID live so the create/update payload satisfies
    Kizen's "Admin must have admin access" rule.
    """
    admin_id = admin_role_id()
    return {
        "private": False,
        "all_team_members": 1,
        "roles": {"view": [], "edit": [], "admin": [admin_id] if admin_id else []},
        "team_members": {"view": [], "edit": [], "admin": []},
    }


def _ids_only(items: Any) -> list[str]:
    """Flatten a permission list to bare UUID strings (accepts ids or {id})."""
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(it["id"])
        elif isinstance(it, str):
            out.append(it)
    return out


def normalize_sharing_settings(sharing: dict[str, Any]) -> dict[str, Any]:
    """Coerce a sharing block to the write shape (bare-UUID permission lists).

    A sharing block copied from ``dashboards get --raw`` carries read-shape
    ``roles.admin: [{id, display_name}]``; the write API wants bare UUID
    strings. This rewrites every ``roles``/``team_members`` bucket in place-safe
    fashion and leaves scalar keys (``private``, ``all_team_members``) untouched.
    """
    result = dict(sharing)
    for bucket in ("roles", "team_members"):
        block = result.get(bucket)
        if isinstance(block, dict):
            result[bucket] = {
                level: _ids_only(block.get(level))
                for level in ("view", "edit", "admin")
            }
    return result


def get_dashboard(dashboard_id: str) -> dict[str, Any]:
    """Fetch a dashboard and its dashlets (raw API response)."""
    config = load_env_config()
    with KizenClient(config) as client:
        return dash_api.get_dashboard(client, dashboard_id)


# ---------------------------------------------------------------------------
# Read tools (normalized summaries for the CLI)
# ---------------------------------------------------------------------------


def _dashboard_summary(d: dict[str, Any]) -> dict[str, Any]:
    """Collapse one raw dashboard dict to the fields the CLI table shows.

    Tolerant of both the ``/api/dashboards/mine`` summary shape (which carries
    ``dashlets_count`` + ``owner`` but no ``type``) and the full dashboard GET
    (which carries ``type`` + ``custom_object``).
    """
    owner = d.get("owner")
    owner_name = None
    if isinstance(owner, dict):
        owner_name = (
            owner.get("display_name")
            or " ".join(filter(None, [owner.get("first_name"), owner.get("last_name")]))
            or owner.get("email")
        )
    return {
        "id": d.get("id"),
        "api_name": d.get("api_name"),
        "name": d.get("name"),
        "type": d.get("type"),  # "homepage" | "dashboard" (full GET only)
        "custom_object": d.get("custom_object"),
        "published": d.get("published"),
        "hidden": d.get("hidden"),
        "dashlets_count": d.get("dashlets_count"),
        "owner": owner_name,
    }


# dashboard_type=generic_dashboard and dashboard_type=homepage are DISTINCT
# queries — generic_dashboard does NOT include homepages, despite what an
# earlier version of this comment (and `kizen docs show reference`) claimed. A
# homepage built entirely in the UI was invisible under generic_dashboard
# alone; confirmed live 2026-07-20. chart_group is excluded here since it
# additionally requires a custom_object_id per object.
_DASHBOARD_LIST_TYPES = ("generic_dashboard", "homepage")


def _list_all_raw(client: KizenClient) -> list[dict[str, Any]]:
    """Fetch and merge every dashboard_type this env's user owns."""
    raw: list[dict[str, Any]] = []
    for t in _DASHBOARD_LIST_TYPES:
        raw.extend(dash_api.list_dashboards(client, dashboard_type=t))
    return raw


def list_dashboards() -> list[dict[str, Any]]:
    """Return a summary of every dashboard/homepage in the configured env."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = _list_all_raw(client)
    return [_dashboard_summary(d) for d in raw]


def _dashlet_summary(dl: dict[str, Any]) -> dict[str, Any]:
    """Collapse one dashlet to a readable row (name, report/chart type, position)."""
    cfg = dl.get("config") or {}
    layout = dl.get("layout") or {}
    return {
        "id": dl.get("id"),
        "name": dl.get("name"),
        "report_type": cfg.get("report_type"),
        "chart_type": cfg.get("chart_type"),
        "custom_object": dl.get("custom_object"),
        "x": layout.get("x"),
        "y": layout.get("y"),
        "w": layout.get("w"),
        "h": layout.get("h"),
    }


def get_dashboard_detail(dashboard_id: str) -> dict[str, Any]:
    """Return one dashboard with a normalized dashlet summary + the raw payload.

    Accepts a dashboard UUID or api_name. If ``dashboard_id`` isn't a UUID we
    resolve it against the dashboard list first (mirrors ``objects get``).
    """
    from kizen_builder.utils import is_uuid

    config = load_env_config()
    with KizenClient(config) as client:
        resolved_id = dashboard_id
        if not is_uuid(dashboard_id):
            summaries = _list_all_raw(client)
            match = next(
                (d for d in summaries if d.get("api_name") == dashboard_id), None
            )
            if match is None:
                raise LookupError(f"no dashboard with api_name '{dashboard_id}'")
            resolved_id = match["id"]
        raw = dash_api.get_dashboard(client, resolved_id)

    dashlets = raw.get("dashlets") or []
    return {
        **_dashboard_summary(raw),
        "style_settings": raw.get("style_settings"),
        "sharing_settings": raw.get("sharing_settings"),
        "dashlets": [_dashlet_summary(dl) for dl in dashlets],
        "raw": raw,
    }

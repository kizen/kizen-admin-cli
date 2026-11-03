"""Generate a ready-to-edit dashlet ``config`` for any dashlet type.

Backs ``kizen dashboards dashlet-config``. The config builders in
``tools.dashboards`` are the source of truth for every dashlet's wire shape;
this module is the thin authoring layer over them so an agent can produce a
valid ``config`` for a named type **without** reading library source or copying
a live dashlet (which fails on an env that has no populated dashboards — the
gap SOL-115 flagged).

Both input modes are UUID-free:

* **template mode** (no ``object_ref``/``field_ref``) — emits the config with
  clearly-labeled ``<...>`` placeholder tokens you swap in. No live calls.
* **resolved mode** (``object_ref``/``field_ref`` given as *api_names*) —
  resolves them live against the schema and bakes real UUIDs into the config.

Because the emitted config comes straight from the ``tools.dashboards``
builders, it cannot drift from what ``dashboards create`` actually accepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import dashboards as dash
from kizen_builder.tools.activities import resolve_activity_id
from kizen_builder.tools.objects import get_object

# Obvious non-UUID sentinels — angle-bracketed so a config pasted unedited
# fails loudly at plan/apply time rather than silently pointing at nothing.
OBJECT_PLACEHOLDER = "<OBJECT_UUID>"
FIELD_PLACEHOLDER = "<FIELD_UUID>"


@dataclass(frozen=True)
class DashletType:
    """One authorable dashlet type: what it is and what refs it takes."""

    key: str
    entity_type: str
    report_type: str
    chart_type: str
    summary: str
    homepage_only: bool = False
    takes_object: bool = False
    takes_field: bool = False
    object_kind: str = "custom object"  # human hint for --object
    parameterized: bool = False  # report_type/chart_type are meaningfully tunable


# The catalog an agent picks from. Keys are stable; order is display order.
TYPES: dict[str, DashletType] = {
    "table_of_records": DashletType(
        "table_of_records",
        "custom_object",
        "table_of_records",
        "table",
        "Filterable list of records with chosen columns",
        homepage_only=True,
        takes_object=True,
        takes_field=True,
    ),
    "field_breakdown": DashletType(
        "field_breakdown",
        "custom_object",
        "field_metrics",
        "donut",
        "Donut breaking records down by a dropdown/status field's values",
        homepage_only=True,
        takes_object=True,
        takes_field=True,
    ),
    "field_sum": DashletType(
        "field_sum",
        "custom_object",
        "field_metrics",
        "numeric",
        "Numeric widget summing a number/money field",
        homepage_only=True,
        takes_object=True,
        takes_field=True,
    ),
    "field_range_breakdown": DashletType(
        "field_range_breakdown",
        "custom_object",
        "field_metrics",
        "bar",
        "Bar chart bucketing a numeric field into ranges",
        homepage_only=True,
        takes_object=True,
        takes_field=True,
    ),
    "pivot_table": DashletType(
        "pivot_table",
        "custom_object",
        "pivot_table",
        "table",
        "Pivot table cross-tabulating records by row/column fields",
        homepage_only=True,
        takes_object=True,
        takes_field=True,
    ),
    "pipeline_metric": DashletType(
        "pipeline_metric",
        "pipeline",
        "records_added",
        "numeric",
        "Pipeline metric (records added/won/lost, values over time, etc.)",
        takes_object=True,
        object_kind="pipeline",
        parameterized=True,
    ),
    "activity_metric": DashletType(
        "activity_metric",
        "activity",
        "records_added",
        "line",
        "Activity metric — count of an activity type over time",
        takes_object=True,
        object_kind="activity type",
        parameterized=True,
    ),
    "scheduled_activities": DashletType(
        "scheduled_activities",
        "activity",
        "scheduled_activities",
        "",
        "Scheduled-activities calendar/list",
        takes_object=True,
        object_kind="activity type",
    ),
    "scheduled_activities_calendar": DashletType(
        "scheduled_activities_calendar",
        "activity",
        "scheduled_activities_calendar",
        "calendar",
        "Dedicated calendar view of scheduled activities",
        takes_object=True,
        object_kind="activity type",
    ),
    "email_metric": DashletType(
        "email_metric",
        "email",
        "email_sent",
        "numeric",
        "Email metric (sent/delivery/opt-out/complaint/interaction)",
        parameterized=True,
    ),
    "marketing_metric": DashletType(
        "marketing_metric",
        "marketing",
        "leads_added",
        "numeric",
        "Lead/marketing metric (leads added, by source, over time)",
        takes_object=True,
        object_kind="custom object (lead source)",
        parameterized=True,
    ),
    "html": DashletType(
        "html",
        "static_content",
        "html",
        "html",
        "Static HTML / banner content block",
    ),
}


# The custom-object types: the only dashlets whose config carries a record filter.
FILTERABLE_TYPES: tuple[str, ...] = tuple(
    k for k, t in TYPES.items() if t.entity_type == "custom_object"
)
FILTERABLE_TYPES_TEXT = f"{', '.join(FILTERABLE_TYPES[:-1])} and {FILTERABLE_TYPES[-1]}"


def filter_problem(
    dashlet_type: str, object_ref: str | None, filters: Any
) -> str | None:
    """Why ``--filter`` can't apply to this dashlet, or None when it can."""
    if dashlet_type in TYPES and dashlet_type not in FILTERABLE_TYPES:
        return (
            f"dashlet type '{dashlet_type}' has no record filter. --filter works "
            f"on {FILTERABLE_TYPES_TEXT}."
        )
    if not object_ref:
        return "--filter names fields, so it needs --object to resolve them against"
    is_wire = (
        isinstance(filters, dict)
        and len(filters) == 1
        and next(iter(filters))
        in ("custom_filters", "in_group_ids", "not_in_group_ids")
    )
    if not (dash.is_filter_spec(filters) or is_wire):
        return (
            '--filter must be a filter spec ({"all"|"any": [...]}) or a wire block '
            "with exactly one key: custom_filters, in_group_ids or not_in_group_ids"
        )
    return None


def available_types() -> list[DashletType]:
    """The catalog of authorable dashlet types, in display order."""
    return list(TYPES.values())


def _resolve_refs(
    dt: DashletType, object_ref: str | None, field_ref: str | None
) -> tuple[str, str, str | None]:
    """Resolve api_name refs to UUIDs, or fall back to placeholder tokens.

    Returns ``(object_id, field_id, field_label)``. A field is always resolved
    *within* its object, so ``field_ref`` requires ``object_ref``.
    """
    object_id = OBJECT_PLACEHOLDER
    field_id = FIELD_PLACEHOLDER
    field_label: str | None = None

    if field_ref and not object_ref:
        raise ValueError(
            "a field is resolved within its object — pass --object too "
            "(or omit --field to get a placeholder)"
        )
    if not object_ref:
        return object_id, field_id, field_label

    if dt.entity_type == "activity":
        # Activity types aren't custom objects; use the activity resolver.
        with KizenClient(load_env_config()) as client:
            object_id, _ = resolve_activity_id(client, object_ref)
        return object_id, field_id, field_label

    obj = get_object(object_ref)
    object_id = obj["id"]
    if field_ref:
        match = next(
            (
                f
                for f in obj["fields"]
                if f.get("api_name") == field_ref and not f.get("deleted")
            ),
            None,
        )
        if match is None:
            raise LookupError(f"field '{field_ref}' not found on object '{object_ref}'")
        field_id = match["id"]
        field_label = match.get("display_name") or field_ref
    return object_id, field_id, field_label


@dataclass
class GeneratedDashlet:
    """A generated dashlet: its ``config`` plus the envelope fields the
    ``DashletDef`` needs (``custom_object``) and hints for wrapping."""

    config: dict[str, Any]
    entity_type: str
    homepage_only: bool
    custom_object: str | None = (
        None  # dashlet-level custom_object (uuid/placeholder/None)
    )


def generate(
    dashlet_type: str,
    *,
    object_ref: str | None = None,
    field_ref: str | None = None,
    report_type: str | None = None,
    chart_type: str | None = None,
    metric_type: str | None = None,
    frequency: str | None = None,
    filter_spec: dict[str, Any] | None = None,
) -> GeneratedDashlet:
    """Resolve refs once, then build the config and its dashlet-envelope fields.

    Unlike :func:`build_dashlet_config` (which returns only the config), this
    also carries the dashlet-level ``custom_object`` — needed because some
    dashlets (``table_of_records``) reference their object *only* through the
    envelope, not the config.

    ``filter_spec`` (a ``FILTERABLE_TYPES`` dashlet with ``object_ref`` only)
    is compiled against the resolved object into ``config.filters``.
    """
    dt = TYPES.get(dashlet_type)
    if dt is None:
        raise ValueError(
            f"unknown dashlet type {dashlet_type!r}. Valid types: " + ", ".join(TYPES)
        )
    if filter_spec is not None and (
        problem := filter_problem(dashlet_type, object_ref, filter_spec)
    ):
        raise ValueError(problem)
    object_id, field_id, field_label = _resolve_refs(dt, object_ref, field_ref)
    filters = (
        dash.compile_filters(filter_spec, object_id)
        if filter_spec is not None
        else None
    )
    config = _dispatch(
        dashlet_type,
        object_id,
        field_id,
        field_label,
        report_type,
        chart_type,
        metric_type,
        frequency,
        filters,
    )
    # The dashlet's custom_object links object-bound dashlets to their object.
    # (Activity/email/marketing dashlets don't set it; their object, if any,
    # lives in the config as object_id/object_ids.)
    custom_object = (
        object_id
        if dt.takes_object and dt.entity_type in ("custom_object", "pipeline")
        else None
    )
    return GeneratedDashlet(config, dt.entity_type, dt.homepage_only, custom_object)


def build_dashlet_config(
    dashlet_type: str,
    *,
    object_ref: str | None = None,
    field_ref: str | None = None,
    report_type: str | None = None,
    chart_type: str | None = None,
    metric_type: str | None = None,
    frequency: str | None = None,
    filter_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one dashlet ``config`` for ``dashlet_type`` (a key from TYPES).

    ``object_ref``/``field_ref`` are *api_names* (never UUIDs); omit them for a
    placeholder template. ``report_type``/``chart_type``/``metric_type``/
    ``frequency`` tune the parameterized metric families and are ignored by the
    fixed types. ``filter_spec`` is as in :func:`generate`.
    """
    return generate(
        dashlet_type,
        object_ref=object_ref,
        field_ref=field_ref,
        report_type=report_type,
        chart_type=chart_type,
        metric_type=metric_type,
        frequency=frequency,
        filter_spec=filter_spec,
    ).config


def _dispatch(
    dashlet_type: str,
    object_id: str,
    field_id: str,
    field_label: str | None,
    report_type: str | None,
    chart_type: str | None,
    metric_type: str | None,
    frequency: str | None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the right builder for an already-resolved ``dashlet_type``."""
    dt = TYPES[dashlet_type]

    if dashlet_type == "table_of_records":
        return dash.table_of_records_config(
            object_id,
            columns=[dash.col(field_id, field_label or "Column Label")],
            filters=filters,
        )
    if dashlet_type == "field_breakdown":
        return dash.field_breakdown_config(object_id, field_id, filters=filters)
    if dashlet_type == "field_sum":
        return dash.field_sum_config(object_id, field_id, filters=filters)
    if dashlet_type == "field_range_breakdown":
        return dash.field_range_breakdown_config(
            object_id,
            field_id,
            buckets=[
                {"min": 0, "max": 100, "label": "0–100"},
                {"min": 100, "max": 1000, "label": "100–1000"},
            ],
            filters=filters,
        )
    if dashlet_type == "pivot_table":
        return dash.pivot_table_config(
            object_id,
            row_fields=[{"id": field_id, "label": field_label or "Row Field"}],
            column_field={"id": FIELD_PLACEHOLDER, "label": "Column Field"},
            filters=filters,
        )
    if dashlet_type == "pipeline_metric":
        rt = report_type or dt.report_type
        ct = chart_type or dt.chart_type
        freq = frequency or ("month" if ct == "line" else None)
        return dash.pipeline_metric_config(
            object_id,
            rt,
            ct,
            metric_type=metric_type or "records_number",
            frequency=freq,
        )
    if dashlet_type == "activity_metric":
        ct = chart_type or dt.chart_type
        freq = frequency or ("week" if ct == "line" else None)
        return dash.activity_metric_config(
            object_id,
            report_type=report_type or dt.report_type,
            chart_type=ct,
            metric_type=metric_type or "records_number",
            frequency=freq,
        )
    if dashlet_type == "scheduled_activities":
        return dash.scheduled_activities_config(object_id)
    if dashlet_type == "scheduled_activities_calendar":
        return dash.scheduled_activities_calendar_config(object_id)
    if dashlet_type == "email_metric":
        ct = chart_type or dt.chart_type
        freq = frequency or ("week" if ct == "line" else None)
        return dash.email_metric_config(
            report_type or dt.report_type,
            ct,
            metric_type=metric_type or "records_number",
            frequency=freq,
        )
    if dashlet_type == "marketing_metric":
        ct = chart_type or dt.chart_type
        freq = frequency or ("week" if ct == "line" else None)
        return dash.marketing_metric_config(
            [object_id],
            report_type or dt.report_type,
            ct,
            metric_type=metric_type or "records_number",
            frequency=freq,
        )
    if dashlet_type == "html":
        return dash.html_dashlet_config(
            "<p>Replace with your HTML. "
            "Merge fields look like {{ entity_record.name }}.</p>"
        )
    # TYPES and this dispatch are edited together; a gap is a programming error.
    raise AssertionError(f"no builder wired for dashlet type {dashlet_type!r}")


def wrap_as_dashboard(
    dashlet_type: str,
    config: dict[str, Any],
    *,
    custom_object: str | None = None,
    name: str = "My Dashboard",
) -> dict[str, Any]:
    """Wrap a config in a full copy-paste-ready ``DashboardDef`` for `create`.

    Picks ``homepage`` vs ``generic_dashboard`` from the dashlet type (custom
    object dashlets are homepage-only). The dashboard ``api_name``/``name`` are
    placeholders to rename. ``custom_object`` sets the dashlet-level object
    link; when not given it falls back to the config's ``object_id`` (which
    ``table_of_records`` doesn't carry — pass it explicitly there, or use
    :func:`generate`).
    """
    dt = TYPES[dashlet_type]
    if custom_object is None:
        custom_object = config.get("object_id")  # placeholder/uuid; None if absent
    return {
        "api_name": "my_dashboard",
        "name": name,
        "type": "homepage" if dt.homepage_only else "generic_dashboard",
        "published": True,
        "dashlets": [
            {
                "name": dt.summary,
                "custom_object": custom_object,
                "layout": dash.layout(0, 0),
                "config": config,
            }
        ],
    }


def has_placeholders(config: dict[str, Any]) -> bool:
    """True if the config still carries an unresolved placeholder token.

    Checks for the sentinel tokens specifically, not any ``<`` — HTML dashlet
    content legitimately contains ``<p>`` etc.
    """
    import json

    blob = json.dumps(config)
    return OBJECT_PLACEHOLDER in blob or FIELD_PLACEHOLDER in blob

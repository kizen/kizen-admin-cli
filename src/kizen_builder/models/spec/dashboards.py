"""Dashboards / homepages + dashlets, and per-object saved views."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kizen_builder.models.spec._base import ApiName


class DashletDef(BaseModel):
    """One dashlet on a dashboard. ``config``/``layout`` are passthrough, except
    a filter spec in ``config.filters``.

    Every field defaults to ``None``, meaning "not in the spec": the planner
    sends only the fields that are set, so an update leaves the rest of the
    live dashlet alone. The one create-time fill is ``name``, which the API
    requires: an unnamed new dashlet is sent as ``"[default]"``.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        default=None,
        description="Existing dashlet UUID (present → update; absent → create).",
    )
    name: str | None = Field(
        default=None,
        description="Dashlet display name. A new dashlet without one is named "
        "'[default]'.",
    )
    custom_object: str | None = Field(
        default=None,
        description="UUID of the custom object the dashlet queries (omit for "
        "non-object dashlets like email metrics).",
    )
    layout: dict[str, Any] | None = Field(
        default=None,
        description="Grid geometry: {x, y, w, h, ...}. Passed through verbatim.",
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Dashlet config (report_type, chart_type, filters, ...). "
        "Passed through, except a filter spec in 'filters' "
        '({"all"|"any": [...]}), compiled at plan time.',
    )


class DashboardDef(BaseModel):
    """A dashboard or homepage plus its dashlets.

    ``api_name`` is the stable identifier. ``type`` is one of Kizen's
    ``TypeBdfEnum`` values: ``generic_dashboard`` (a standalone dashboard),
    ``homepage`` (the team landing page), or ``chart_group`` (a custom
    object's chart group — then ``custom_object`` is required).

    ``None`` means "not in the spec": the planner sends only what is set, and
    an update never touches a field the spec omits. On create, ``type``
    falls back to ``generic_dashboard`` (the API requires one) and an omitted
    ``sharing_settings`` gets ``tools.dashboards.default_sharing_settings``
    (private, Admin role as admin — the UI's default).
    """

    model_config = ConfigDict(extra="allow")

    api_name: ApiName
    name: str
    type: Literal["generic_dashboard", "homepage", "chart_group"] | None = None
    custom_object: str | None = None
    hidden: bool | None = None
    published: bool | None = None
    style_settings: dict[str, Any] | None = None
    sharing_settings: dict[str, Any] | None = None
    dashlets: list[DashletDef] = Field(default_factory=list)


class FilterGroupDef(BaseModel):
    """A per-object saved filter (segment). Endpoint: ``/filter-groups``.

    ``config`` is the opaque list-view filter blob — the same shape as an
    automation condition step's ``filter_config`` (see
    ``tools.planners.saved_views``). Either a JSON filter spec (``{"all"|"any":
    [...]}``, resolved via the filtering DSL against the target object) or a
    raw ``{"query": [...]}`` dict.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None
    hidden: bool = False
    sharing_settings: dict[str, Any] | None = None


class QuickFilterDef(BaseModel):
    """A per-object quick-filter template. Endpoint: ``/quick-filters``.

    ``chips`` lists which filter chips show, not their values: ``field``,
    ``relation:related_field`` or ``relation:in_groups`` (see
    ``tools.planners.saved_views._build_chips``). ``facets`` is the wire's
    ``quick_filtering_facets``.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    chips: list[str] = Field(default_factory=list)
    facets: bool = False
    owner: str | None = None
    sharing_settings: dict[str, Any] | None = None


class ColumnTemplateDef(BaseModel):
    """A per-object saved column layout. Endpoint: ``/columns``.

    ``columns`` lists field api_names in order and is built into the
    ``configuration_json`` the UI saves (see
    ``tools.planners.saved_views._build_columns``). ``configuration_json`` is
    the raw alternative, passed through as-is. Set one, not both; with neither,
    the template shows just the name column.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    columns: list[str] | None = None
    configuration_json: dict[str, Any] | None = None
    owner: str | None = None
    sharing_settings: dict[str, Any] | None = None


class LayoutDef(BaseModel):
    """A full record-layout config for a custom object (PUT-replace).

    ``config`` is the list of column-group dicts. Block ``id``s are injected
    automatically at apply time, so authored blocks may omit them. Non-``fields``
    block types are passed through opaquely.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        default="Standard View",
        description="Layout name to target (an object's auto-created layout is "
        "'Standard View').",
    )
    config: list[dict[str, Any]] = Field(
        description="List of column-group dicts. See `kizen docs show reference`."
    )
    tabs: dict[str, Any] | None = Field(
        default=None,
        description="Optional tabs block (e.g. {'automations': true}). Preserved "
        "from the live layout when omitted.",
    )

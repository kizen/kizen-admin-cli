"""Dashboards / homepages + dashlets, and per-object saved views."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kizen_builder.models.spec._base import ApiName


class DashletDef(BaseModel):
    """One dashlet on a dashboard. ``config``/``layout`` are passthrough, except
    a filter spec in ``config.filters``."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        default=None,
        description="Existing dashlet UUID (present → update; absent → create).",
    )
    name: str = Field(default="[default]", description="Dashlet display name.")
    custom_object: str | None = Field(
        default=None,
        description="UUID of the custom object the dashlet queries (None for "
        "non-object dashlets like email metrics).",
    )
    layout: dict[str, Any] = Field(
        default_factory=dict,
        description="Grid geometry: {x, y, w, h, ...}. Passed through verbatim.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Dashlet config (report_type, chart_type, filters, ...). "
        "Passed through, except a filter spec in 'filters' "
        '({"all"|"any": [...]}), compiled at plan time.',
    )


class DashboardDef(BaseModel):
    """A dashboard or homepage plus its dashlets.

    ``api_name`` is the stable identifier. ``type`` is one of Kizen's
    ``TypeBdfEnum`` values: ``generic_dashboard`` (a standalone dashboard,
    the default), ``homepage`` (the team landing page), or ``chart_group`` (a
    custom object's chart group — then ``custom_object`` is required).
    ``style_settings``/``sharing_settings`` default to the standard palette
    when omitted (see ``tools.dashboards``).
    """

    model_config = ConfigDict(extra="allow")

    api_name: ApiName
    name: str
    type: Literal["generic_dashboard", "homepage", "chart_group"] = "generic_dashboard"
    custom_object: str | None = None
    hidden: bool = False
    published: bool = True
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
    """A per-object quick-filter chip. Endpoint: ``/quick-filters``.

    ``filters`` is the same opaque filter-config shape as
    :class:`FilterGroupDef`'s ``config`` (different wire key, same DSL).
    """

    model_config = ConfigDict(extra="allow")

    name: str
    filters: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None
    sharing_settings: dict[str, Any] | None = None


class ColumnTemplateDef(BaseModel):
    """A per-object saved column layout. Endpoint: ``/columns``.

    ``configuration_json`` is an opaque, undocumented blob (which columns show,
    in what order/width) — no DSL exists for it here. Copy one from a live
    ``columns get <object> <id> --raw`` and edit rather than authoring from
    scratch.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    configuration_json: dict[str, Any] = Field(default_factory=dict)
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

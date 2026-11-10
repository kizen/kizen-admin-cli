"""Plan creation/update/delete for filter groups, quick filters, and column
templates — the three per-object "saved view" resources under
``/api/custom-objects/{object_pk}/{filter-groups,quick-filters,columns}``.

All three share one CRUD shape and the dashboard-style ``EntityPermission``
sharing block (see ``tools.dashboards.normalize_sharing_settings`` /
``default_sharing_settings``, reused here as-is — confirmed byte-identical
schema in the full API spec). They differ in the wire key for their
config blob: ``config`` (filter groups), ``filters`` (quick filters),
``configuration_json`` (column templates, opaque passthrough only).

A filter group's config accepts either a JSON filter spec (``{"all"|"any":
[...]}``, resolved via the same ``kizen_builder.filtering`` DSL that backs
``records list --filter`` and automation condition steps) or a raw
``{"query": [...]}`` dict, mirroring
``tools.planners.automations._render_filter_config``.

A quick filter's ``filters`` is not a query: it lists which chips show
(``quick_filter_settings``), and users pick the values when they filter.
"""

from __future__ import annotations

import uuid
from typing import Any

from kizen_builder import filtering
from kizen_builder.api.saved_views import (
    COLUMNS_BASE,
    FILTER_GROUPS_BASE,
    QUICK_FILTERS_BASE,
)
from kizen_builder.config import load_env_config
from kizen_builder.models.spec import ColumnTemplateDef, FilterGroupDef, QuickFilterDef
from kizen_builder.tools.dashboards import (
    default_sharing_settings,
    normalize_sharing_settings,
)
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from kizen_builder.tools.saved_views import (
    find_saved_view,
    list_saved_views,
    resolve_object_id,
)


def _render_filter_config(fc: dict[str, Any], object_api_name: str) -> dict[str, Any]:
    """Compile a filter spec/raw dict to the opaque list-view filter shape.

    Empty/omitted input passes through as ``{}`` — the API accepts an empty
    config for a filter group with no clauses yet.
    """
    if not fc:
        return {}
    if "all" in fc or "any" in fc:
        try:
            with filtering.filter_context(object_api_name):
                return filtering.as_filter_config(filtering.from_spec(fc))
        except (ValueError, LookupError) as e:
            raise PlanError(f"invalid filter spec: {e}") from e
    try:
        return filtering.normalize_filter_config(fc)
    except ValueError as e:
        raise PlanError(f"invalid filter config: {e}") from e


def _sharing_payload(sharing: dict[str, Any] | None) -> dict[str, Any]:
    return (
        normalize_sharing_settings(sharing) if sharing else default_sharing_settings()
    )


def _op_key(object_api_name: str, name: str) -> str:
    return f"{object_api_name}.{name}"


def _unwrap_owner(value: Any) -> str | None:
    """Read shape expands ``owner`` to ``{id, display_name}``; writes take a bare id."""
    if isinstance(value, dict):
        return value.get("id")
    return value


# ---------------------------------------------------------------------------
# Filter groups
# ---------------------------------------------------------------------------


def plan_create_filter_group(
    object_api_name: str, spec: dict[str, Any] | FilterGroupDef
) -> Plan:
    fg = (
        spec
        if isinstance(spec, FilterGroupDef)
        else FilterGroupDef.model_validate(spec)
    )
    env = load_env_config().name

    try:
        object_id = resolve_object_id(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = next(
        (
            v
            for v in list_saved_views(object_api_name, FILTER_GROUPS_BASE)
            if v.get("name") == fg.name
        ),
        None,
    )
    if existing is not None:
        raise PlanError(
            f"filter group '{fg.name}' already exists on '{object_api_name}' "
            f"(uuid {existing['id']}). Use update instead."
        )

    payload: dict[str, Any] = {
        "name": fg.name,
        "config": _render_filter_config(fg.config, object_api_name),
        "hidden": fg.hidden,
        "sharing_settings": _sharing_payload(fg.sharing_settings),
    }
    if fg.owner is not None:
        payload["owner"] = fg.owner
    op = PlanOperation(
        action="create",
        kind="filter_group",
        key=_op_key(object_api_name, fg.name),
        preview={
            "env": env,
            "object": object_api_name,
            "name": fg.name,
            "hidden": fg.hidden,
        },
        payload=payload,
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Create filter group '{fg.name}' on {object_api_name}",
        operations=[op],
    )


def plan_update_filter_group(
    object_api_name: str, id_or_name: str, changes: dict[str, Any]
) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, FILTER_GROUPS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != existing.get("name"):
        payload["name"] = changes["name"]
        diff["name"] = (existing.get("name"), changes["name"])
    if "config" in changes:
        rendered = _render_filter_config(changes["config"], object_api_name)
        if rendered != existing.get("config"):
            payload["config"] = rendered
            diff["config"] = ("…", "…(changed)")
    if "hidden" in changes and changes["hidden"] != existing.get("hidden", False):
        payload["hidden"] = changes["hidden"]
        diff["hidden"] = (existing.get("hidden"), changes["hidden"])
    if "owner" in changes and changes["owner"] != _unwrap_owner(existing.get("owner")):
        payload["owner"] = changes["owner"]
        diff["owner"] = (_unwrap_owner(existing.get("owner")), changes["owner"])
    if changes.get("sharing_settings") is not None:
        normalized = normalize_sharing_settings(changes["sharing_settings"])
        if normalized != existing.get("sharing_settings"):
            payload["sharing_settings"] = normalized
            diff["sharing_settings"] = ("…", "…(changed)")

    action = "update" if payload else "skip"
    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="filter_group",
        key=_op_key(object_api_name, label),
        preview={
            "env": env,
            "object": object_api_name,
            "filter_group": label,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    summary = (
        f"Update filter group '{label}' on {object_api_name}"
        if diff
        else f"No changes to filter group '{label}' on {object_api_name}"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_filter_group(object_api_name: str, id_or_name: str) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, FILTER_GROUPS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action="delete",
        kind="filter_group",
        key=_op_key(object_api_name, label),
        preview={"env": env, "object": object_api_name, "filter_group": label},
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Delete filter group '{label}' from {object_api_name}",
        operations=[op],
    )


# ---------------------------------------------------------------------------
# Quick filters
# ---------------------------------------------------------------------------

# `relatedObjectFieldId` value for an "Is In Group(s)" relationship chip.
IN_GROUPS = "in_groups"


def _live_field(obj: dict[str, Any], api_name: str) -> dict[str, Any]:
    for f in obj["fields"]:
        if f["api_name"] == api_name and not f.get("deleted"):
            return f
    raise PlanError(f"no field '{api_name}' on '{obj['api_name']}'")


def _build_chips(
    object_api_name: str,
    chips: list[str],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve ``field`` / ``relation:related_field`` / ``relation:in_groups``
    to ``quick_filter_settings`` entries, the shape the UI saves.

    A chip already on the template keeps its ``id``; a new one gets a fresh
    UUID, as the UI does.
    """
    obj = get_object(object_api_name)
    existing_ids = {
        (c.get("fieldId"), c.get("relatedObjectFieldId")): c.get("id") for c in existing
    }
    settings: list[dict[str, Any]] = []
    for chip in chips:
        field_name, _, related_name = chip.partition(":")
        field = _live_field(obj, field_name)
        related_id: str | None = None
        label = field["display_name"]
        if related_name:
            if field["field_type"] != "relationship":
                raise PlanError(
                    f"chip '{chip}': '{field_name}' is a {field['field_type']} "
                    "field; only relationship fields take ':<related field>'"
                )
            if related_name == IN_GROUPS:
                related_id = IN_GROUPS
                label = f"{label} Groups"
            else:
                related = _live_field(
                    get_object(field["relation_target"]), related_name
                )
                related_id = related["id"]
                label = f"{label}: {related['display_name']}"
        setting: dict[str, Any] = {
            "id": existing_ids.get((field["id"], related_id)) or str(uuid.uuid4()),
            "fieldId": field["id"],
        }
        if related_id:
            setting["relatedObjectFieldId"] = related_id
        setting["label"] = label
        settings.append(setting)
    return settings


def _chip_labels(filters: dict[str, Any]) -> list[str]:
    return [c.get("label", "?") for c in filters.get("quick_filter_settings") or []]


def plan_create_quick_filter(
    object_api_name: str, spec: dict[str, Any] | QuickFilterDef
) -> Plan:
    qf = (
        spec
        if isinstance(spec, QuickFilterDef)
        else QuickFilterDef.model_validate(spec)
    )
    env = load_env_config().name

    try:
        object_id = resolve_object_id(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = next(
        (
            v
            for v in list_saved_views(object_api_name, QUICK_FILTERS_BASE)
            if v.get("name") == qf.name
        ),
        None,
    )
    if existing is not None:
        raise PlanError(
            f"quick filter '{qf.name}' already exists on '{object_api_name}' "
            f"(uuid {existing['id']}). Use update instead."
        )

    settings = _build_chips(object_api_name, qf.chips, [])
    payload: dict[str, Any] = {
        "name": qf.name,
        "filters": {
            "quick_filter_settings": settings,
            "quick_filtering_facets": qf.facets,
        },
        "filter_count": len(settings),
        "sharing_settings": _sharing_payload(qf.sharing_settings),
    }
    if qf.owner is not None:
        payload["owner"] = qf.owner
    op = PlanOperation(
        action="create",
        kind="quick_filter",
        key=_op_key(object_api_name, qf.name),
        preview={
            "env": env,
            "object": object_api_name,
            "name": qf.name,
            "chips": [s["label"] for s in settings],
        },
        payload=payload,
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Create quick filter '{qf.name}' on {object_api_name}",
        operations=[op],
    )


def plan_update_quick_filter(
    object_api_name: str, id_or_name: str, changes: dict[str, Any]
) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, QUICK_FILTERS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != existing.get("name"):
        payload["name"] = changes["name"]
        diff["name"] = (existing.get("name"), changes["name"])
    if "chips" in changes or "facets" in changes:
        old = existing.get("filters") or {}
        settings = old.get("quick_filter_settings") or []
        if "chips" in changes:
            settings = _build_chips(object_api_name, changes["chips"], settings)
        new = {
            "quick_filter_settings": settings,
            "quick_filtering_facets": changes.get(
                "facets", old.get("quick_filtering_facets", False)
            ),
        }
        if new != old:
            payload["filters"] = new
            payload["filter_count"] = len(settings)
            diff["chips"] = (_chip_labels(old), _chip_labels(new))
            if new["quick_filtering_facets"] != old.get("quick_filtering_facets"):
                diff["facets"] = (
                    old.get("quick_filtering_facets"),
                    new["quick_filtering_facets"],
                )
    if "owner" in changes and changes["owner"] != _unwrap_owner(existing.get("owner")):
        payload["owner"] = changes["owner"]
        diff["owner"] = (_unwrap_owner(existing.get("owner")), changes["owner"])
    if changes.get("sharing_settings") is not None:
        normalized = normalize_sharing_settings(changes["sharing_settings"])
        if normalized != existing.get("sharing_settings"):
            payload["sharing_settings"] = normalized
            diff["sharing_settings"] = ("…", "…(changed)")

    action = "update" if payload else "skip"
    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="quick_filter",
        key=_op_key(object_api_name, label),
        preview={
            "env": env,
            "object": object_api_name,
            "quick_filter": label,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    summary = (
        f"Update quick filter '{label}' on {object_api_name}"
        if diff
        else f"No changes to quick filter '{label}' on {object_api_name}"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_quick_filter(object_api_name: str, id_or_name: str) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, QUICK_FILTERS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action="delete",
        kind="quick_filter",
        key=_op_key(object_api_name, label),
        preview={"env": env, "object": object_api_name, "quick_filter": label},
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Delete quick filter '{label}' from {object_api_name}",
        operations=[op],
    )


def plan_apply_quick_filter(
    object_api_name: str,
    id_or_name: str,
    role_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> Plan:
    """Push a quick filter's visibility to roles and/or users in one call each."""
    if not role_ids and not user_ids:
        raise PlanError("pass at least one --role or --user to apply.")
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, QUICK_FILTERS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    label = existing.get("name") or existing["id"]
    ops: list[PlanOperation] = []
    if role_ids:
        ops.append(
            PlanOperation(
                action="apply",
                kind="quick_filter",
                key=_op_key(object_api_name, f"{label}.roles"),
                preview={
                    "object": object_api_name,
                    "quick_filter": label,
                    "apply_to": "roles",
                    "role_ids": role_ids,
                },
                payload={"target": "roles", "ids": role_ids},
                existing_uuid=existing["id"],
                parent_object_uuid=object_id,
            )
        )
    if user_ids:
        ops.append(
            PlanOperation(
                action="apply",
                kind="quick_filter",
                key=_op_key(object_api_name, f"{label}.users"),
                preview={
                    "object": object_api_name,
                    "quick_filter": label,
                    "apply_to": "users",
                    "user_ids": user_ids,
                },
                payload={"target": "users", "ids": user_ids},
                existing_uuid=existing["id"],
                parent_object_uuid=object_id,
            )
        )
    return Plan.build(
        env=env,
        summary=f"Apply quick filter '{label}' visibility on {object_api_name}",
        operations=ops,
    )


# ---------------------------------------------------------------------------
# Column templates
# ---------------------------------------------------------------------------


def plan_create_column_template(
    object_api_name: str, spec: dict[str, Any] | ColumnTemplateDef
) -> Plan:
    ct = (
        spec
        if isinstance(spec, ColumnTemplateDef)
        else ColumnTemplateDef.model_validate(spec)
    )
    env = load_env_config().name

    try:
        object_id = resolve_object_id(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = next(
        (
            v
            for v in list_saved_views(object_api_name, COLUMNS_BASE)
            if v.get("name") == ct.name
        ),
        None,
    )
    if existing is not None:
        raise PlanError(
            f"column template '{ct.name}' already exists on '{object_api_name}' "
            f"(uuid {existing['id']}). Use update instead."
        )

    payload: dict[str, Any] = {
        "name": ct.name,
        "configuration_json": ct.configuration_json,
        "sharing_settings": _sharing_payload(ct.sharing_settings),
    }
    if ct.owner is not None:
        payload["owner"] = ct.owner
    op = PlanOperation(
        action="create",
        kind="column_template",
        key=_op_key(object_api_name, ct.name),
        preview={"env": env, "object": object_api_name, "name": ct.name},
        payload=payload,
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Create column template '{ct.name}' on {object_api_name}",
        operations=[op],
    )


def plan_update_column_template(
    object_api_name: str, id_or_name: str, changes: dict[str, Any]
) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, COLUMNS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != existing.get("name"):
        payload["name"] = changes["name"]
        diff["name"] = (existing.get("name"), changes["name"])
    if "configuration_json" in changes and changes[
        "configuration_json"
    ] != existing.get("configuration_json"):
        payload["configuration_json"] = changes["configuration_json"]
        diff["configuration_json"] = ("…", "…(changed)")
    if "owner" in changes and changes["owner"] != _unwrap_owner(existing.get("owner")):
        payload["owner"] = changes["owner"]
        diff["owner"] = (_unwrap_owner(existing.get("owner")), changes["owner"])
    if changes.get("sharing_settings") is not None:
        normalized = normalize_sharing_settings(changes["sharing_settings"])
        if normalized != existing.get("sharing_settings"):
            payload["sharing_settings"] = normalized
            diff["sharing_settings"] = ("…", "…(changed)")

    action = "update" if payload else "skip"
    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="column_template",
        key=_op_key(object_api_name, label),
        preview={
            "env": env,
            "object": object_api_name,
            "column_template": label,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    summary = (
        f"Update column template '{label}' on {object_api_name}"
        if diff
        else f"No changes to column template '{label}' on {object_api_name}"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_column_template(object_api_name: str, id_or_name: str) -> Plan:
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, COLUMNS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    label = existing.get("name") or existing["id"]
    op = PlanOperation(
        action="delete",
        kind="column_template",
        key=_op_key(object_api_name, label),
        preview={"env": env, "object": object_api_name, "column_template": label},
        existing_uuid=existing["id"],
        parent_object_uuid=object_id,
    )
    return Plan.build(
        env=env,
        summary=f"Delete column template '{label}' from {object_api_name}",
        operations=[op],
    )


def plan_apply_column_template(
    object_api_name: str,
    id_or_name: str,
    role_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    permission_group_ids: list[str] | None = None,
) -> Plan:
    """Push a column template's visibility to roles/users/permission groups."""
    if not role_ids and not user_ids and not permission_group_ids:
        raise PlanError("pass at least one --role, --user, or --group to apply.")
    env = load_env_config().name
    try:
        object_id = resolve_object_id(object_api_name)
        existing = find_saved_view(object_api_name, COLUMNS_BASE, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e

    label = existing.get("name") or existing["id"]
    ops: list[PlanOperation] = []
    if role_ids:
        ops.append(
            PlanOperation(
                action="apply",
                kind="column_template",
                key=_op_key(object_api_name, f"{label}.roles"),
                preview={
                    "object": object_api_name,
                    "column_template": label,
                    "apply_to": "roles",
                    "role_ids": role_ids,
                },
                payload={"target": "roles", "ids": role_ids},
                existing_uuid=existing["id"],
                parent_object_uuid=object_id,
            )
        )
    if user_ids:
        ops.append(
            PlanOperation(
                action="apply",
                kind="column_template",
                key=_op_key(object_api_name, f"{label}.users"),
                preview={
                    "object": object_api_name,
                    "column_template": label,
                    "apply_to": "users",
                    "user_ids": user_ids,
                },
                payload={"target": "users", "ids": user_ids},
                existing_uuid=existing["id"],
                parent_object_uuid=object_id,
            )
        )
    if permission_group_ids:
        ops.append(
            PlanOperation(
                action="apply",
                kind="column_template",
                key=_op_key(object_api_name, f"{label}.permission_groups"),
                preview={
                    "object": object_api_name,
                    "column_template": label,
                    "apply_to": "permission_groups",
                    "permission_group_ids": permission_group_ids,
                },
                payload={"target": "permission_groups", "ids": permission_group_ids},
                existing_uuid=existing["id"],
                parent_object_uuid=object_id,
            )
        )
    return Plan.build(
        env=env,
        summary=f"Apply column template '{label}' visibility on {object_api_name}",
        operations=ops,
    )

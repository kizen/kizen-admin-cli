"""Plan creation/update/delete for filter groups, quick filters, and column
templates — the three per-object "saved view" resources under
``/api/custom-objects/{object_pk}/{filter-groups,quick-filters,columns}``.

All three share one CRUD shape and the dashboard-style ``EntityPermission``
sharing block (see ``tools.dashboards.normalize_sharing_settings`` /
``default_sharing_settings``, reused here as-is — confirmed byte-identical
schema in the full API spec). A new one with no sharing in its spec starts
private, like the UI's save dialog. They differ in the wire key for their
config blob: ``config`` (filter groups), ``filters`` (quick filters),
``configuration_json`` (column templates, built from a field list by
``_build_columns``).

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
    admin_role_id,
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


# EntityPermission's PermissionLevel, as `all_team_members` stores it.
PERMISSION_LEVELS = {"none": 0, "view": 1, "edit": 2, "admin": 3}
_BUCKETS = ("roles", "team_members")
_LEVEL_KEYS = ("view", "edit", "admin")


def _reshare(
    current: dict[str, Any],
    *,
    private: bool | None = None,
    all_level: str | None = None,
    roles: dict[str, str] | None = None,
    users: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return ``current`` (write shape) with the given grants applied.

    ``roles``/``users`` map an id to a level name; each id moves to that
    level's list, or out of every list for ``none``. Everything else is kept.
    """
    new = dict(current)
    if private is not None:
        new["private"] = private
    if all_level is not None:
        new["all_team_members"] = PERMISSION_LEVELS[all_level]
    for bucket, grants in (("roles", roles), ("team_members", users)):
        if not grants:
            continue
        old_block = current.get(bucket) or {}
        block = {
            lvl: [i for i in old_block.get(lvl, []) if i not in grants]
            for lvl in _LEVEL_KEYS
        }
        for id_, level in grants.items():
            if level != "none":
                block[level].append(id_)
        new[bucket] = block
    return new


def _sharing_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Per-key ``(old, new)`` for two write-shape sharing blocks."""
    out: dict[str, Any] = {}
    for key in ("private", "all_team_members"):
        if old.get(key) != new.get(key):
            out[f"sharing.{key}"] = (old.get(key), new.get(key))
    for bucket in _BUCKETS:
        for lvl in _LEVEL_KEYS:
            a = (old.get(bucket) or {}).get(lvl, [])
            b = (new.get(bucket) or {}).get(lvl, [])
            if sorted(a) != sorted(b):
                out[f"sharing.{bucket}.{lvl}"] = (a, b)
    return out


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
        sharing_diff = _sharing_diff(
            normalize_sharing_settings(existing.get("sharing_settings") or {}),
            normalized,
        )
        if sharing_diff:
            payload["sharing_settings"] = normalized
            diff.update(sharing_diff)

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


def _reshare_template(
    object_api_name: str,
    base: str,
    noun: str,
    id_or_name: str,
    *,
    private: bool | None,
    all_level: str | None,
    roles: dict[str, str] | None,
    users: dict[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    """Find a quick filter or column template and return its id and its
    sharing block with the given grants applied."""
    if private is None and all_level is None and not roles and not users:
        raise PlanError(
            "pass at least one of --private/--public, --all, --role, --user."
        )
    try:
        existing = find_saved_view(object_api_name, base, id_or_name)
    except LookupError as e:
        raise PlanError(str(e)) from e
    # Both always have admin (the API 400s without the Admin role). A grant
    # moves an id between levels, so a lower one would take them out of admin.
    if roles and roles.get(admin_role_id() or "", "admin") != "admin":
        raise PlanError(
            f"the Admin role already has admin on a {noun}, so a lower "
            "level has no effect; it can't be removed."
        )
    owner_id = _unwrap_owner(existing.get("owner"))
    if users and users.get(owner_id or "", "admin") != "admin":
        raise PlanError(
            f"the {noun}'s owner already has admin, so a lower level has "
            "no effect; change the owner to remove it."
        )
    new = _reshare(
        normalize_sharing_settings(existing.get("sharing_settings") or {}),
        private=private,
        all_level=all_level,
        roles=roles,
        users=users,
    )
    return existing["id"], new


def plan_share_quick_filter(
    object_api_name: str,
    id_or_name: str,
    *,
    private: bool | None = None,
    all_level: str | None = None,
    roles: dict[str, str] | None = None,
    users: dict[str, str] | None = None,
) -> Plan:
    """Change who can view, edit or admin a quick filter template."""
    id_, new = _reshare_template(
        object_api_name,
        QUICK_FILTERS_BASE,
        "quick filter",
        id_or_name,
        private=private,
        all_level=all_level,
        roles=roles,
        users=users,
    )
    return plan_update_quick_filter(object_api_name, id_, {"sharing_settings": new})


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

# Built-in fields whose column id is the camelCased field name. Every other
# field's column id is its UUID. Mirrors react-app's DefaultColumns helpers.
_OBJECT_BUILTIN_COLUMNS = {
    "name",
    "owner",
    "created",
    "updated",
    "entity_value",
    "display_name",
    "stage",
    "estimated_close_date",
    "actual_close_date",
    "percentage_chance_to_close",
}
_CONTACT_BUILTIN_COLUMNS = {
    "email",
    "mobile_phone",
    "titles",
    "tags",
    "created",
    "updated",
}
# Contacts' "Full Name" column points at first_name; the list renders it as
# first + last name.
FULL_NAME = "full_name"


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(p.capitalize() for p in rest)


def _build_columns(object_api_name: str, names: list[str]) -> dict[str, Any]:
    """Resolve field api_names to the ``configuration_json`` the UI saves.

    The name column (``full_name`` on contacts) is always kept: it's added
    first when left out, and it's the only one the UI won't let you hide.
    """
    obj = get_object(object_api_name)
    contacts = obj["api_name"] == "client_client"
    lead = FULL_NAME if contacts else "name"
    if lead not in names:
        names = [lead, *names]
    builtins = _CONTACT_BUILTIN_COLUMNS if contacts else _OBJECT_BUILTIN_COLUMNS
    columns: dict[str, Any] = {}
    for name in names:
        if name == lead:
            field = _live_field(obj, "first_name" if contacts else "name")
            col_id = _camel(name)
            label = "Full Name" if contacts else field["display_name"]
        else:
            field = _live_field(obj, name)
            col_id = _camel(name) if name in builtins else field["id"]
            label = field["display_name"]
        columns[col_id] = {
            "id": col_id,
            "fieldId": field["id"],
            "visible": True,
            "sortable": True,
            "width": "200px",
            "disabled": name == lead,
            "label": label,
        }
    return {"columnOrder": list(columns), "columns": columns}


def _column_labels(config: dict[str, Any]) -> list[str]:
    columns = config.get("columns") or {}
    return [
        (columns.get(c) or {}).get("label", c) for c in config.get("columnOrder") or []
    ]


def _column_config(
    object_api_name: str, columns: list[str] | None, config: dict[str, Any] | None
) -> dict[str, Any] | None:
    if columns is not None and config is not None:
        raise PlanError("pass columns or configuration_json, not both.")
    if columns is not None:
        return _build_columns(object_api_name, columns)
    return config


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

    config = _column_config(
        object_api_name, ct.columns, ct.configuration_json
    ) or _build_columns(object_api_name, [])
    payload: dict[str, Any] = {
        "name": ct.name,
        "configuration_json": config,
        "sharing_settings": _sharing_payload(ct.sharing_settings),
    }
    if ct.owner is not None:
        payload["owner"] = ct.owner
    op = PlanOperation(
        action="create",
        kind="column_template",
        key=_op_key(object_api_name, ct.name),
        preview={
            "env": env,
            "object": object_api_name,
            "name": ct.name,
            "columns": _column_labels(config),
        },
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
    config = _column_config(
        object_api_name, changes.get("columns"), changes.get("configuration_json")
    )
    old_config = existing.get("configuration_json") or {}
    if config is not None and config != old_config:
        payload["configuration_json"] = config
        diff["columns"] = (_column_labels(old_config), _column_labels(config))
    if "owner" in changes and changes["owner"] != _unwrap_owner(existing.get("owner")):
        payload["owner"] = changes["owner"]
        diff["owner"] = (_unwrap_owner(existing.get("owner")), changes["owner"])
    if changes.get("sharing_settings") is not None:
        normalized = normalize_sharing_settings(changes["sharing_settings"])
        sharing_diff = _sharing_diff(
            normalize_sharing_settings(existing.get("sharing_settings") or {}),
            normalized,
        )
        if sharing_diff:
            payload["sharing_settings"] = normalized
            diff.update(sharing_diff)

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


def plan_share_column_template(
    object_api_name: str,
    id_or_name: str,
    *,
    private: bool | None = None,
    all_level: str | None = None,
    roles: dict[str, str] | None = None,
    users: dict[str, str] | None = None,
) -> Plan:
    """Change who can view, edit or admin a column template."""
    id_, new = _reshare_template(
        object_api_name,
        COLUMNS_BASE,
        "column template",
        id_or_name,
        private=private,
        all_level=all_level,
        roles=roles,
        users=users,
    )
    return plan_update_column_template(object_api_name, id_, {"sharing_settings": new})


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

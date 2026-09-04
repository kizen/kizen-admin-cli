"""Plan creation/update/deletion for roles and permission groups.

Roles are simple: name + a list of app-permission flags + a set of
permission-group ids + a default-for-new-users flag. Permission groups are
created from a full default structure (see
:mod:`kizen_builder.tools.permission_builder`) and then optionally shaped by
``permission_setting`` operations in the same plan.
"""

from __future__ import annotations

import copy
from typing import Any

from kizen_builder.api import permissions as perm_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import objects as obj_tools
from kizen_builder.tools.permission_builder import (
    Leaf,
    build_default_group_payload,
    enumerate_leaves,
    substitute_object_label,
    value_to_level,
)
from kizen_builder.tools.permissions import LEVELS, LEVELS_BY_NAME
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation


def _client() -> KizenClient:
    return KizenClient(load_env_config())


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------


def plan_create_role(
    name: str,
    permissions: list[str] | None = None,
    permission_group_ids: list[str] | None = None,
    default_for_new_users: bool = False,
) -> Plan:
    env = load_env_config().name
    with _client() as c:
        existing = next(
            (r for r in perm_api.list_roles(c) if r.get("name") == name), None
        )
        if existing is not None:
            raise PlanError(
                f"role '{name}' already exists (uuid {existing['id']}). "
                "Use plan_update_role instead."
            )
        groups = {g["id"]: g["name"] for g in perm_api.list_permission_groups(c)}

    group_ids = permission_group_ids or []
    unknown = [g for g in group_ids if g not in groups]
    if unknown:
        raise PlanError(
            f"permission group id(s) not found: {unknown}. Available: {list(groups)}"
        )

    payload: dict[str, Any] = {
        "name": name,
        "permission_groups": group_ids,
        "default_for_new_users": default_for_new_users,
    }
    # The create endpoint rejects an explicit empty ``permissions`` list
    # ("This list may not be empty.") but accepts the key being absent
    # (stored as []). Only send it when non-empty.
    if permissions:
        payload["permissions"] = permissions
    op = PlanOperation(
        action="create",
        kind="role",
        key=name,
        preview={
            "env": env,
            "name": name,
            "permission_groups": [groups[g] for g in group_ids],
            "default_for_new_users": default_for_new_users,
            "app_permissions": len(permissions or []),
        },
        payload=payload,
    )
    return Plan.build(env=env, summary=f"Create role '{name}'", operations=[op])


def plan_update_role(role_id: str, changes: dict[str, Any]) -> Plan:
    env = load_env_config().name
    with _client() as c:
        current = perm_api.get_role(c, role_id)

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for field in ("name", "permissions", "permission_groups", "default_for_new_users"):
        if field in changes and changes[field] != current.get(field):
            payload[field] = changes[field]
            diff[field] = (current.get(field), changes[field])

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="role",
        key=current.get("name") or role_id,
        preview={
            "env": env,
            "role": current.get("name"),
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=role_id,
    )
    summary = (
        f"Update role '{current.get('name')}' ({len(diff)} change(s))"
        if diff
        else f"No changes to role '{current.get('name')}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_role(role_id: str) -> Plan:
    env = load_env_config().name
    with _client() as c:
        current = perm_api.get_role(c, role_id)
    op = PlanOperation(
        action="delete",
        kind="role",
        key=current.get("name") or role_id,
        preview={"env": env, "role": current.get("name"), "id": role_id},
        existing_uuid=role_id,
    )
    return Plan.build(
        env=env, summary=f"Delete role '{current.get('name')}'", operations=[op]
    )


# ---------------------------------------------------------------------------
# permission groups
# ---------------------------------------------------------------------------


def plan_create_permission_group(
    name: str,
    base: str = "default",
    template_id: str | None = None,
    settings: list[dict[str, Any]] | None = None,
) -> Plan:
    """Plan a new permission group.

    ``base='default'`` builds a fresh group at Kizen's default access levels;
    ``base='clone'`` copies ``template_id`` (or the first existing group) as-is.
    ``settings`` is an optional list of shaping ops applied after creation:

    * ``{"type": "object", "object_id", "key", "level"}``
    * ``{"type": "field", "object_id", "field_id", "level"}``
    * ``{"type": "section", "section_key", "value": {..full section dict..}}``
    """
    env = load_env_config().name
    with _client() as c:
        groups = perm_api.list_permission_groups(c)
        if any(g["name"] == name for g in groups):
            raise PlanError(f"permission group '{name}' already exists.")
        if not groups:
            raise PlanError(
                "no existing permission group to use as a shape template; "
                "create the first group in the Kizen UI."
            )
        tmpl_id = template_id or groups[0]["id"]
        template = perm_api.get_permission_group(c, tmpl_id)
        meta = perm_api.get_permissions_meta_data(c)

    if base == "default":
        payload = build_default_group_payload(name, template, meta)
    elif base == "clone":
        payload = copy.deepcopy(template)
        for k in ("id", "summary", "user_count", "role_count", "created", "updated"):
            payload.pop(k, None)
        payload["name"] = name
    else:
        raise PlanError(f"unknown base {base!r} (expected 'default' or 'clone')")

    ops = [
        PlanOperation(
            action="create",
            kind="permission_group",
            key=name,
            preview={
                "env": env,
                "name": name,
                "base": base,
                "custom_objects": len(payload.get("custom_objects", [])),
            },
            payload=payload,
        )
    ]
    for i, s in enumerate(settings or []):
        ops.append(_setting_op(name, i, s, meta=meta))

    summary = f"Create permission group '{name}' (base={base})"
    if settings:
        summary += f" + {len(settings)} setting(s)"
    return Plan.build(env=env, summary=summary, operations=ops)


def _find_leaf(
    group: dict[str, Any], meta: dict[str, Any], area: str, block_key: str, row_key: str
) -> Leaf | None:
    """Locate the enumerate_leaves() leaf a setting op targets, or None.

    None means the control has no entry in the live group at all — an
    object/field/section key the group's payload doesn't carry. The caller
    renders that as "(not present)" rather than guessing a level.
    """
    for leaf in enumerate_leaves(group, meta, include_fields=True):
        if (
            leaf.area == area
            and leaf.block_key == block_key
            and leaf.row_key == row_key
        ):
            return leaf
    return None


def _meta_control(meta: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The `meta["custom_objects"]` descriptor for an object-control key, or
    None. Object control keys (``all_records`` etc.) match one; a field id
    never does — meta has no per-field descriptors — so callers that fall
    back to a field id's raw value on a `None` here are also correct: a
    missing field degrading to its id is the same behavior as a found one
    whose name couldn't be resolved.
    """
    for desc in meta.get("custom_objects", []):
        if desc.get("key") == key:
            return desc
    return None


def _setting_op(
    group_key: str,
    idx: int,
    s: dict[str, Any],
    *,
    existing_group_id: str | None = None,
    current_group: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    obj_entities: dict[str, str] | None = None,
) -> PlanOperation:
    """Build a permission_setting op.

    By default the op defers its parent group id to a ``create`` op earlier
    in the same plan (``group_key``) — the group doesn't exist yet at plan
    time. Pass ``existing_group_id`` (with the live ``current_group`` +
    ``meta`` it was read from) to target an already-existing group directly:
    ``parent_object_uuid`` is set immediately and the preview gains a
    ``change`` string (current level -> target level), read from the live
    group rather than guessed. ``obj_entities`` (object id -> display name)
    fills the ``{0}`` placeholder in object-area control labels.
    """
    stype = s.get("type")
    if stype in ("object", "field"):
        level = s["level"]
        level_int = LEVELS_BY_NAME[level] if isinstance(level, str) else int(level)
        body: dict[str, Any] = {
            "custom_object": {"id": s["object_id"]},
            "permission_level": level_int,
        }
        if stype == "field":
            body["field"] = {"id": s["field_id"]}
        elif "key" in s:
            body["key"] = s["key"]
        target = s.get("field_id") or s.get("key") or s["object_id"]
        preview = {"target": f"{stype}:{target}", "level": level}
        payload: dict[str, Any] = {"mode": "object_update", "body": body}
        after = LEVELS.get(level_int, str(level_int))
        # Default: no live group to check against (group-create) or meta
        # unavailable — assume the control is/will-be present rather than
        # risk mislabeling a server normalization as the genuine "no entry
        # at all" defect. group-create's `create` op always inserts every
        # object that currently exists, so its settings ops are never that
        # defect either way.
        payload["control_present"] = True
        if meta is not None:
            # Validated whenever meta is available — both group-create's
            # settings (current_group is None, group doesn't exist yet) and
            # group-update's (current_group is the live group) — so an
            # out-of-range level (e.g. `associated_records: none`, which has
            # no "none" in its allowed_access) is a PlanError before any
            # write, not a server-side clamp that later looks identical to
            # the fresh-insert bug this item fixes. See docs/specs/
            # permission-group.md.
            row_key = (
                s["field_id"] if stype == "field" else s.get("key", s["object_id"])
            )
            leaf = (
                _find_leaf(current_group, meta, stype, s["object_id"], row_key)
                if current_group is not None
                else None
            )
            if current_group is not None:
                payload["control_present"] = leaf is not None
            # Meta only describes object controls (`all_records` etc.), not
            # fields, so a field with no live entry has no allowed_access
            # source at plan time — it's left unvalidated, same as before.
            desc = (
                None
                if leaf is not None or stype == "field"
                else _meta_control(meta, str(row_key))
            )
            allowed = (
                leaf.allowed_access
                if leaf is not None
                else (desc.get("allowed_access") if desc else None)
            )
            if allowed is not None and after not in allowed:
                raise PlanError(
                    f"{row_key!r} does not accept level {after!r} — allowed: "
                    f"{', '.join(allowed)}"
                )
            if existing_group_id is not None:
                label = (
                    leaf.row_label
                    if leaf is not None
                    else (desc.get("label", str(row_key)) if desc else str(row_key))
                )
                if stype == "object":
                    label = substitute_object_label(
                        label, (obj_entities or {}).get(s["object_id"], "")
                    )
                before = leaf.current_level if leaf else "(not present)"
                # Even a present, in-range control isn't a guaranteed outcome
                # — a cross-field rule (`associated_records >= all_records`
                # etc.) can still normalize it based on the group's *final*
                # state, which this planner doesn't (and per this item's
                # constraints, shouldn't) simulate. Only the "no entry at
                # all" case renders a bare target: that one always lands at
                # `none` regardless, so there's nothing to hedge.
                shown_after = (
                    after if leaf is None else f"{after} (subject to server rules)"
                )
                preview["change"] = f"{label}: {before} -> {shown_after}"
    elif stype == "section":
        preview = {"target": f"section:{s['section_key']}", "value": s["value"]}
        payload = {"mode": "section", "body": {s["section_key"]: s["value"]}}
        if existing_group_id is not None:
            assert current_group is not None and meta is not None
            parts = []
            for row_key, wire_value in s["value"].items():
                leaf = _find_leaf(
                    current_group, meta, "section", s["section_key"], row_key
                )
                allowed = leaf.allowed_access if leaf else list(LEVELS.values())
                label = leaf.row_label if leaf else row_key
                before = leaf.current_level if leaf else "(not present)"
                after = value_to_level(wire_value, allowed)
                parts.append(f"{label}: {before} -> {after}")
            preview["change"] = "; ".join(parts)
    else:
        raise PlanError(f"unknown setting type {stype!r}")

    op_kwargs: dict[str, Any] = {}
    if existing_group_id is not None:
        op_kwargs["parent_object_uuid"] = existing_group_id
    else:
        op_kwargs["deferred_parent_object_key"] = group_key

    return PlanOperation(
        action="update",
        kind="permission_setting",
        key=f"{group_key}.setting[{idx}]",
        preview=preview,
        payload=payload,
        **op_kwargs,
    )


def plan_update_permission_group(group_id: str, settings: list[dict[str, Any]]) -> Plan:
    """Plan shaping updates against an *existing* permission group.

    Same op shapes as ``plan_create_permission_group``'s ``settings``
    (object/field/section — see docs/specs/permission-group.md), reused
    verbatim. Unlike create, the group already exists, so each op targets
    its id directly (no ``deferred_parent_object_key``) and the preview
    carries a ``change`` (current -> target) read from the live group, not
    just the target level. Applying raises/lowers exactly those controls —
    it never assembles a full-group PUT, so the cross-field rules
    (``associated_records >= all_records`` etc.) are left to the server's
    ``object-update``/section-PATCH normalization.
    """
    if not settings:
        raise PlanError("group-update requires at least one setting to apply.")

    env = load_env_config().name
    with _client() as c:
        group = perm_api.get_permission_group(c, group_id)
        meta = perm_api.get_permissions_meta_data(c)

    # Object-area labels are templated ("All {0} Records") and meta carries no
    # object names to fill them with — only fetch the object list (a second
    # round trip) when an object-type op actually needs one.
    obj_entities: dict[str, str] = {}
    if any(s.get("type") == "object" for s in settings):
        obj_entities = {
            o["id"]: (o.get("entity_name") or o.get("display_name") or "")
            for o in obj_tools.list_objects()
        }

    ops = [
        _setting_op(
            group.get("name") or group_id,
            i,
            s,
            existing_group_id=group_id,
            current_group=group,
            meta=meta,
            obj_entities=obj_entities,
        )
        for i, s in enumerate(settings)
    ]
    summary = (
        f"Update permission group '{group.get('name')}' ({len(settings)} setting(s))"
    )
    return Plan.build(env=env, summary=summary, operations=ops)


def plan_delete_permission_group(group_id: str) -> Plan:
    env = load_env_config().name
    with _client() as c:
        current = perm_api.get_permission_group(c, group_id)
    op = PlanOperation(
        action="delete",
        kind="permission_group",
        key=current.get("name") or group_id,
        preview={"env": env, "group": current.get("name"), "id": group_id},
        existing_uuid=group_id,
    )
    return Plan.build(
        env=env,
        summary=f"Delete permission group '{current.get('name')}'",
        operations=[op],
    )

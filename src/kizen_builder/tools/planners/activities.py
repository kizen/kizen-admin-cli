"""Plan creation/update/delete/duplication for activity types and their fields.

Mirrors the custom-object field planner: activity fields are structurally the
same as custom-object fields, so the payload builder and option handling are
close ports of ``planners/fields.py``.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import ActivityDef, ActivityFieldDef
from kizen_builder.tools.activities import get_activity, list_activities
from kizen_builder.tools.plans import (
    Plan,
    PlanError,
    PlanOperation,
    copy_name_preview,
    fetch_existing,
)

_OPTION_FIELD_TYPES = {
    "dropdown",
    "radio",
    "status",
    "choices",
    "selector",
    "checkboxes",
    "dynamictags",
    "yesnomaybe",
}


# ---------------------------------------------------------------------------
# Activity type
# ---------------------------------------------------------------------------


def plan_create_activity(spec: dict[str, Any] | ActivityDef) -> Plan:
    """Plan creation of one activity type, plus any inline ``fields``.

    The type is created first; each field is a follow-on op that resolves its
    parent activity id from the create result via a deferred ref, so the whole
    thing applies in one confirm.
    """
    act_def = (
        spec if isinstance(spec, ActivityDef) else ActivityDef.model_validate(spec)
    )
    env = load_env_config().name

    existing = next(
        (
            a
            for a in list_activities()
            if a.get("api_name") and a["api_name"] == act_def.api_name
        ),
        None,
    )
    if act_def.api_name and existing is not None:
        raise PlanError(
            f"activity '{act_def.api_name}' already exists (uuid {existing['id']}). "
            "Use plan_update_activity instead."
        )

    activity_key = f"activity:{act_def.api_name or act_def.name}"
    ops: list[PlanOperation] = [
        PlanOperation(
            action="create",
            kind="activity",
            key=activity_key,
            preview={
                "env": env,
                "name": act_def.name,
                "api_name": act_def.api_name or "(server-derived)",
                "association_mode": act_def.association_mode or "(default)",
                "fields": len(act_def.fields or []),
            },
            payload=_build_activity_payload(act_def),
        )
    ]

    seen: set[str] = set()
    for idx, field in enumerate(act_def.fields or []):
        label = field.api_name or field.name
        if label in seen:
            raise PlanError(f"duplicate activity field '{label}' in the batch.")
        seen.add(label)
        ops.append(
            PlanOperation(
                action="create",
                kind="activity_field",
                key=f"{activity_key}.field:{label}",
                preview={
                    "env": env,
                    "field": label,
                    "field_type": field.field_type,
                    "required": field.required,
                },
                payload=_build_activity_field_payload(field, default_order=idx),
                deferred_parent_object_key=activity_key,
            )
        )

    return Plan.build(
        env=env,
        summary=(
            f"Create activity '{act_def.name}'"
            + (f" with {len(act_def.fields)} field(s)" if act_def.fields else "")
        ),
        operations=ops,
    )


_UPDATABLE_ACTIVITY_KEYS = {
    "name": "name",
    "api_name": "api_name",
    "description": "description",
    "is_editable": "is_editable",
    "association_mode": "association_mode",
    "visibility_rules": "visibility_rules",
    "submission_action": "submission_action",
    "webhook_url": "webhook_url",
    "redirect_url": "redirect_url",
    "calendar_sync_enabled": "calendar_sync_enabled",
    "custom_object_ids": "custom_object_ids",
    "selected_object_ids": "selected_object_ids",
    "associated_objects": "associated_objects",
    "loggable_sharing_settings": "loggable_sharing_settings",
}


def plan_update_activity(identifier: str, changes: dict[str, Any]) -> Plan:
    """Plan a PATCH to one activity type. Only keys present in ``changes`` are sent."""
    env = load_env_config().name
    current = _load_activity(identifier, include_fields=False)

    raw = current.get("raw") or {}
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for key, wire in _UPDATABLE_ACTIVITY_KEYS.items():
        if key not in changes:
            continue
        new = changes[key]
        # visibility_rules / *_ids / sharing are structural — always include when
        # explicitly passed; scalar keys skip when unchanged.
        if key in (
            "visibility_rules",
            "custom_object_ids",
            "selected_object_ids",
            "associated_objects",
            "loggable_sharing_settings",
        ):
            payload[wire] = new
            diff[wire] = ("…", "(updated)")
            continue
        old = raw.get(wire)
        if new != old:
            payload[wire] = new
            diff[wire] = (old, new)

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="activity",
        key=f"activity:{current.get('api_name') or identifier}",
        preview={
            "env": env,
            "activity": current.get("name"),
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=current["id"],
    )
    summary = (
        f"Update activity '{current.get('name')}' ({len(diff)} change(s))"
        if diff
        else f"No changes to activity '{current.get('name')}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_activity(identifier: str) -> Plan:
    """Plan deletion of one activity type (and every logged instance's schema link)."""
    env = load_env_config().name
    current = _load_activity(identifier, include_fields=False)

    op = PlanOperation(
        action="delete",
        kind="activity",
        key=f"activity:{current.get('api_name') or identifier}",
        preview={
            "env": env,
            "activity": current.get("name"),
            "n_submissions": current.get("n_submissions"),
        },
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete activity '{current.get('name')}'",
        operations=[op],
    )


def plan_duplicate_activity(identifier: str, *, name: str | None = None) -> Plan:
    """Plan duplication of one activity type, renaming the copy when ``name`` is given."""
    env = load_env_config().name
    current = _load_activity(identifier, include_fields=False)

    op = PlanOperation(
        action="duplicate",
        kind="activity",
        key=f"activity:{current.get('api_name') or identifier}:duplicate",
        preview={
            "env": env,
            "source": current.get("name"),
            "new_name": copy_name_preview(current.get("name"), name),
        },
        payload={"name": name} if name else {},
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Duplicate activity '{current.get('name')}'",
        operations=[op],
    )


# ---------------------------------------------------------------------------
# Activity fields
# ---------------------------------------------------------------------------


def _load_activity(identifier: str, *, include_fields: bool = True) -> dict[str, Any]:
    return fetch_existing(
        "activity",
        identifier,
        lambda: get_activity(identifier, include_fields=include_fields),
    )


def _find_activity_field(
    activity: dict[str, Any], field_api_name: str
) -> dict[str, Any]:
    match = next(
        (f for f in activity["fields"] if f.get("api_name") == field_api_name), None
    )
    if match is None:
        available = [f.get("api_name") for f in activity["fields"]]
        raise PlanError(
            f"field '{field_api_name}' not found on activity "
            f"'{activity.get('name')}'. Available: {available}"
        )
    return match


def plan_create_activity_fields(
    identifier: str, fields: list[dict[str, Any] | ActivityFieldDef]
) -> Plan:
    """Plan creation of one or more fields on an existing activity type."""
    if not fields:
        raise PlanError("no fields provided to create")
    env = load_env_config().name
    activity = _load_activity(identifier)
    activity_id = activity["id"]

    have = {f.get("api_name") for f in activity["fields"] if f.get("api_name")}
    base_order = len(activity["fields"])
    ops: list[PlanOperation] = []
    seen: set[str] = set()
    for idx, field in enumerate(fields):
        fd = (
            field
            if isinstance(field, ActivityFieldDef)
            else ActivityFieldDef.model_validate(field)
        )
        label = fd.api_name or fd.name
        if fd.api_name and fd.api_name in have:
            raise PlanError(
                f"field '{fd.api_name}' already exists on activity "
                f"'{activity.get('name')}'. Use plan_update_activity_field."
            )
        if label in seen:
            raise PlanError(f"duplicate activity field '{label}' in the batch.")
        seen.add(label)
        ops.append(
            PlanOperation(
                action="create",
                kind="activity_field",
                key=f"activity:{activity.get('api_name') or identifier}.field:{label}",
                preview={
                    "env": env,
                    "activity": activity.get("name"),
                    "field": label,
                    "field_type": fd.field_type,
                    "required": fd.required,
                },
                payload=_build_activity_field_payload(
                    fd, default_order=base_order + idx
                ),
                parent_object_uuid=activity_id,
            )
        )
    return Plan.build(
        env=env,
        summary=f"Create {len(ops)} field(s) on activity '{activity.get('name')}'",
        operations=ops,
    )


def plan_update_activity_field(
    identifier: str, field_api_name: str, changes: dict[str, Any]
) -> Plan:
    """Plan an update to one field on an activity type."""
    env = load_env_config().name
    activity = _load_activity(identifier)
    existing = _find_activity_field(activity, field_api_name)

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != existing.get("display_name"):
        payload["display_name"] = changes["name"]
        diff["display_name"] = (existing.get("display_name"), changes["name"])
    if "description" in changes and changes["description"] != (
        existing.get("description") or ""
    ):
        payload["description"] = changes["description"]
        diff["description"] = (existing.get("description"), changes["description"])
    if "required" in changes and changes["required"] != existing.get("is_required"):
        payload["is_required"] = changes["required"]
        diff["is_required"] = (existing.get("is_required"), changes["required"])
    if "read_only" in changes and changes["read_only"] != existing.get("is_read_only"):
        payload["is_read_only"] = changes["read_only"]
        diff["is_read_only"] = (existing.get("is_read_only"), changes["read_only"])
    if "hidden" in changes and changes["hidden"] != existing.get("is_hidden"):
        payload["is_hidden"] = changes["hidden"]
        diff["is_hidden"] = (existing.get("is_hidden"), changes["hidden"])
    if "order" in changes and changes["order"] != existing.get("order"):
        payload["order"] = changes["order"]
        diff["order"] = (existing.get("order"), changes["order"])

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="activity_field",
        key=f"activity:{activity.get('api_name') or identifier}.field:{field_api_name}",
        preview={
            "env": env,
            "activity": activity.get("name"),
            "field": field_api_name,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=activity["id"],
    )
    summary = (
        f"Update field '{field_api_name}' on activity '{activity.get('name')}'"
        if diff
        else f"No changes to field '{field_api_name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_activity_field(identifier: str, field_api_name: str) -> Plan:
    """Plan deletion of one field from an activity type."""
    env = load_env_config().name
    activity = _load_activity(identifier)
    existing = _find_activity_field(activity, field_api_name)

    op = PlanOperation(
        action="delete",
        kind="activity_field",
        key=f"activity:{activity.get('api_name') or identifier}.field:{field_api_name}",
        preview={
            "env": env,
            "activity": activity.get("name"),
            "field": field_api_name,
            "field_type": existing.get("field_type"),
        },
        existing_uuid=existing["id"],
        parent_object_uuid=activity["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete field '{field_api_name}' from activity '{activity.get('name')}'",
        operations=[op],
    )


def plan_add_activity_field_options(
    identifier: str, field_api_name: str, options: list[str]
) -> Plan:
    """Plan adding options to a select-type field on an activity type."""
    if not options:
        raise PlanError("no options provided to add")
    env = load_env_config().name
    activity = _load_activity(identifier)
    existing = _find_activity_field(activity, field_api_name)
    if existing.get("field_type") not in _OPTION_FIELD_TYPES:
        raise PlanError(
            f"field '{field_api_name}' is type '{existing.get('field_type')}', "
            f"which has no options. Option fields are: {sorted(_OPTION_FIELD_TYPES)}"
        )

    have = {(o.get("name") or "").lower() for o in (existing.get("options") or [])}
    ops: list[PlanOperation] = []
    for name in options:
        already = name.lower() in have
        ops.append(
            PlanOperation(
                action="skip" if already else "create",
                kind="activity_field_option",
                key=f"activity:{activity.get('api_name') or identifier}.field:{field_api_name}.option:{name}",
                preview={
                    "env": env,
                    "field": f"{activity.get('name')}.{field_api_name}",
                    "option": name,
                    "note": "already exists" if already else "add",
                },
                payload={"field_id": existing["id"], "name": name},
                parent_object_uuid=activity["id"],
            )
        )
    n_add = sum(1 for o in ops if o.action == "create")
    return Plan.build(
        env=env,
        summary=f"Add {n_add} option(s) to {activity.get('name')}.{field_api_name}",
        operations=ops,
    )


def plan_remove_activity_field_option(
    identifier: str, field_api_name: str, option: str, remap_to: str | None = None
) -> Plan:
    """Plan removal of one option from a select-type field on an activity type."""
    env = load_env_config().name
    activity = _load_activity(identifier)
    existing = _find_activity_field(activity, field_api_name)
    opts = existing.get("options") or []

    def _match(token: str) -> dict[str, Any]:
        for o in opts:
            if token in (o.get("id"), o.get("name"), o.get("code")) or (
                (o.get("name") or "").lower() == token.lower()
            ):
                return o
        raise PlanError(
            f"option '{token}' not found on {activity.get('name')}.{field_api_name}. "
            f"Available: {[o.get('name') for o in opts]}"
        )

    target = _match(option)
    payload: dict[str, Any] = {"field_id": existing["id"]}
    remap_note = "dropped (records lose this value)"
    if remap_to:
        replacement = _match(remap_to)
        payload["remap_to"] = replacement["id"]
        remap_note = f"records remapped to '{replacement.get('name')}'"

    op = PlanOperation(
        action="delete",
        kind="activity_field_option",
        key=f"activity:{activity.get('api_name') or identifier}.field:{field_api_name}.option:{target.get('name')}",
        preview={
            "env": env,
            "field": f"{activity.get('name')}.{field_api_name}",
            "option": target.get("name"),
            "on_delete": remap_note,
        },
        payload=payload,
        existing_uuid=target["id"],
        parent_object_uuid=activity["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Remove option '{target.get('name')}' from {activity.get('name')}.{field_api_name}",
        operations=[op],
    )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _build_activity_payload(act: ActivityDef) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": act.name}
    optional = {
        "api_name": act.api_name,
        "description": act.description,
        "is_editable": act.is_editable,
        "association_mode": act.association_mode,
        "visibility_rules": act.visibility_rules,
        "submission_action": act.submission_action,
        "webhook_url": act.webhook_url,
        "redirect_url": act.redirect_url,
        "calendar_sync_enabled": act.calendar_sync_enabled,
        "custom_object_ids": act.custom_object_ids,
        "selected_object_ids": act.selected_object_ids,
        "loggable_sharing_settings": act.loggable_sharing_settings,
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return payload


def _build_activity_field_payload(
    field: ActivityFieldDef, *, default_order: int | None = None
) -> dict[str, Any]:
    wire_field_type = "longtext" if field.field_type == "wysiwyg" else field.field_type
    payload: dict[str, Any] = {
        "display_name": field.name,
        "field_type": wire_field_type,
        "is_required": field.required,
        "is_read_only": field.read_only,
        "is_hidden": field.hidden,
    }
    if field.api_name:
        payload["name"] = field.api_name
    if field.field_type == "wysiwyg":
        payload["meta"] = {"is_markdown": True}
    if field.description:
        payload["description"] = field.description
    order = field.order if field.order is not None else default_order
    if order is not None:
        payload["order"] = order
    if field.custom_object_field:
        payload["custom_object_field"] = field.custom_object_field

    if field.options is not None:
        payload["options"] = [{"name": o, "code": o} for o in field.options]
    if field.status_options is not None:
        payload["options"] = [
            {"name": s.name, "code": s.code or s.name.lower()}
            for s in field.status_options
        ]
    if field.money_options is not None:
        payload["money_options"] = field.money_options.model_dump(exclude_none=True)
    if field.rating is not None:
        payload["rating"] = field.rating.model_dump(exclude_none=True)
    if field.decimal_options is not None:
        payload["decimal_options"] = field.decimal_options.model_dump(exclude_none=True)
    if field.phone_options is not None:
        payload["phonenumber_options"] = field.phone_options.model_dump(
            exclude_none=True
        )
    return payload

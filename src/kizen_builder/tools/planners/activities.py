"""Plan creation/update/delete/duplication for activity types and their fields.

Mirrors the custom-object field planner: activity fields are structurally the
same as custom-object fields, so the payload builder and option handling are
close ports of ``planners/fields.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec import ActivityDef, ActivityFieldDef
from kizen_builder.models.spec.activities import check_association_keys
from kizen_builder.tools.activities import (
    describe_visibility_rule,
    get_activity,
    get_scheduled,
    list_activities,
)
from kizen_builder.tools.objects import list_objects
from kizen_builder.tools.permissions import resolve_role
from kizen_builder.tools.plans import (
    Plan,
    PlanError,
    PlanOperation,
    copy_name_preview,
    fetch_existing,
)
from kizen_builder.tools.team import resolve_team_member_id

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
    if any("rule" not in r for r in act_def.visibility_rules or []):
        raise PlanError(
            "visibility rules by name need the fields to exist; create the type, "
            "then run kizen activities update "
            f"{act_def.api_name or '<api_name>'} --visibility-rules-file <f>"
        )
    for n, rule in enumerate(act_def.visibility_rules or [], 1):
        _check_raw_rule(n, rule)
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
    payload = _build_activity_payload(act_def)
    ops: list[PlanOperation] = [
        PlanOperation(
            action="create",
            kind="activity",
            key=activity_key,
            preview={
                "env": env,
                "name": act_def.name,
                "api_name": act_def.api_name or "(server-derived)",
                "association_mode": payload.get("association_mode", "(default)"),
                "fields": len(act_def.fields or []),
            },
            payload=payload,
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
    "associated_objects": "associated_objects",
    "loggable_sharing_settings": "loggable_sharing_settings",
}


def plan_update_activity(identifier: str, changes: dict[str, Any]) -> Plan:
    """Plan a PATCH to one activity type. Only keys present in ``changes`` are sent.

    ``selected_object_ids`` is sent as ``associated_objects``, which replaces
    the whole association set; objects already in it keep their flags.
    """
    try:
        check_association_keys(changes)
    except ValueError as e:
        raise PlanError(str(e)) from e
    changes = _with_associated_objects(changes, create=False)
    env = load_env_config().name
    rules = changes.get("visibility_rules")
    current = _load_activity(identifier, include_fields=rules is not None)
    if rules is not None:
        changes = {
            **changes,
            "visibility_rules": _expand_visibility_rules(rules, current, identifier),
        }

    raw = current.get("raw") or {}
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for key, wire in _UPDATABLE_ACTIVITY_KEYS.items():
        if key not in changes:
            continue
        new = changes[key]
        # visibility_rules / associations / sharing are structural — always
        # include when explicitly passed; scalar keys skip when unchanged.
        if key == "associated_objects":
            stored = raw.get(wire) or []
            payload[wire] = new
            diff[wire] = (
                _association_labels(stored, stored),
                _association_labels(new, stored),
            )
            continue
        if key == "visibility_rules":
            payload[wire] = new
            diff[wire] = (
                _describe_rules(current.get("visibility_rules"), current["fields"]),
                _describe_rules(new, current["fields"]),
            )
            continue
        if key == "loggable_sharing_settings":
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
# Visibility rules
# ---------------------------------------------------------------------------

_RULE_OPTION_TYPES = ("dropdown", "radio", "yesnomaybe")

# The editor's option lookup, verbatim from a UI-built rule.
_OPTION_NAMES_TRANSFORM = [
    "xf_hoist",
    [
        "xf_concat",
        [
            "xf_map",
            "@result_path/not_found/activity_options",
            {
                "id": ["xf_pick", "$", ["id"]],
                "name": ["xf_join", "[Deleted] ", ["xf_pick", "$", ["display_name"]]],
                "not_found": True,
                "error_message": "The option has been deleted",
            },
        ],
        [
            "xf_map",
            "@result_path/results/activity_options",
            {
                "id": ["xf_pick", "$", ["id"]],
                "name": ["xf_pick", "$", ["display_name"]],
            },
        ],
    ],
]


def _expand_visibility_rules(
    rules: list[Any], activity: dict[str, Any], identifier: str
) -> list[Any]:
    """Raw rules (a ``rule`` key) as given; each ``{show, when}`` rule expanded
    to the wire shape the Kizen editor writes."""
    if not isinstance(rules, list):
        raise PlanError("visibility rules must be a JSON array")
    out = []
    for n, entry in enumerate(rules, 1):
        if isinstance(entry, dict) and "rule" in entry:
            _check_raw_rule(n, entry)
            out.append(entry)
        else:
            out.append(_expand_rule(n, entry, activity, identifier))
    return out


def _check_raw_rule(n: int, entry: dict[str, Any]) -> None:
    """The editor loads each filter from its ``view_model``. One filter without
    it, on any rule, leaves the type's Advanced Rules list empty."""
    body = entry.get("rule")
    filters = body.get("filters") if isinstance(body, dict) else None
    for m, f in enumerate(filters or [], 1):
        if not isinstance(f, dict) or not f.get("view_model"):
            raise PlanError(
                f"visibility rule {n}: filter {m} has no view_model, and Kizen's "
                "editor then lists none of the type's rules. Write the rule by "
                'name ({"show": [...], "when": {...}}), or copy one the editor '
                "saved from kizen activities get --json"
            )


def _expand_rule(
    n: int, entry: Any, activity: dict[str, Any], identifier: str
) -> dict[str, Any]:
    def fail(message: str) -> PlanError:
        return PlanError(f"visibility rule {n}: {message}")

    if not isinstance(entry, dict):
        raise fail("a rule takes show and when")
    for key in entry:
        if key not in ("show", "when"):
            raise fail(f"unknown key '{key}'. A rule takes show and when")
    show, when = entry.get("show"), entry.get("when")
    if not isinstance(show, list) or not show or not isinstance(when, dict):
        raise fail(
            'a rule takes show (a list of fields) and when ({"field": ..., '
            '"is_any_of": [...]} or {"field": ..., "is_checked": true})'
        )
    for key in when:
        if key not in ("field", "is_any_of", "is_checked"):
            raise fail(
                f"unknown key '{key}' in when. when takes field, and is_any_of "
                "or is_checked"
            )
    if "field" not in when:
        raise fail("when.field names the field to test")

    def field(token: Any) -> dict[str, Any]:
        match = next(
            (f for f in activity["fields"] if token in (f.get("api_name"), f["id"])),
            None,
        )
        if match is None:
            raise fail(
                f"no field '{token}' on {identifier}. "
                f"kizen activities get {identifier} lists them"
            )
        return match

    tested = field(when["field"])
    label = tested.get("api_name") or tested["id"]
    field_type = _rule_field_type(tested)
    if field_type == "checkbox":
        if "is_any_of" in when or not isinstance(when.get("is_checked"), bool):
            raise fail(
                f'{label} is a checkbox field; test it with "is_checked": true or false'
            )
    elif field_type in _RULE_OPTION_TYPES:
        if "is_checked" in when:
            raise fail(f"{label} is a {field_type} field; test it with is_any_of")
    else:
        raise fail(
            f"{label} is a {field_type} field; rules can test dropdown, radio, "
            "yesnomaybe and checkbox fields"
        )
    shown = [field(token) for token in show]
    if any(f["id"] == tested["id"] for f in shown):
        raise fail(f"{label} can't be shown by its own answer")
    option_ids = []
    if field_type != "checkbox":
        wanted = when.get("is_any_of")
        if not isinstance(wanted, list) or not wanted:
            raise fail("when.is_any_of needs at least one option")
        options = tested.get("options") or []
        for token in wanted:
            match = next(
                (o for o in options if token in (o.get("name"), o["id"])), None
            )
            if match is None:
                raise fail(
                    f"'{token}' isn't an option of {label}. "
                    f"Options: {', '.join(o.get('name') or o['id'] for o in options)}"
                )
            option_ids.append(match["id"])

    field_ref = {
        "url": f"/activities/{activity['id']}/fields/{tested['id']}",
        "method": "GET",
        "error_message": {"404": "The field has been deleted"},
        "error_response": {"field_type": field_type},
    }
    if field_type == "checkbox":
        checked = when["is_checked"]
        filters = [
            {
                "type": "activity_field",
                "value": checked,
                "condition": "=",
                "view_model": [
                    ["field_id", field_ref],
                    ["condition", "is_checked" if checked else "is_not_checked"],
                ],
                "activity_field_id": tested["id"],
            }
        ]
    else:
        filters = [
            {
                "type": "activity_field",
                "value": option_id,
                "condition": "=",
                "view_model": [
                    ["field_id", field_ref],
                    ["condition", "equals"],
                    [
                        "value",
                        {
                            "url": "/records/display-names",
                            "body": {"activity_option_ids": [option_id]},
                            "method": "POST",
                            "result_path": [],
                            "result_transform": _OPTION_NAMES_TRANSFORM,
                        },
                    ],
                ],
                "activity_field_id": tested["id"],
            }
            for option_id in option_ids
        ]
    return {
        "rule": {"and": False, "filters": filters},
        "layout_items": [{"field_id": f["id"]} for f in shown],
    }


def _rule_field_type(field: dict[str, Any]) -> str:
    """A linked field is tested, and named in the editor, by its record field's type."""
    linked = field.get("custom_object_field")
    if field.get("field_type") == "activity_custom_field" and isinstance(linked, dict):
        return linked.get("field_type") or "activity_custom_field"
    return field.get("field_type") or ""


def _describe_rules(rules: list[Any] | None, fields: list[dict[str, Any]]) -> str:
    if not rules:
        return "(none)"
    return "; ".join(
        (isinstance(r, dict) and describe_visibility_rule(r, fields)) or "raw rule"
        for r in rules
    )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _with_associated_objects(spec: dict[str, Any], *, create: bool) -> dict[str, Any]:
    """Swap ``selected_object_ids`` for the ``associated_objects`` they mean.

    POST rejects an entry without ``is_required``/``is_readonly``. PATCH keeps
    the stored flags of an object already in the set when its entry omits them,
    so an update leaves them out rather than resetting them. Without an
    ``association_mode`` the server picks ``all_objects_associated`` even when a
    list is sent, so a non-empty list defaults it.
    """
    out = dict(spec)
    ids = out.pop("selected_object_ids", None)
    if ids is not None:
        entries: list[dict[str, Any]] = [
            {"custom_object": {"id": object_id}, "order": i}
            for i, object_id in enumerate(ids)
        ]
        if create:
            for entry in entries:
                entry.update(is_required=False, is_readonly=False)
        out["associated_objects"] = entries
    if out.get("associated_objects") and out.get("association_mode") is None:
        out["association_mode"] = "selected_objects_associated"
    return out


def _association_labels(
    entries: list[dict[str, Any]], stored: list[dict[str, Any]]
) -> str:
    """``patients (required), <uuid>``: what the read will show after the PATCH."""
    by_id = {e["custom_object"]["id"]: e for e in stored}
    labels = []
    for entry in entries:
        object_id = entry["custom_object"]["id"]
        old = by_id.get(object_id, {})
        flags = [
            word
            for key, word in (("is_required", "required"), ("is_readonly", "read-only"))
            if entry.get(key, old.get(key, False))
        ]
        name = (
            old.get("custom_object", {}).get("name")
            or entry["custom_object"].get("name")
            or object_id
        )
        labels.append(f"{name} ({', '.join(flags)})" if flags else name)
    return ", ".join(labels) or "(none)"


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
        "selected_object_ids": act.selected_object_ids,
        "associated_objects": act.associated_objects,
        "loggable_sharing_settings": act.loggable_sharing_settings,
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return _with_associated_objects(payload, create=True)


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

    options = field.options
    if options is None and field.field_type == "yesnomaybe":
        # Kizen 400s an activity yesnomaybe field without options
        # (confirmed live 2026-11-09).
        options = ["Yes", "No", "Maybe"]
    if options is not None:
        if field.field_type == "yesnomaybe":
            payload["options"] = [{"name": o, "code": o.lower()} for o in options]
        else:
            payload["options"] = [{"name": o, "code": o} for o in options]
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


# ---------------------------------------------------------------------------
# Scheduled activities (instances)
# ---------------------------------------------------------------------------


def _resolve_records(records: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """``(object api_name or UUID, record UUID)`` → ``(object UUID, record UUID)``."""
    objects = list_objects()
    by_ref = {o["api_name"]: o for o in objects} | {o["id"]: o for o in objects}
    resolved = []
    for obj_ref, record_id in records:
        obj = by_ref.get(obj_ref)
        if obj is None:
            raise PlanError(
                f"object '{obj_ref}' not found. "
                f"Available: {sorted(o['api_name'] for o in objects)}"
            )
        resolved.append((obj["id"], record_id))
    return resolved


_NOTIFICATION_TYPES = ("email", "text")
_NOTIFICATION_UNITS = ("minute", "hour", "day")


def _due_datetime(due: str) -> str:
    """An ISO datetime with an offset or ``Z`` → its normalized ISO string."""
    try:
        due_at = datetime.fromisoformat(due)
    except ValueError as e:
        raise PlanError(f"--due '{due}' is not an ISO datetime") from e
    if due_at.tzinfo is None:
        raise PlanError(
            f"--due '{due}' has no UTC offset; add one, e.g. {due}Z or {due}-05:00"
        )
    return due_at.isoformat()


def _notification(spec: str) -> dict[str, Any]:
    """``type:amount:unit`` (``email:1:day``) → a notification dict."""
    parts = spec.split(":")
    if (
        len(parts) != 3
        or parts[0] not in _NOTIFICATION_TYPES
        or not parts[1].isdigit()
        or int(parts[1]) < 1
        or parts[2] not in _NOTIFICATION_UNITS
    ):
        raise PlanError(
            f"--notification '{spec}' must be type:amount:unit, with type "
            f"{'|'.join(_NOTIFICATION_TYPES)}, amount at least 1 and unit "
            f"{'|'.join(_NOTIFICATION_UNITS)} (e.g. email:1:day)"
        )
    return {"type": parts[0], "time_amount": int(parts[1]), "time_unit": parts[2]}


def _assignment(assignee: str | None, role: str | None) -> dict[str, str]:
    """``{"employee_id": ...}`` or ``{"role_id": ...}``; exactly one is allowed."""
    if bool(assignee) == bool(role):
        raise PlanError("pass exactly one of --assignee or --role")
    if role:
        try:
            return {"role_id": resolve_role(role)["id"]}
        except LookupError as e:
            raise PlanError(str(e)) from e
    with KizenClient(load_env_config()) as client:
        try:
            return {"employee_id": resolve_team_member_id(client, str(assignee))}
        except LookupError as e:
            raise PlanError(str(e)) from e


def _associated_entities(records: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {"custom_object": {"id": obj_id}, "entity": {"id": record_id}}
        for obj_id, record_id in _resolve_records(records)
    ]


def plan_schedule_activity(
    identifier: str,
    due: str,
    records: list[tuple[str, str]],
    *,
    assignee: str | None = None,
    role: str | None = None,
    note: str | None = None,
    notifications: Sequence[str] = (),
) -> Plan:
    """Plan scheduling one instance of an activity type.

    ``due`` is an ISO datetime with an offset or ``Z``. Assign it to a team
    member (``assignee``: email, name or UUID) or a ``role`` (name or UUID).
    ``records`` are ``(object api_name or UUID, record UUID)`` pairs, and each
    of ``notifications`` is ``type:amount:unit``.
    """
    due_datetime = _due_datetime(due)
    notification_list = [_notification(n) for n in notifications]
    config = load_env_config()
    activity = _load_activity(identifier, include_fields=False)

    payload: dict[str, Any] = {
        "activity_object_id": activity["id"],
        "due_datetime": due_datetime,
        **_assignment(assignee, role),
        "associated_entities": _associated_entities(records),
    }
    if note:
        payload["note"] = note
    if notification_list:
        payload["notifications"] = notification_list

    op = PlanOperation(
        action="create",
        kind="scheduled_activity",
        key=f"{activity['api_name']}#schedule",
        preview={
            "env": config.name,
            "activity": activity["api_name"],
            "due": due_datetime,
            **({"assignee": assignee} if assignee else {"role": role}),
            "records": [f"{o}:{r}" for o, r in records],
            **({"note": note} if note else {}),
            **({"notifications": list(notifications)} if notifications else {}),
        },
        payload=payload,
    )
    return Plan.build(
        env=config.name,
        summary=f"Schedule one '{activity['name']}' activity",
        operations=[op],
    )


def _load_scheduled(scheduled_id: str) -> dict[str, Any]:
    return fetch_existing(
        "scheduled activity", scheduled_id, lambda: get_scheduled(scheduled_id)
    )


def _scheduled_summary(scheduled: dict[str, Any]) -> dict[str, Any]:
    """The parts of a scheduled activity a preview shows."""
    employee = scheduled.get("employee") or {}
    role = scheduled.get("role") or {}
    return {
        "activity": (scheduled.get("activity_object") or {}).get("name"),
        "due": scheduled.get("due_datetime"),
        "assignee": employee.get("email") or role.get("name"),
        "records": [
            e.get("display_name") or e.get("entity_id")
            for e in scheduled.get("associated_entities") or []
        ],
        "completed": scheduled.get("completed_at") is not None,
    }


def plan_update_scheduled_activity(
    scheduled_id: str,
    *,
    due: str | None = None,
    assignee: str | None = None,
    role: str | None = None,
    note: str | None = None,
    records: list[tuple[str, str]] | None = None,
    notifications: Sequence[str] | None = None,
    clear_notifications: bool = False,
) -> Plan:
    """Plan a PATCH of one scheduled activity, sending only what's given.

    ``records`` and ``notifications`` replace their whole lists. Records
    can't be cleared: the API ignores an empty list.
    """
    if notifications and clear_notifications:
        raise PlanError("pass --notification or --clear-notifications, not both")
    if records == []:
        raise PlanError("records can't be cleared; pass at least one --record")
    scheduled = _load_scheduled(scheduled_id)

    payload: dict[str, Any] = {}
    if due:
        payload["due_datetime"] = _due_datetime(due)
    if assignee or role:
        payload.update(_assignment(assignee, role))
    if note is not None:
        payload["note"] = note
    if records:
        payload["associated_entities"] = _associated_entities(records)
    if notifications:
        payload["notifications"] = [_notification(n) for n in notifications]
    if clear_notifications:
        payload["notifications"] = []
    if not payload:
        raise PlanError("nothing to change; pass at least one option")

    env = load_env_config().name
    op = PlanOperation(
        action="update",
        kind="scheduled_activity",
        key=f"scheduled:{scheduled_id}",
        preview={
            "env": env,
            **_scheduled_summary(scheduled),
            "changes": sorted(payload),
        },
        payload=payload,
        existing_uuid=scheduled_id,
    )
    return Plan.build(
        env=env, summary=f"Update scheduled activity {scheduled_id}", operations=[op]
    )


def plan_delete_scheduled_activity(scheduled_id: str) -> Plan:
    """Plan a hard delete of one scheduled activity."""
    scheduled = _load_scheduled(scheduled_id)
    env = load_env_config().name
    op = PlanOperation(
        action="delete",
        kind="scheduled_activity",
        key=f"scheduled:{scheduled_id}",
        preview={"env": env, **_scheduled_summary(scheduled), "permanent": True},
        existing_uuid=scheduled_id,
    )
    return Plan.build(
        env=env,
        summary=f"Permanently delete scheduled activity {scheduled_id}",
        operations=[op],
    )

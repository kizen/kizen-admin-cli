"""Read tools for activities (activity types) and their logged/scheduled
instances in a Kizen environment.

An "activity" here is the activity **type** (a loggable definition with custom
fields + visibility rules). Logged and scheduled activities are read-only
instances of a type.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import activities as act_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config


def list_activities(
    *, custom_object_id: str | None = None, search: str | None = None
) -> list[dict[str, Any]]:
    """Return a summary of every activity type in the configured env."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = act_api.list_activities(
            client, custom_object_id=custom_object_id, search=search
        )

    out: list[dict[str, Any]] = []
    for a in raw:
        out.append(
            {
                "env": config.name,
                "id": a.get("id"),
                "name": a.get("name"),
                "api_name": a.get("api_name"),
                "n_submissions": a.get("n_submissions"),
                "is_editable": a.get("is_editable"),
                "association_mode": a.get("association_mode"),
                "deleted": a.get("deleted", False),
                "created": a.get("created"),
            }
        )
    return out


def resolve_activity_id(client: KizenClient, identifier: str) -> tuple[str, str]:
    """Resolve an activity identifier (api_name or UUID) to (id, name).

    The API accepts either in the path, but we resolve so callers get the
    real UUID and a display name. Raises LookupError if not found.
    """
    try:
        detail = act_api.get_activity(client, identifier)
        return detail["id"], detail.get("name") or identifier
    except KizenAPIError as e:
        if e.status_code != 404:
            raise
        # Fall back to a name/api_name scan of the list endpoint.
        for a in act_api.list_activities(client):
            if identifier in (a.get("id"), a.get("api_name"), a.get("name")):
                return a["id"], a.get("name") or identifier
        raise LookupError(f"activity '{identifier}' not found") from e


def _linked_field_label(cof: Any) -> str | None:
    """For an activity_custom_field, name the custom-object field it references
    as ``<object_api_name>.<field_api_name>``. These surface an existing record
    field on the activity (view-only or editable). Reads return an expanded
    dict; writes send a bare UUID (nothing to describe)."""
    if not isinstance(cof, dict):
        return None
    field_name = cof.get("name")
    obj = cof.get("custom_object")
    obj_name = obj.get("name") if isinstance(obj, dict) else None
    if obj_name and field_name:
        return f"{obj_name}.{field_name}"
    return field_name


def _normalize_field(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f.get("id"),
        "api_name": f.get("name"),
        "display_name": f.get("display_name"),
        "description": f.get("description"),
        "field_type": f.get("field_type"),
        "custom_object_field": f.get("custom_object_field"),
        "linked_field": _linked_field_label(f.get("custom_object_field")),
        "is_default": f.get("is_default"),
        "is_required": f.get("is_required"),
        "is_read_only": f.get("is_read_only"),
        "is_hidden": f.get("is_hidden"),
        "is_deletable": f.get("is_deletable"),
        "order": f.get("order"),
        "options": [
            {"id": o.get("id"), "name": o.get("name"), "code": o.get("code")}
            for o in (f.get("options") or [])
        ]
        or None,
        "relation": f.get("relation"),
    }


def get_activity(identifier: str, *, include_fields: bool = True) -> dict[str, Any]:
    """Return one activity type plus its fields and visibility rules.

    ``identifier`` may be the api_name or the UUID.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = act_api.get_activity(client, identifier)
        fields: list[dict[str, Any]] = []
        if include_fields:
            fields = act_api.list_activity_fields(client, detail["id"])

    custom_objects = detail.get("custom_objects") or []

    return {
        "env": config.name,
        "id": detail.get("id"),
        "name": detail.get("name"),
        "api_name": detail.get("api_name"),
        "description": detail.get("description"),
        "is_editable": detail.get("is_editable"),
        "association_mode": detail.get("association_mode"),
        "submission_action": detail.get("submission_action"),
        "webhook_url": detail.get("webhook_url"),
        "redirect_url": detail.get("redirect_url"),
        "n_submissions": detail.get("n_submissions"),
        "visibility_rules": detail.get("visibility_rules"),
        "calendar_sync_enabled": detail.get("calendar_sync_enabled"),
        "custom_objects": [
            {"id": o.get("id"), "name": o.get("name") or o.get("object_name")}
            for o in custom_objects
        ],
        "associated_objects": detail.get("associated_objects") or [],
        "loggable_sharing_settings": detail.get("loggable_sharing_settings"),
        "deleted": detail.get("deleted", False),
        "created": detail.get("created"),
        "fields": [_normalize_field(f) for f in fields],
        "raw": detail,
    }


# ---------------------------------------------------------------------------
# Logged activity instances (read-only)
# ---------------------------------------------------------------------------


def get_logged_activity(logged_id: str) -> dict[str, Any]:
    """Return one logged activity instance with its field values."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = act_api.get_logged_activity(client, logged_id)

    activity_object = raw.get("activity_object") or {}
    return {
        "env": config.name,
        "id": raw.get("id"),
        "activity_name": activity_object.get("name"),
        "activity_id": activity_object.get("id"),
        "notes": raw.get("notes"),
        "logged_at": raw.get("logged_at"),
        "logged_by": _employee_label(raw.get("logged_by")),
        "completed_at": raw.get("completed_at"),
        "completed_by": _employee_label(raw.get("completed_by")),
        "scheduled_activity_id": raw.get("scheduled_activity_id"),
        "associated_entities": [
            {
                "object_api_name": e.get("object_api_name"),
                "entity_id": e.get("entity_id"),
                "display_name": e.get("display_name") or e.get("name"),
            }
            for e in (raw.get("associated_entities") or [])
        ],
        "fields": [
            {
                "api_name": f.get("name"),
                "display_name": f.get("display_name"),
                "field_type": f.get("field_type"),
                "value": f.get("value"),
            }
            for f in (raw.get("fields") or [])
        ],
        "raw": raw,
    }


def list_logged(
    identifier: str, *, custom_object_id: str | None = None, search: str | None = None
) -> list[dict[str, Any]]:
    """Return logged instances of one activity type (via the responses list)."""
    config = load_env_config()
    with KizenClient(config) as client:
        act_id, _name = resolve_activity_id(client, identifier)
        raw = act_api.list_responses(
            client, act_id, custom_object_id=custom_object_id, search=search
        )

    out: list[dict[str, Any]] = []
    for r in raw:
        entities = r.get("associated_entities") or []
        out.append(
            {
                "env": config.name,
                "id": r.get("id"),
                "logged_at": r.get("logged_at"),
                "logged_by": _employee_label(r.get("logged_by")),
                "associated": ", ".join(
                    e.get("display_name") or e.get("name") or "" for e in entities
                ),
                "fields_with_values": r.get("fields_with_values"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Scheduled activity instances (read-only)
# ---------------------------------------------------------------------------


def list_scheduled(
    *,
    activity: str | None = None,
    assigned_to_me: bool | None = None,
    completed: bool | None = None,
) -> list[dict[str, Any]]:
    """Return scheduled activity instances, optionally filtered by activity type."""
    config = load_env_config()
    with KizenClient(config) as client:
        activity_id: str | None = None
        if activity:
            activity_id, _name = resolve_activity_id(client, activity)
        raw = act_api.list_scheduled(
            client,
            activity_id=activity_id,
            assigned_to_me=assigned_to_me,
            completed=completed,
        )

    out: list[dict[str, Any]] = []
    for s in raw:
        entities = s.get("associated_entities") or []
        out.append(
            {
                "env": config.name,
                "id": s.get("id"),
                "activity_object": s.get("activity_object"),
                "due_datetime": s.get("due_datetime"),
                "completed_at": s.get("completed_at"),
                "logged_activity_id": s.get("logged_activity_id"),
                "employee": s.get("employee"),
                "associated": ", ".join(
                    (e.get("display_name") or e.get("name") or "")
                    if isinstance(e, dict)
                    else str(e)
                    for e in entities
                ),
            }
        )
    return out


def get_scheduled(scheduled_id: str) -> dict[str, Any]:
    """Return one scheduled activity instance."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = act_api.get_scheduled(client, scheduled_id)
    raw["env"] = config.name
    return raw


def _employee_label(emp: Any) -> str | None:
    if not emp:
        return None
    if isinstance(emp, dict):
        return (
            emp.get("display_name")
            or emp.get("full_name")
            or emp.get("name")
            or emp.get("email")
            or emp.get("id")
        )
    return str(emp)

"""Where-used: what in Kizen uses a resource, and the warning a delete shows.

Every references endpoint returns the same loose shape, so one reader covers
them all: a top-level integer (or a ``{count, …}`` dict) is a count, and a
top-level list is a section of things that use the target. A new resource
needs only its API call; nothing here branches on which endpoint answered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kizen_builder.api import references as ref_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import permissions as perm_tools
from kizen_builder.tools.objects import get_object

ReferencesCall = Callable[[KizenClient], dict[str, Any]]

_COUNT_LABELS = {
    "entities_with_values": "records with a value",
    "team": "team members",
    "scheduled_activities": "scheduled activities",
    "logged_activities": "logged activities",
}

# Keys that identify an item rather than say where it uses the target.
_IDENTITY_KEYS = {
    "id",
    "asset_id",
    "display_name",
    "name",
    "can_view",
    "object_id",
    "custom_object_id",
}

_PREVIEW_NAMES = 3


class WhereUsedRefused(Exception):
    """Kizen answered the where-used lookup itself with a 403."""


def _humanize(key: str) -> str:
    return key.replace("_", " ")


def _name(item: dict[str, Any]) -> str:
    return str(item.get("display_name") or item.get("name") or "(unnamed)")


def _where(item: dict[str, Any]) -> str:
    """Say where the item uses the target. The named parts (the steps,
    triggers, dashlets…) lead; plain values such as ``use_types`` follow."""
    named, plain = [], []
    for key, value in item.items():
        if key in _IDENTITY_KEYS or value is None or value == "" or value == []:
            continue
        label = _humanize(key)
        if isinstance(value, list) and isinstance(value[0], dict):
            named.append(f"{label}: {', '.join(_name(v) for v in value)}")
        elif isinstance(value, list):
            plain.append(f"{label}: {', '.join(str(v) for v in value)}")
        elif isinstance(value, dict):
            named.append(f"{label}: {_name(value)}")
        else:
            plain.append(f"{label}: {value}")
    return "; ".join(named + plain)


def normalize(body: dict[str, Any]) -> dict[str, Any]:
    """Read any references response into ``{"counts": […], "used_by": […]}``."""
    counts: list[dict[str, Any]] = []
    used_by: list[dict[str, Any]] = []
    for key, value in body.items():
        if isinstance(value, dict) and "count" in value:
            value = value["count"]
        if isinstance(value, int) and not isinstance(value, bool):
            counts.append(
                {"label": _COUNT_LABELS.get(key, _humanize(key)), "count": value}
            )
        elif isinstance(value, list):
            used_by += [
                {
                    "section": _humanize(key),
                    "name": _name(item),
                    "id": item.get("id") or item.get("asset_id") or "",
                    "where": _where(item),
                    "can_view": item.get("can_view", True),
                }
                for item in value
                if isinstance(item, dict)
            ]
    return {"counts": counts, "used_by": used_by}


def used_by_line(normalized: dict[str, Any], command: str) -> str:
    """One line for a delete preview: what uses the target, in brief."""
    sections: dict[str, list[str]] = {}
    for row in normalized["used_by"]:
        sections.setdefault(row["section"], []).append(row["name"])
    parts = []
    for section, names in sections.items():
        unique = list(dict.fromkeys(names))
        shown = ", ".join(unique[:_PREVIEW_NAMES])
        more = len(unique) - _PREVIEW_NAMES
        if more > 0:
            shown += f", +{more} more"
        parts.append(f"{section}: {len(names)} ({shown})")
    parts += [f"{c['label']}: {c['count']}" for c in normalized["counts"] if c["count"]]
    if not parts:
        return "nothing found"
    return "; ".join(parts) + f" — run `{command}` for the list"


def api_error_detail(e: KizenAPIError) -> str:
    """The server's own reason, without the client's credentials advice."""
    if isinstance(e.body, dict) and isinstance(e.body.get("detail"), str):
        return e.body["detail"]
    if e.body is None:  # no response at all, e.g. a network error
        return e.message
    return "no detail in the response"


def _get(call: ReferencesCall) -> dict[str, Any]:
    with KizenClient(load_env_config()) as client:
        return call(client)


def where_used(
    call: ReferencesCall, target: str, refused_hint: str = ""
) -> dict[str, Any]:
    """Run one references GET and normalize it. A 403 on the lookup itself
    raises `WhereUsedRefused`, worded from Kizen's detail, never the client's
    credentials advice."""
    try:
        body = _get(call)
    except KizenAPIError as e:
        if e.status_code != 403:
            raise
        message = (
            f"Kizen refused the where-used lookup for {target} "
            f"(HTTP 403: {api_error_detail(e)})."
        )
        raise WhereUsedRefused(f"{message} {refused_hint}".strip()) from e
    return normalize(body)


def preview_used_by(call: ReferencesCall, command: str) -> str:
    """The ``used_by`` preview value. A failed lookup never blocks the plan."""
    try:
        return used_by_line(normalize(_get(call)), command)
    except KizenAPIError as e:
        return f"unknown (HTTP {e.status_code}: {api_error_detail(e)})"


def field_where_used(object_api_name: str, field_api_name: str) -> dict[str, Any]:
    """Everything Kizen reports using one custom-object field."""
    # Local import: the field planner imports this module for its preview.
    from kizen_builder.tools.planners.fields import find_field

    try:
        obj = get_object(object_api_name)
    except KizenAPIError as e:
        if e.status_code != 404:
            raise
        raise LookupError(
            f"object '{object_api_name}' not found: {api_error_detail(e)}"
        ) from e
    field = find_field(obj, field_api_name)
    result = where_used(
        lambda c: ref_api.field_references(c, obj["id"], field["id"]),
        f"{object_api_name}.{field_api_name}",
        "It refuses it for the built-in created, updated and display_name fields.",
    )
    return {
        "target": {
            "object": object_api_name,
            "field": field_api_name,
            "field_id": field["id"],
        },
        **result,
    }


def role_where_used(ref: str) -> dict[str, Any]:
    """Everything Kizen reports naming one role (by name or UUID)."""
    role = perm_tools.resolve_role(ref)
    result = where_used(
        lambda c: ref_api.role_references(c, role["id"]), f"role '{role['name']}'"
    )
    return {"target": {"role": role["name"], "id": role["id"]}, **result}


def group_where_used(ref: str) -> dict[str, Any]:
    """Everything Kizen reports using one permission group (by name or UUID)."""
    group = perm_tools.resolve_group(ref)
    result = where_used(
        lambda c: ref_api.permission_group_references(c, group["id"]),
        f"permission group '{group['name']}'",
    )
    return {"target": {"group": group["name"], "id": group["id"]}, **result}

"""Read tools for custom objects in a Kizen environment."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import pipelines as pipelines_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config


def _normalize_stage(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "status": s.get("status"),
        "percentage_chance_to_close": s.get("percentage_chance_to_close"),
        "order": s.get("order"),
    }


def list_objects() -> list[dict[str, Any]]:
    """Return a summary of every object in the configured env, custom and built-in.

    ``custom_only=False`` so built-ins like ``client_client`` (Contacts) come
    back alongside custom objects in the same paginated call — the pattern
    already used by ``schema.py`` and the smart-connector authoring helpers.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        raw = co_api.list_objects(client, custom_only=False)

    out: list[dict[str, Any]] = []
    for obj in raw:
        out.append(
            {
                "env": config.name,
                "id": obj.get("id"),
                "api_name": obj.get("name"),
                "display_name": obj.get("object_name") or obj.get("name"),
                "entity_name": obj.get("entity_name"),
                "object_type": obj.get("object_type"),
                "deleted": obj.get("deleted", False),
            }
        )
    return out


def _relation_target(
    relation: dict[str, Any] | None, id_to_api_name: dict[str, Any]
) -> str | None:
    """Resolve a relationship field's target object to a readable api_name.

    Read responses expand the relation block with ``related_object_object_name``
    (the target's api_name) — prefer it. Fall back to resolving the
    ``related_object`` UUID against the object list, and finally to the raw
    UUID so the caller always gets *something* identifying the target.
    """
    if not relation:
        return None
    api_name = relation.get("related_object_object_name")
    if api_name:
        return api_name
    related_uuid = relation.get("related_object")
    if related_uuid:
        return id_to_api_name.get(related_uuid) or related_uuid
    return None


def get_object(api_name: str) -> dict[str, Any]:
    """Return one object plus its categories and fields.

    Works for custom objects (looked up by api_name) and for special identifiers
    like ``client_client`` that aren't in the custom-objects list but are
    accepted directly as a path parameter by the API.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        objs = co_api.list_objects(client)
        match = next(
            (o for o in objs if o.get("name") == api_name and o.get("is_custom", True)),
            None,
        )
        if match is None:
            # Not a custom object — try resolving directly. GET
            # /api/custom-objects/{identifier} accepts special identifiers
            # like client_client and returns the object's real UUID (needed
            # anywhere the caller uses `id`, e.g. as a relation target).
            try:
                match = co_api.get_object(client, api_name)
            except KizenAPIError as exc:
                if exc.status_code != 404:
                    raise
                match = None
        obj_id = match["id"] if match else api_name
        categories = co_api.list_categories(client, obj_id)
        fields = co_api.list_fields(client, obj_id)
        object_type = match.get("object_type") if match else None
        stages = (
            [_normalize_stage(s) for s in pipelines_api.list_stages(client, obj_id)]
            if object_type == "pipeline"
            else None
        )

    # UUID → api_name map, so a relationship field's target UUID can be
    # resolved to a readable api_name when the relation block doesn't already
    # carry it.
    id_to_api_name = {o["id"]: o.get("name") for o in objs if o.get("id")}

    return {
        "env": config.name,
        "id": obj_id,
        "api_name": match.get("name") if match else api_name,
        "display_name": (match.get("object_name") or match.get("name"))
        if match
        else api_name,
        "entity_name": match.get("entity_name") if match else None,
        "object_type": object_type,
        "stages": stages,
        "categories": [
            {
                "id": c["id"],
                "name": c.get("name"),
                "order": c.get("order"),
            }
            for c in categories
        ],
        "fields": [
            {
                "id": f["id"],
                "api_name": f.get("name"),
                "display_name": f.get("display_name"),
                "description": f.get("description"),
                "field_type": f.get("field_type"),
                "category_id": f.get("category"),
                "is_required": f.get("is_required"),
                "deleted": f.get("deleted", False),
                "relation": f.get("relation"),
                "relation_target": _relation_target(f.get("relation"), id_to_api_name),
                "relation_cardinality": (f.get("relation") or {}).get("cardinality"),
                "options": [
                    {"id": o["id"], "name": o.get("name"), "code": o.get("code")}
                    for o in (f.get("options") or [])
                ]
                or None,
            }
            for f in fields
        ],
        "raw": match,
    }

"""Read tools for individual records in a Kizen environment."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import records as records_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config


def get_record(object_api_name: str, record_id: str) -> dict[str, Any]:
    """Fetch one record by UUID from a custom object."""
    config = load_env_config()
    with KizenClient(config) as client:
        record = records_api.get_record(client, object_api_name, record_id)
    return {"env": config.name, "object_api_name": object_api_name, **record}


def _record_name(record: dict[str, Any]) -> str:
    """Best-effort display name for a record (top-level or a ``name`` field)."""
    name = record.get("name")
    if name:
        return str(name)
    for fdata in record.get("fields", {}).values():
        if fdata.get("name") == "name":
            return str(fdata.get("value") or "")
    return ""


def get_record_by_name(object_api_name: str, name: str) -> dict[str, Any]:
    """Fetch one record whose name matches ``name`` exactly.

    Uses the API's text search to narrow, then filters to exact
    (case-insensitive) name matches so a substring hit doesn't win. Raises
    ``LookupError`` if nothing matches and ``ValueError`` if more than one
    record shares the name (with their UUIDs, so the caller can pick one).
    """
    config = load_env_config()
    with KizenClient(config) as client:
        candidates = records_api.search_records(
            client, object_api_name, search=name, page_size=100, limit=100
        )
    exact = [r for r in candidates if _record_name(r).lower() == name.lower()]
    if not exact:
        raise LookupError(
            f"no record named '{name}' in {object_api_name}"
            + (
                f" ({len(candidates)} partial match(es) — try records list --search)"
                if candidates
                else ""
            )
        )
    if len(exact) > 1:
        ids = ", ".join(r.get("id", "?") for r in exact)
        raise ValueError(
            f"{len(exact)} records in {object_api_name} are named '{name}': {ids}. "
            "Pass the UUID to records get instead."
        )
    record = exact[0]
    return {"env": config.name, "object_api_name": object_api_name, **record}


def search_records(
    object_api_name: str,
    filters: list[dict[str, Any]] | None = None,
    search: str | None = None,
    field_names: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search records in a custom object, returning up to ``limit`` results.

    ``search`` is an optional text string forwarded to the API's ``?search=``
    query param for fast keyword filtering without building filter groups.

    ``field_names`` limits which field values come back (bandwidth, and lets
    callers ask for exactly the columns they want). Every entry must be a
    live, undeleted field's api_name on ``object_api_name`` — an unrecognized
    name raises ``LookupError`` (same wording as the write-side validation in
    `tools/planners/records.py`) *before* any search request is sent, since
    the server itself accepts and silently drops an unknown name instead of
    rejecting it. The contract for every caller, CLI or not, is ``id`` +
    ``name`` + whatever was asked for: ``name`` is added to the wire request
    if the caller's list omits it, so an explicit ``[]`` still returns
    ``name`` and nothing else. Omit ``field_names`` (``None``, the default)
    to get every field instead — the server's own default.
    """
    wire_field_names = None
    if field_names is not None:
        from kizen_builder.tools.objects import get_object

        obj = get_object(object_api_name)
        index = {
            f["api_name"]: f
            for f in obj["fields"]
            if f.get("api_name") and not f.get("deleted")
        }
        for name in field_names:
            if name not in index:
                available = sorted(index)
                raise LookupError(
                    f"field '{name}' not found on '{object_api_name}'. "
                    f"Available: {available}"
                )
        wire_field_names = list(field_names)
        if "name" not in wire_field_names:
            wire_field_names.append("name")

    config = load_env_config()
    with KizenClient(config) as client:
        results = records_api.search_records(
            client,
            object_api_name,
            filters=filters,
            search=search,
            field_names=wire_field_names,
            page_size=min(limit, 500),
            limit=limit,
        )
    return results[:limit]


def active_record_ids(object_api_name: str) -> list[str]:
    """Every active record's id on ``object_api_name``, across all pages.

    Archived records drop out of search, so this is exactly the set Kizen
    makes you archive before it will delete the object. Deduped, because an
    offset page that shifts between requests can repeat a record.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        records = records_api.search_records(
            client, object_api_name, field_names=["name"], page_size=500
        )
    return list(dict.fromkeys(r["id"] for r in records))


def related_records(
    record_id: str,
    object_ids: list[str] | None = None,
    field_ids: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List a record's related pipeline records.

    Works across objects — ``record_id`` is any record's UUID, no object
    identifier needed.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        results = records_api.related_pipeline_records(
            client,
            record_id,
            object_ids=object_ids,
            field_ids=field_ids,
            page_size=min(limit, 500),
            limit=limit,
        )
    return results[:limit]


def field_values(record_id: str, field: str, limit: int = 200) -> list[dict[str, Any]]:
    """Pull all values from a summarized relationship field on one record.

    ``field`` accepts a bare field UUID, or an 'object_api_name.field_api_name'
    ref (resolved against the live schema — same convention as automation
    field_refs) since this endpoint's UUID-only path gives no other way to
    identify which object's field is meant.
    """
    from kizen_builder.tools.objects import get_object
    from kizen_builder.utils import is_uuid

    field_id = field
    if not is_uuid(field):
        if "." not in field:
            raise ValueError(f"field '{field}' is not a UUID or an 'object.field' ref")
        obj_api, fld_api = field.split(".", 1)
        obj = get_object(obj_api)
        match = next((f for f in obj["fields"] if f["api_name"] == fld_api), None)
        if match is None:
            available = [f["api_name"] for f in obj["fields"]]
            raise LookupError(
                f"field '{fld_api}' not found on '{obj_api}'. Available: {available}"
            )
        field_id = match["id"]

    config = load_env_config()
    with KizenClient(config) as client:
        results = records_api.field_values(
            client,
            record_id,
            field_id,
            page_size=min(limit, 500),
            limit=limit,
        )
    return results[:limit]

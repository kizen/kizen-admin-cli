"""Custom-object / category / field CRUD against the Kizen API.

Each method takes a raw payload dict (built by the planner from Pydantic models)
and returns the parsed JSON response. Errors surface as KizenAPIError.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient

# ---------------------------------------------------------------------------
# Custom objects
# ---------------------------------------------------------------------------


def create_object(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/custom-objects. Returns the created object including its UUID."""
    return client.post("/api/custom-objects", json=payload)


def update_object(
    client: KizenClient, object_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/custom-objects/{id}."""
    return client.patch(f"/api/custom-objects/{object_id}", json=payload)


def get_object(client: KizenClient, object_id: str) -> dict[str, Any]:
    return client.get(f"/api/custom-objects/{object_id}")


def delete_object(client: KizenClient, object_id: str) -> dict[str, Any]:
    """DELETE /api/custom-objects/{id}. Archives the object and its data."""
    resp = client.delete(f"/api/custom-objects/{object_id}")
    return resp if isinstance(resp, dict) else {}


def list_objects(
    client: KizenClient, *, custom_only: bool = True
) -> list[dict[str, Any]]:
    """GET /api/custom-objects, transparently paginated.

    Returns the flat list of result dicts. Each dict's `name` is the api_name
    and `object_name` is the display name; `id` is the UUID.

    ``custom_only=False`` also includes built-in objects like ``client_client``
    (contacts) — the server excludes them by default.
    """
    items: list[dict[str, Any]] = []
    path: str | None = "/api/custom-objects"
    params: dict[str, str] | None = None if custom_only else {"custom_only": "false"}
    while path:
        resp = client.get(path, params=params)
        params = None  # `next` URLs carry their own querystring
        # DRF-style paginator
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            # `next` is a full URL; strip to path so httpx reuses base_url auth
            if nxt:
                # relative path + querystring
                from urllib.parse import urlsplit

                parts = urlsplit(nxt)
                path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                path = None
        elif isinstance(resp, list):
            items.extend(resp)
            path = None
        else:
            path = None
    return items


# ---------------------------------------------------------------------------
# Field categories
# ---------------------------------------------------------------------------


def create_category(
    client: KizenClient, object_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_id}/categories."""
    return client.post(f"/api/custom-objects/{object_id}/categories", json=payload)


def update_category(
    client: KizenClient,
    object_id: str,
    category_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return client.patch(
        f"/api/custom-objects/{object_id}/categories/{category_id}", json=payload
    )


def delete_category(
    client: KizenClient, object_id: str, category_id: str
) -> dict[str, Any]:
    """DELETE /api/custom-objects/{object_id}/categories/{category_id}."""
    resp = client.delete(f"/api/custom-objects/{object_id}/categories/{category_id}")
    return resp if isinstance(resp, dict) else {}


def list_categories(client: KizenClient, object_id: str) -> list[dict[str, Any]]:
    """GET /api/custom-objects/{object_id}/categories.

    Returns a plain list of {id, name (display), order, meta} dicts. Kizen does
    not currently surface an api_name for categories — callers should slugify
    `name` to derive a spec-style key.
    """
    resp = client.get(f"/api/custom-objects/{object_id}/categories")
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


def create_field(
    client: KizenClient, object_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_id}/fields."""
    return client.post(f"/api/custom-objects/{object_id}/fields", json=payload)


def update_field(
    client: KizenClient,
    object_id: str,
    field_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return client.patch(
        f"/api/custom-objects/{object_id}/fields/{field_id}", json=payload
    )


def delete_field(client: KizenClient, object_id: str, field_id: str) -> dict[str, Any]:
    """DELETE /api/custom-objects/{object_id}/fields/{field_id}.

    Removes a custom field and the data stored in it across all records.
    Returns an empty dict (the endpoint answers 204 No Content).
    """
    resp = client.delete(f"/api/custom-objects/{object_id}/fields/{field_id}")
    return resp if isinstance(resp, dict) else {}


def add_field_option(
    client: KizenClient, object_id: str, field_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_id}/fields/{field_id}/options.

    ``payload`` is ``{"name": <label>, "order"?: <int>, "meta"?: {...}}``
    (CustomObjectFieldOptionWriteRequest). Returns the created option incl. id.
    """
    return client.post(
        f"/api/custom-objects/{object_id}/fields/{field_id}/options", json=payload
    )


def delete_field_option(
    client: KizenClient, object_id: str, field_id: str, option_id: str
) -> dict[str, Any]:
    """DELETE .../fields/{field_id}/options/{option_id}.

    Drops the option outright — records currently set to it lose that value.
    To move those records onto another option first, use
    :func:`replace_field_option`.
    """
    resp = client.delete(
        f"/api/custom-objects/{object_id}/fields/{field_id}/options/{option_id}"
    )
    return resp if isinstance(resp, dict) else {}


def replace_field_option(
    client: KizenClient,
    object_id: str,
    field_id: str,
    option_id: str,
    replacement: dict[str, Any],
) -> dict[str, Any]:
    """POST .../fields/{field_id}/options/{option_id}/replace.

    Remaps every record currently set to ``option_id`` onto the option named
    by ``replacement`` (a FieldOptionRequest, e.g. ``{"id": <other_uuid>}``)
    and removes the old option. The safe way to retire an in-use option.
    """
    return client.post(
        f"/api/custom-objects/{object_id}/fields/{field_id}/options/{option_id}/replace",
        json=replacement,
    )


def bulk_change_field_value(
    client: KizenClient, object_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_id}/bulk-change-field-value.

    Sets one field to one value across every record in ``payload["record_ids"]``
    in a single call. ``payload["field_value"]`` must be the *bare* wire scalar
    — for select-type fields that means the option's UUID string directly, not
    ``{"id": ...}``; confirmed live 2026-07-20 (the OpenAPI spec's ``field_value:
    type: object`` is misleading — a wrapped or dict value 400s with "Not a
    valid string").
    """
    return client.post(
        f"/api/custom-objects/{object_id}/bulk-change-field-value", json=payload
    )


def upload_records(
    client: KizenClient, object_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_id}/uploader — the UI's CSV import.

    ``body`` is a ``CustomObjectEntityRecordUploadRequest`` naming an uploaded
    CSV by ``s3_object_id``. Confirmed live 2026-09-28: the response is 200
    with an echo of the request plus ``status_id``, the
    ``bulk-action-progress`` row to poll. Keys outside the schema (such as
    ``send_email_notification``) are dropped.
    """
    return client.post(f"/api/custom-objects/{object_id}/uploader", json=body)


def list_fields(client: KizenClient, object_id: str) -> list[dict[str, Any]]:
    """GET /api/custom-objects/{object_id}/fields.

    Returns a plain list of field dicts. `name` is the api_name, `display_name`
    is the display name, `category` is the parent category UUID.
    """
    resp = client.get(f"/api/custom-objects/{object_id}/fields")
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []

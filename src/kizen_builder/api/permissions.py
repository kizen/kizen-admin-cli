"""Roles, permission groups, and the permissions catalog against the Kizen API.

Three related surfaces:

* ``/api/role`` — a Role bundles app-level permission flags (``permissions``),
  a set of permission-group UUIDs (``permission_groups``), and a
  ``default_for_new_users`` flag. Roles are what team members are assigned.
* ``/api/permission-group`` — a named bundle of per-entity access levels. The
  group is created with just a name; the actual access levels are attached via
  the ``object-update`` endpoint (custom objects / contacts + their fields) and
  carried on the retrieved group's ``sections`` map (homepages, dashboards,
  activities, …).
* ``/api/permissions/meta-data`` — the catalog of everything permissionable in
  the business (sections, custom objects + categories, contacts, default
  fields), plus the display ``order``.

Access levels are integers 0–3: 0=None, 1=View, 2=Edit, 3=Remove — mirrored by
the group ``summary`` counts (nb_none / nb_view / nb_edit / nb_remove).
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def _unwrap(resp: Any) -> list[dict[str, Any]]:
    """Return a plain list from either a bare list or a paginated envelope."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []


# --- roles --------------------------------------------------------------


def list_roles(client: KizenClient, search: str | None = None) -> list[dict[str, Any]]:
    """GET /api/role — list the business's roles ({id, name, ...})."""
    params: dict[str, Any] = {"page_size": 200}
    if search:
        params["search"] = search
    return _unwrap(client.get("/api/role", params=params))


def get_role(client: KizenClient, role_id: str) -> dict[str, Any]:
    """GET /api/role/{id} — one role incl. permissions + permission_groups."""
    return client.get(f"/api/role/{role_id}")


def create_role(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/role — {name, permissions?, permission_groups?, default_for_new_users?}."""
    return client.post("/api/role", json=payload)


def update_role(
    client: KizenClient, role_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/role/{id} — partial update (only supplied keys change)."""
    return client.patch(f"/api/role/{role_id}", json=payload)


def delete_role(client: KizenClient, role_id: str) -> None:
    """DELETE /api/role/{id}."""
    client.delete(f"/api/role/{role_id}")


# --- permission groups --------------------------------------------------


def list_permission_groups(
    client: KizenClient, search: str | None = None
) -> list[dict[str, Any]]:
    """GET /api/permission-group — list groups (id, name, role_count, ...)."""
    params: dict[str, Any] = {"page_size": 200}
    if search:
        params["search"] = search
    return _unwrap(client.get("/api/permission-group", params=params))


def get_permission_group(client: KizenClient, group_id: str) -> dict[str, Any]:
    """GET /api/permission-group/{id} — one group incl. its access-level map."""
    return client.get(f"/api/permission-group/{group_id}")


def create_permission_group(
    client: KizenClient, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/permission-group — {name}. Access levels are set separately."""
    return client.post("/api/permission-group", json=payload)


def update_permission_group(
    client: KizenClient, group_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PUT /api/permission-group/{id} — replace group metadata (name)."""
    return client.put(f"/api/permission-group/{group_id}", json=payload)


def delete_permission_group(client: KizenClient, group_id: str) -> None:
    """DELETE /api/permission-group/{id}."""
    client.delete(f"/api/permission-group/{group_id}")


def patch_permission_group(
    client: KizenClient, group_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/permission-group/{id} — set one or more *full* section dicts.

    Section values must be complete (all sub-keys) or the server 400s; the
    server also normalizes each value to the section's ``allowed_access``."""
    return client.patch(f"/api/permission-group/{group_id}", json=payload)


def object_update_permission(
    client: KizenClient, group_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/permission-group/{id}/object-update — set one custom-object or
    contact access level (optionally scoped to a single field).

    Payload: ``{custom_object, field?, key?, permission_level}`` where
    permission_level is 0–3."""
    return client.patch(f"/api/permission-group/{group_id}/object-update", json=payload)


# --- catalog ------------------------------------------------------------


def get_permissions_meta_data(client: KizenClient) -> dict[str, Any]:
    """GET /api/permissions/meta-data — the catalog of permissionable entities."""
    return client.get("/api/permissions/meta-data")

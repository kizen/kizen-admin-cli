"""Where-used lookups against the Kizen API (``/api/references/…``).

Each endpoint answers "what in Kizen uses this?" with the same loose shape:
integer counts, plus lists of the automations, forms, dashboards and so on
that name the target. ``tools/references.py`` reads them all the same way.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def field_references(
    client: KizenClient, object_id: str, field_id: str
) -> dict[str, Any]:
    """GET /api/references/custom-objects/{object_id}/fields/{field_id}.

    Both ids are UUIDs; an api_name in either place gets an HTML 404.
    """
    return client.get(f"/api/references/custom-objects/{object_id}/fields/{field_id}")


def role_references(client: KizenClient, role_id: str) -> dict[str, Any]:
    """GET /api/references/roles/{role_id}. An unknown UUID gets a JSON 404."""
    return client.get(f"/api/references/roles/{role_id}")


def permission_group_references(client: KizenClient, group_id: str) -> dict[str, Any]:
    """GET /api/references/permission-groups/{group_id}. An unknown UUID gets a
    JSON 404."""
    return client.get(f"/api/references/permission-groups/{group_id}")

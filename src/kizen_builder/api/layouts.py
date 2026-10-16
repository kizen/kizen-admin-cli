"""Thin httpx wrappers for the Kizen record-layout API.

Layouts are attached to a custom object and control how its fields/blocks are
arranged on the record detail view.

Endpoints (no trailing slashes):
  GET  /api/custom-objects/{object_id}/layouts               list layouts
  PUT  /api/custom-objects/{object_id}/layouts/{layout_id}   replace one layout

Kizen auto-creates a "Standard View" layout on object creation, so there is no
create path in normal use — always PUT to update the existing layout. See
`kizen docs show reference` ("Record Layout API") for the config/block shape and
the every-level-needs-a-UUID rule.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def list_layouts(client: KizenClient, object_id: str) -> list[dict[str, Any]]:
    """GET /api/custom-objects/{object_id}/layouts, every page.

    Normalizes the response envelope (DRF ``results`` or a bare list) to a
    plain list of layout dicts.
    """
    return client.paginate(f"/api/custom-objects/{object_id}/layouts")


def update_layout(
    client: KizenClient,
    object_id: str,
    layout_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """PUT /api/custom-objects/{object_id}/layouts/{layout_id}."""
    return client.put(
        f"/api/custom-objects/{object_id}/layouts/{layout_id}", json=payload
    )

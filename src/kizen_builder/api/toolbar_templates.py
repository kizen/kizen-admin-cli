"""Toolbar templates: named toolbars (the bar across the top of the page) a user or role can be given.

Endpoints:
  GET    /api/toolbar-templates        list (DRF page; ``light=true`` → {id, name})
  POST   /api/toolbar-templates        create
  GET    /api/toolbar-templates/{id}   one template
  PATCH  /api/toolbar-templates/{id}   partial update
  DELETE /api/toolbar-templates/{id}   delete (204)

The ``apply-to-roles``/``apply-to-users`` endpoints replace each person's own
toolbar and are deliberately not wrapped.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient

BASE = "/api/toolbar-templates"


def list_toolbar_templates(
    client: KizenClient, search: str | None = None
) -> list[dict[str, Any]]:
    """Every template, in the full detail shape (``light=false``)."""
    params: dict[str, Any] = {"light": "false"}
    if search:
        params["search"] = search
    return client.paginate(BASE, params)


def get_toolbar_template(client: KizenClient, template_id: str) -> dict[str, Any]:
    return client.get(f"{BASE}/{template_id}")


def create_toolbar_template(
    client: KizenClient, payload: dict[str, Any]
) -> dict[str, Any]:
    return client.post(BASE, json=payload)


def update_toolbar_template(
    client: KizenClient, template_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return client.patch(f"{BASE}/{template_id}", json=payload)


def delete_toolbar_template(client: KizenClient, template_id: str) -> None:
    client.delete(f"{BASE}/{template_id}")

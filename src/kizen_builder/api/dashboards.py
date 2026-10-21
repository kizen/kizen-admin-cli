"""Thin httpx wrappers for the Kizen dashboards + dashlets API.

Endpoints (all undocumented in the public spec):
  GET    /api/dashboards          list dashboards (paginated, DRF-style)
  POST   /api/dashboards          create a dashboard (no trailing slash)
  GET    /api/dashboards/{id}     fetch a dashboard with embedded dashlets
  PATCH  /api/dashboards/{id}     update dashboard metadata
  DELETE /api/dashboards/{id}     delete a dashboard
  POST   /api/dashboards/{id}/duplicate  copy a dashboard (body ignored)

  POST   /api/dashboards/{id}/dashlet    create a dashlet
  PATCH  /api/dashboards/{id}/dashlet/{dl_id}  update a dashlet
  DELETE /api/dashboards/{id}/dashlet/{dl_id}  delete a dashlet
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from kizen_builder.api.client import KizenClient


def list_dashboards(
    client: KizenClient,
    dashboard_type: str = "generic_dashboard",
    custom_object_id: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/dashboards/mine, transparently paginated.

    ``/api/dashboards`` is POST-only (create); the list of the current user's
    dashboards/homepages lives at ``/api/dashboards/mine`` (there is also
    ``/api/dashboards/other`` for ones the user *lacks* access to). Each entry
    is a summary: ``id, api_name, name, published, hidden, dashlets_count,
    owner``. Handles both a DRF paginated envelope and a bare list, mirroring
    :func:`custom_objects.list_objects`.

    ``dashboard_type`` is required by the API and **``generic_dashboard`` does
    NOT include homepages** despite earlier docs here claiming otherwise —
    confirmed live 2026-07-20 (a homepage was invisible under
    ``generic_dashboard`` and only appeared under ``dashboard_type=homepage``).
    Valid values: ``generic_dashboard`` (standalone dashboards), ``homepage``
    (team landing pages), or ``chart_group`` (a custom object's chart-group
    dashboards, then ``custom_object_id`` is required). Callers that want
    "everything" must query multiple types and merge — see
    ``tools.dashboards.list_dashboards``.
    """
    params: dict[str, Any] = {"dashboard_type": dashboard_type}
    if custom_object_id:
        params["custom_object_id"] = custom_object_id

    items: list[dict[str, Any]] = []
    path: str | None = "/api/dashboards/mine"
    first = True
    while path:
        resp = client.get(path, params=params if first else None)
        first = False
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            nxt = resp.get("next")
            if nxt:
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


def get_dashboard(client: KizenClient, dashboard_id: str) -> dict[str, Any]:
    return client.get(f"/api/dashboards/{dashboard_id}")


def create_dashboard(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    return client.post("/api/dashboards", json=payload)


def update_dashboard(
    client: KizenClient, dashboard_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return client.patch(f"/api/dashboards/{dashboard_id}", json=payload)


def duplicate_dashboard(client: KizenClient, dashboard_id: str) -> dict[str, Any]:
    """POST /api/dashboards/{id}/duplicate with an empty body.

    The server ignores the body: the copy is named ``<name> (copy #N)``, is
    unpublished and has empty sharing. Set those with :func:`update_dashboard`.
    """
    return client.post(f"/api/dashboards/{dashboard_id}/duplicate", json={})


def delete_dashboard(client: KizenClient, dashboard_id: str) -> None:
    client.delete(f"/api/dashboards/{dashboard_id}")


def create_dashlet(
    client: KizenClient, dashboard_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return client.post(f"/api/dashboards/{dashboard_id}/dashlet", json=payload)


def update_dashlet(
    client: KizenClient,
    dashboard_id: str,
    dashlet_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return client.patch(
        f"/api/dashboards/{dashboard_id}/dashlet/{dashlet_id}", json=payload
    )


def delete_dashlet(client: KizenClient, dashboard_id: str, dashlet_id: str) -> None:
    client.delete(f"/api/dashboards/{dashboard_id}/dashlet/{dashlet_id}")

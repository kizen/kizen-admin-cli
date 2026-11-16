"""Installed plugins and business apps, as the Kizen UI reads them.

Endpoints:
  GET /api/external-integrations/bootstrap
      every install the caller sees, keyed by plugin api_name: ``name``,
      ``version``, ``installed_version``, ``business_config``,
      ``employee_config``, ``routable_pages[]``, ``toolbar_items[]``, ...
  GET /api/external-integrations/plugin-apps/{business_id}/apps
      the business's own apps: ``api_name``, ``base_api_name``,
      ``live_version`` (null until published), ``status``, ``versions[]``
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def get_bootstrap(client: KizenClient) -> dict[str, Any]:
    return client.get("/api/external-integrations/bootstrap")


def list_business_apps(client: KizenClient, business_id: str) -> list[dict[str, Any]]:
    return client.paginate(f"/api/external-integrations/plugin-apps/{business_id}/apps")

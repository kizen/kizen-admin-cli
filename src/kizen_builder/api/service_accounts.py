"""Service accounts against the Kizen API (``/api/service-accounts``).

A service account is a non-human employee that holds API keys and permission
groups. Integration accounts (``account_type: service_integration``) are the
ones a business creates; automation and plugin accounts are Kizen's own.

Nothing here touches ``…/api-tokens/*``: the CLI never reads, reveals or
rotates a key.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient

_BASE = "/api/service-accounts"


def list_service_accounts(
    client: KizenClient, search: str | None = None
) -> list[dict[str, Any]]:
    """GET /api/service-accounts — every account, previews only.

    ``search`` filters by name; the schema doesn't declare it, but it works
    (confirmed live 2026-10-27).
    """
    params: dict[str, Any] = {"page_size": 100}
    if search:
        params["search"] = search
    return client.paginate(_BASE, params)


def get_service_account(client: KizenClient, account_id: str) -> dict[str, Any]:
    """GET /api/service-accounts/{id} — the list row shape, never a key."""
    return client.get(f"{_BASE}/{account_id}")


def create_service_account(
    client: KizenClient, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/service-accounts — ``{name, permission_groups}``.

    The response carries the new account's API key in full under an
    ``api_key`` object the schema doesn't declare (confirmed live 2026-10-27).
    It is removed here, before anything else can see it, so no plan result,
    table, log or ``--json`` output can contain it.
    """
    resp = client.post(_BASE, json=payload)
    if isinstance(resp, dict):
        resp.pop("api_key", None)
    return resp


def update_service_account(
    client: KizenClient, account_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/service-accounts/{id} — only the supplied keys change.

    A body that includes ``name`` re-derives ``api_name`` from it unless
    ``api_name`` is sent too (confirmed live 2026-10-27).
    """
    return client.patch(f"{_BASE}/{account_id}", json=payload)


def delete_service_account(client: KizenClient, account_id: str) -> None:
    """DELETE /api/service-accounts/{id} — archives it; no read can see it after."""
    client.delete(f"{_BASE}/{account_id}")


def list_service_account_history(
    client: KizenClient, account_id: str, limit: int
) -> list[dict[str, Any]]:
    """GET /api/service-accounts/{id}/history — one page of up to ``limit`` events."""
    resp = client.get(f"{_BASE}/{account_id}/history", params={"page_size": limit})
    if isinstance(resp, dict):
        return list(resp.get("results") or [])
    return list(resp or [])

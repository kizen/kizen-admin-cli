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
_HISTORY_PAGE_MAX = 1000  # endpoint's max page_size; not confirmed live 2026-11-18


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
    client: KizenClient, account_id: str, limit: int | None
) -> list[dict[str, Any]]:
    """GET /api/service-accounts/{id}/history — up to ``limit`` events.

    Follows ``next`` until ``limit`` events are in hand or none remain;
    ``None`` returns every event. Pass ``None``, not ``0``, for "all".
    """
    page_size = _HISTORY_PAGE_MAX if limit is None else min(limit, _HISTORY_PAGE_MAX)
    rows = client.paginate(
        f"{_BASE}/{account_id}/history", {"page_size": page_size}, limit=limit
    )
    return rows if limit is None else rows[:limit]

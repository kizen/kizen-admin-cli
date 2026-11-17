"""Business-level settings against the Kizen API. Only business variables so far."""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def list_business_variables(
    client: KizenClient, business_id: str
) -> list[dict[str, Any]]:
    """GET /api/business/{id} — its ``business_variables`` list.

    Variables have no endpoint of their own; they ride on the business record
    as ``[{id, name, api_name, value_string}]``.
    """
    business = client.get(f"/api/business/{business_id}")
    return list(business.get("business_variables") or [])


def replace_business_variables(
    client: KizenClient, business_id: str, variables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """PATCH /api/business/{id} with the business's full variable list.

    Replaces the whole set: an entry with a known ``id`` is updated, one without
    is created, and any live variable whose id is missing is deleted.
    """
    business = client.patch(
        f"/api/business/{business_id}", json={"business_variables": variables}
    )
    return list(business.get("business_variables") or [])

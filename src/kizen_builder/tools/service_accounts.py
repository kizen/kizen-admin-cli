"""Read tools for service accounts.

Config-resolving wrappers over ``kizen_builder.api.service_accounts``.
Mutations live in the planners. No read here returns a key: the list and
detail rows carry only Kizen's own preview.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import service_accounts as sa_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.permissions import resolve_by_name
from kizen_builder.utils import is_uuid

INTEGRATION = "service_integration"


def resolve_service_account(client: KizenClient, ref: str) -> dict[str, Any]:
    """Resolve a UUID, an exact api_name or a name to one account row."""
    items = sa_api.list_service_accounts(client)
    if not is_uuid(ref):
        by_api_name = [i for i in items if i.get("api_name") == ref]
        if len(by_api_name) == 1:
            return by_api_name[0]
    return resolve_by_name(ref, items, "service account")


def list_service_accounts(search: str | None = None) -> list[dict[str, Any]]:
    with KizenClient(load_env_config()) as client:
        return sa_api.list_service_accounts(client, search=search)


def get_service_account(ref: str) -> dict[str, Any]:
    with KizenClient(load_env_config()) as client:
        account = resolve_service_account(client, ref)
        return sa_api.get_service_account(client, account["id"])


def list_service_account_history(ref: str, limit: int = 25) -> list[dict[str, Any]]:
    with KizenClient(load_env_config()) as client:
        account = resolve_service_account(client, ref)
        return sa_api.list_service_account_history(client, account["id"], limit)

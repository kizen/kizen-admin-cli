"""Read tools for MCP servers: resolve, list, detail, tool catalogue, history.

Servers are referenced by UUID or ``api_name``. Display names are not unique in
Kizen, so they are never matched.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import mcp_servers as mcp_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.utils import is_uuid


def _client() -> KizenClient:
    return KizenClient(load_env_config())


def _resolve(client: KizenClient, ref: str) -> dict[str, Any]:
    if is_uuid(ref):
        try:
            return mcp_api.get_mcp_server(client, ref)
        except KizenAPIError as e:
            if e.status_code != 404:
                raise
        servers = mcp_api.list_mcp_servers(client)
    else:
        servers = mcp_api.list_mcp_servers(client)
        match = next((s for s in servers if s.get("api_name") == ref), None)
        if match is not None:
            return mcp_api.get_mcp_server(client, match["id"])
    names = ", ".join(sorted(s.get("api_name") or "" for s in servers)) or "none"
    raise LookupError(f"no MCP server '{ref}'; available: {names}")


def resolve_mcp_server(ref: str) -> dict[str, Any]:
    """The server's full GET, from its UUID or api_name."""
    with _client() as c:
        return _resolve(c, ref)


def list_mcp_servers() -> list[dict[str, Any]]:
    """Every server in the business, as the list endpoint returns them."""
    with _client() as c:
        return mcp_api.list_mcp_servers(c)


def get_mcp_server_detail(ref: str) -> dict[str, Any]:
    """One server's summary, its tools as ``{group, name}`` rows, and ``raw``."""
    raw = resolve_mcp_server(ref)
    owner = raw.get("owner") or {}
    return {
        "name": raw.get("name"),
        "api_name": raw.get("api_name"),
        "description": raw.get("description"),
        "is_enabled": raw.get("is_enabled"),
        "is_default_for_business": raw.get("is_default_for_business"),
        "is_system_managed": raw.get("is_system_managed"),
        "endpoint_url": raw.get("endpoint_url"),
        "owner": owner.get("display_name"),
        "id": raw.get("id"),
        "tools": [
            {"group": t.get("group"), "name": t.get("name")}
            for t in raw.get("tools") or []
        ],
        "raw": raw,
    }


def tool_catalog() -> dict[str, Any]:
    """The business's tool catalogue: ``{groups: [{key, label, tools}]}``."""
    with _client() as c:
        return mcp_api.get_tool_catalog(c)


def catalog_tool_names(catalog: dict[str, Any]) -> set[str]:
    return {
        t["name"]
        for g in catalog.get("groups") or []
        for t in g.get("tools") or []
        if t.get("name")
    }


def list_history(ref: str) -> list[dict[str, Any]]:
    """The server's change events, as Kizen returns them."""
    with _client() as c:
        server = _resolve(c, ref)
        return mcp_api.list_events_history(c, server["id"])


def changed_keys(event: dict[str, Any]) -> list[str]:
    """Keys of ``data.new_value`` whose value differs from ``data.old_value``.

    A create event has no ``old_value``, so it reports nothing changed.
    """
    data = event.get("data") or {}
    old, new = data.get("old_value"), data.get("new_value")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    return [k for k, v in new.items() if old.get(k) != v]


def initiator_label(event: dict[str, Any]) -> str:
    who = event.get("initiator") or {}
    if who.get("email"):
        return str(who["email"])
    return " ".join(p for p in (who.get("first_name"), who.get("last_name")) if p)

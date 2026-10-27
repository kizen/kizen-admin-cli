"""MCP server configuration against the Kizen API.

An MCP server is a named set of Kizen tools that AI steps and outside MCP
clients call. This module configures servers; it does not call one. A server's
``endpoint_url`` (``/api/mcp/{api_name}``) is the JSON-RPC endpoint MCP clients
use at run time, and nothing here touches it.

Endpoints:
  GET    /api/mcp-servers                      list servers (DRF page)
  POST   /api/mcp-servers                      create
  GET    /api/mcp-servers/{id}                 one server, incl. its tools
  PATCH  /api/mcp-servers/{id}                 partial update
  DELETE /api/mcp-servers/{id}                 hard delete (204)
  GET    /api/mcp-servers/tools                the tool catalogue, by group
  GET    /api/mcp-servers/{id}/events-history  change history (DRF page)
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def list_mcp_servers(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/mcp-servers — every server, with ``tool_count`` and no ``tools``."""
    return client.paginate("/api/mcp-servers")


def get_mcp_server(client: KizenClient, server_id: str) -> dict[str, Any]:
    """GET /api/mcp-servers/{id} — one server, with ``tools`` expanded."""
    return client.get(f"/api/mcp-servers/{server_id}")


def create_mcp_server(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/mcp-servers — returns the created server's read shape."""
    return client.post("/api/mcp-servers", json=payload)


def update_mcp_server(
    client: KizenClient, server_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/mcp-servers/{id} — only the supplied keys change."""
    return client.patch(f"/api/mcp-servers/{server_id}", json=payload)


def delete_mcp_server(client: KizenClient, server_id: str) -> None:
    """DELETE /api/mcp-servers/{id}."""
    client.delete(f"/api/mcp-servers/{server_id}")


def get_tool_catalog(client: KizenClient) -> dict[str, Any]:
    """GET /api/mcp-servers/tools — ``{groups: [{key, label, tools: [...]}]}``."""
    return client.get("/api/mcp-servers/tools")


def list_events_history(client: KizenClient, server_id: str) -> list[dict[str, Any]]:
    """GET /api/mcp-servers/{id}/events-history — written a second or so after a change."""
    return client.paginate(f"/api/mcp-servers/{server_id}/events-history")

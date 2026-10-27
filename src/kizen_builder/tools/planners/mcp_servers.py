"""Plan creation, update and deletion of MCP servers. One op per server.

A spec may be ``kizen mcp-servers get --raw`` output: its read-only keys are
dropped before validation and listed in the preview. Tool names are checked
against the live catalogue, because Kizen rejects the whole request for one
unknown tool. Kizen-managed servers (``is_system_managed``) are refused.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import MCPServerDef, MCPServerSharing
from kizen_builder.tools.dashboards import normalize_sharing_settings
from kizen_builder.tools.mcp_servers import (
    catalog_tool_names,
    list_mcp_servers,
    resolve_mcp_server,
    tool_catalog,
)
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation, fetch_existing

READ_ONLY_KEYS = (
    "id",
    "tools",
    "tool_count",
    "is_system_managed",
    "endpoint_url",
    "created",
    "updated",
    "owner",
    "employee_access",
    "sharing_stats",
)

DEFAULT_SHARING: dict[str, Any] = {
    "private": False,
    "all_team_members": 1,
    "roles": {"view": [], "edit": [], "admin": []},
    "team_members": {"view": [], "edit": [], "admin": []},
}

_SCALAR_KEYS = ("name", "description", "is_enabled", "is_default_for_business")


def _parse(spec: dict[str, Any] | MCPServerDef) -> tuple[MCPServerDef, list[str]]:
    if isinstance(spec, MCPServerDef):
        return spec, []
    if not isinstance(spec, dict):
        raise PlanError("an MCP server spec is a JSON object.")
    dropped = [k for k in READ_ONLY_KEYS if k in spec]
    kept = {k: v for k, v in spec.items() if k not in READ_ONLY_KEYS}
    return MCPServerDef.model_validate(kept), dropped


def _check_tools(names: list[str]) -> list[str]:
    """Drop duplicates (first seen wins) and refuse names the catalogue lacks."""
    deduped = list(dict.fromkeys(names))
    valid = catalog_tool_names(tool_catalog())
    unknown = [n for n in deduped if n not in valid]
    if unknown:
        raise PlanError(
            f"unknown MCP tool(s): {', '.join(unknown)}. "
            f"Valid tools: {', '.join(sorted(valid))}. "
            "List them with `kizen mcp-servers tools`."
        )
    return deduped


def _sharing(sharing: MCPServerSharing) -> dict[str, Any]:
    return normalize_sharing_settings(sharing.model_dump())


def _comparable(sharing: dict[str, Any]) -> dict[str, Any]:
    """A normalised sharing block with each grant list as a set."""
    norm = normalize_sharing_settings(sharing)
    for bucket in ("roles", "team_members"):
        if isinstance(norm.get(bucket), dict):
            norm[bucket] = {lvl: set(ids) for lvl, ids in norm[bucket].items()}
    return norm


def _sharing_diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    """Which scalars changed and which grants are added (+) or removed (-)."""
    names = {
        g["id"]: g.get("display_name") or g["id"]
        for block in (old, new)
        for bucket in ("roles", "team_members")
        for grants in (block.get(bucket) or {}).values()
        for g in grants
        if isinstance(g, dict) and g.get("id")
    }
    a, b = _comparable(old), _comparable(new)
    parts = [
        f"{k} {a.get(k)} → {b.get(k)}"
        for k in ("private", "all_team_members")
        if a.get(k) != b.get(k)
    ]
    for bucket in ("roles", "team_members"):
        for lvl in ("view", "edit", "admin"):
            was = (a.get(bucket) or {}).get(lvl, set())
            now = (b.get(bucket) or {}).get(lvl, set())
            parts += [f"+{bucket}.{lvl}:{names.get(i, i)}" for i in sorted(now - was)]
            parts += [f"-{bucket}.{lvl}:{names.get(i, i)}" for i in sorted(was - now)]
    return ", ".join(parts)


def _default_takeover(servers: list[dict[str, Any]], api_name: str) -> str | None:
    """Preview text for a server becoming the business default.

    Kizen keeps one default per business: setting the flag clears it on the
    current default (confirmed live 2026-10-27).
    """
    current = next((s for s in servers if s.get("is_default_for_business")), None)
    if current is None or current.get("api_name") == api_name:
        return None
    old = current.get("api_name")
    return (
        f"replaces '{old}', which stops being the default. To restore it, run "
        f"`kizen mcp-servers update {old}` with "
        '{"is_default_for_business": true}'
    )


def _system_message(live: dict[str, Any]) -> str:
    return (
        f"'{live.get('api_name')}' is managed by Kizen and can't be changed or "
        "deleted from the CLI. The one exception: an update that sets only "
        '{"is_default_for_business": true}.'
    )


def _check_system_update(live: dict[str, Any], d: MCPServerDef) -> None:
    """A Kizen-managed server takes only the default flag set to true."""
    if live.get("is_system_managed") and (
        d.model_fields_set != {"is_default_for_business"}
        or d.is_default_for_business is not True
    ):
        raise PlanError(_system_message(live))


def plan_create_mcp_server(spec: dict[str, Any] | MCPServerDef) -> Plan:
    """Plan one new MCP server."""
    d, dropped = _parse(spec)
    env = load_env_config().name
    missing = [k for k in ("name", "api_name") if getattr(d, k) is None]
    if missing:
        raise PlanError(f"an MCP server create needs {' and '.join(missing)}.")
    assert d.api_name is not None

    servers = list_mcp_servers()
    existing = next((s for s in servers if s.get("api_name") == d.api_name), None)
    if existing is not None:
        raise PlanError(
            f"MCP server '{d.api_name}' already exists (id {existing['id']}). "
            "Use `kizen mcp-servers update` instead."
        )

    payload: dict[str, Any] = {"name": d.name, "api_name": d.api_name}
    for key in ("description", "is_enabled", "is_default_for_business"):
        if key in d.model_fields_set:
            payload[key] = getattr(d, key)
    if d.tool_names is not None:
        payload["tool_names"] = _check_tools(d.tool_names)
    payload["sharing_settings"] = (
        _sharing(d.sharing_settings)
        if d.sharing_settings is not None
        else DEFAULT_SHARING
    )

    preview: dict[str, Any] = {
        "env": env,
        "api_name": d.api_name,
        "name": d.name,
        "tools": len(payload.get("tool_names", [])),
    }
    if d.is_default_for_business:
        takeover = _default_takeover(servers, d.api_name)
        if takeover:
            preview["business_default"] = takeover
    if dropped:
        preview["ignored_read_only_keys"] = dropped
    op = PlanOperation(
        action="create",
        kind="mcp_server",
        key=d.api_name,
        preview=preview,
        payload=payload,
    )
    return Plan.build(
        env=env, summary=f"Create MCP server '{d.api_name}'", operations=[op]
    )


def plan_update_mcp_server(server: str, spec: dict[str, Any] | MCPServerDef) -> Plan:
    """Plan a PATCH of the keys the spec sets that differ from live.

    ``server`` is a UUID or api_name. Tools compare as a set and sharing
    compares after normalising both sides, so re-sending ``get --raw`` output
    unchanged plans a skip.
    """
    d, dropped = _parse(spec)
    env = load_env_config().name
    live = fetch_existing("MCP server", server, lambda: resolve_mcp_server(server))
    _check_system_update(live, d)
    api_name = live.get("api_name") or live["id"]
    if d.is_default_for_business is False and live.get("is_default_for_business"):
        raise PlanError(
            f"'{api_name}' is the business default and Kizen keeps one. To change "
            "it, set another server as the default instead "
            '({"is_default_for_business": true} on that server).'
        )
    if d.api_name is not None and d.api_name != live.get("api_name"):
        raise PlanError(
            "api_name can't change after creation; create a new server instead."
        )

    payload: dict[str, Any] = {}
    diff: dict[str, str] = {}
    for key in _SCALAR_KEYS:
        if key in d.model_fields_set and getattr(d, key) != live.get(key):
            payload[key] = getattr(d, key)
            diff[key] = f"{live.get(key)} → {getattr(d, key)}"
    if d.tool_names is not None:
        wanted = _check_tools(d.tool_names)
        current = live.get("tool_names") or []
        if set(wanted) != set(current):
            payload["tool_names"] = wanted
            added = [t for t in wanted if t not in current]
            removed = [t for t in current if t not in wanted]
            diff["tool_names"] = ", ".join(
                [f"+{t}" for t in added] + [f"-{t}" for t in removed]
            )
    if d.sharing_settings is not None:
        new = _sharing(d.sharing_settings)
        if _comparable(new) != _comparable(live.get("sharing_settings") or {}):
            payload["sharing_settings"] = new
            diff["sharing_settings"] = (
                _sharing_diff(
                    live.get("sharing_settings") or {},
                    d.sharing_settings.model_dump(),
                )
                + " (the whole block is sent)"
            )

    preview: dict[str, Any] = {
        "env": env,
        "mcp_server": api_name,
        "diff": diff or "no changes",
    }
    if payload.get("is_default_for_business"):
        takeover = _default_takeover(list_mcp_servers(), api_name)
        if takeover:
            preview["business_default"] = takeover
    if dropped:
        preview["ignored_read_only_keys"] = dropped
    op = PlanOperation(
        action="update" if payload else "skip",
        kind="mcp_server",
        key=api_name,
        preview=preview,
        payload=payload,
        existing_uuid=live["id"],
    )
    summary = (
        f"Update MCP server '{api_name}' ({len(diff)} change(s))"
        if payload
        else f"No changes to MCP server '{api_name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_mcp_server(server: str) -> Plan:
    """Plan a hard delete. ``server`` is a UUID or api_name."""
    env = load_env_config().name
    live = fetch_existing("MCP server", server, lambda: resolve_mcp_server(server))
    if live.get("is_system_managed"):
        raise PlanError(_system_message(live))
    api_name = live.get("api_name") or live["id"]
    preview: dict[str, Any] = {
        "env": env,
        "name": live.get("name"),
        "api_name": live.get("api_name"),
        "id": live["id"],
        "tool_count": len(live.get("tool_names") or []),
    }
    if live.get("is_default_for_business"):
        preview["is_business_default"] = True
    op = PlanOperation(
        action="delete",
        kind="mcp_server",
        key=api_name,
        preview=preview,
        existing_uuid=live["id"],
    )
    return Plan.build(
        env=env, summary=f"Delete MCP server '{api_name}'", operations=[op]
    )

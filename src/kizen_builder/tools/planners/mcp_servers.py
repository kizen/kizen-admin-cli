"""Plan creation, update and deletion of MCP servers. One op per server.

A spec may be ``kizen mcp-servers get --raw`` output: its read-only keys are
dropped before validation and listed in the preview. Tool names are checked
against the live catalogue, and ``upstreams`` against the live upstream servers
and their tools, because Kizen rejects the whole request for one unknown tool.
Kizen-managed servers (``is_system_managed``) are refused.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import (
    MCPServerDef,
    MCPServerSharing,
    UpstreamSelection,
)
from kizen_builder.tools.dashboards import normalize_sharing_settings
from kizen_builder.tools.mcp_servers import (
    catalog_tool_names,
    list_mcp_servers,
    list_upstream_servers,
    resolve_mcp_server,
    tool_catalog,
    upstream_tools,
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
    "kizen_tool_count",
    "upstream_tool_count",
    "upstream_servers",
)

# What the UI's New MCP Server modal sends when its sharing controls are left
# alone: no access for all team members and no grants. It sends no `private`;
# the server stores False. Unlike dashboards, it adds no Admin role.
DEFAULT_SHARING: dict[str, Any] = {
    "all_team_members": 0,
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


def _server_tools(server: dict[str, Any]) -> list[dict[str, Any]]:
    """An upstream's tools, fetched once and kept on its row."""
    if "tools" not in server:
        server["tools"] = upstream_tools(server)
    tools: list[dict[str, Any]] = server["tools"]
    return tools


def _picked_tool_ids(
    server: dict[str, Any], entry: UpstreamSelection, held: frozenset[str]
) -> list[str]:
    """The entry's tools as ids, duplicates dropped (first seen wins).

    Ids in ``held`` (the server's live selection) aren't re-checked, so
    ``get --raw`` still re-applies after the upstream stops listing one.
    """
    api_name = server.get("api_name")
    if entry.tool_names:
        tools = _server_tools(server)
        ids = {t["name"]: t["id"] for t in tools}
        wanted = list(dict.fromkeys(entry.tool_names))
        unknown = [n for n in wanted if n not in ids]
        if unknown:
            names = ", ".join(sorted(ids)) or "none"
            raise PlanError(
                f"upstream '{api_name}' has no tool(s) {', '.join(unknown)}. "
                f"Its tools: {names}."
            )
        return list(dict.fromkeys(ids[n] for n in wanted))
    wanted = list(dict.fromkeys(entry.tool_ids or []))
    if all(i in held for i in wanted):
        return wanted
    tools = _server_tools(server)
    known = held | {t["id"] for t in tools}
    unknown = [i for i in wanted if i not in known]
    if unknown:
        names = ", ".join(sorted(t["name"] for t in tools)) or "none"
        raise PlanError(
            f"upstream '{api_name}' has no tool with id(s) {', '.join(unknown)}. "
            f"Its tools: {names}."
        )
    return wanted


def _upstreams(
    entries: list[UpstreamSelection], live: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Kizen's ``upstreams`` list for the spec's entries, and the upstream
    servers by id. Refuses an unknown or repeated server and unknown tools,
    except what ``live`` (the server's current upstreams) already holds."""
    servers = list_upstream_servers()
    by_id = {s["id"]: s for s in servers}
    by_name = {s.get("api_name"): s for s in servers}
    held = _selection(live or [])
    payload: list[dict[str, Any]] = []
    for entry in entries:
        server = by_id.get(entry.server) or by_name.get(entry.server)
        if server is None and entry.server in held:
            server = {"id": entry.server, "api_name": entry.server}
        if server is None:
            names = ", ".join(sorted(s.get("api_name") or "" for s in servers))
            raise PlanError(
                f"unknown upstream MCP server '{entry.server}'. "
                f"Available: {names or 'none'}."
            )
        if any(p["server"] == server["id"] for p in payload):
            raise PlanError(
                f"upstream '{server.get('api_name')}' is listed twice in upstreams; "
                "list each server once."
            )
        if entry.include_all:
            payload.append({"server": server["id"], "include_all": True})
        else:
            live_ids = held.get(server["id"])
            held_ids = live_ids if isinstance(live_ids, frozenset) else frozenset()
            payload.append(
                {
                    "server": server["id"],
                    "include_all": False,
                    "tool_ids": _picked_tool_ids(server, entry, held_ids),
                }
            )
    return payload, by_id


def _selection(upstreams: list[dict[str, Any]]) -> dict[str, str | frozenset[str]]:
    """``{server id: "all" | tool ids}``, for comparing a spec with live."""
    return {
        u["server"]: "all"
        if u.get("include_all")
        else frozenset(u.get("tool_ids") or [])
        for u in upstreams
    }


def _upstreams_diff(
    old: list[dict[str, Any]],
    new: list[dict[str, Any]],
    servers: dict[str, dict[str, Any]],
) -> str:
    """Servers and tools added (+) or removed (-), and all-tools switches."""
    live_names = {u["server"]: u.get("server_name") for u in old}

    def label(sid: str) -> str:
        server = servers.get(sid) or {}
        return server.get("api_name") or live_names.get(sid) or sid

    def tool_names(sid: str, ids: frozenset[str]) -> list[str]:
        server = servers.get(sid)
        names = {t["id"]: t["name"] for t in _server_tools(server)} if server else {}
        return sorted(names.get(i, i) for i in ids)

    def count(sel: str | frozenset[str]) -> str:
        if sel == "all":
            return "all tools"
        return f"{len(sel)} tool" + ("" if len(sel) == 1 else "s")

    a, b = _selection(old), _selection(new)
    parts: list[str] = []
    for sid, now in b.items():
        name = label(sid)
        was = a.get(sid)
        if was is None:
            if now == "all":
                parts.append(f"+{name}: all tools")
            else:
                assert isinstance(now, frozenset)
                parts.append(f"+{name}")
                parts += [f"+{name}: {t}" for t in tool_names(sid, now)]
        elif "all" in (was, now):
            if was != now:
                parts.append(f"{name}: {count(was)} → {count(now)}")
        else:
            assert isinstance(was, frozenset) and isinstance(now, frozenset)
            parts += [f"+{name}: {t}" for t in tool_names(sid, now - was)]
            parts += [f"-{name}: {t}" for t in tool_names(sid, was - now)]
    parts += [f"-{label(sid)}" for sid in a if sid not in b]
    return ", ".join(parts)


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
    servers_by_id: dict[str, dict[str, Any]] = {}
    if d.upstreams is not None:
        payload["upstreams"], servers_by_id = _upstreams(d.upstreams)
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
    if "upstreams" in payload:
        preview["upstream_tools"] = {
            servers_by_id[u["server"]].get("api_name"): (
                "all" if u["include_all"] else len(u["tool_ids"])
            )
            for u in payload["upstreams"]
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
    if d.upstreams is not None:
        current_upstreams = live.get("upstreams") or []
        wanted_upstreams, servers_by_id = _upstreams(d.upstreams, current_upstreams)
        if _selection(wanted_upstreams) != _selection(current_upstreams):
            payload["upstreams"] = wanted_upstreams
            diff["upstreams"] = (
                _upstreams_diff(current_upstreams, wanted_upstreams, servers_by_id)
                + " (the whole list is sent)"
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
        "tool_count": len(live.get("tools") or []),
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

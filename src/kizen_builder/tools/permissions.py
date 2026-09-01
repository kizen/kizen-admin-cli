"""Read tools for roles, permission groups, and the permissions catalog.

Config-resolving wrappers over ``kizen_builder.api.permissions`` that return
normalized dicts for the CLI to render. Mutations live in the planners.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import permissions as perm_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config

# Access-level integer -> label. 0=None, 1=View, 2=Edit, 3=Remove.
LEVELS = {0: "none", 1: "view", 2: "edit", 3: "remove"}
LEVELS_BY_NAME = {v: k for k, v in LEVELS.items()}


def level_label(value: Any) -> str:
    try:
        return LEVELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def _resolve(ref: str, items: list[dict[str, Any]], what: str) -> dict[str, Any]:
    """Resolve ``ref`` (a UUID or an exact/case-insensitive name) to one item."""
    from kizen_builder.utils import is_uuid

    if is_uuid(ref):
        match = next((i for i in items if i.get("id") == ref), None)
        if match is None:
            raise LookupError(f"{what} with id '{ref}' not found.")
        return match
    exact = [i for i in items if i.get("name") == ref]
    if len(exact) == 1:
        return exact[0]
    ci = [i for i in items if (i.get("name") or "").lower() == ref.lower()]
    if len(ci) == 1:
        return ci[0]
    names = [i.get("name") for i in items]
    if not exact and not ci:
        raise LookupError(f"{what} '{ref}' not found. Available: {names}")
    raise LookupError(
        f"{what} '{ref}' is ambiguous ({len(exact or ci)} matches). Use the UUID."
    )


def resolve_role(ref: str) -> dict[str, Any]:
    """Resolve a role by name or UUID → the role dict (id, name, ...)."""
    config = load_env_config()
    with KizenClient(config) as client:
        return _resolve(ref, perm_api.list_roles(client), "role")


def resolve_group(ref: str) -> dict[str, Any]:
    """Resolve a permission group by name or UUID → the group dict."""
    config = load_env_config()
    with KizenClient(config) as client:
        return _resolve(
            ref, perm_api.list_permission_groups(client), "permission group"
        )


def list_roles(search: str | None = None) -> list[dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        raw = perm_api.list_roles(client, search=search)
    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "user_count": r.get("user_count"),
            "permission_groups": r.get("permission_groups") or [],
            "default_for_new_users": r.get("default_for_new_users"),
        }
        for r in raw
    ]


def get_role(role_id: str) -> dict[str, Any]:
    config = load_env_config()
    with KizenClient(config) as client:
        return perm_api.get_role(client, role_id)


def list_permission_groups(search: str | None = None) -> list[dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        return perm_api.list_permission_groups(client, search=search)


def get_permission_group(group_id: str) -> dict[str, Any]:
    config = load_env_config()
    with KizenClient(config) as client:
        return perm_api.get_permission_group(client, group_id)


def get_meta_data() -> dict[str, Any]:
    config = load_env_config()
    with KizenClient(config) as client:
        return perm_api.get_permissions_meta_data(client)


def describe_role(role_ref: str) -> dict[str, Any]:
    """Resolve a role and expand its permission-group ids to names + summaries."""
    config = load_env_config()
    with KizenClient(config) as client:
        role = _resolve(role_ref, perm_api.list_roles(client), "role")
        full = perm_api.get_role(client, role["id"])
        names = {
            g["id"]: g.get("name") for g in perm_api.list_permission_groups(client)
        }
        # The list endpoint zeroes ``summary``; the detail GET has real counts.
        groups = []
        for gid in full.get("permission_groups") or []:
            try:
                detail = perm_api.get_permission_group(client, gid)
                groups.append(
                    {
                        "id": gid,
                        "name": detail.get("name") or names.get(gid),
                        "summary": detail.get("summary"),
                    }
                )
            except KizenAPIError:
                groups.append(
                    {"id": gid, "name": names.get(gid, "(unknown)"), "summary": None}
                )
    return {
        "id": full.get("id"),
        "name": full.get("name"),
        "permissions": full.get("permissions") or [],
        "default_for_new_users": full.get("default_for_new_users"),
        "groups": groups,
    }


def describe_group(group_id: str, include_fields: bool = False) -> dict[str, Any]:
    """Return a labeled, UI-ordered view of one permission group.

    Joins :func:`kizen_builder.tools.permission_builder.enumerate_leaves` output
    with resolved custom-object / field names and orders blocks like the Kizen
    permission editor. ``include_fields`` resolves per-field rows (extra API
    calls to name the fields) — off by default so ``get`` stays fast.

    Name resolution never raises: anything it can't resolve keeps its raw id and
    is reported in the returned ``warnings`` list.
    """
    from kizen_builder.tools import objects as obj_tools
    from kizen_builder.tools import permission_builder as pb

    config = load_env_config()
    with KizenClient(config) as client:
        group = perm_api.get_permission_group(client, group_id)
        meta = perm_api.get_permissions_meta_data(client)
        # user_count / role_count live on the list entry, not the detail GET.
        listed = next(
            (g for g in perm_api.list_permission_groups(client) if g["id"] == group_id),
            {},
        )
    group.setdefault("user_count", listed.get("user_count"))
    group.setdefault("role_count", listed.get("role_count"))

    section_order = {k: i for i, k in enumerate(meta.get("order", []))}
    obj_names: dict[str, str] = {}
    obj_entities: dict[str, str] = {}
    field_names: dict[str, str] = {}
    # Name resolution is best-effort: an unresolved label degrades to a raw id
    # rather than failing the whole view. It is *reported*, though — a broken
    # lookup and a name that legitimately has no label otherwise look identical.
    warnings: list[str] = []
    try:
        objs = obj_tools.list_objects()
        obj_names = {
            o["id"]: (o.get("display_name") or o.get("api_name") or "") for o in objs
        }
        obj_entities = {
            o["id"]: (o.get("entity_name") or o.get("display_name") or "") for o in objs
        }
        if include_fields:
            api_by_id = {o["id"]: o["api_name"] for o in objs}
            present = {
                o.get("custom_object_id") for o in group.get("custom_objects", [])
            }
            for oid in present:
                api = api_by_id.get(oid)
                if not api:
                    continue
                try:
                    for f in obj_tools.get_object(api).get("fields", []):
                        field_names[f["id"]] = (
                            f.get("display_name") or f.get("api_name") or f["id"]
                        )
                except (LookupError, KizenAPIError) as exc:
                    warnings.append(
                        f"could not resolve field names for object '{api}' "
                        f"({exc}) — those rows show raw field ids."
                    )
            # Contacts custom fields live under contacts_section, not in
            # group["custom_objects"], so the loop above never sees them —
            # they need their own lookup. Gate on there being any, since
            # get_object() is a multi-request round trip.
            if (group.get("contacts_section") or {}).get("custom_fields"):
                try:
                    for f in obj_tools.get_object("client_client").get("fields", []):
                        field_names[f["id"]] = (
                            f.get("display_name") or f.get("api_name") or f["id"]
                        )
                except (LookupError, KizenAPIError) as exc:
                    warnings.append(
                        f"could not resolve contacts field names ({exc}) — "
                        f"contacts field rows show raw field ids."
                    )
    except Exception as exc:  # noqa: BLE001 — never let name resolution break the view
        warnings.append(
            f"could not resolve object and field names ({type(exc).__name__}: {exc}) "
            f"— blocks and rows show raw ids."
        )

    # Group leaves into blocks keyed by block_key — a section key, a custom
    # object id, or "contacts_section". Per-field leaves (area "field") share
    # their parent block's key, so they nest under it rather than splitting off.
    _CAT_LABEL = {"default_fields": "Default Fields", "custom_fields": "Fields"}
    blocks: dict[str, dict[str, Any]] = {}
    for leaf in pb.enumerate_leaves(group, meta, include_fields=include_fields):
        blk = blocks.get(leaf.block_key)
        if blk is None:
            if leaf.block_key in obj_names or leaf.area == "object":
                label, area = obj_names.get(leaf.block_key, leaf.block_label), "object"
            else:
                label, area = leaf.block_label, leaf.area
            blk = {
                "area": area,
                "block_key": leaf.block_key,
                "label": label,
                "enabled": None,
                "rows": [],
            }
            blocks[leaf.block_key] = blk
        if leaf.area == "section" and leaf.row_key == "enabled":
            blk["enabled"] = leaf.current_level != "none"
            continue
        row_label = leaf.row_label
        if blk["area"] == "object":
            row_label = pb.substitute_object_label(
                row_label, obj_entities.get(leaf.block_key, "")
            )
        if leaf.category == "custom_fields":
            row_label = field_names.get(leaf.row_key, leaf.row_key)
        category_label = (
            _CAT_LABEL.get(leaf.category, leaf.category)
            if leaf.category is not None
            else None
        )
        blk["rows"].append(
            {
                "label": row_label,
                "category": category_label,
                "level": leaf.current_level,
                "allowed": leaf.allowed_access,
                "affordance": leaf.affordance,
            }
        )

    def sort_key(b: dict[str, Any]) -> tuple[int, str]:
        if b["area"] == "object":
            return (section_order.get("custom_object_entities", 50), b["label"].lower())
        return (section_order.get(b["block_key"], 99), b["label"].lower())

    ordered = sorted(blocks.values(), key=sort_key)
    return {
        "id": group.get("id"),
        "name": group.get("name"),
        "user_count": group.get("user_count"),
        "role_count": group.get("role_count"),
        "summary": group.get("summary"),
        "blocks": ordered,
        "warnings": warnings,
    }

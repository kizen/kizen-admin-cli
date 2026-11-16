"""Read tools for toolbar templates, and the entry catalog both sides share.

A template's ``configuration_json`` is the ordered list of toolbar entries.
``entry_kind`` names what an entry is, ``to_spec`` turns a live template into
the spec ``create``/``update`` take, and ``BUILTIN_ITEMS``/
``BUILTIN_CATEGORIES`` hold the built-in entries in the form the toolbar
editor saves them (see ``kizen docs show toolbar-templates``).
"""

from __future__ import annotations

import copy
from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import toolbar_templates as tt_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.dashboards import normalize_sharing_settings
from kizen_builder.utils import is_uuid

DEFAULT_PREFIX = "default__"
CONTACTS_ID = "default__contacts"
NOTIFICATION_CENTER_ID = "default__notification_center"
MY_ACCOUNT_ID = "default__my_account"

# Keys a rebuild keeps from the entry rather than refreshing from the installed
# plugin: display and drag state, and `employee_config`. Bootstrap's
# `employee_config` is the caller's own per-user plugin state, so it is never
# copied into a template; a new entry gets `{}`.
PLUGIN_KEPT_KEYS = frozenset(
    {
        "employee_config",
        "id",
        "label",
        "icon",
        "color",
        "widthPercent",
        "isLocked",
        "isDefault",
        "isDynamic",
        "dimensions",
        "fromColumn",
        "column",
    }
)
# A plugin entry holding any of these is a snapshot of the installed plugin.
# The toolbar editor's own default `kizen_ai-build` entry holds none of them.
PLUGIN_SNAPSHOT_MARKERS = ("script", "event_scripts", "css", "args")


def _item(icon: str, label: str, **flags: Any) -> dict[str, Any]:
    return {"icon": icon, "label": label, **flags}


_LOCKED = {"isLocked": True}
_TEXT_COLOR_LOCKED = {"isTextLocked": True, "isColorLocked": True}

# name → the entry minus id/type/isDefault/isLocked/widthPercent. Labels,
# icons and colours as the toolbar editor saved them on 2026-11-13.
BUILTIN_ITEMS: dict[str, dict[str, Any]] = {
    "dashboard": _item("chart-bar", "Dashboard", color="#285DE4"),
    "homepages": _item("house", "Homepage"),
    "contacts": _item("user", "Contacts", isDefaultClientObject=True),
    "custom_objects": _item("layer-group", "Custom Objects"),
    "activities": _item("clipboard-list", "Activities"),
    "automations": _item("arrow-progress", "Agentic Workflows", color="#4926CC"),
    "broadcasts": _item("calendar-alt", "Broadcasts"),
    "forms": _item("form-entity", "Forms"),
    "surveys": _item("survey-entity", "Surveys"),
    "library": _item("book-alt", "Library"),
    "smart_connectors": _item("arrows-to-circle", "SmartConnectors"),
    "knowledge": _item("books", "Custom Knowledge"),
    "mcp_servers": _item("mcp-entity", "MCP Servers"),
    "ads": _item("bullseye-pointer", "Ad Manager"),
    "marketing_costs": _item("receipt", "Marketing Costs"),
    "marketplace": _item("store", "Apps", color="#AE764A"),
    "business_apps": _item("rectangle-code", "Business Apps"),
    "settings": _item("cog", "Settings", color="#38B3B9", editable=True),
    "knowledge_base": _item("graduation-cap", "Knowledge Base"),
    "chat_with_us": _item("comments", "Chat With Us"),
    "notification_center": _item(
        "bell", "Notification Center", topLevelOnly=True, **_TEXT_COLOR_LOCKED
    ),
    "my_profile": _item("user-circle", "My Profile", **_LOCKED),
    "change_business": _item("repeat", "Change Business", **_LOCKED),
    "add_business": _item("add", "Add Business"),
    "logout": _item("sign-out", "Logout", **_LOCKED),
}
BUILTIN_CATEGORIES: dict[str, dict[str, Any]] = {
    "data": _item("database", "Data", color="#32C055"),
    "platform": _item("browser", "Platform", color="#F3A800"),
    "help": _item("question-circle", "Help", **_TEXT_COLOR_LOCKED),
    "recent_records": _item(
        "history",
        "Recent Records",
        color="#285DE4",
        hasPopup=True,
        wasDynamic=True,
        dynamicallyPopulated=True,
    ),
    "my_account": _item("user", "My Account", isColorLocked=True, **_LOCKED),
}


def builtin_entry(name: str) -> dict[str, Any]:
    """The built-in item or category ``default__<name>``, as the editor saves it."""
    is_category = name in BUILTIN_CATEGORIES
    base = (BUILTIN_CATEGORIES if is_category else BUILTIN_ITEMS)[name]
    entry: dict[str, Any] = {
        "id": f"{DEFAULT_PREFIX}{name}",
        "type": "category" if is_category else "item",
        "isLocked": False,
        "isDefault": True,
        "widthPercent": 100,
        **copy.deepcopy(base),
    }
    if is_category:
        entry["children"] = []
    return entry


def locked_entries() -> list[dict[str, Any]]:
    """Notification Center, then My Account with My Profile, Change Business
    and Logout: every template the UI saves carries them."""
    account = builtin_entry("my_account")
    account["children"] = [
        builtin_entry(n) for n in ("my_profile", "change_business", "logout")
    ]
    return [builtin_entry("notification_center"), account]


def entry_kind(entry: dict[str, Any]) -> tuple[str, str]:
    """``(kind, ref)`` for one wire entry. ``ref`` identifies it within a
    template: object UUID (or ``client_client``), plugin entry id, URL,
    built-in name, category id or custom category name."""
    id_ = str(entry.get("id") or "")
    if id_ == CONTACTS_ID:
        return "object", "client_client"
    if entry.get("isPluginItem"):
        return "plugin", id_
    if entry.get("isCustomObject") or is_uuid(id_):
        return "object", id_
    if entry.get("type") == "category":
        if id_.startswith(DEFAULT_PREFIX):
            return "category", id_
        return "category", str(entry.get("customLabel") or entry.get("label") or id_)
    if id_.startswith("new_custom_url--") or "url" in entry:
        return "url", str(entry.get("url") or "")
    if id_.startswith(DEFAULT_PREFIX):
        return "builtin", id_.removeprefix(DEFAULT_PREFIX)
    return "raw", id_


def entry_label(entry: dict[str, Any]) -> str:
    """What the toolbar shows for an entry."""
    return str(
        entry.get("customLabel")
        or entry.get("displayName")
        or entry.get("label")
        or entry.get("id")
    )


def is_plugin_snapshot(entry: dict[str, Any]) -> bool:
    return bool(entry.get("isPluginItem")) and any(
        k in entry for k in PLUGIN_SNAPSHOT_MARKERS
    )


def _client() -> KizenClient:
    return KizenClient(load_env_config())


def list_toolbar_templates(search: str | None = None) -> list[dict[str, Any]]:
    with _client() as c:
        return tt_api.list_toolbar_templates(c, search=search)


def find_toolbar_template(ref: str) -> dict[str, Any]:
    """A template's full read shape, by UUID or exact name."""
    with _client() as c:
        if is_uuid(ref):
            try:
                return tt_api.get_toolbar_template(c, ref)
            except KizenAPIError as e:
                if e.status_code != 404:
                    raise
        items = tt_api.list_toolbar_templates(c)
    matches = [t for t in items if t.get("name") == ref]
    if not matches:
        names = ", ".join(sorted(t.get("name") or "" for t in items)) or "none"
        raise LookupError(f"no toolbar template '{ref}'; available: {names}")
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise LookupError(
            f"'{ref}' names {len(matches)} toolbar templates; use one of {ids}"
        )
    return matches[0]


def owner_name(template: dict[str, Any]) -> str:
    owner = template.get("owner")
    return (owner.get("display_name") or "") if isinstance(owner, dict) else ""


def template_entries(template: dict[str, Any]) -> list[dict[str, Any]]:
    """``configuration_json`` as a list; a template saved with no entries reads ``{}``."""
    config = template.get("configuration_json")
    return config if isinstance(config, list) else []


def template_summary(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": template.get("name"),
        "owner": owner_name(template),
        "entries": len(template_entries(template)),
        "updated": template.get("updated"),
        "id": template.get("id"),
    }


def object_api_names() -> dict[str, str]:
    """Object UUID → api_name, Contacts included."""
    with _client() as c:
        objs = co_api.list_objects(c, custom_only=False)
    return {o["id"]: o["name"] for o in objs if o.get("id") and o.get("name")}


def _display_overrides(entry: dict[str, Any], kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if kind not in ("category", "url") and entry.get("label") is not None:
        out["label"] = entry["label"]
    for key in ("icon", "color"):
        if entry.get(key) is not None:
            out[key] = entry[key]
    if entry.get("widthPercent") == 50:
        out["width"] = 50
    return out


def entry_to_spec(
    entry: dict[str, Any], object_names: dict[str, str]
) -> dict[str, Any]:
    """One wire entry in the spec shape. Anything without a spec kind is ``raw``."""
    kind, ref = entry_kind(entry)
    spec: dict[str, Any]
    if kind == "object" and ref == "client_client":
        spec = {"object": "client_client"}
    elif kind == "object" and ref in object_names:
        spec = {"object": object_names[ref]}
    elif kind == "plugin" and is_plugin_snapshot(entry):
        spec = {"plugin": entry.get("plugin_api_name"), "page": entry.get("api_name")}
    elif kind == "url":
        spec = {"url": ref, "label": entry.get("displayName") or ""}
    elif kind == "builtin" and ref in BUILTIN_ITEMS:
        spec = {"builtin": ref}
    elif kind == "category" and not entry.get("config"):
        name = ref.removeprefix(DEFAULT_PREFIX)
        children = entry.get("children") or []
        spec = {
            "category": name,
            "entries": [entry_to_spec(c, object_names) for c in children],
        }
    else:
        return {"raw": entry}
    return {**spec, **_display_overrides(entry, kind)}


def to_spec(template: dict[str, Any], object_names: dict[str, str]) -> dict[str, Any]:
    """``{id, name, owner, sharing_settings, entries}``, which ``update`` takes back."""
    owner = template.get("owner")
    return {
        "id": template.get("id"),
        "name": template.get("name"),
        "owner": owner.get("id") if isinstance(owner, dict) else owner,
        "sharing_settings": normalize_sharing_settings(
            template.get("sharing_settings") or {}
        ),
        "entries": [entry_to_spec(e, object_names) for e in template_entries(template)],
    }

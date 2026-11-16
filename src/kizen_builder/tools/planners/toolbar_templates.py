"""Plan creation, update and deletion of toolbar templates. One op per template.

A spec lists entries (objects, plugin pages, links, built-ins, categories),
built here into the ``configuration_json`` the toolbar editor saves. Every
plugin page in the result, business app pages included, is rebuilt from
``/api/external-integrations/bootstrap`` at plan time, the way the editor
builds it: an entry is a copy of the plugin page's scripts, so a copy made
before a plugin release runs the old scripts until the template is saved
again. A plugin entry holding no copy is a reference and is left as is.
Nothing here applies a template to a role or user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import plugins as plugins_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec import ToolbarEntryDef, ToolbarTemplateDef
from kizen_builder.tools.dashboards import (
    default_sharing_settings,
    normalize_sharing_settings,
)
from kizen_builder.tools.planners.saved_views import _sharing_diff, _unwrap_owner
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation, fetch_existing
from kizen_builder.tools.toolbar_templates import (
    BUILTIN_CATEGORIES,
    BUILTIN_ITEMS,
    CONTACTS_ID,
    MY_ACCOUNT_ID,
    NOTIFICATION_CENTER_ID,
    PLUGIN_KEPT_KEYS,
    builtin_entry,
    entry_kind,
    entry_label,
    find_toolbar_template,
    is_plugin_snapshot,
    list_toolbar_templates,
    locked_entries,
    template_entries,
)
from kizen_builder.utils import is_uuid

URL_PREFIX = "new_custom_url--"
CATEGORY_PREFIX = "new_category--"
_NUMBERED = re.compile(r"^(new_custom_url|new_category)--(\d+)$")
_CONTACTS_NAMES = ("client_client", "contacts")
DEFAULT_URL_COLOR = "#285DE4"
DEFAULT_CATEGORY_ICON = {
    "label": "folder",
    "value": "folder",
    "className": "fa-light fa-folder",
}


@dataclass
class _Context:
    """Live state one plan reads: objects, installed plugins, business apps."""

    client: KizenClient
    business_id: str
    objects: list[dict[str, Any]]
    bootstrap: dict[str, Any]
    _apps: list[dict[str, Any]] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def apps(self) -> list[dict[str, Any]]:
        if self._apps is None:
            self._apps = plugins_api.list_business_apps(self.client, self.business_id)
        return self._apps

    def object_by_name(self, api_name: str) -> dict[str, Any] | None:
        return next((o for o in self.objects if o.get("name") == api_name), None)

    def object_ids(self) -> set[str]:
        return {o["id"] for o in self.objects if o.get("id")}

    def plugin_key(self, ref: str) -> str | None:
        """The bootstrap key for an installed plugin api_name or a business
        app's ``base_api_name``."""
        if ref in self.bootstrap:
            return ref
        app = next((a for a in self.apps if a.get("base_api_name") == ref), None)
        key = app.get("api_name") if app else None
        return key if key in self.bootstrap else None


def _load_context(client: KizenClient, business_id: str) -> _Context:
    return _Context(
        client=client,
        business_id=business_id,
        objects=co_api.list_objects(client, custom_only=False),
        bootstrap=plugins_api.get_bootstrap(client) or {},
    )


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------


def _overrides(e: ToolbarEntryDef) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if e.label is not None:
        out["displayName" if e.kind == "url" else "label"] = e.label
    if e.icon is not None:
        out["icon"] = e.icon
    if e.color is not None:
        out["color"] = e.color
    if e.width is not None:
        out["widthPercent"] = e.width
    return out


def _object_entry(name: str, ctx: _Context) -> dict[str, Any]:
    if name in _CONTACTS_NAMES:
        return builtin_entry("contacts")
    obj = ctx.object_by_name(name)
    if obj is None or not obj.get("is_custom", True):
        raise PlanError(f"object '{name}' not found")
    return {
        "id": obj["id"],
        "icon": obj.get("default_icon"),
        "type": "item",
        "color": obj.get("default_color"),
        "label": obj.get("object_name") or name,
        "isLocked": False,
        "isDefault": False,
        "widthPercent": 100,
        "isCustomObject": True,
    }


def _toolbar_pages(install: dict[str, Any]) -> list[str]:
    pages = [
        p["api_name"]
        for p in install.get("routable_pages") or []
        if p.get("is_toolbar_item")
    ]
    return pages + [t["api_name"] for t in install.get("toolbar_items") or []]


def _plugin_entry(
    key: str, api_name: str, ctx: _Context, *, page_first: bool = True
) -> dict[str, Any] | None:
    """The entry the editor builds for one plugin page or toolbar item, or
    None when the install has neither by that api_name."""
    install = ctx.bootstrap[key]
    page = next(
        (
            p
            for p in install.get("routable_pages") or []
            if p.get("api_name") == api_name
        ),
        None,
    )
    item = next(
        (
            t
            for t in install.get("toolbar_items") or []
            if t.get("api_name") == api_name
        ),
        None,
    )
    source = (page or item) if page_first else (item or page)
    if source is None:
        return None
    plugin_keys = {
        "type": "item",
        "isLocked": False,
        "isDefault": False,
        "isDynamic": False,
        "widthPercent": 100,
        "isPluginItem": True,
        "plugin_api_name": key,
        "plugin_name": install.get("name"),
        "args": install.get("business_config") or {},
        "employee_config": {},
    }
    if source is page:
        return {
            "id": f"{key}-{api_name}",
            **page,
            **plugin_keys,
            "label": page.get("name"),
            "icon": page.get("toolbar_icon"),
            "color": page.get("toolbar_color"),
            "script": "",
        }
    return {"id": f"{key}-{api_name}", **source, **plugin_keys}


def _spec_plugin_entry(e: ToolbarEntryDef, ctx: _Context) -> dict[str, Any]:
    assert e.plugin is not None and e.page is not None
    key = ctx.plugin_key(e.plugin)
    if key is None:
        installed = ", ".join(sorted(ctx.bootstrap)) or "none"
        raise PlanError(f"plugin '{e.plugin}' is not installed; installed: {installed}")
    install = ctx.bootstrap[key]
    if e.page not in _toolbar_pages(install):
        pages = ", ".join(_toolbar_pages(install)) or "none"
        raise PlanError(
            f"plugin '{key}' has no toolbar page '{e.page}'; toolbar pages: {pages}"
        )
    built = _plugin_entry(key, e.page, ctx)
    assert built is not None
    return built


def _rebuild(entry: dict[str, Any], ctx: _Context, *, given: bool) -> dict[str, Any]:
    """A plugin entry with its copy of the plugin refreshed from bootstrap.

    Display keys (label, icon, colour, width, drag state) stay as they are.
    An entry that isn't a snapshot, like the editor's default ``kizen_ai-build``,
    is left alone. ``given`` says whether the user supplied it: then a plugin
    or page that isn't installed is an error, otherwise a warning.
    """
    if not is_plugin_snapshot(entry):
        return entry
    key = str(entry.get("plugin_api_name") or "")
    api_name = str(entry.get("api_name") or "")
    page_first = "when" not in entry
    fresh = (
        _plugin_entry(key, api_name, ctx, page_first=page_first)
        if key in ctx.bootstrap
        else None
    )
    if fresh is None:
        what = (
            f"'{key}' has no page '{api_name}'"
            if key in ctx.bootstrap
            else f"'{key}' is not installed"
        )
        if given:
            raise PlanError(f"plugin entry '{entry.get('id')}': {what}")
        ctx.warnings.append(f"plugin entry {entry.get('id')}: {what}; left as saved")
        return entry
    snapshot = {k: v for k, v in fresh.items() if k not in PLUGIN_KEPT_KEYS}
    return {**entry, **snapshot}


def _check_raw(entry: Any, ctx: _Context, *, top_level: bool, where: str) -> None:
    if not isinstance(entry, dict) or not entry.get("id") or not entry.get("type"):
        raise PlanError(f"{where}: each entry is an object with 'id' and 'type'")
    if entry["type"] == "category" and not top_level:
        raise PlanError(f"{where}: category '{entry['id']}' is inside a category")
    kind, ref = entry_kind(entry)
    if kind == "object" and is_uuid(ref) and ref not in ctx.object_ids():
        raise PlanError(f"{where}: object '{ref}' not found")
    for child in entry.get("children") or []:
        _check_raw(child, ctx, top_level=False, where=where)


def _carry(
    entries: list[dict[str, Any]], ctx: _Context, *, given: bool
) -> list[dict[str, Any]]:
    """Entries kept as they are, with plugin entries rebuilt."""
    out = []
    for entry in entries:
        entry = _rebuild(entry, ctx, given=given)
        if entry.get("children"):
            entry = {**entry, "children": _carry(entry["children"], ctx, given=given)}
        out.append(entry)
    return out


def _flatten(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        out.append(e)
        out.extend(_flatten(e.get("children") or []))
    return out


class _Builder:
    """Builds spec entries, reusing the live entry each one matches."""

    def __init__(self, ctx: _Context, live: list[dict[str, Any]]):
        self.ctx = ctx
        self.live: dict[tuple[str, str], dict[str, Any]] = {}
        for e in _flatten(live):
            self.live.setdefault(entry_kind(e), e)
        self.new: list[dict[str, Any]] = []

    def _match(self, kind: str, ref: str) -> dict[str, Any] | None:
        return self.live.pop((kind, ref), None)

    def entries(
        self, specs: list[ToolbarEntryDef], *, top_level: bool
    ) -> list[dict[str, Any]]:
        return [self.entry(e, top_level=top_level) for e in specs]

    def entry(self, e: ToolbarEntryDef, *, top_level: bool) -> dict[str, Any]:
        overrides = _overrides(e)
        if e.kind == "raw":
            assert e.raw is not None
            _check_raw(e.raw, self.ctx, top_level=top_level, where="raw entry")
            return {**_carry([e.raw], self.ctx, given=True)[0], **overrides}
        if e.kind == "category":
            return self._category(e, overrides, top_level=top_level)
        if e.kind == "object":
            assert e.object is not None
            built = _object_entry(e.object, self.ctx)
            ref = "client_client" if built["id"] == CONTACTS_ID else built["id"]
            return self._merge("object", ref, built, overrides)
        if e.kind == "builtin":
            assert e.builtin is not None
            return self._builtin(e.builtin, overrides)
        if e.kind == "url":
            assert e.url is not None
            built = {
                "id": "",
                "url": e.url,
                "icon": "toolbar-external-link",
                "type": "item",
                "color": DEFAULT_URL_COLOR,
                "label": "New Custom URL (Drag to Create)",
                "column": 1,
                "hasPopup": True,
                "isLocked": False,
                "isDynamic": False,
                "wasDynamic": True,
                "customLabel": "",
                "hasPoppedUp": True,
                "widthPercent": 100,
            }
            return self._merge("url", e.url, built, overrides)
        built = _spec_plugin_entry(e, self.ctx)
        matched = self._match("plugin", built["id"])
        if matched is None:
            return {**built, **overrides}
        snapshot = {k: v for k, v in built.items() if k not in PLUGIN_KEPT_KEYS}
        return {"employee_config": {}, **matched, **snapshot, **overrides}

    def _merge(
        self, kind: str, ref: str, built: dict[str, Any], overrides: dict[str, Any]
    ) -> dict[str, Any]:
        matched = self._match(kind, ref)
        if matched is not None:
            return {**matched, **overrides}
        entry = {**built, **overrides}
        if not entry["id"]:
            self.new.append(entry)
        return entry

    def _builtin(self, name: str, overrides: dict[str, Any]) -> dict[str, Any]:
        if name in BUILTIN_CATEGORIES:
            raise PlanError(
                f"builtin '{name}' is a category; use "
                f'{{"category": "{name}", "entries": [...]}}'
            )
        if name not in BUILTIN_ITEMS:
            known = ", ".join(sorted(BUILTIN_ITEMS))
            raise PlanError(f"unknown builtin '{name}'; known: {known}")
        if name == "contacts":
            return self._merge(
                "object", "client_client", builtin_entry(name), overrides
            )
        return self._merge("builtin", name, builtin_entry(name), overrides)

    def _category(
        self, e: ToolbarEntryDef, overrides: dict[str, Any], *, top_level: bool
    ) -> dict[str, Any]:
        assert e.category is not None
        if not top_level:
            raise PlanError(f"category '{e.category}' is inside a category")
        children = self.entries(e.entries or [], top_level=False)
        if e.category in BUILTIN_CATEGORIES:
            ref = f"default__{e.category}"
            built = builtin_entry(e.category)
        else:
            ref = e.category
            built = {
                "id": "",
                "icon": DEFAULT_CATEGORY_ICON,
                "type": "category",
                "label": "New Category (Drag to Create)",
                "column": 1,
                "children": [],
                "isLocked": False,
                "isDynamic": False,
                "wasDynamic": True,
                "customLabel": e.category,
                "widthPercent": 100,
            }
        entry = self._merge("category", ref, built, overrides)
        if e.entries is not None or not entry.get("children"):
            entry["children"] = children
        else:
            entry["children"] = _carry(entry["children"], self.ctx, given=False)
        return entry

    def number_new(self, result: list[dict[str, Any]]) -> None:
        """Give each new link and category the next free ``--<n>`` id."""
        taken: dict[str, int] = {URL_PREFIX: 0, CATEGORY_PREFIX: 0}
        for e in _flatten(result):
            m = _NUMBERED.match(str(e.get("id") or ""))
            if m:
                prefix = f"{m.group(1)}--"
                taken[prefix] = max(taken[prefix], int(m.group(2)))
        for e in self.new:
            prefix = URL_PREFIX if e["type"] == "item" else CATEGORY_PREFIX
            taken[prefix] += 1
            e["id"] = f"{prefix}{taken[prefix]}"


# ---------------------------------------------------------------------------
# Whole-list assembly
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    entries: list[dict[str, Any]]
    added: list[str]
    warnings: list[str]


def _assemble(
    ctx: _Context,
    *,
    entries: list[ToolbarEntryDef] | None,
    config: Any,
    live: list[dict[str, Any]],
) -> _Result:
    if entries is not None:
        builder = _Builder(ctx, live)
        result = builder.entries(entries, top_level=True)
        builder.number_new(result)
    elif config is not None:
        if not isinstance(config, list):
            raise PlanError("configuration_json: expected a list of entries")
        for entry in config:
            _check_raw(entry, ctx, top_level=True, where="configuration_json")
        result = _carry(config, ctx, given=True)
    else:
        result = _carry(live, ctx, given=False)

    for category in result:
        for child in _flatten(category.get("children") or []):
            if child.get("topLevelOnly"):
                raise PlanError(
                    f"'{entry_label(child)}' goes at the top level, "
                    f"not inside category '{entry_label(category)}'"
                )

    added = []
    present = {e.get("id") for e in _flatten(result)}
    notification, account = locked_entries()
    if NOTIFICATION_CENTER_ID not in present:
        result.append(notification)
        added.append("Notification Center")
    if MY_ACCOUNT_ID not in present:
        result.append(account)
        added.append("My Account")

    # After the append: My Account brings My Profile, Change Business and Logout.
    ids = [str(e.get("id")) for e in _flatten(result)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        hint = (
            " (the added My Account holds My Profile, Change Business and Logout)"
            if "My Account" in added
            else ""
        )
        raise PlanError(f"duplicate entry id(s): {', '.join(dupes)}{hint}")

    warnings = list(ctx.warnings)
    plugin_entries = [e for e in _flatten(result) if e.get("isPluginItem")]
    for e in plugin_entries:
        key = str(e.get("plugin_api_name") or "")
        if "_preview_" in key:
            warnings.append(f"plugin entry {e.get('id')} uses a preview install")
    if plugin_entries:
        keys = {e.get("plugin_api_name") for e in plugin_entries}
        for app in ctx.apps:
            if app.get("api_name") in keys and app.get("live_version") is None:
                for e in plugin_entries:
                    if e.get("plugin_api_name") == app["api_name"]:
                        warnings.append(
                            f"{app['api_name']} is not published; "
                            f"{e.get('id')} carries your preview"
                        )
    return _Result(result, added, warnings)


def _labels(entries: list[dict[str, Any]]) -> list[str]:
    out = []
    for e in entries:
        label = entry_label(e)
        if e.get("type") == "category":
            label += f" [{', '.join(entry_label(c) for c in e.get('children') or [])}]"
        out.append(label)
    return out


def _refreshed(
    ctx: _Context, live: list[dict[str, Any]], result: list[dict[str, Any]]
) -> list[str]:
    before = {e.get("id"): e for e in _flatten(live) if e.get("isPluginItem")}
    out = []
    for e in _flatten(result):
        old = before.get(e.get("id"))
        if old is None or not e.get("isPluginItem"):
            continue
        changed = any(
            old.get(k) != v for k, v in e.items() if k not in PLUGIN_KEPT_KEYS
        )
        if changed:
            install = ctx.bootstrap.get(str(e.get("plugin_api_name")), {})
            version = install.get("installed_version") or install.get("version")
            suffix = f" ({version})" if version else ""
            out.append(f"plugin snapshot refreshed: {e.get('id')}{suffix}")
    return out


def _notes(result: _Result, refreshed: list[str]) -> dict[str, Any]:
    notes = list(refreshed)
    if result.added:
        notes.append(f"added: {', '.join(result.added)} (every template carries them)")
    out: dict[str, Any] = {}
    if notes:
        out["notes"] = notes
    if result.warnings:
        out["warnings"] = result.warnings
    return out


def _parse(spec: dict[str, Any] | ToolbarTemplateDef) -> ToolbarTemplateDef:
    if isinstance(spec, ToolbarTemplateDef):
        return spec
    if not isinstance(spec, dict):
        raise PlanError("a toolbar template spec is a JSON object.")
    return ToolbarTemplateDef.model_validate(spec)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def plan_create_toolbar_template(spec: dict[str, Any] | ToolbarTemplateDef) -> Plan:
    d = _parse(spec)
    if not d.name:
        raise PlanError("a new toolbar template needs a name.")
    config = load_env_config()
    existing = next(
        (t for t in list_toolbar_templates() if t.get("name") == d.name), None
    )
    if existing is not None:
        raise PlanError(
            f"toolbar template '{d.name}' already exists (uuid {existing['id']}). "
            "Use update instead."
        )
    with KizenClient(config) as client:
        ctx = _load_context(client, config.business_id)
        result = _assemble(ctx, entries=d.entries, config=d.configuration_json, live=[])

    payload: dict[str, Any] = {
        "name": d.name,
        "configuration_json": result.entries,
        "sharing_settings": (
            normalize_sharing_settings(d.sharing_settings)
            if d.sharing_settings
            else default_sharing_settings()
        ),
    }
    if d.owner is not None:
        payload["owner"] = d.owner
    op = PlanOperation(
        action="create",
        kind="toolbar_template",
        key=d.name,
        preview={
            "env": config.name,
            "name": d.name,
            "entries": _labels(result.entries),
            **_notes(result, []),
        },
        payload=payload,
    )
    return Plan.build(
        env=config.name, summary=f"Create toolbar template '{d.name}'", operations=[op]
    )


def plan_update_toolbar_template(
    template: str, spec: dict[str, Any] | ToolbarTemplateDef | None
) -> Plan:
    """PATCH the keys that changed. With no spec, or one without ``entries``/
    ``configuration_json``, the live list is re-sent only when rebuilding its
    plugin entries changed it."""
    d = _parse(spec) if spec is not None else ToolbarTemplateDef()
    config = load_env_config()
    live = fetch_existing(
        "toolbar template", template, lambda: find_toolbar_template(template)
    )
    live_entries = template_entries(live)
    with KizenClient(config) as client:
        ctx = _load_context(client, config.business_id)
        result = _assemble(
            ctx, entries=d.entries, config=d.configuration_json, live=live_entries
        )

    payload: dict[str, Any] = {}
    diff: dict[str, str] = {}
    if d.name is not None and d.name != live.get("name"):
        payload["name"] = d.name
        diff["name"] = f"{live.get('name')} → {d.name}"
    refreshed: list[str] = []
    if result.entries != live_entries:
        payload["configuration_json"] = result.entries
        before, after = _labels(live_entries), _labels(result.entries)
        if before != after:
            diff["entries"] = f"{before} → {after}"
        refreshed = _refreshed(ctx, live_entries, result.entries)
    live_owner = _unwrap_owner(live.get("owner")) or None
    if d.owner is not None and d.owner != live_owner:
        payload["owner"] = d.owner
        diff["owner"] = f"{live_owner} → {d.owner}"
    if d.sharing_settings is not None:
        new = normalize_sharing_settings(d.sharing_settings)
        sharing = _sharing_diff(
            normalize_sharing_settings(live.get("sharing_settings") or {}), new
        )
        if sharing:
            payload["sharing_settings"] = new
            diff.update({k: f"{a} → {b}" for k, (a, b) in sharing.items()})

    name = live.get("name") or live["id"]
    if "configuration_json" in payload and "entries" not in diff:
        diff["entries"] = "same labels; entry details changed"
    op = PlanOperation(
        action="update" if payload else "skip",
        kind="toolbar_template",
        key=name,
        preview={
            "env": config.name,
            "toolbar_template": name,
            "diff": diff or "no changes",
            **(_notes(result, refreshed) if payload else {}),
        },
        payload=payload,
        existing_uuid=live["id"],
    )
    summary = (
        f"Update toolbar template '{name}'"
        if payload
        else f"No changes to toolbar template '{name}'"
    )
    return Plan.build(env=config.name, summary=summary, operations=[op])


def plan_delete_toolbar_template(template: str) -> Plan:
    env = load_env_config().name
    live = fetch_existing(
        "toolbar template", template, lambda: find_toolbar_template(template)
    )
    name = live.get("name") or live["id"]
    op = PlanOperation(
        action="delete",
        kind="toolbar_template",
        key=name,
        preview={"env": env, "toolbar_template": name},
        existing_uuid=live["id"],
    )
    return Plan.build(
        env=env, summary=f"Delete toolbar template '{name}'", operations=[op]
    )

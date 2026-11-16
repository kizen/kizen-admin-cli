# Spec shape: `ToolbarTemplateDef` — toolbar templates

**Consumed by:** `kizen toolbar-templates create|update <template> --spec-file <f>`
(also stdin). `delete <template>` takes no spec.

A toolbar template is a named, ordered left-hand menu. A template is
referenced by UUID or exact name; names are unique.

Read first: `kizen toolbar-templates list`, and `get <template> --json`, which
prints the spec shape `update` takes (`--raw` for the wire JSON).

**Nothing here applies the template to anyone.** Applying replaces each
person's toolbar, so do it in the Kizen UI.

---

## Quick example

```json
{"name": "Field Staff", "entries": [
  {"object": "sites"}, {"object": "client_client"}, {"object": "inspections"},
  {"object": "work_orders"}, {"object": "crews"}, {"object": "equipment"},
  {"plugin": "field_ops", "page": "staff", "label": "Field Staff"},
  {"plugin": "stock_tracker", "page": "inventory"},
  {"url": "https://example.com/handbook", "label": "Handbook"},
  {"category": "Admin", "entries": [
    {"builtin": "settings", "width": 50}, {"builtin": "automations", "width": 50}]}]}
```

## `ToolbarTemplateDef` fields

Any other key is a spec error.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Required on create. Unique. |
| `entries` | list | The menu, top to bottom. See below. Replaces the whole list. |
| `configuration_json` | list | Raw wire entries instead of `entries` (not both). Each needs `id` and `type`. |
| `sharing_settings` | object | `private`, `all_team_members` (0–3), `roles` and `team_members` as `{view, edit, admin}` lists of UUIDs (`{id, display_name}` entries are accepted). |
| `owner` | UUID | Create leaves it out unless given; the caller becomes the owner. |
| `id` | — | Ignored. `get --json` carries it. |

## Entries

Each entry has exactly one kind key:

| Kind | Shape | Becomes |
|---|---|---|
| object | `{"object": "<api_name>"}` | The object's menu item, labelled with its display name. `client_client` (or `contacts`) is the built-in Contacts item. |
| plugin page | `{"plugin": "<api_name>", "page": "<api_name>"}` | An installed plugin's toolbar page (`is_toolbar_item: true`) or toolbar item. A business app is a plugin too: give its bootstrap key or its `base_api_name`, so a spec ports between businesses. |
| link | `{"url": "<https url>", "label": "<text>"}` | A custom URL item. |
| built-in | `{"builtin": "<name>"}` | A Kizen item: `dashboard`, `homepages`, `contacts`, `custom_objects`, `activities`, `automations`, `broadcasts`, `forms`, `surveys`, `library`, `smart_connectors`, `knowledge`, `mcp_servers`, `ads`, `marketing_costs`, `marketplace`, `business_apps`, `settings`, `knowledge_base`, `chat_with_us`, `notification_center`, `my_profile`, `change_business`, `add_business`, `logout`. |
| category | `{"category": "<name>", "entries": [...]}` | A top-level group of items. `data`, `platform`, `help`, `recent_records` and `my_account` are Kizen's own categories; any other name is a new one. No category inside a category. |
| raw | `{"raw": {"id": ..., "type": ..., ...}}` | One wire entry passed through (a dynamic category, an unknown kind). A raw plugin page is still rebuilt. |

Items also take `label`, `icon` (a name, or the UI's `{label, value,
className}` dict), `color` and `width` (`50` for half a row, or `100`).

**Every write rebuilds plugin pages from the installed plugin, business app
pages included, the way the UI does.** A plugin page entry is a copy of the
page's scripts, so a template saved before a plugin release runs the old
scripts; `update <template>` with no spec re-saves it. A saved plan carries
the copy from when it was planned, so re-plan after a release. A plugin entry
with no `script`, `event_scripts`, `css` or `args`, like the UI's default
`kizen_ai-build`, is a reference to the plugin's toolbar item rather than a
copy, and is kept as a reference, as the UI keeps it.

**Every template carries Notification Center and My Account** (My Profile,
Change Business, Logout). When the entries leave them out they are appended,
and the preview says so. Notification Center goes at the top level only, and
My Profile, Change Business or Logout placed elsewhere would appear twice once
My Account is appended, so both are plan errors.

**New templates are private to their owner unless `sharing_settings` says
otherwise:** `private: true`, `all_team_members: 0`, and the Admin role as
admin.

## What the plan does

- **create** refuses a name that exists. Objects, plugins and pages are
  checked live; an unknown one is a plan error that lists what exists.
- **update** sends only what changed. An entry that matches a live one (same
  object, plugin page, built-in, URL or category name) keeps the live entry's
  keys the spec doesn't set, such as an icon picked in the UI. With no spec,
  the template is re-sent only when a plugin rebuild changed it, and the
  preview lists `plugin snapshot refreshed: <id> (<version>)`; otherwise the
  op is a skip. `get --json` fed back unchanged is a skip.
- **Warnings** (the write still goes ahead): a business app with no published
  version (`<app> is not published; <id> carries your preview`), a `_preview_`
  install, and a plugin entry on the live template whose plugin is no longer
  installed (left as saved). A plugin the spec names that isn't installed is
  an error.

---

# Wire format & API behavior

## Endpoints

`GET/POST /api/toolbar-templates`, `GET/PATCH/PUT/DELETE
/api/toolbar-templates/{id}`. The list items carry the full detail shape
(`light=false`). Updates are PATCH. `confirmed live 2026-11-13`

Plugin pages come from `GET /api/external-integrations/bootstrap`, keyed by
plugin api_name; business apps from `GET
/api/external-integrations/plugin-apps/{business_id}/apps`, whose
`live_version` is `null` until the app is published. `confirmed live
2026-11-13`

## Entry wire shapes

`configuration_json` is a list of entries with `id`, `type` (`item` or
`category`), `label`, `icon`, `color`, `widthPercent`, `isLocked`,
`isDefault`, and UI drag state (`dimensions`, `fromColumn`). Categories hold
`children`. `confirmed live 2026-11-13`

| Kind | `id` | Notes |
|---|---|---|
| Built-in | `default__<name>` | `isDefault: true`. Contacts adds `isDefaultClientObject`. |
| Custom object | the object UUID | `isCustomObject: true`. |
| Link | `new_custom_url--<n>` | `url`; `displayName` is the shown text. |
| Category | `new_category--<n>` | `customLabel` is the shown name. |
| Plugin page | `<plugin>-<page>` | Every key of the bootstrap page, then `label`/`icon`/`color` from its `name`/`toolbar_icon`/`toolbar_color`, `script: ""`, `isPluginItem: true`, `plugin_api_name`, `plugin_name`, `args` (the install's `business_config`) and `employee_config: {}`. |
| Plugin toolbar item | `<plugin>-<api_name>` | The bootstrap `toolbar_items[]` keys plus the same plugin keys. |

A business app's preview owner sees the preview's pages in bootstrap under
the live api_name, so a template they save carries the preview for everyone;
that is what the unpublished warning flags. `confirmed live 2026-11-13`

## Server rules

- Names are unique: a duplicate is a 400 `Template names must be unique.`
  `confirmed live 2026-11-13`
- `sharing_settings` is required on POST and PUT, and the Admin role must
  have admin access (400 otherwise). Role and member ids are bare UUIDs; an
  `{"id": ...}` dict is a 400. `private: true, all_team_members: 0` is stored
  as sent. `confirmed live 2026-11-13`
- `configuration_json` is not validated: any JSON is stored verbatim, which is
  why the CLI checks entries before sending. A template saved with no entries
  reads back as `{}`. `confirmed live 2026-11-13`

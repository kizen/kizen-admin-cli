# Spec shape: `DashboardDef` — dashboards & homepages

**Consumed by:** `kizen dashboards create|update --spec-file <f>` (also stdin).

> The dashlet `config`/`layout` surface is wide, so a spec still carries them
> as **passthrough** dicts (a filter spec in `config.filters` is the one part
> compiled). But you don't hand-write a `config` and you don't
> need a live dashlet to copy — **generate one with
> `kizen dashboards dashlet-config`** (see below). It emits a valid config for
> any dashlet type straight from the library builders, so it can't drift from
> what `create` accepts and it works on an env with no dashboards yet.

---

## Authoring a dashlet config — `dashboards dashlet-config`

Run `kizen dashboards dashlet-config` with no args to list every dashlet type,
then generate one:

```bash
# just the config (drop it into a DashboardDef's dashlets[].config)
kizen dashboards dashlet-config --type field_breakdown

# resolve object/field api_names → real UUIDs (never pass raw UUIDs)
kizen dashboards dashlet-config --type field_breakdown --object clinics --field status

# a full DashboardDef, ready to pipe into `create`
kizen dashboards dashlet-config --type field_breakdown --object clinics --field status --full \
  | kizen dashboards create --dry-run
```

- **`--object` / `--field` take api_names**, resolved live to UUIDs. Omit them
  and the config comes back with `<OBJECT_UUID>` / `<FIELD_UUID>` placeholder
  tokens to fill in later (no live calls, works offline).
- **`--report-type` / `--chart-type` / `--metric-type` / `--frequency`** tune
  the parameterized metric families (`pipeline_metric`, `activity_metric`,
  `email_metric`, `marketing_metric`). `--frequency` (day/week/month) is
  required when `--chart-type line`; the command fills a sensible default.
- **`--filter` / `--filter-file`** narrow a custom-object dashlet's records,
  for example to the viewer's own. See
  [Showing each viewer their own records](#showing-each-viewer-their-own-records).
- **`--full`** wraps the config in a full `DashboardDef` (picking `homepage`
  vs `generic_dashboard` for you — see the homepage rule below). The dashlet
  has no `layout`; add one before `create` — see
  [Layout & sizing](#layout--sizing--dont-guess-wh).
- Guidance (placeholders present, homepage-only) prints to **stderr**, so
  stdout stays pure JSON you can pipe.

### Dashlet types

| `--type` | entity_type | report/chart | takes | homepage-only |
|----------|-------------|--------------|-------|:---:|
| `table_of_records` | custom_object | table_of_records / table | `--object` `--field`* | ✓ |
| `field_breakdown` | custom_object | field_metrics / donut | `--object` `--field` | ✓ |
| `field_sum` | custom_object | field_metrics / numeric | `--object` `--field` | ✓ |
| `field_range_breakdown` | custom_object | field_metrics / bar | `--object` `--field` | ✓ |
| `pivot_table` | custom_object | pivot_table / table | `--object` `--field`* | ✓ |
| `pipeline_metric` | pipeline | records_added… / numeric·line·… | `--object` (a pipeline) | |
| `activity_metric` | activity | records_added / line·numeric | `--object` (activity type) | |
| `scheduled_activities` | activity | scheduled_activities / — | `--object` (activity type) | |
| `scheduled_activities_calendar` | activity | …_calendar / calendar | `--object` (activity type) | |
| `email_metric` | email | email_sent… / numeric·line | — | |
| `marketing_metric` | marketing | leads_added… / numeric·bar·… | `--object` | |
| `html` | static_content | html / html | — | |

\* `--field` seeds one example column/row; add the rest by hand.

- **Custom-object dashlets are homepage-only.** The five `custom_object` types
  above 400 on a `generic_dashboard` ("Cannot create Custom Object Dashlet for
  generic Dashboards") — put them on a `type: "homepage"` dashboard. The
  planner catches this before apply; `--full` sets the right type for you.
- **Copying a live dashlet still works** (`dashboards get <id> --raw`) if you
  want to mirror an existing one, but it's no longer the only path.

---

## Quick example (envelope)

```json
{
  "api_name": "ops_overview",
  "name": "Ops Overview",
  "type": "generic_dashboard",
  "dashlets": [
    {
      "name": "Accounts by Region",
      "custom_object": "<custom_object_uuid>",
      "layout": { "x": 0, "y": 0, "w": 6, "h": 4 },
      "config": { "…copied from a live dashlet…": true }
    }
  ]
}
```

---

## `DashboardDef` fields

The CLI sends only the keys the spec sets. On `update`, a key you leave out
keeps its live value. On `create`, the server fills in what's left out, apart
from the two the API needs, noted below.

| Key | Type | Notes |
|-----|------|-------|
| `api_name` | string | **Required.** Stable identifier (`^[A-Za-z0-9_]+$`). |
| `name` | string | **Required.** Display name. |
| `type` | enum | `generic_dashboard`, `homepage` (team landing page), or `chart_group` (then `custom_object` is required). Omitted on create: `generic_dashboard`, since the API requires a type. |
| `custom_object` | UUID | Required only for `chart_group`. |
| `hidden` | bool | Server default `false`. |
| `published` | bool | Server default `false`. Kizen uses it only for homepages. |
| `style_settings` | object | Omitted: the dashboard gets `{}`, Kizen's default look. See [Styling](#styling). |
| `sharing_settings` | object | Omitted on create: the UI's default — see [`sharing_settings`](#sharing_settings-is-required-on-create). |
| `dashlets` | `DashletDef[]` | The tiles. |

## `DashletDef` fields

Same rule: only the keys you set are sent, and `update` PATCHes, so a key you
leave out keeps its live value.

| Key | Type | Notes |
|-----|------|-------|
| `id` | UUID | **Present → update that dashlet; absent → create a new one.** This is how `dashboards update` diffs. |
| `name` | string | Display name. Omitted on create: `"[default]"`, since the API requires a name. |
| `custom_object` | UUID | Object the dashlet queries; omit for non-object dashlets (e.g. email metrics). |
| `layout` | object | Grid geometry `{x, y, w, h, …}`. Passed through verbatim. Omitted on create: the server stores `{}`. |
| `config` | object | **Required to create.** `report_type`, `chart_type`, filters, … Generate with `dashboards dashlet-config` (above). `filters` is the wire block, or a filter spec, compiled at plan time. |

## Layout & sizing — don't guess `w`/`h`

The `layout` block places a dashlet on a **12-column grid**. Getting the
numbers wrong makes a dashboard look terrible (giant charts, tiny content), so
size deliberately:

- **`w` is a column span, 1–12.** `w: 12` is full width, `w: 6` half, `w: 4` a
  third. Dashlets in the same row should have `x` values that tile without
  gaps and `w` values that sum to 12 (e.g. two charts at `x:0 w:6` and
  `x:6 w:6`).
- **`h` is a row span, and real dashboards run SMALL** — typically 2–5, not
  the ~5–6 a first guess tends to produce. A donut at `h: 5` renders as a
  huge near-empty box.
- **`x`/`y` are grid coordinates** (columns / rows from the top-left), not
  pixels. Stack rows by advancing `y` by the previous row's `h`.

Recommended sizes, from real hand-built dashboards:

| Dashlet | `w` (typical) | `h` (typical) |
|---------|:---:|:---:|
| numeric tile (`field_sum`, metric `numeric`) | 2–3 | 2 |
| donut / bar (`field_breakdown`, `field_range_breakdown`) | 4–6 | 3 |
| line chart (metrics over time) | 6 | 3 |
| leaderboard / `opportunity_conversion` | 5–6 | 3 |
| `table_of_records` | 6–12 | 3–5 |
| `pivot_table` | 12 | 3–4 |
| `scheduled_activities` (calendar) | 12 | 4–5 |
| `scheduled_activities` (list) | 6–12 | 3–4 |
| `html` banner | 12 | 2–3 |

The minimal `{x, y, w, h}` shown above applies fine (the server fills the rest);
`dashboards.layout(x, y, w, h)` builds the same dict with an `i` cell id.

**A dashlet saved without a layout** is stored as `layout: {}`, and the UI
draws each missing key as `x: 0`, `y: 0`, `w: 2`, `h: 2` — a small tile in the
top-left corner (`getDashletGridLayout`, read from the Kizen web app source,
2026-11-11).

### HTML sizing is inside the content, not the layout

An `html` dashlet's text size comes from the HTML itself, not `h`. A bare
`<h2>` renders small in the block editor — for a real banner headline use an
inline style, e.g.
`<p style="font-size:32px;font-weight:700;margin:0">Title</p>`.

### HTML dashlet styling

`html_dashlet_config` gives every node the props the Kizen block builder
writes for a new block, and nothing else (read from the Kizen web app source,
2026-11-11; cited next to `HOMEPAGE_DEFAULTS` in `tools/form_ui.py`):

| node | default |
|---|---|
| `Root` | white background, `height` 335, `max_width` 1372, `has_shadow`, `columns` 6 (full width) |
| `Section`, `Row` | transparent, square corners, padding 10, `max_width` "1372" |

Text (padding 10, no margins), Button and HTML blocks get the same props as on
a form (`kizen docs show form`, "Styling defaults"). `background_color` (`"transparent"`, a colour, or omit
for white) and a button's `color` replace the default.

A URL button opens in the same tab: the builder's buttons start as URL
buttons, so it never writes `open_link_in_new_tab`.

`display_name` on a Button stays `"v0e"`, as read from a live dashlet. It's
the production builder's minified component name, which the source can't give.

### Two `scheduled_activities` render modes

`scheduled_activities` defaults to a **calendar** (`calendar_view: true` inside
`config.fe_extra_info.scheduled_activities_config`). For a **flat list**, set
`calendar_view: false` there. (`dashlet-config` emits the calendar default;
flip the flag in the generated JSON, or use the distinct
`scheduled_activities_calendar` type for the dedicated calendar view.)

## Gotchas

- **`update` diffs dashlets by `id`.** Keep the `id` on dashlets you want to
  modify; omit it to add a new one. A dropped `id` won't update — it creates.
- **`custom_object` is a UUID, not an api_name** here (unlike most spec refs).
  Get it from `kizen objects get <api_name> -o json`.

---

# Wire format & API behavior

## Endpoints (no trailing slashes)

```
GET    /api/dashboards/mine?dashboard_type=<type>     # LIST — see below
GET    /api/dashboards/other                          # inverse: ones you lack access to
GET    /api/dashboards/mine/reorder
POST   /api/dashboards                                # create
GET    /api/dashboards/{id}                           # full, with embedded dashlets
PATCH/PUT /api/dashboards/{id}   ·  DELETE  ·  POST /api/dashboards/{id}/duplicate
GET/POST  /api/dashboards/{id}/dashlet
GET/PATCH/DELETE /api/dashboards/{id}/dashlet/{dashlet_id}
```

`/duplicate` ignores its body. The copy is named `<name> (copy #N)` with
api_name `<api_name>_copy_N`, is unpublished, and has empty sharing.
`dashboards duplicate` PATCHes the name and the source's sharing back on.
Confirmed live 2026-10-06.

**Listing is `GET /api/dashboards/mine`, not `GET /api/dashboards`** — the bare
collection path is POST-only and a GET returns **HTTP 405**.

- **`dashboard_type` is required**, and **`generic_dashboard` and `homepage` are
  DISTINCT queries — `generic_dashboard` does not include homepages.** Confirmed
  live 2026-07-20: a UI-built homepage was completely invisible under
  `dashboard_type=generic_dashboard` and appeared only under
  `dashboard_type=homepage`. The CLI queries both and merges.
- `chart_group` additionally requires `custom_object_id`. **Omitting it 403s**,
  which reads like a permissions error and isn't one — passing a real object's
  UUID works fine.
- Each list entry is a summary: `id`, `api_name`, `name`, `published`, `hidden`,
  `dashlets_count`, `owner`. **It does not include `type`** — that's only on the
  full GET.

## `type` is `TypeBdfEnum`

`generic_dashboard` (standalone, the default), `homepage` (team landing page), or
`chart_group` (a custom object's chart group — then `custom_object` is required).

Every custom object appears to get a `chart_group` dashboard auto-created
(`dashlets: []` until populated). They work fine and need no special
permissions.

## ⚠️ `entity_type: "custom_object"` dashlets are homepage-only

Confirmed live 2026-07-20:

- **`table_of_records`, `field_metrics` (donut/numeric/bar), and `pivot_table`
  can only be created on a `homepage`.** A `generic_dashboard` 400s with
  *"Cannot create Custom Object Dashlet for generic Dashboards"* — true even of
  configs that are otherwise perfectly valid and round-trip fine once moved to a
  homepage. The CLI raises this at plan time rather than letting it 400 on apply.
- **A dashlet's top-level `custom_object` key** (not `config.object_id`) **is
  itself rejected on a `generic_dashboard`**: *"Dashlets from Generic dashboards
  types don't accept 'custom_object' key"*. Leave it `None` there even for
  custom-object metric dashlets; a homepage dashlet may set it.
- `entity_type: "pipeline"`, `"activity"`, `"email"`, and `"static_content"`
  have no such restriction — confirmed working on both.

`entity_type: "custom_object"` dashlets also round-trip fine on a `chart_group`
dashboard, which makes sense: it's specifically for one object's own charts.

## `sharing_settings` is required on create

And **the built-in "Admin" role must hold admin access**, or create/update 400s
with *"The 'Admin' role must have Admin-level access to this Dashboard."*

On the wire, `roles.*` / `team_members.*` are arrays of **bare UUID strings** —
read responses expand them to `{id, display_name}`, so a sharing block copied
out of `--raw` must be normalized back to bare ids before writing. The Admin
role's UUID is **env-specific**: look it up via `GET /api/role`. The CLI resolves
it live.

A spec with no sharing block gets the UI's default: `private: true`,
`all_team_members: 0` and the Admin role as admin, so only the owner and
admins see it until it's shared. Dashboards, filter groups, quick filters and
column templates all start this way. To share with everyone, set
`"all_team_members": 1` (view) and `"private": false` in the spec's
`sharing_settings`.

The same `EntityPermission` block backs filter groups, quick filters and column
templates — `kizen docs show saved-views`.

## Styling

- **Dashboard `style_settings`** paints every dashlet's card and title bar
  (colour, border, radius, shadow, title font). `{}` is Kizen's default look;
  the UI sends `{}` unless "Customize style" is on. The server stores `{}`
  when the key is left out. The CLI sends it only when the spec sets it.
- **A dashlet's `config.fe_extra_info.dashlet_style_config`** overrides the
  card only when `fe_extra_info.custom_styles_enabled` is true. Its
  `chart_theme` recolours bar, donut, line, trend and leaderboard charts
  either way.
  `dashlet-config` doesn't emit it.

Both read from the Kizen web app source, 2026-11-11.

## Dashlet anatomy

`{id, name, custom_object, layout, config, dashboard}`. `layout` is grid
geometry (`x`, `y`, `w`, `h`, plus `min/max_h/w`); `config` carries `report_type`
+ `chart_type` + `entity_type` + type-specific blocks.

**A dashlet with no filter must send**
`{"custom_filters": {"and": true, "query": []}}`, or a filter spec, compiled at
plan time. **Not `{}`**: an empty dict 400s with *"You must send only one type
of filters: in_group_ids, not_in_group_ids or custom_filters"* (confirmed live
2026-07-20).

### The full `entity_type` enum

`["activity", "custom_object", "email", "marketing", "pipeline",
"static_content", "plugin"]` — read straight from the API's own validation error
by POSTing a bogus value. Every per-report_type `chart_type` list below was
obtained the same way, so these are **exhaustive enumerations, not samples**.

**There is no "goal" or "funnel" entity_type, report_type, or chart_type
anywhere.** Despite being commonly-assumed CRM dashlet types, they don't exist
as configurable dashlets in this API version. The closest analog to a funnel is
`opportunity_conversion` (`chart_type=horizontal_bar`); there is no analog to a
goal/progress-bar at all.

### By `entity_type`

Every shape below confirmed live 2026-07-20, either read from a real dashboard or
round-tripped by creating a dashlet and reading its config back.

- **`custom_object`** (homepage-only): `table_of_records_config`,
  `field_breakdown_config` (`chart_type=donut`,
  `metric_type=fields_value_breakdown`, discrete buckets), `field_sum_config`
  (`chart_type=numeric`, `metric_type=fields_value_sum`),
  `field_range_breakdown_config` (`chart_type=bar`,
  `metric_type=fields_range_breakdown` — numeric min/max ranges, not discrete
  values), `pivot_table_config`. `report_type` enum: `["field_metrics",
  "pivot_table", "sales_projection", "table_of_records"]`. `field_metrics`'s
  `chart_type` choices are `["numeric", "bar", "donut"]` — **no line or area
  chart for a single field's values.**
- **`pipeline`** (`pipeline_metric_config`, one shape for every report_type).
  `report_type` enum: `["leaderboard", "opportunity_conversion",
  "pipeline_values_over_time", "records_added", "records_dq", "records_lost",
  "records_won", "stage_values_over_time"]` (`records_dq` = disqualified). Each
  report_type's `chart_type` choices are a **fixed, narrower set**, not the
  general numeric/line pair:
  `records_added`/`records_won`/`records_lost`/`records_dq` →
  `["numeric", "line"]`; `pipeline_values_over_time` → `["line"]`;
  `stage_values_over_time` → `["line"]` only (**not** numeric, despite carrying
  `metric_type=records_value`/`records_weighted_value`);
  `opportunity_conversion` → `["horizontal_bar"]`; `leaderboard` →
  `["leaderboard"]`. `object_id` is the pipeline object.
  `pipeline_level_of_detail` is `"sum_of_stages"` or `"stages_breakdown"`.
  **`frequency` (required for `chart_type=line`) accepts only
  `day`/`week`/`month`** — `"quarter"` 400s here despite being valid on
  `sales_projection`.
- **`activity`**: `activity_metric_config` for `records_added`-style
  line/numeric metrics — **`object_id` is an activity *type* UUID**, not a
  custom object. `report_type` enum: `["records_added",
  "scheduled_activities", "scheduled_activities_calendar"]` — two distinct
  calendar/schedule types. `scheduled_activities_config`
  (`report_type=scheduled_activities`) has a **server-unvalidated
  `chart_type`** — literally any string is accepted, including `"bogus"` — and
  carries a nested `{time_period, calendar_view, allow_external_calendars,
  showing_only_working_days}`. `scheduled_activities_calendar_config`
  (`report_type=scheduled_activities_calendar`) requires
  `chart_type: "calendar"`, takes a **top-level** `showing_only_working_days`,
  and needs no nested config block or `fe_extra_info` at all. The rendering
  difference between the two isn't visually confirmed; both create and read back
  cleanly.
- **`email`** (`email_metric_config`): `report_type` one of
  `email_sent`/`email_delivery`/`email_opt_out`/`email_complaint` (numeric), or
  `email_sent`/`email_delivery`/`email_interaction_stats` (line, same
  day/week/month `frequency` restriction). The plainest shape here — no
  `object_id`, `fe_extra_info`, or `include_roles`.
- **`marketing`** (`marketing_metric_config`): `report_type` enum
  `["lead_source_breakdown_over_time", "leads_added",
  "leads_added_by_source"]`. **Unlike every other entity_type this takes a
  plural `object_ids` list**, not `object_id` — omitting it or passing an empty
  list 400s (*"At least one Custom Object must be passed in 'object_ids'
  property."*). `chart_type`: `leads_added` → `["numeric", "line"]`;
  `leads_added_by_source` → `["bar", "donut", "line"]`;
  `lead_source_breakdown_over_time` → `["line"]`. The latter two also require a
  `lead_sources` key — 400s if missing entirely, though an **empty list is
  accepted** and reads back as "all sources". The shape of a populated entry
  isn't confirmed.
- **`static_content`** (`html_dashlet_config`): `report_type = chart_type =
  "html"`. `config.content` is a craft.js-style node graph (Sections > Rows >
  Cells > `Text`/`Image`/`Button`/`Divider`/`HTMLBlock`) — **snake_case here**,
  unlike a form's camelCase `page_data`. The builder reproduces one Section with
  one Row per row you supply, split into equal-width columns. Confirmed live
  including a URL button, a log-an-activity button (needs an activity type
  UUID), a text block with a working merge field, and a transparent background.
  Multiple Sections, images, and dividers are **not** modeled — copy a live
  example via `dashboards get <id> --raw` and edit by hand.
- **`plugin`**: `report_type` and `chart_type` are **both** fixed to the literal
  string `"plugin"` — the only entity_type where they aren't independent. Also
  requires `plugin_api_name` and `block_api_name`. Not modeled; no installed
  plugin was available to determine the rest of the shape.
- **`sales_projection`** sits outside the `custom_object` group despite being in
  its report_type enum (`chart_type=line`, `metric_type=records_value`). Observed
  with `frequency: "quarter"` accepted — the one report_type where quarter is
  valid — and with a dashlet-level `custom_object` *and* a `config.object_id`
  pointing at two different UUIDs, neither of them a pipeline. Semantics not
  understood well enough to build for; copy a live example.

## Showing each viewer their own records

A dashlet filtered with `is_me` shows each person the records they own, so one
homepage serves the whole team as "my deals", "my At Risk deals" or "my
commissions":

- `{"field": "owner", "op": "is_me"}` matches the record owner.
- The same op on a team-member field matches whoever that field names, e.g. a
  Commission's `kizen_user`.
- Add more clauses for the rest of the filter: "my At Risk deals" is
  `owner is_me` and `health = At Risk`.

The ops, and how a spec compiles, are in `kizen docs show filters`.

**Only the five custom-object types take a record filter:** `table_of_records`,
`field_breakdown`, `field_sum`, `field_range_breakdown` and `pivot_table`.
Metric dashlets (pipeline, activity, email, marketing) and scheduled-activity
dashlets don't, and both ways in below refuse a filter spec on them. A
scheduled-activity dashlet's people filter, `include_employees`, takes
team-member ids.

There are two ways in, and both compile against the dashlet's object:

- `kizen dashboards dashlet-config --type table_of_records --object deals
  --field name --filter '{"all": [{"field": "owner", "op": "is_me"}]}'` writes
  the compiled filter into the printed config. `--filter` needs `--object`.
  It also takes a wire block with exactly one of `custom_filters`,
  `in_group_ids` or `not_in_group_ids`, passed through; anything else exits 2.
- In a `dashboards create|update` spec, set a dashlet's `config.filters` to a
  filter spec. The planner compiles it against the dashlet's `custom_object`,
  or else `config.object_id`. Wire filters, such as a `custom_filters` block
  from `dashboards get --raw`, pass through unchanged, so a read-back dashlet
  re-applies as it was.

Confirmed live 2026-10-30: all five types saved an `owner is_me` filter and
read it back unchanged. The dashlet data the UI loads counted only the API
user's two records of three: a `field_sum` of 150 (all three total 1050) and a
`pivot_table` of 2. The CLI user created those dashlets and was also the
viewer, so this doesn't yet show that `is_me` follows the viewer rather than
the creator.

This homepage for `deals` (owner, plus a `health` dropdown with On Track /
Needs Attention / At Risk) has three dashlets: "My open deals", "My deals by
health" and "My At Risk deals". `<deals_object_uuid>` is the `id` from
`kizen objects get deals --json`, and the two field tokens are the `id`s of
`deals.name` and `deals.health` in the same output:

<!-- fixture: examples/first_build/rep_home.dashboard.json -->
```json
{
  "api_name": "rep_home",
  "name": "Rep Home",
  "type": "homepage",
  "dashlets": [
    { "name": "My open deals", "custom_object": "<deals_object_uuid>",
      "layout": { "x": 0, "y": 0, "w": 8, "h": 3 },
      "config": { "entity_type": "custom_object", "report_type": "table_of_records",
        "chart_type": "table", "metric_type": "records_number",
        "filters": { "all": [ { "field": "owner", "op": "is_me" } ] },
        "fe_extra_info": { "columns": [
          { "id": "<deals.name_field_uuid>", "label": "Name", "display_name": "Name" },
          { "id": "<deals.health_field_uuid>", "label": "Health", "display_name": "Health" } ] } } },
    { "name": "My deals by health", "custom_object": "<deals_object_uuid>",
      "layout": { "x": 8, "y": 0, "w": 4, "h": 3 },
      "config": { "entity_type": "custom_object", "report_type": "field_metrics",
        "chart_type": "donut", "metric_type": "fields_value_breakdown",
        "object_id": "<deals_object_uuid>", "field": "<deals.health_field_uuid>",
        "filters": { "all": [ { "field": "owner", "op": "is_me" } ] },
        "metric_type_extra_info": { "include_summary": true, "summary_explanation": "",
          "fields_value_breakdown": { "type": "custom_values", "buckets": [],
            "consider_null_values": false } } } },
    { "name": "My At Risk deals", "custom_object": "<deals_object_uuid>",
      "layout": { "x": 0, "y": 3, "w": 12, "h": 3 },
      "config": { "entity_type": "custom_object", "report_type": "table_of_records",
        "chart_type": "table", "metric_type": "records_number",
        "filters": { "all": [ { "field": "owner", "op": "is_me" },
                              { "field": "health", "op": "=", "value": "At Risk" } ] },
        "fe_extra_info": { "columns": [
          { "id": "<deals.name_field_uuid>", "label": "Name", "display_name": "Name" } ] } } }
  ]
}
```

Each dashlet carries only the keys it needs. Kizen saved and served these
three as written (confirmed live 2026-10-30).

## See also

- `kizen docs show filters` — the filter structure inside `custom_filters`.
- `kizen docs show saved-views` — the identical sharing block.
- `kizen docs show objects` — where `custom_object` UUIDs come from.
- `kizen docs show email-templates` — merge fields, same convention as
  static-content text blocks.

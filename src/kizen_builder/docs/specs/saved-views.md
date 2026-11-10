# Spec shapes: filter groups, quick filters, column templates

Three per-object saved-view resources. They don't share one file format — note
which flag each uses:

| Resource | Command | Input flag | Shape |
|----------|---------|-----------|-------|
| Filter group (segment) | `filter-groups create\|update` | `--filter '<json>'` / `--filter-file` | filter DSL |
| Quick filter | `quick-filters create\|update` | `--chip` (repeatable) | chip list |
| Column template | `columns create\|update` | `--column` (repeatable) | column list |

---

## The filter DSL

Filter groups take the **same filter DSL** as `records list
--filter` and automation condition steps — one shape, documented once in
`kizen docs show filters`. The short version:

```json
{ "all": [
    { "field": "account_region", "op": "is_any_of", "value": ["North", "West"] },
    { "field": "account_seats",  "op": ">=",        "value": 10 }
] }
```

Top level is `{"all": …}` (AND) or `{"any": …}` (OR); `field` is a bare api_name;
ops are per field type (`kizen filters ops <type>`); a single condition still has
to be wrapped in `{"all": [...]}`.

```bash
kizen filter-groups create accounts --name "Big North accounts" \
  --filter '{"all":[{"field":"account_region","op":"is_any_of","value":["North"]}]}' --dry-run
```

## Quick filter chips (`--chip`)

A quick filter is **not a saved query**. It picks which filter chips appear
above an object's record list; whoever uses a chip picks its values then. So a
chip names a field and never a value — there is no way to preset "Status =
Open" or "in group X". For a fixed condition, make a filter group.

Each `--chip` is one of:

| chip | shows | UI label |
|---|---|---|
| `field` | a chip for that field | the field's name |
| `relation:related_field` | a relationship chip filtering on a field of the related record | `Relation: Related Field` |
| `relation:in_groups` | a relationship chip filtering on the related record's filter groups — the user picks the groups | `Relation Groups` |

`relation` must be a relationship field on `<object>`; `related_field` is an
api_name on the object it points to. Chips show in the order given. On
`update`, `--chip` replaces the whole list.

```bash
kizen objects update accounts --quick-filtering --dry-run   # chips stay hidden until this is on
kizen quick-filters create accounts --name "Regional" \
  --chip account_region --chip primary_contact:in_groups --chip primary_contact:email --dry-run
```

## Quick filter sharing (`share`)

A new quick filter starts with the same sharing the UI's save dialog does:
**private, `all_team_members: 0` (none), and the Admin role as admin**. The API
caller owns it, so other users don't see the template until it's shared.

`quick-filters share` changes only what you pass and keeps every other grant:

```bash
kizen quick-filters share accounts "Regional" --all view --dry-run        # all team members can view/use
kizen quick-filters share accounts "Regional" --role Sales=edit --dry-run # role name or UUID
kizen quick-filters share accounts "Regional" --user <uuid>=none --dry-run # remove a team member
```

Levels are `none`, `view`, `edit`, `admin`; `all_team_members` stores them as
0–3. Two grants always have admin: the **Admin role** (the API 400s with "The
'Admin' role must have Admin-level access" because quick filters set
`sharing_settings_undeletable_admin_role`) and the **owner** (who has admin
through `owner` regardless of the lists). The UI locks their admin entry but
lets you also add them at a lower level, which has no effect because the
highest level wins. In `share` a level moves an id rather than adding one, so
it refuses a lower level for either; change the owner to remove the owner.

`share` controls who can see and manage the template. `apply-to-roles` /
`apply-to-users` are separate: they put the template's chips on those users'
record lists.

## Column templates (`--column`)

Each `--column` is a field api_name, in display order. The CLI builds the
`configuration_json` the UI saves (see the wire format below).

```bash
kizen columns create deals --name "Pipeline" \
  --column owner --column stage --column estimated_close_date --column deal_value --dry-run
kizen columns create client_client --name "Contacts" --column email --column tags --dry-run
```

- **The name column is always kept.** Leave it out and it goes first; list
  it (`name`, or `full_name` on contacts) to put it somewhere else.
- **`update --column` replaces the whole list.** Widths reset to `200px`.
- **New templates start private**, with the Admin role as admin, like the UI.
  `columns share` changes that and takes the same flags as `quick-filters
  share`.
- `--config-file` sends a raw `configuration_json` as-is, instead of
  `--column`.

## Gotchas

- **`owner: null` 500s on create** for quick filters and columns (not filter
  groups). **Omit the `owner`/`--owner` key entirely** when there's no owner —
  don't send null.
- **`apply-to-roles`/`apply-to-users` return 200 with an empty body** and don't
  change the resource's own `sharing_settings`. For a quick filter they
  overwrite each target user's chips with the template's.
- A filter group's `config` is the **same shape** as an automation condition
  step's `filter_config` (`{"and", "query": [...], "invalid"}`).

---

# Wire format & API behavior

All three live under one CRUD shape,
`/api/custom-objects/{object_pk}/{filter-groups,quick-filters,columns}`, and
share the **same `EntityPermission` sharing block dashboards use** — confirmed
byte-identical, which is why sharing normalization is reused as-is. All
confirmed live 2026-07-20.

They differ only in the wire key holding their config blob:

| resource | key | contents |
|---|---|---|
| Filter group | `config` | the filter wire format — `{"and", "query": […], "invalid"}` |
| Quick filter | `filters` | a chip list — see below, **not** the filter wire format |
| Column template | `configuration_json` | column order and settings — see below |

A filter group's `config` is the **same shape as an automation condition step's
`filter_config`** — confirmed by reading a real live filter group carrying a
`field: "owner", value: "is_me"` clause.

## Column template `configuration_json`

What the UI POSTs (confirmed live 2026-11-10):

```json
{
  "columnOrder": ["name", "owner", "estimatedCloseDate", "<field uuid>"],
  "columns": {
    "name": {"id": "name", "fieldId": "<uuid>", "visible": true, "sortable": true,
             "width": "200px", "disabled": true, "label": "Deal Name"},
    "<field uuid>": {"id": "<field uuid>", "fieldId": "<field uuid>", "...": "..."}
  }
}
```

- **Column `id`:** built-in fields use their camelCased api_name. On custom
  objects that's `name`, `owner`, `created`, `updated`, `entityValue`,
  `displayName`, `stage`, `estimatedCloseDate`, `actualCloseDate` and
  `percentageChanceToClose`. On contacts it's `fullName`, `email`,
  `mobilePhone`, `titles`, `tags`, `created` and `updated`. Every other field
  uses its UUID.
- **`fullName`** (contacts) points at the `first_name` field, labeled "Full
  Name". The list shows first and last name together and sorts by
  `full_name`.
- **`disabled: true`** only on `name` / `fullName`, which the UI won't let you
  hide.
- `label` is the field's display name.
- The UI's columns editor PATCHes slimmer entries (`id`, `label`, `visible`
  only). The UI reads both.

## Quick filter `filters`

What the UI POSTs (confirmed live 2026-11-10):

```json
{
  "name": "Sample QF",
  "filters": {
    "quick_filter_settings": [
      {"id": "<uuid>", "fieldId": "<field uuid>", "label": "First Name"},
      {"id": "<uuid>", "fieldId": "<relationship field uuid>",
       "relatedObjectFieldId": "in_groups", "label": "Company Groups"},
      {"id": "<uuid>", "fieldId": "<relationship field uuid>",
       "relatedObjectFieldId": "<related object's field uuid>",
       "label": "Company: Industry"}
    ],
    "quick_filtering_facets": false
  },
  "filter_count": 3,
  "sharing_settings": { "...": "EntityPermission" }
}
```

- `id` is a UUID the client generates per chip. `update` keeps the id of a
  chip that's already on the template.
- `label` is what the chip shows; the CLI builds it the way the UI does.
- `filter_count` is the number of chips.
- The schema types `filters` as `any` and the backend stores it unchecked, so
  any JSON is accepted with a 201. A query blob saves fine and shows no chips.
- **`apply-to-roles`/`apply-to-users` copy `filters` into each target user's
  own quick-filter config for the object**, replacing theirs (backend source,
  2026-11-10). That's why the
  template's `sharing_settings` doesn't change.
- Chips only show once the object has **`quick_filtering_enabled: true`**
  (`objects update --quick-filtering`). The object list leaves this key out;
  only the detail GET returns it. The UI turns it on with a PATCH of the whole
  object. A PATCH of only `{"quick_filtering_enabled": …}` works too and
  leaves every other key on the object detail unchanged (confirmed live
  2026-11-10, both directions).

## Quirks

- **`owner: null` on create 500s for quick filters and columns** — but not
  filter groups, which tolerates it fine. A bare HTTP 500 with no useful error
  body, unlike the 400s elsewhere here; confirmed by isolating each payload key
  one at a time. **Omit the `owner` key entirely** when unset, despite the schema
  marking it nullable.
- **List endpoints return a leaner shape than the detail GET** for filter groups
  (`CustomObjectFilterGroup` has no `owner`/`hidden`/`sharing_settings`), so the
  CLI always does one more GET-by-id after a name match rather than trusting a
  list item to be full detail. Quick filters' per-item list shape is undocumented
  too, so the same defensive fetch applies there even though it empirically
  already returns full detail.
- **`apply-to-roles`/`apply-to-users`/`apply-to-permission-groups` return HTTP
  200 with an empty body**, and don't change the resource's own
  `sharing_settings` on a follow-up GET. For quick filters the effect is on
  each user's own chip config (above). Bodies
  are `{"role_ids": […]}`, `{"team_member_ids": […]}`,
  `{"permission_group_ids": […]}`.

**In the spec but not built:** `.../{entity_id}/admins`, `.../mine`,
`.../other`, `.../visible` (filter groups only), `.../request-access` and
`.../{request_access_id}/request-access-response`. Filter groups and column
templates default to all-team-members-visible. Quick filters start private (see
above) — share them rather than relying on the access-request flow.

## See also

- `kizen docs show filters` — every clause type, the operator lists, and
  custom-field refs.
- `kizen docs show automation` — condition steps reuse this DSL; the
  `search_records` step references filter groups by name or id.
- `kizen docs show dashboard` — same sharing block, and dashlet filters.

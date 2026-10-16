# Spec shape: `ActivityDef` / `ActivityFieldDef` — activity types

**Consumed by:**
- `kizen activities create --spec-file <f>` — an `ActivityDef` (may include `fields`).
- `kizen activities fields create <activity> --spec-file <f>` — an `ActivityFieldDef` list.
- `kizen activities update <activity> --spec-file <f>` — advanced: a JSON dict of
  changes whose keys map to the wire body; `--visibility-rules-file` sets the
  conditional field-visibility rules.

> Simple cases use flags: `activities create --name X [--association-mode ...]`,
> `activities fields create <activity> --name X --type text`. Reach for a spec
> file to create a type **with its fields in one plan**, or for the advanced
> update surface. Logging/scheduling of instances stays in the UI.

---

## Quick example (`activities create`)

```json
{
  "name": "Account Review",
  "association_mode": "selected_objects_associated",
  "selected_object_ids": ["<accounts_object_uuid>"],
  "is_editable": true,
  "fields": [
    { "name": "Visit Notes", "field_type": "longtext", "order": 0 },
    { "name": "Outcome", "field_type": "dropdown", "order": 1,
      "options": ["Completed", "No-show", "Rescheduled"] }
  ]
}
```

---

## `ActivityDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** Display name. |
| `api_name` | string | Optional (2–255). Kizen may rewrite it on create — read back. |
| `description` | string (≤500) | Optional. |
| `is_editable` | bool | Whether logged instances can be edited after submission. |
| `association_mode` | enum | `all_objects_associated`, `selected_objects_associated` (needs `selected_object_ids`), or `no_objects_associated`. |
| `custom_object_ids` | UUID[] | Objects this activity can be logged against. |
| `selected_object_ids` | UUID[] | Objects for `selected_objects_associated` mode. |
| `visibility_rules` | object[] | Conditional field-visibility rules (opaque). |
| `submission_action` | enum | `redirect` or `trigger_webhook` (+ `webhook_url`/`redirect_url`). |
| `fields` | `ActivityFieldDef[]` | Optional — create fields in the same plan. |

## `ActivityFieldDef` fields

Structurally close to `FieldDef` (see `field.md`), with differences:

- **No category** — fields order within one flat list via `order` (0–32767).
- **No reserved-api_name restriction**; `api_name` is **optional** (Kizen
  derives one from the display name when omitted).
- Field types: the same 24 as custom objects **plus `activity_custom_field`** —
  a field mirrored from a related custom-object field, set via
  `custom_object_field: "<field_uuid>"` (CLI: `--linked-field object_api.field_api`).
- Type-specific blocks (`options`, `status_options`, `money_options`, `rating`,
  `decimal_options`, `phone_options`) behave exactly as in `field.md`.

## Gotchas

- **`object_ids` are UUIDs**, not api_names — get them from `kizen objects get`.
  (The CLI's `activities update --object <api_name>` resolves names for you; the
  spec file wants UUIDs.)
- **api_name may be rewritten** on create — read back with `kizen activities get`.

---

# Wire format & API behavior

An "activity" under `/api/activities` is the activity **TYPE** — a loggable
definition: name + custom fields + visibility rules — **not** a logged instance.
`kizen activities` wraps the type; logging and scheduling instances stays in the
Kizen UI (except scheduled activities, below).

## Type CRUD

```
GET/POST              /api/activities
GET/PUT/PATCH/DELETE  /api/activities/{id_or_api_name}      # path accepts either
POST                  /api/activities/{id}/duplicate
```

`/duplicate` ignores `name`; the CLI renames the copy with a PATCH. Confirmed
live 2026-10-06.

`name` is the only required create field. (`GET /api/activities/loggable` also
exists — a light id/name/api_name list of loggable-only types — but the full
list is a strict superset with more information, so the CLI doesn't wrap it.)

## Fields are a sub-resource, not embedded

```
GET/POST              /api/activities/{id}/fields
GET/PUT/PATCH/DELETE  .../fields/{fid}
POST                  .../fields/{fid}/options          # add
DELETE                .../fields/{fid}/options/{oid}    # delete
POST                  .../fields/{fid}/options/{oid}/replace
```

Field shape ≈ custom-object fields: `display_name` + `field_type` required,
`name` (api_name) optional and server-derived, options are `{name, code}`,
`wysiwyg` → `longtext` + `meta.is_markdown`. The field_type enum adds
`activity_custom_field`. Fields order within one flat list via `order` — there
are no categories. **Kizen auto-adds a default `notes` (wysiwyg) field** to
every type.

## Non-obvious wire quirks

- **Object associations.** To set `association_mode:
  selected_objects_associated` you must send
  `associated_objects: [{"custom_object": {"id": <object_uuid>}}, …]` — NOT the
  documented `selected_object_ids`/`custom_object_ids`, which are ignored. Each
  item's `custom_object` must be a nested dict, not a bare id string. The read
  model returns these under `custom_objects`. The CLI's `--object <api_name>`
  flag builds this shape.
- **Option remove-with-remap needs two calls.** The activities
  `.../options/{oid}/replace` endpoint only *reassigns records* onto the
  replacement — body key is `option_id`, which the spec mislabels `id` — and
  does **not** delete the old option, so you must DELETE it afterward. (The
  custom-object replace endpoint does both. This is the divergence.)
- **Visibility rules** (`visibility_rules`) are opaque rule dicts with a rich
  `view_model`: field-ref URLs, condition tokens, option display-name lookups.
  They round-trip through PATCH byte-for-byte if passed through untouched — do
  **not** synthesize them by hand. Author in the UI, then read and round-trip.
- **Two kinds of field.** The editor offers plain *activity fields* (native to
  the type) and *custom fields* that surface an existing custom-object field on
  the activity — view-only, or editable back onto the record. The latter is
  `field_type: activity_custom_field` + `custom_object_field: <CO-field-uuid>`
  (bare UUID on write; reads expand it to source object + field; the CLI shows
  it as `→ obj.field`). **It references the live record field — it is not a
  copy**, and the activity's associated object set must include that custom
  object.

## Reading logged instances (read-only)

```
GET  /api/activities/logged/{id}        # fields+values, notes, associated_entities,
                                        # logged_at/by, completed_at/by
POST /api/activities/{id}/responses     # list instances — POST because the body
                                        # can carry filters; empty body lists all
```

## Scheduled activities

```
GET    /api/activities/scheduled-activity?from_date=<ISO>&employee_ids=<uuid>,<uuid>&completed=false
POST   /api/activities/scheduled-activity
GET    /api/activities/scheduled-activity/<id>
PUT    /api/activities/scheduled-activity/<id>     # full replace
PATCH  /api/activities/scheduled-activity/<id>     # partial
DELETE /api/activities/scheduled-activity/<id>
```

```json
{
  "activity_object_id": "<loggable-activity-uuid>",
  "due_datetime": "2026-07-01T14:00:00Z",
  "employee_id": "<team-member-uuid>",
  "note": "Optional note",
  "associated_entities": [ {"custom_object": "<object-uuid>", "entity_id": "<record-uuid>"} ]
}
```

`activity_object_id` is the activity **type**'s UUID. In
`associated_entities`, `custom_object` is the object UUID and `entity_id` the
record UUID — note both are bare ids here, unlike the nested-dict shape the
type's own `associated_objects` wants.

## See also

- `kizen docs show field` — the shared type-specific blocks.
- `kizen docs show automation` — the `schedule_activity` step, its
  `association_configs`, and its 11 "Assign To" options.
- `kizen docs show smart-connectors` — `activity`-type connectors read logged
  activities as their input.

# Spec shape: `ActivityDef` / `ActivityFieldDef` — activity types

**Consumed by:**
- `kizen activities create --spec-file <f>` — an `ActivityDef` (may include `fields`).
- `kizen activities fields create <activity> --spec-file <f>` — an `ActivityFieldDef` list.
- `kizen activities update <activity> --spec-file <f>` — advanced: a JSON dict of
  changes whose keys map to the wire body; `--visibility-rules-file` sets the
  conditional field-visibility rules.

> Simple cases use flags: `activities create --name X [--association-mode ...]
> [--object <api_name> ...]`,
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

## Branching on what someone logged

A workflow can't read a plain activity field. A variable's
`logged_activity_trigger` source offers only `logged_at`, `logged_by`,
`due_datetime`, `scheduled_activity_id` and `record_association` (a UI-built
capture), and none of them is an answer on the form. So put each answer a
workflow needs on the **record**:

1. Make the question an `activity_custom_field` linked to a field on the
   record, with `read_only` left `false`. The form shows the record's field,
   and an answer entered there is saved to the record (the editor's "editable"
   custom field; the CLI can't log an activity to confirm it).
2. Trigger the workflow with `activity_logged` on the activity type.
3. Branch with an ordinary condition on the record field.

This Call type puts its Outcome and notes on the deal. Its two tokens are the
`id`s of `deals.call_outcome` and `deals.call_notes` in `kizen objects get deals
--json`, and `<deals_object_uuid>` is the object's own `id`:

<!-- fixture: examples/first_build/call.activity.json -->
```json
{
  "name": "Call",
  "api_name": "call",
  "association_mode": "selected_objects_associated",
  "selected_object_ids": ["<deals_object_uuid>"],
  "fields": [
    { "name": "Outcome", "field_type": "activity_custom_field", "order": 0,
      "custom_object_field": "<deals.call_outcome_field_uuid>" },
    { "name": "Call Notes", "field_type": "activity_custom_field", "order": 1,
      "custom_object_field": "<deals.call_notes_field_uuid>" }
  ]
}
```

The workflow starts when a Call is logged, and the condition reads the Outcome
off the deal:

<!-- fixture: examples/first_build/log_call.automation.json#/triggers/0 -->
```json
{ "trigger_type": "activity_logged", "order": 0,
  "trigger_activity_logged": { "activity_type": "call" } }
```

<!-- fixture: examples/first_build/log_call.automation.json#/steps/4 -->
```json
{ "key": "no_conversation", "parent_key": "rate_health", "step_type": "condition",
  "order": 4,
  "step_condition": { "type": "custom_filter", "filter_config": { "all": [
    { "field": "call_outcome", "op": "is_any_of",
      "value": ["Left Voicemail", "No Answer"] } ] } } }
```

The rest of that workflow, which stamps the date, rates the deal and
schedules a follow-up, is in `kizen docs show automation`. Confirmed live
2026-10-30: the type saved with both fields linked, and a run on a deal whose
Outcome was No Answer took the yes branch. Logging an activity is a UI action
(`POST /api/activities/{id}/log-activity` answered 500 to every body tried), so
the runs were started by hand.

## Show a field only for some answers

A visibility rule shows fields on the logging form only when another field
has one of a set of answers. Write it with field and option names, and set it
with `update` once the fields exist. `activities create` refuses a rule by
name, because its fields have no ids yet:

```bash
kizen activities update call --visibility-rules-file call.visibility_rules.json
```

This file shows Call Notes only when the Outcome was Connected or Left
Voicemail:

<!-- fixture: examples/first_build/call.visibility_rules.json -->
```json
[
  { "show": ["call_notes"],
    "when": { "field": "outcome", "is_any_of": ["Connected", "Left Voicemail"] } }
]
```

- `show`: the activity fields to show, by api_name or UUID.
- `when.field`: a dropdown, radio, yesnomaybe or checkbox field, or a linked
  field (`activity_custom_field`) over one. Other types are a plan error.
- `when.is_any_of`: for a dropdown, radio or yesnomaybe field, option names or
  UUIDs, matched exactly.
- `when.is_checked`: for a checkbox, `true` (the editor's "Is Checked") or
  `false` ("Isn't Checked"):

  ```json
  { "show": ["follow_up_reason"], "when": { "field": "needs_follow_up", "is_checked": true } }
  ```

The CLI expands each rule to the one the Kizen editor writes: one `=` filter
per option, OR'd, each with the editor's `view_model`, plus `layout_items` for
the fields shown:

```json
{ "rule": { "and": false, "filters": [
    { "type": "activity_field", "activity_field_id": "<outcome field uuid>",
      "condition": "=", "value": "<Connected option uuid>",
      "view_model": [["field_id", "…"], ["condition", "equals"], ["value", "…"]] },
    "…one filter per option…" ] },
  "layout_items": [ { "field_id": "<call_notes field uuid>" } ] }
```

A checkbox rule is one `=` filter on `true` or `false`, whose `view_model`
stops at the condition (`is_checked` or `is_not_checked`), as the editor's
does.

`--dry-run` and `activities get` print a rule of that shape in words: `show
call_notes when outcome is any of Connected, Left Voicemail`, or `show
follow_up_reason when needs_follow_up is checked`. Any other rule, such as one
built in the UI on a date or with "is not", is a raw rule. `activities get
--json` returns it, and an entry with a `rule` key is sent exactly as given, so
a rule read back re-applies unchanged. One file can mix both kinds. The file
replaces the whole set, so list every rule you keep.

Every filter in a raw rule needs its `view_model`. The editor's Advanced Rules
tab loads each rule from it, and one filter without it leaves the list empty
for every rule on the type, while the field list still marks the fields. So a
raw rule with a filter that lacks it is a plan error: write that rule by name
instead.

Confirmed live 2026-10-30 and 2026-11-02: this file, dropdown rules and a
checkbox rule saved and read back exactly as sent, and a UI-built type's four
raw rules planned unchanged.

---

## `ActivityDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** Display name. |
| `api_name` | string | Optional (2–255). Kept as given; omit it and Kizen derives one from `name`. |
| `description` | string (≤500) | Optional. |
| `is_editable` | bool | Whether logged instances can be edited after submission. |
| `association_mode` | enum | `all_objects_associated`, `selected_objects_associated` (needs `selected_object_ids`), or `no_objects_associated`. |
| `selected_object_ids` | UUID[] | Object UUIDs. The CLI sends them as `associated_objects`, optional, and sets the mode by default. |
| `associated_objects` | object[] | The wire/read shape, passed through; use it to set `is_required`/`is_readonly`. Sets the mode by default too. Not with `selected_object_ids`. |
| `visibility_rules` | object[] | Conditional field-visibility rules: short form by name, or the raw rule dicts a read returns. Set short ones with `update` ([below](#show-a-field-only-for-some-answers)). |
| `submission_action` | enum | `redirect` or `trigger_webhook` (+ `webhook_url`/`redirect_url`). |
| `fields` | `ActivityFieldDef[]` | Optional — create fields in the same plan. |

## `ActivityFieldDef` fields

Structurally close to `FieldDef` (see `field.md`), with differences:

- **No category** — fields order within one flat list via `order` (0–32767).
- **No reserved-api_name restriction**; `api_name` is **optional** (Kizen
  derives one from the display name when omitted).
- Field types: the same 21 as custom objects **plus `activity_custom_field`** —
  a field mirrored from a related custom-object field, set via
  `custom_object_field: "<field_uuid>"` (CLI: `--linked-field object_api.field_api`).
- Type-specific blocks (`options`, `status_options`, `money_options`, `rating`,
  `decimal_options`, `phone_options`) behave as in `field.md`, except that
  `status_options` is optional on a `status` field.
- A `yesnomaybe` field with no `options` gets Yes/No/Maybe from the planner, as
  on objects. Kizen 400s an activity `yesnomaybe` field created without
  options (confirmed live 2026-11-09).
- Unknown keys are rejected exactly as in `field.md`.

## Gotchas

- **`object_ids` are UUIDs**, not api_names — get them from `kizen objects get`.
  (The CLI's `activities create|update --object <api_name>` resolves names for
  you; the spec file wants UUIDs.)
- **An explicit `api_name` is kept as given**, including after the same name
  was deleted, and a live duplicate gets a 400 ("Activity 'api name' must be
  unique."). Only a derived name can come back with a random suffix
  (`site_visit_sk6iwzxh`), so pin `api_name` when other commands will refer to
  the type. `confirmed live 2026-10-14`.

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

- **Object associations.** `associated_objects` is the only key that sets
  them: `[{"custom_object": {"id": <object_uuid>}, "is_required": false,
  "is_readonly": false, "order": 0}, …]`, with `custom_object` a nested dict,
  not a bare id. POST rejects an entry missing any of the three flags. A PATCH
  replaces the set: objects left out are removed, an object already in it
  keeps its stored `is_required`/`is_readonly` when its entry omits them, and a
  new object gets `false`. The read returns the same list under
  `associated_objects` (with `custom_object` expanded), and it round-trips.
  Without an `association_mode`, the server picks `all_objects_associated` even
  when a list is sent, so the CLI defaults the mode whenever it sends a list.
  `selected_object_ids` and `custom_object_ids` are ignored on the wire; the
  CLI turns `selected_object_ids` and `--object` into `associated_objects`
  (flags `false` on create, left out on update, so `update --object` keeps
  the flags of objects already associated), and a spec with
  `custom_object_ids` is a plan error. `confirmed live 2026-10-16`.
- **Option remove-with-remap needs two calls.** The activities
  `.../options/{oid}/replace` endpoint only *reassigns records* onto the
  replacement — body key is `option_id`, which the spec mislabels `id` — and
  does **not** delete the old option, so you must DELETE it afterward. (The
  custom-object replace endpoint does both. This is the divergence.)
- **Visibility rules** (`visibility_rules`) carry the editor's `view_model`:
  a field-ref URL, a condition token and an option display-name lookup. They
  round-trip through PATCH byte-for-byte. Write them by name, as in
  [Show a field only for some answers](#show-a-field-only-for-some-answers).
- **Two kinds of field.** The editor offers plain *activity fields* (native to
  the type) and *custom fields* that surface an existing custom-object field on
  the activity — view-only, or editable back onto the record. The latter is
  `field_type: activity_custom_field` + `custom_object_field: <CO-field-uuid>`
  (bare UUID on write; reads expand it to source object + field; the CLI shows
  it as `→ obj.field`). **It references the live record field — it is not a
  copy**, and the activity's associated object set must include that custom
  object. It's how a workflow reads an answer: see
  [Branching on what someone logged](#branching-on-what-someone-logged).

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

# Automations on the wire

How the automation2 API behaves underneath `kizen automations`: the read and
write dialects, the transforms between them, the graph rules the server
enforces, and the wire keys that don't match the spec. This is for changing the
CLI. A spec author needs none of it: `kizen docs show automation` and `kizen
automations step-types show <type>` cover writing a spec, and `--dry-run`
enforces the rules below before anything is sent.

Every finding here was a live 400, 500 or silent data loss first. A spec-facing
rule the planner or dry-run now enforces is listed here with the function that
enforces it, so the doc and the check can be compared.

## GET and PUT are different dialects

Automation GET and PUT are **not the same wire format**. Every write path in
this CLI runs the raw GET response through a translator first. The fidelity
proof is `kizen automations roundtrip <name> --execute`: PUT the translated
payload unchanged, re-GET, semantic-diff — zero drift means the translator is
faithful for every type in that automation.

Why a translator is mandatory rather than nice-to-have:

- **GET discards step keys** (`key: null`), so a GET→PUT cycle needs fresh
  keys. The translator synthesizes deterministic ones (`s07_condition`,
  `t3_new_entity_created`) from order + type and rewrites id-based
  cross-references (`go_to` targets) onto those keys. **`id` is a separate,
  persistent identity** — the translator always echoes the `id` it read from
  GET back on PUT, which is what keeps a step's execution history attached
  across `kizen automations steps add/edit/remove`.
- **Read linkage is `parent_step_id` + `parent_condition`; write linkage is
  `parent_key` + `parent_yes_no`.** `yes_steps`/`no_steps`/`groups` on
  conditions are derived read-only mirrors — **never send them back**.
- **The server accepts corrupt graphs silently** (multi-root, dangling
  parents), so validation has to happen client-side before the PUT.
- **Wrong-dialect keys are silently ignored**, which means data loss with no
  error. Three confirmed: `start_automation.automations` (write wants
  `automation_ids`), `archive_record.relationship_fields` (wants
  `relationship_field_ids`), and `field_updated.to_value` as a dict (stored as
  **null**).
- **A key the planner doesn't read is a plan-time error.** Any top-level key
  in a step or trigger config block that the type's builder doesn't read
  fails `create`/`update`/`steps add`/`steps edit` before the plan renders,
  even under `--dry-run --json`, naming the key and every key that block does
  read (`plan error:` from `create`/`update`, `error:` from `steps`). One
  limit: keys *inside* nested items (`change_field_value.actions[]`,
  `code_step.inputs`/`outputs`, `plugin_code_step.inputs`/`outputs`,
  `schedule_activity.schedule`) aren't checked.
  Types whose builder forwards the whole block to Kizen (`delay`, `goal`,
  `stop_execution`, `initialize_variable`, `update_variable`,
  `send_related_contact_text`, and every trigger except `manual`,
  `new_entity_created`, `activity_logged` and `email_received`) accept any
  key, and Kizen may still ignore one. `send_related_contact_text` is the
  exception within them: an extra key whose value is a dict or list is
  dropped, not forwarded.
- **So is a key a live read carries that the builder doesn't model.** Every
  verb that reads an automation back, whether to write it (`activate`,
  `deactivate`, `steps add|edit|remove`, `roundtrip --execute`) or to
  show or diff it (`show`, `steps get`, `diff`), stops with an error naming
  the automation, the step or trigger and the key, before any PUT.
  Only a non-empty value counts, and the same limits apply: keys inside
  nested items aren't checked, and neither are the forwarding types above.
  Derived read-only keys are expected and never sent: `yes_steps`/`no_steps`
  on a condition, and the message fields (`subject`, `content`, `name`, …) a
  `notify_member_via_email`/`_text` read flattens into the block (confirmed
  live 2026-10-16). A whole non-empty `step_*`/`action_*` block the CLI
  doesn't read for that step's type stops the same verbs, forwarding types
  included.

Also: **automation updates need PUT, not PATCH.** PATCH refuses step/trigger
changes, and PUT requires the current `revision` as `last_revision`.

## Previewing an update before you send it

`kizen automations diff <api_name> --spec-file <path>` (stdin also accepted)
shows what `automations update` from that spec would actually change on the
live automation — trigger/step additions, removals, reparenting, and
config-field changes — without writing anything. It normalizes both sides
into the wire (PUT) dialect above and matches steps/triggers by `id` first,
falling back to position for a spec with no `id`s at all.

Because `key` is synthesized fresh from live order on one side and
hand-authored on the other, a literal field comparison would show every
step's `key`/`parent_key` as "changed" even when nothing actually changed.
`diff` excludes `key`, `parent_key`, and `prefix` from the comparison — they
are per-side synthetic naming, not automation content — but still catches a
genuine reparenting by comparing each step's parent by matched identity, not
by the raw key string. Each diff line is labelled with the first octet of
the step/trigger's `id` (e.g. `76af48bd`) so it can be matched against the
same value visible in the UI, and it is unique within one automation. A
changed field is identified by that octet alone; an added or removed
step/trigger carries the whole step/trigger, full `id` included, in its
`--json` leaf value.

A key the spec leaves out compares as the value Kizen stores when it's
omitted, so a spec that leaves out a default shows no change. A non-default
value on either side still shows. Confirmed live 2026-10-27:

| Key | Omitted reads back as |
|---|---|
| `error_notification_email` | `''` |
| `deduplicate_array` on `initialize_variable`/`update_variable` | `false` |
| `array_separator` on a static source whose `value` is a list | `'comma'` |
| `value_html` on an `update_variable` source | `''` |
| `description` on a step or trigger | the live description, for a step or trigger whose `id` matches. For one the spec adds, a fixed text per type: `Initialize Variable`, `Update Variable`, `Action: Search Records`, `Action: Schedule Activity`, `Loop`, `Next/Finish`, `Condition`, `Branch`, `Merge Branches`, `Delay`, `Stop Execution`, and `Manual` for the manual trigger. Other types compare as sent |

## Read→write transforms

Each of these was discovered via a live 400, 500, or silent data loss:

| Read shape | Write shape |
|---|---|
| Expanded refs `{id, name, display_name, …}` | bare UUID string |
| `business_plugin_app` (contains obfuscated secrets — **never echo back**) | `business_plugin_app_id` |
| Variable definition dicts in step configs | bare variable `name` string |
| Variable `id`s (top-level and in `initialize_variable`) | **stripped** — the server recreates the variable set every PUT, and an id inside `action_initialize_variable` is an HTTP 500 |
| `{value, label}` choice dicts in sources | bare `value` |
| Team-member selectors (`role`, `employee`, `employees`, `field`) | `*_id` keys — `role_id`, `employee_id`, `employee_ids`, `field_id`, `related_field_id` |
| Tag values `{tags_to_add: [{id,name,deleted}], …}` | bare UUID lists in **both** keys — the `{id, name}` shape the OpenAPI spec documents is an HTTP 500 |
| Null keys everywhere (reads include every key as null) | dropped — writes reject them |
| `email`/`text` message resources (expanded) | `{"id": uuid}` — and note these are bound to their automation, so a POSTed copy can't reference them ("Invalid pk") |
| Step/trigger orders with gaps or nulls | sequential 0..N |
| `change_field_value.fields_to_clear` (expanded field objects) | bare field UUIDs |
| `start_automation.automation_variable_overrides` — flat list, every ref expanded (`target_automation`, `variable_to_override`, plus a `value_source`-specific ref) | grouped by target automation id: `[{automation_id, variable_overrides: [...]}]`, and the unwrapping is non-uniform — `context_entity_field` → bare UUID, `variable_to_override`/`automation_variable` → bare **name**, `specific_value` → passthrough (already a bare scalar on read), `blank` → no key at all |
| `is_branch_group_initiator` on every step | echoed as read, default `false`, and always `true` on a loop (`kizen docs show automation`, "Loops"). Dropped, every merge step 400s: "Merge step's parent (…) must be a branch group initiator step." Confirmed live 2026-09-28 |
| `continue_with_branch`, present only when set | echoed when non-null. A condition or goal with `should_skip_execution: true` 400s without it. Confirmed live 2026-09-28 |
| `error_notification_severity_level` on every step | echoed as read. Omitted, the step is silently reset to `inherit`. Confirmed live 2026-09-28 |
| `branch` and `merge_branches` steps, read with no config block | no `action_*` block written. Confirmed live 2026-09-28 |
| `step_loop` with `array_variable`/`item_variable` as variable definitions | bare variable **names**; their ids are a 400 ("The loop must iterate an array variable…"). `next_or_finish` is read with no config block, and none is written. Confirmed live 2026-10-27 |
| A loop's item variable read with `is_array_item_variable: true` and `source_array_variable: <array>` | echoed as read. The server sets both itself from the loop, so sending them is harmless and omitting them changes nothing. `diff` ignores both. Confirmed live 2026-10-27 |
| Extraction/transcription `input_field` plus its mirrored `context_entity_field` `input_value_source` | `input_field` only — see `kizen docs show automation`, "Extraction input" |
| `modify_related_entities.automation_variables` (expanded variable definitions) and the block's own `id` | not sent; `record_source_variables` (bare names) is the write key |

`automation_variable_overrides` items carry a fixed key set on read, most null
per entry — `value_source` says which one is live: `context_entity_field`,
`automation_variable`, `specific_value`, and `blank` are confirmed live
2026-09-01 (`sets_variable_overrides`/`Receives Variable Overrides`, five
overrides on one target automation, incl. one `specific_value` and one
`blank`). The schema's `ValueSourceEnum` adds two more:
`related_object_field`, which pairs `relationship_field` (the hop, a UUID)
with `related_record_field` (the field on the related record, a UUID), and
`automation_entity_variable_field`, which pairs `automation_entity_variable`
(a variable name) with `automation_entity_variable_field` (a field UUID).
The translator carries every non-null value key with the unwrap its schema
type calls for. Per the published schema, 2026-10-06; not yet seen live.

## Graph rules the server enforces

Validated client-side before every PUT, because the server's own enforcement is
inconsistent:

- Exactly one root step; no dangling `parent_key`; `parent_yes_no` only under a
  condition or goal; no parent cycles; `go_to` targets must exist.
- **`go_to` may not target an `initialize_variable` step** (400).
- **All `initialize_variable` steps must precede every other step** (400). In
  practice each one has to sit on the unbranched chain from the root: moving
  one to be the first step inside a condition's branch, with no other change,
  is a plain HTTP 400 with no field-level detail (found by bisection). Steps
  that only *read* a declared variable may sit inside a branch.
  `validate_payload` checks the orders.
- Condition steps need `action_on_failure: notify_pause`; the default
  `notify_continue` is a 400. `_build_step_payload` swaps it.
- **A branch group initiator (`is_branch_group_initiator: true`) has exactly
  one `merge_branches` child, and every `merge_branches` step's parent is an
  initiator** (400). A skipped condition or goal needs `continue_with_branch`
  `yes` or `no` (400). Confirmed live 2026-09-28.
- **Loops** (each a 400, confirmed live 2026-10-27):
  - a `loop` has exactly one `next_or_finish` child;
  - a `loop` has no `merge_branches` child;
  - a `next_or_finish` hangs directly off a `loop`;
  - a loop iterates an array variable of records, strings or UUIDs, declared
    by an `initialize_variable` step;
  - its item variable is declared by an `initialize_variable` step as a single
    variable of the array's type (and, for records, the same object), and no
    two loops share one.

  The server has no nesting limit (6 deep saves) and no limit on body lanes.
  The CLI adds two rules of its own, from Kizen's builder: at most three
  levels, and no loop over the same array as a loop around it (`kizen docs show automation`, "Loops").

## Unlisted step types

A step type with no dedicated builder falls through a generic pass-through that
strips volatile keys. Some survive it — `http_request` round-trips clean. A
400 or 500 on PUT means the type needs a real builder.

## Wire keys that differ from the obvious guess

| where | the key |
|---|---|
| `field_updated` trigger | `field_id` (UUID). This spec's `field_ref` resolves to it: `"object.field"`, or a bare field name resolved against `target_object`. `from_value`/`to_value` write as bare UUIDs; the builder sends an option name, or a read's `{id, …}`, as its UUID. A value without its match mode is planned as `specific_value`: Kizen stores `any_including_blank` for a mode left out, and the trigger then fires on every change (confirmed live 2026-11-16). Keys and modes: `kizen automations step-types show field_updated` |
| `on_or_around_date` trigger | `field_id` (UUID). This spec's `field_ref` resolves to it. The other keys: `kizen automations step-types show on_or_around_date` |
| `scheduled_activity_overdue` trigger | `activity` writes as `{"id": uuid}`; the spec takes an api_name or UUID |
| `schedule` trigger | `trigger_schedule: {rrule, is_advanced}` — matches a live read exactly, nothing to unwrap |
| `stage_updated` trigger | `from_stage`/`to_stage` (bare stage UUIDs, both required, null = any); the spec's stage name resolves; `stage_field` is ignored by Kizen and never sent |
| `file_content_extraction` | input key is `input_field` (UUID); `input_field_ref` resolves; or `input_value_source` (`field`/`relationship_field` bare UUIDs, `variable` the name); never both — see `kizen docs show automation`, "Extraction input" |
| `business_plugin_app_id` | a bare UUID string — **do not** wrap in `{id: …}` |
| `change_field_value` (choice/status) | `specific_field_value` is the bare option UUID string, or a list of them for checkboxes; a spec label resolves to it |
| `start_automation` | `automation_ids`, `relationship_field_ids`. Each `relationship_field_ids` entry accepts an `"object.field"` ref (or a bare name on `target_object`), as with `archive_record`. Every target must be active, or Kizen 400s "Automation not found" for any record source; the CLI refuses it at plan time (`inactive_start_targets`) |
| `archive_record` | `relationship_field_ids` (entries accept `"object.field"` refs), `automation_variable_name` |
| `assign_team_member` | the `*_id` dialect: `role_id`, `employee_id`, `employee_ids`, `field_id`, `related_field_id`. Its `type` is its own six-value enum (`kizen automations step-types show assign_team_member`) |
| `go_to_automation_step` | `step_key`, pointing at a synthesized key |

The `manual` trigger is prepended to every spec automatically if absent.

## Fields that break the bare-scalar convention

This codebase's usual "reads expand, writes take a bare scalar" rule is **a good
prior, not a universal law**. The ones below were confirmed against
`GET /api/docs/schema` — the *full* schema, which covers automation2 in depth,
unlike `/api/docs/public/schema` — and/or a live create. **When wiring a new
step type, check the schema before assuming.**

### `search_records`

`custom_object`, `filter_groups[]`, and `destination_variable` are each an
`{"id": uuid}` / `{"name": str}` **object** on write — not the bare UUID/name
string most other steps use (contrast `modify_related_entities`'s
`object_to_modify`, `archive_record`'s `automation_variable_name`). **Sending
bare strings 500s.**

### `schedule_activity.association_configs`

Per-object record linkage, for activity types associated with more than one
custom object. Write keys are **flat** —
`custom_object_id`/`relationship_field_id`/`automation_variable_name`, bare
UUIDs and strings — **not** the nested `custom_object: {id}` shape a live read
returns, which the server silently ignores.

`association_source` is `none`/`context_record`/`related_field`/
`record_variable`/`variable_related_field` — **not** `relationship_field`/
`automation_variable`, which 400 with *"… is not a valid choice"*. The
rules for which source fits which object are in `kizen docs show automation`.

### `schedule_activity.assigned_to`

Backs all 11 "Assign To" options in the UI (round_robin_all/_role/_team_members,
owner, team_member, team_selector_field, last_active(_role), specific_role,
team_member_from_variable, role_from_variable). Wire keys are
`role_id`/`employee_id`/`employee_ids`/`field_id` — the same `*_id` dialect
`assign_team_member` uses, and a live 400 caught the builder emitting bare
`role`/`employee`/`field` here. `team_selector_field` takes the field as
`field_ref` (`<object>.<field>`), which the CLI resolves to `field_id`, as
it does in every team-member selector.

But **`variable` must be an `{"id"|"name": …}` object** — not the bare name
string this codebase's other variable references use. `assign_team_member` has a
smaller enum with no variable-based options, so it doesn't share this quirk.

## Folders

- **A spec's `folder` goes on the wire as a bare `folder_id`.** A POST with it
  creates the automation in that folder (confirmed live 2026-10-30). A read
  returns `folder: {id, name}`, and that shape is **silently ignored** on write:
  it PUTs fine (200, revision bumps) and the folder doesn't change (confirmed
  live 2026-07-22). `update` and every verb that writes a read-back automation
  send the live folder's `folder_id`, unless the spec names another.
- **Root is a real folder**, `<business_root>`, always present in `kizen
  automations folders list`, not an absent value. `folder_id: null` 400s with
  *"This field may not be null."* (confirmed live 2026-07-22).
- **`automations move` doesn't PUT.** It sends `POST
  /api/automation2/folders/move` with `{"automation_ids": [<id>], "folder_ids":
  [], "new_parent_folder_id": <folder id>}`, which answers 200 with an empty
  body and leaves the revision unchanged (confirmed live 2026-10-30). So it
  works on automations the CLI can't fully read back. With no `--folder` it
  targets `<business_root>`.

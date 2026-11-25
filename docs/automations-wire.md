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
  live 2026-10-16). The AI steps' fallback model and `output_format` are
  never sent either, nor Create Related Record's owner and stage resolution
  and `new_entity_fields`: Kizen keeps them when a PUT leaves them out
  (confirmed live 2026-11-25). A whole non-empty `step_*`/`action_*` block
  the CLI doesn't read for that step's type stops the same verbs, forwarding
  types included.

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
| `value_html` on an `initialize_variable` or `update_variable` source | `''` |
| `description` on a step or trigger | the live description, for a step or trigger whose `id` matches. For one the spec adds, a fixed text per type: `Initialize Variable`, `Update Variable`, `Action: Search Records`, `Action: Schedule Activity`, `Loop`, `Next/Finish`, `Exit Loop`, `Condition`, `Branch`, `Merge Branches`, `Delay`, `Stop Execution`, and `Manual` for the manual trigger. Other types compare as sent |

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
| `is_branch_group_initiator` on every step | echoed as read, default `false`, and always `true` on a loop (`_build_step_payload` sends it). Dropped, every merge step 400s: "Merge step's parent (…) must be a branch group initiator step." Confirmed live 2026-09-28 |
| `continue_with_branch`, present only when set | echoed when non-null. A condition or goal with `should_skip_execution: true` 400s without it. Confirmed live 2026-09-28 |
| `error_notification_severity_level` on every step | echoed as read. Omitted, the step is silently reset to `inherit`. Confirmed live 2026-09-28 |
| `branch` and `merge_branches` steps, read with no config block | no `action_*` block written. Confirmed live 2026-09-28 |
| `step_loop` with `array_variable`/`item_variable` as variable definitions | bare variable **names**; their ids are a 400 ("The loop must iterate an array variable…"). `next_or_finish` is read with no config block, and none is written. Confirmed live 2026-10-27 |
| `action_exit_loop.step` read as the target loop's **id** | the loop's step **key**. A PUT that echoes the id 400s ("Exit Loop step references a loop that does not exist."). A null `step` is echoed as null, and it ends the innermost loop. Confirmed live 2026-11-11 |
| A loop's item variable read with `is_array_item_variable: true` and `source_array_variable: <array>` | echoed as read. The server sets both itself from the loop, so sending them is harmless and omitting them changes nothing. `diff` ignores both. Confirmed live 2026-10-27 |
| Extraction/transcription `input_field` plus its mirrored `context_entity_field` `input_value_source` | `input_field` only — see `kizen automations step-types show file_content_extraction` |
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
    two loops share one;
  - an `exit_loop` sits inside a loop body, its `action_exit_loop.step` names
    a loop around it or is null, and it carries an `action_exit_loop` block,
    `{}` at least (confirmed live 2026-11-11).

  The server has no nesting limit (6 deep saves) and no limit on body lanes.
  The CLI adds two rules of its own, from Kizen's builder: at most three
  levels, and no loop over the same array as a loop around it (`translate._builder_loop_problems`).

## Unlisted step types

A step type with no dedicated builder falls through a generic pass-through that
strips volatile keys. Some survive it — `http_request` round-trips clean. A
400 or 500 on PUT means the type needs a real builder.

## Wire keys that differ from the obvious guess

| where | the key |
|---|---|
| `field_updated` trigger | `field_id` (UUID). This spec's `field_ref` resolves to it: `"object.field"`, or a bare field name resolved against `target_object`. `from_value`/`to_value` write as bare UUIDs; the builder sends an option name, or a read's `{id, …}`, as its UUID. A value without its match mode is planned as `specific_value`, or `date_between` for a `{"date_start", "date_end", "type": "date_range"}` value (the UI's choice; Kizen also accepts `specific_value` for one, probed 2026-11-17): Kizen stores `any_including_blank` for a mode left out, and the trigger then fires on every change (confirmed live 2026-11-16). Keys and modes: `kizen automations step-types show field_updated` |
| `on_or_around_date` trigger | `field_id` (UUID). This spec's `field_ref` resolves to it. The other keys: `kizen automations step-types show on_or_around_date` |
| `scheduled_activity_overdue` trigger | `activity` writes as `{"id": uuid}`; the spec takes an api_name or UUID |
| `schedule` trigger | `trigger_schedule: {rrule, is_advanced}` — matches a live read exactly, nothing to unwrap |
| `stage_updated` trigger | `from_stage`/`to_stage` (bare stage UUIDs, both required, null = any); the spec's stage name resolves; `stage_field` is ignored by Kizen and never sent |
| `file_content_extraction` | input key is `input_field` (UUID); `input_field_ref` resolves; or `input_value_source` (`field`/`relationship_field` bare UUIDs, `variable` the name); never both — see `kizen automations step-types show file_content_extraction` |
| `business_plugin_app_id` | a bare UUID string — **do not** wrap in `{id: …}` |
| `change_field_value` (choice/status) | `specific_field_value` is the bare option UUID string, or a list of them for checkboxes; a spec label resolves to it |
| `start_automation` | `automation_ids`, `relationship_field_ids`. Each `relationship_field_ids` entry accepts an `"object.field"` ref (or a bare name on `target_object`), as with `archive_record`. Every target must be active, or Kizen 400s "Automation not found" for any record source; the CLI refuses it at plan time (`inactive_start_targets`) |
| `archive_record` | `relationship_field_ids` (the spec's `relationship_fields`, `"object.field"` refs, resolve to it), `automation_variable_name`. `record_source` is `this_record`, `related_object_field` or `automation_variable`; `this_record` stores relationship fields sent beside it (confirmed live 2026-11-17) |
| `send_related_contact_email` / `_text` | `send_to_contact_field` (bare UUID; a ref resolves, and a missing one is a plan-time error naming the Contacts relationship fields), `email` / `text` as `{"id": uuid}` |
| variable sources (`initialize_variable`, `update_variable`, extraction `input_value_source`) | `field` (bare UUID); the CLI sends a `field_ref` or `field_id` as `field`. A source with `field_id` and no `field` is a 400, "field is required" (confirmed live 2026-11-17). A `record_variable_field` source takes `variable` (the name) and `field` on that variable's object (confirmed live 2026-11-25) |
| `assign_team_member` | the `*_id` dialect: `role_id`, `employee_id`, `employee_ids`, `field_id`, `related_field_id`. Its `type` is its own six-value enum (`kizen automations step-types show assign_team_member`) |
| `go_to_automation_step` | `step_key`, pointing at a synthesized key |

The `manual` trigger is prepended to every spec automatically if absent.

## Per-type wire behaviour

What each type does on the wire that a spec author never sees. The author-facing
half of each is on its card, `kizen automations step-types show <type>`.

- **`email_received`.** Reads expand each selected inbox to `{id, email}`; the
  write takes bare UUIDs (`_trigger_email_received`). Confirmed live
  2026-10-14.
- **`form_submitted` / `survey_submitted`.** The write sends `{form_id}` /
  `{survey_id}`, and reads return `{form: {id, name, deleted}}` / `{survey:
  …}` (`_trigger_form_submitted`). Confirmed live 2026-11-02.
- **Webhook triggers on `update`.** The live trigger is matched by trigger
  `id`, else by webhook `name`; a renamed trigger without its `id` is a new
  trigger and gets `kizen_api` (`diff` shows it).
- **`plugin_code_step`.** `secrets` go out as `[{name}]` (`_secret_names`). A
  `business_plugin_config` input is `{name, input_type, data_type?,
  is_list?}` on the wire, with no source key. A UI-built step reads back in
  that shape (seen 2026-10-14), bound to a parameter the manifest declares
  `static_value`. Kizen's 2026-10-13 release notes say the step receives the
  install config as a JSON string and that the manifest declares the
  parameter with `input_source: business_plugin_config`. Neither is
  **confirmed live**, and neither is writing one from the CLI.
- **Extraction input.** Both keys, even agreeing, or neither, is a 400: `Only
  one of input_field or input_value_source should be set`. Every read mirrors
  `input_field` into a `context_entity_field` source as well, and nulls
  `input_field` for the other source types; the planner collapses the mirror
  back to `input_field`, so `steps get` output re-applies as it is. The type
  enum also lists `specific_value`, `automation_entity_variable_field` and
  `blank`; those are unprobed as file inputs and pass through with the same
  unwrapping. Confirmed live 2026-10-14.
- **Related-object LLM destinations.** Confirmed live 2026-07-27: the wire
  repurposes `field` as the **relationship hop**, not a second destination.
  `related_object_field` alone 400s (`"Either field or variable should be
  provided"`), and `field` pointed at the destination itself 400s against the
  wrong object (`"field should belong to agentic workflow custom object"`).
  `field_ref`/`field` are accepted as the hop too, the shape the builder UI
  produces.
- **`business_plugin_app_id` on LLM steps.** The write doesn't need it for
  `kizen/*`, but then stores the step with `business_plugin_app: null`,
  unlike a UI-saved step; for any other provider, `model_name` alone 400s with
  `{'business_plugin_app_id': ['Business plugin app not found']}`. The write
  accepts `kizen/pro` with the OpenAI install's id and stores that pairing
  (confirmed live 2026-10-30). Two endpoints return an id that looks right
  and 400s the same way as a missing one:
  - `GET /api/external-integrations/bootstrap` returns each plugin's
    **catalog** id, identical across businesses.
  - `GET /api/external-integrations/business-plugin-apps` returns the correct
    per-business **installed instance** id (each entry's own `id`, not the
    nested `plugin_app.id`), keyed by `plugin_app.api_name` (`gemini`,
    `anthropic`, `open_ai`, `mistral`), but needs a join against the models.
  - `GET /api/automation2/automations/metadata`'s
    `llm.provider_model_details[].plugin_app.id` is the installed id already
    joined to each provider's models. `automations llm-models` reads it.
- **MCP tools on `call_llm`.** `mcp_server` / `run_tools_as` write as
  `mcp_server_id` / `run_tools_as_id`, resolved against the step's pickers:
  `GET /api/mcp-servers/step-options` and `GET
  /api/automation2/automations/run-tools-as-options`. The second leaves out a
  plugin's own service account, and a write naming that account is a 400.
  Reads expand both (`mcp_server {id, name, api_name, …}`, `run_tools_as {id,
  name}`), and every `call_llm` step reads `max_sequential_turns` and
  `tool_failure_policy`, server or not. Kizen, given a PUT that omits both
  ids, keeps the server and clears the account, which leaves a step its own
  validator rejects; so a live step with a server but no account gets neither
  from `update`. Confirmed live 2026-10-27.
- **`update_variable.value_resolution`.** A PUT that omits it keeps the stored
  value on a step whose `id` is carried; only an explicit `null` resets it,
  and the planner drops nulls. Confirmed live 2026-10-14. A POST without it
  stores `null`, which replaces like `overwrite` (re-confirmed 2026-11-11).
  `update` sends a matched step's live value itself, so `diff` shows no
  change.
- **`schedule_activity.target_variable`.** A read expands it to `{id, name,
  data_type, …}`, and `steps get` prints the name again. A step that binds no
  variable reads `target_variable: null` with `overwrite`. A PUT that leaves
  either key out resets it to `null`/`overwrite`; `steps edit`, `activate`
  and the other read-back verbs send both from the live read. Confirmed live
  2026-10-30. The `schedule` values are those in `GET /api/docs/schema` on
  2026-10-30.
- **Kept settings.** A Kizen PUT without the settings `update` keeps resets
  each to its default, except `stop_execution`'s, which Kizen keeps when the
  block is empty, and `value_resolution` (confirmed live 2026-10-30 and
  2026-11-11).

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
rules for which source fits which object are on `kizen automations step-types
show schedule_activity`.

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

## Run endpoints

What `kizen automations start`, `runs` and the diagnostics verbs send. The CLI
behaviour is in `kizen docs show automation-runtime`.

**Starting.** `POST /api/automation2/automations/<automation-identifier>/start`
with `{"record_id" | "client_id": "<uuid>", "variable_overrides":
[{"variable_name": "org_match", "value": "true"}]}`. `record_id` and
`client_id` are mutually exclusive: Contacts (`client_client`) use
`client_id`. `variable_overrides` is a list, not a dict
(`StartAutomationRequest` → `VariableOverrideRequest`), keyed by the
variable's `name`, and `value` is always a nullable string that the server
coerces by the variable's `data_type` (public schema plus a live start). The
response is `{"execution": {ReadAutomationExecution}}`, or `{"execution":
null}` when a UUID was passed as the identifier instead of the api_name.

**Watching.**

```
GET /api/automation2/automation-execution?automation_id=<uuid>&record_id=<uuid>&size=25
GET /api/automation2/automation-execution/<execution-id>
GET /api/automation2/automation-execution/<execution-id>/history
```

- `size` maxes at 100.
- On a halted execution the detail carries `paused_on_step` (`{id, type,
  branching_step, label}`).
- **No trailing slash**: `…/<execution-id>/` 404s with an HTML page.
- In `/history`, type and description live in a nested `step` / `trigger`
  object (`{type, description}`), not at the row top level. Timing is
  `execution_time_ms` (null for async steps like `code_step` / `call_llm`)
  plus `created` / `updated`.
- `/history` is a paginated DRF envelope of 100 rows a page; a 211-row run
  came back as three pages (confirmed live 2026-10-16). The CLI follows every
  `next` link.

**Controlling.**

```
POST /api/automation2/automation-execution/<id>/pause
POST /api/automation2/automation-execution/<id>/play             # resume
POST /api/automation2/automation-execution/<id>/cancel           # irreversible
POST /api/automation2/automation-execution/<id>/skip-and-resume
POST /api/automation2/automation-execution/<id>/debug-sendit
POST /api/automation2/automation-execution/<id>/debug-rerun
POST /api/automation2/automation-execution/<id>/debug-restart
POST /api/automation2/automation-execution/<id>/debug-step
```

- pause / play / cancel / debug-sendit take a
  `LightReadAutomationExecutionRequest`: `id`, `automation_id`, `client_id`,
  `record_id`, `status`, `trigger_history_id`, `debug_mode`, all marked
  required though the endpoint implies the action. The CLI echoes the
  execution's own current GET, `status` included; sending the
  pre-transition status worked in every direction tested.
- `skip-and-resume` takes `InlineFormRequest`: `{skip_step_id,
  continue_with_branch?}`. `debug-rerun` / `debug-restart` take
  `DebugRerunRequest`: `{step_id}`. `debug-step` takes `DebugStepRequest`:
  `{action: "execute"|"skip"|"debug", history_id, continue_with_branch?}`.
- Only `debug-sendit` returns an empty/`null` body. `debug-step` returns a full
  `LightAutomationHistory` (the step's new history row, not the execution),
  and the schema `$ref`s the same shape for `debug-rerun`/`debug-restart`
  (confirmed live 2026-08-13). The CLI re-GETs the execution afterwards.

**Diagnostics.**

```
GET /api/automation2/automations/<identifier>/modification-history
  ?date_from=<iso>&date_to=<iso>&event_type=<type>&search=<text>
GET /api/automation2/automations/<identifier>/histories/failures?page=<n>&page_size=<n>
```

`modification-history` accepts more filters than the CLI exposes
(`custom_object_ids`, `has_comment`, `role_ids`, `team_member`,
`include_related`, `ordering`).

## Rules the CLI enforces or defaults

Each rule here was a quirk in the author guide. It now lives in code, so the
guide no longer states it. The function is where to look before changing the
rule.

| Rule | Where | Confirmed live |
|---|---|---|
| Branching is `parent_key` + `parent_branch`; `yes_step_ids`/`no_step_ids` 500 | `AutomationStepDef` (`extra="forbid"`), `_step_condition` | — |
| A condition's `action_on_failure` is `notify_pause`; `notify_continue` 400s | `_build_step_payload` | — |
| A new variable's `initialize_variable` sits on the unbranched root chain; nested under a branch is a bare 400, found by bisection | `validate_payload` | — |
| `variable.data_type` is one of ten; an unknown one is a bare 500 | `AutomationVariableConfig` | 2026-10-14 |
| Only `string`, `uuid` and `entity` variables can be arrays | `AutomationVariableConfig` | 2026-10-27 |
| An `entity` variable needs `data_subtype`; the server 400s on a missing one or an api_name, so the CLI sends the UUID | `_entity_subtype_errors`, `_step_initialize_variable` | 2026-11-16 |
| Unknown keys in `variable` are dropped silently by the server (`isArray` saved a scalar) | `AutomationVariableConfig` (`extra="forbid"`) | 2026-10-14 |
| A `static` source needs `source_subtype`; without it the server 400s "source_subtype should be one of [...]" | `VariableSourceConfig._static_has_subtype` | 2026-11-17 |
| A `static`, `logged_activity_trigger` or `activity_past_due_trigger` source's `source_subtype` is one of that type's list; the write schema has it as a free string, and the server 400s with the list | `SOURCE_SUBTYPES`, `VariableSourceConfig._known_subtype` | 2026-11-17 |
| `value_resolution` on a scalar variable 400s ("Value resolution is supported for array variables only."); on `initialize_variable` it's dropped; any other value is refused | `_scalar_value_resolution_problem`, `_step_initialize_variable` | 2026-10-14 |
| A new array `update_variable` without `value_resolution` gets `add_only`, the builder's default for a new step since 2026-11-10; a matched step keeps its live value | `_build_step_payload` (`_ARRAY_UPDATE_DEFAULT_RESOLUTION`), `_KEPT_BLOCK_KEYS` | 2026-11-11 |
| A `stop_execution` without `action` gets `stop_and_complete`; on update a matched step keeps its live `action`/`notify`, and a live `null` becomes `stop_and_complete` | `_build_step_payload` (`_STOP_EXECUTION_DEFAULT_ACTION`), `_carry_omitted_step_keys` | 2026-10-30 |
| An option field's label resolves to its UUID; an unknown label is refused. Kizen answers a label with a 500, or saves an automation it can't read | `_option_labels_to_ids` | 2026-10-30 |
| A condition needs at least one rule (empty `query`, a group with no `filters`, `in_group` with no `group_ids`); the UI shows an error on an empty one | `_condition_rules_problem` | reported 2026-09-29 |
| A filter value can't hold a `{{ … }}` merge field; Kizen stores it as text and matches nothing | `_refuse_merge_fields` | 2026-11-02 |
| The plan warns about each compiled rule without a `view_model` | `_unlaid_rule_warnings` | — |
| Webhook `headers` only with `http_header`, `secret` only with `basic_auth` (Kizen drops them silently); retired `auth_header_name`/`auth_header_value` refused | `TriggerWebhookConfig._validate_auth` | 2026-10-27 |
| Webhook header names are 2-100 characters | `WebhookAuthHeader` | 2026-10-27 |
| Each webhook header's `html_value` is filled the way the UI does | `_webhook_auth` | 2026-10-27 |
| On update, a webhook trigger that leaves out `auth_type` keeps its live auth | `_carry_webhook_auth` | 2026-10-27 |
| A date trigger's `time` and `period` are required unless `on_day_and_time`; either missing is a 400 ("This field is required.") | `TriggerOnOrAroundDateConfig._time_unless_from_field` | 2026-11-16 |
| A schedule's `time` is required with a left-out or `specific_time` `time_offset` | `ScheduleOnOrAroundDateConfig._time_when_specific` | 2026-11-16 |
| `on_day_and_time` needs a datetime field, on a date trigger, a goal's trigger and a schedule; a date field 400s "Field must be datetime when date_offset is on_day_and_time" | `_date_field_finding` | 2026-11-17 |
| `days_before`/`days_after` need `date_offset_days` 1 or more (a schedule's static count only); 0 or a left-out count is a 400 | `TriggerOnOrAroundDateConfig._days_with_days_offset`, `ScheduleOnOrAroundDateConfig._days_with_days_offset` | 2026-11-17 |
| `schedule.type` needs its block; other values are refused | `ActivityScheduleConfig._block_matches_type`, `KnownChoices` | 2026-10-30 |
| `past_date_action` and the other choice lists come from the 2026-10-30 schema | `KnownChoices` | 2026-10-30 |
| Two unordered triggers both send `order` 0 | `trigger_order_problem` | — |
| `stage_updated` stage names resolve to UUIDs; an unknown stage, or a non-pipeline target, is a plan error; the description defaults to the UI's text | `_resolve_trigger_stages`, `_resolve_goal_trigger_stages` | 2026-10-30 |
| `update_pipeline_status` and other unwired types are refused | `_unwired_types`, `_build_step_payload` | — |
| `business_plugin_app_id` in a `plugin_code_step` block is refused | `_unread_block_keys` | — |
| Extraction input takes exactly one of `input_field_ref` and `input_value_source` | `_step_file_content_extraction` | 2026-10-14 |
| An LLM destination's relationship hop is auto-detected when exactly one exists, else refused; a choice destination's `options` fill from the field | `_resolve_llm_destinations` | 2026-07-27 |
| LLM `business_plugin_app_id` fills from the catalog; a model the catalog doesn't list is refused without an explicit id; an id of another provider is refused | `_resolve_llm_plugin_app`, `ActionLlmCallConfig` | 2026-10-30 |
| More than one destination on `call_llm`, `file_content_extraction` or `audio_transcription` needs `is_advanced: true` | `_check_destinations` | 2026-11-17 |
| `html_prompt` is derived from `prompt` | `_step_call_llm` | — |
| `mcp_server` and `run_tools_as` are set together or not at all | `_llm_tools_pairing_problem` | 2026-10-27 |
| A `start_automation` target must be active | `inactive_start_targets`, `_built_block_problem` | 2026-10-30 |
| A `create_related_entity` can't create a Contact | `_contact_creation_problem` | 2026-10-30 |
| An `association_configs` entry with no object is refused (a null `custom_object_id` 400s "This field may not be null.") | `_normalize_association_config` | 2026-10-14 |
| `assign_team_member` keys per `type`; a `related_team_selector_field` hop must be a single-value relationship on `target_object` (skipped on a global automation or an unreadable target). The server 400s *"This field is required for "<type>" type."* and *"Custom object has no single value relationship field with id "<uuid>""* (naming the field passed) | `_assign_team_member_problem`, `_is_single_value_relationship` | 2026-10-16 |
| `create_related_entity` resolution defaults to `add_only` on a to-many field | `_context_field_resolution` | 2026-11-16 |
| One root, no dangling `parent_key`, no cycles, `go_to_automation_step.step_key` names a real key | `validate_payload` | — |
| Step and trigger `order` run `0..N` without gaps or duplicates (a warning) | `_order_warnings` | — |
| A branch group initiator has exactly one `merge_branches` child with no `parent_branch`; a skipped condition or goal needs `continue_with_branch` | `_branch_group_problems` | 2026-09-28 |
| A plain step with two or more children warns (a group's closer isn't a lane; a two-lane loop body counts) | `_fan_out`, `_fan_out_warnings` | 2026-09-28, 2026-10-27 |
| A matched step keeps its live `error_notification_severity_level` | `_KEPT_STEP_KEYS` | 2026-09-28 |
| `step_loop` takes variable names (their ids are a 400); a loop always goes out as `is_branch_group_initiator: true`, because the server stores true even when false is sent; an item variable needs no sources, though the server accepts one | `_step_loop`, `_loop_problems`, `_loop_variable_problems` | 2026-10-27 |
| Loops nest at most three levels, over a different array from every loop around them (Kizen's builder rule; the API accepts six levels and a repeated array) | `_builder_loop_problems` | 2026-10-27 |
| An `exit_loop` sits in a loop body and targets a loop around it; `loop_to_exit` goes out as the loop's key, a matched step's read-back id is turned back into the key, and an update whose target loop has no spec step by `id` is refused; steps hung under one warn | `exit_loop_problems`, `resolve_exit_loop_targets`, `exit_loop_dead_end_warnings` | 2026-11-11 |
| `diff` ignores `is_array_item_variable` and `source_array_variable`, which the server adds to an item variable, and reads a null key in a variable definition as absent | `_DERIVED_VARIABLE_KEYS` | 2026-10-27 |
| `show`, `diff`, `roundtrip`, `activate` and `deactivate` warn, not refuse, about an inactive `start_automation` target, because Kizen also refuses a PUT of an automation whose target was deactivated later | `inactive_start_targets` | 2026-10-30 |
| What an update keeps: `active`, `folder`, `skip_non_working_days` (a PUT without it turns a live `true` off), webhook auth; on a step whose `id` matches, `description`, `user_description`, `should_skip_execution`, `continue_with_branch` (with a kept skip), `action_on_failure`, `error_notification_severity_level`; on a trigger, `description`, `user_description`, `should_skip_execution`; per type, `schedule_activity` `target_variable`/`variable_field_resolution`, LLM `merge_field_validation`/`is_advanced` (a condition's `llm_decision.merge_field_validation`), `call_llm`'s MCP pair, `max_sequential_turns`, `tool_failure_policy`, a stop's `action`/`notify`, `update_variable`'s `value_resolution`, an `exit_loop`'s target, `create_related_entity`'s resolution, and a `field_updated` trigger's match modes | `_KEPT_STEP_KEYS`, `_KEPT_TRIGGER_KEYS`, `_KEPT_BLOCK_KEYS`, `_carry_omitted_step_keys`, `_kept_match_modes`, `_carry_webhook_auth` | 2026-10-30 |
| A step or trigger without `id` gets a fresh one, orphaning its run history | `_carry_omitted_step_keys` (kept only by `id`) | 2026-08-10 |

## Facts the author guide no longer states

Confirmed-live findings cut from `kizen docs show automation` because
`step-types` now prints the author-facing half, or because only a contributor
needs them. Each keeps its date here.

- **Goal `wait_type`.** `wait_forever` is a 400; `step-types show goal` lists
  `never`, `delay` and `specific_datetime`. Confirmed live 2026-10-14.
- **Branch lanes.** A lane of a `branch` step can be a `branch` step itself,
  merged or not, and a `branch` can follow a merge.
- **`plugin_code_step` round-trips.** `activate`, `deactivate`, `steps` edits
  to other steps and `roundtrip --execute` keep a plugin step's inputs,
  outputs and secrets intact. Confirmed live 2026-10-14.
- **LLM destination defaults.** `is_required` defaults to `false` and
  `confidence_threshold` (extraction only) to `0.7`.
- **`merge_field_validation` UI labels**, confirmed live against the builder
  dropdown: `error_if_required` "Error if any merge field is blank",
  `default_to_unknown` "Default to 'Unknown'", `blank` "Leave Blank".
- **MCP keys on `call_llm`.** `max_sequential_turns` takes 1–32767 (default
  25), and `tool_failure_policy` takes only `never_fail`. Confirmed live
  2026-10-27.
- **Array variables in the builder.** `array_aggregation_mode` is "Choose
  Source Handling" (*Add All Sources* / *First Non-Blank Source Only*) and
  `deduplicate_array` is "Unique Values Only". Confirmed live 2026-10-14.
- **`schedule_activity.note` and `html_note`.** Kizen stores them separately,
  and a PUT without `html_note` clears it while `note` survives (the
  `step-types show schedule_activity` note). Confirmed live 2026-10-06.
- **`schedule_activity` times.** A `specific_time` takes `time` on a 24-hour
  clock: `"21:00"` saves, unlike the date trigger's 12-hour `time`.
  Confirmed live 2026-10-30.
- **`schedule_activity.target_variable`** takes a String or UUID variable;
  `variable_field_resolution` defaults to `overwrite`. Confirmed live
  2026-10-30.
- **Date trigger defaults.** A left-out `skip_non_working_days` reads back
  `null`; `past_date_action`'s other values are `proceed_down_not_met_path`
  and `wait_for_not_met_path_delay`. Confirmed live 2026-10-30.
- **`assign_team_member` sources.** Whether `field_id` may name a
  team-selector field other than the far object's `owner` is unconfirmed (as
  of 2026-08-13).
- **Merge-field spans.** The planner, like Kizen's builder, wraps each token
  in a `kzn-merge-field` span; a bare token is inert text. A token the spec
  already wrapped is sent as written. The span's attributes are in `kizen
  docs show email-templates` (confirmed live 2026-08-26).

# Spec shape: `AutomationDef` — automations

**Consumed by:** `kizen automations create|update --spec-file <f>` (also stdin).
An automation is triggers + a step graph, sent as one atomic POST/PUT.

> This is the richest spec. The graph rules below (one root, `parent_key`
> chaining, `parent_branch` on branch entries) are enforced at plan time — a
> violation fails the dry-run with a specific message. This file covers the
> shape, the wired types, and the common patterns; if a step or trigger's
> field list isn't fully covered here, `--dry-run` reports the exact field
> Kizen rejected.

---

## Skeleton

```json
{
  "name": "Flag big North accounts",
  "api_name": "flag_big_north_accounts",
  "type": "record_based",
  "target_object": "accounts",
  "active": false,
  "triggers": [
    { "trigger_type": "new_entity_created", "order": 0,
      "trigger_new_entity_created": { "action": "create_only" } }
  ],
  "steps": [
    { "key": "check", "parent_key": null, "step_type": "condition", "order": 0,
      "step_condition": {
        "type": "custom_filter",
        "filter_config": { "all": [
          { "field": "account_region", "op": "is_any_of", "value": ["North"] },
          { "field": "account_seats", "op": ">=", "value": 10 }
        ] }
      } },
    { "key": "flag", "parent_key": "check", "parent_branch": "yes",
      "step_type": "change_field_value", "order": 1,
      "action_change_field_value": {
        "field_ref": "accounts.account_flagged",
        "specific_field_value": true
      } },
    { "key": "done", "parent_key": "check", "parent_branch": "no",
      "step_type": "stop_execution", "order": 2 }
  ]
}
```

```bash
kizen automations create --spec-file auto.json --dry-run
```

---

## `AutomationDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** |
| `api_name` | string | **Required.** `^[a-z][a-z0-9_]*$`. |
| `type` | enum | `record_based` (default) or `global`. |
| `target_object` | api_name | **Required for `record_based`.** Resolved to `custom_object_id`. |
| `active` | bool \| null | On create, an omitted/`null` value resolves to `false` — prefer authoring inactive, then `automations activate`. On update, an omitted/`null` value **preserves whatever the live automation already is**; only an explicit `true`/`false` changes it. |
| `triggers` | `AutomationTriggerDef[]` | See wired triggers below. |
| `steps` | `AutomationStepDef[]` | The graph. See wiring rules below. |

## The step graph

- Every step has a unique **`key`**. Chain by **`parent_key`** (the predecessor's
  key). **Exactly one** step has `parent_key: null` — the root.
- **`id`** (steps and triggers both) is optional and separate from `key` —
  set it to a step/trigger's real server UUID (from a live read, e.g. `kizen
  automations show`) to keep that step's identity and execution history
  across this update. Omit it for anything actually new; the server assigns
  one.
- For the **first** step inside a YES/NO branch under a `condition` (or `goal`),
  set **`parent_branch: "yes"`** (or `"no"`). Linear successors *inside* a branch
  leave `parent_branch` unset.
- `order` (int ≥ 0) orders steps for display. **Triggers need this too** — a
  gap the skeleton above used to leave implicit: `HTTP 400: triggers: Trigger
  orders must be sequential from 0 to 1` if omitted. Sequential `0..N` on
  both `triggers` and `steps`, no gaps or duplicates.
- Cycles are rejected; `go_to_automation_step.step_key` must reference a real key.
- **Merge branches with `go_to_automation_step`** rather than duplicating the tail
  of both branches. Flip the condition so YES is the short/skip path where it
  keeps the graph simpler.

**Field references — two forms, don't mix them up:**
- **Action-block `field_ref`** (e.g. `change_field_value`, `call_llm` destinations)
  uses the object-qualified `"<object_api>.<field_api>"` — resolved to a UUID at
  apply time, portable across envs. Bare `field_id` UUIDs also work but are env-bound.
- **Condition `filter_config` `field`** uses the **bare field api_name**
  (`account_region`), *not* object-qualified — it's the filter DSL, same as
  `records list --filter` and saved views.

---

## Wired trigger types (10)

`manual`, `new_entity_created`, `activity_logged`, `on_or_around_date`,
`webhook`, `field_updated`, `schedule`, `scheduled_activity_overdue`,
`form_submitted`, `survey_submitted`.

Each requiring config sets a `trigger_<name>` block matching `trigger_type`
(e.g. `field_updated` → `trigger_field_updated: { field_ref, fire_on_create, … }`).
`manual` is an empty `trigger_manual: {}`. `schedule` → `trigger_schedule:
{rrule, is_advanced}` — a recurring trigger (works on global automations too,
where it's the only "time-based" option since `on_or_around_date` needs a
target_object field); the wire shape matches a live read exactly (`rrule` is
a full RFC 5545 string including `DTSTART`). Other trigger types exist in
Kizen (e.g. `email_interaction`, `email_link_clicked`,
`contact_tag_added_removed`) but aren't wired here and raise `PlanError` if
you try to spec them.

## Wired step types (24)

| step_type | config block | purpose |
|-----------|--------------|---------|
| `condition` | `step_condition` | branch on a filter / group membership / LLM decision |
| `delay` | `step_delay` | wait a duration or until a time |
| `goal` | `step_goal` | wait for a condition/event |
| `stop_execution` | `action_stop_execution` | end this branch — the "Action Options" choice below is required in the UI |
| `go_to_automation_step` | `action_go_to_automation_step` | jump to another step (`step_key`) |
| `code_step` | `action_code_step` | run Python (`script`, `inputs`, `outputs`, `secrets`) |
| `call_llm` | `action_call_llm` | prompt an LLM, write to `destinations` |
| `file_content_extraction` | `action_file_content_extraction` | extract from a file field |
| `audio_transcription` | `action_audio_transcription` | transcribe an audio field |
| `change_field_value` | `action_change_field_value` | set/clear a field |
| `archive_record` | `action_archive_record` | archive the record |
| `create_related_entity` | `action_create_related_entity` | create a related record |
| `modify_related_entities` | `action_modify_related_entities` | update related records |
| `start_automation` | `action_start_automation` | start another automation |
| `assign_team_member` | `action_assign_team_member` | assign owner (round-robin, role, field, …) |
| `schedule_activity` | `action_schedule_activity` | schedule an activity |
| `initialize_variable` / `update_variable` | `action_initialize_variable` / `action_update_variable` | automation variables — see root-chain rule below |
| `math_operator` | `action_math_operator` | arithmetic into a variable/field |
| `notify_member_via_email` | `action_notify_member_via_email` | email a team member (see quirk) |
| `notify_member_via_text` | `action_notify_member_via_text` | text a team member (inline content) |
| `send_related_contact_email` | `action_send_related_contact_email` | email a related contact |
| `send_related_contact_text` | `action_send_related_contact_text` | text a related contact |
| `search_records` | `action_search_records` | search an object, write results into an array variable |

`search_records` (the automation-builder equivalent of `records list
--filter`) needs its own `custom_object` — independent of the automation's
own `target_object` (global automations have none) — plus `filter_type`
(`all_records`/`in_group`/`not_in_group`/`custom_filter`),
`destination_variable` (an array-type automation variable, by name), and
`destination_variable_resolution` (`overwrite`/`overwrite_except_null`/
`add_only`/`remove_only`/`update_if_blank`). For `in_group`/`not_in_group`,
`filter_groups` names/ids one or more saved filter groups on `custom_object`
(same resource as `kizen filter-groups`). For `custom_filter`, `filter_config`
is the same DSL shape as a condition step's, resolved against `custom_object`
instead of the automation's target_object. Confirmed live 2026-07-22 — see
the "four fields that break the bare-scalar convention" section below for the wire
quirks (custom_object/filter_groups/destination_variable are each
`{"id"|"name": ...}` objects on write, unlike most other action steps' bare
scalars).

Other step types exist in Kizen (`send_email`, `change_tags`,
`http_request`, `plugin_code_step`, `update_pipeline_status`,
`modify_automation`, `request_info_via_text`, `delete_scheduled_activity`,
`modify_related_entities_automation` — not to be confused with the wired
`modify_related_entities`) but aren't wired here and raise `PlanError` if you
try to spec them.

### `code_step` input/output shapes

Each entry in `action_code_step.inputs`/`.outputs` picks its shape via
`input_type`/`output_type`:

| `input_type` | Wire shape | Notes |
|---|---|---|
| `field` (default) | `{"field": {"id": <uuid>}}` | From `field_ref` (`"object.field"`, resolved at apply time), `field_id`, or a raw `field` UUID — first one present wins. |
| `variable` | `{"variable": {"name": <name>}}` | From `variable_name` or a `variable` dict/string. |
| `static_value` | `{"static_value": {"value": ..., "entity_record": ..., "employee": ...}}` | `static_value` may be the literal value directly, or a dict with those three keys for the richer forms. |
| `related_field` | `{"field": {"id": <uuid>}, "related_field": {"id": <uuid>}}` | **Both required**: `field` is the relationship field on the automation's own object (the hop); `related_field` is the field on the object it points to. |

`output_type` is the same enum minus `static_value` (an output can't be a
literal): `field` (default), `variable`, `related_field` — same wire shapes
as their `input_type` counterparts. `inputs`/`outputs` entries also accept
`data_type` and `is_list` when the script needs to declare a type/array
explicitly rather than inferring it from the bound field.

For the Python side — the `inputs.` / `outputs.` / `secrets[…]` / `kizen.api`
namespace, how each `data_type` arrives inside the script, and unit-testing a
script before wiring it in — see `kizen docs show code-steps`.

### `modify_related_entities` — field shape

Updates one or more fields on a record related to the trigger record, via
one or more relationship-field hops.

```json
{ "step_type": "modify_related_entities", "key": "sync_encounter",
  "parent_key": null,
  "action_modify_related_entities": {
    "object_to_modify": "encounters",
    "automation_target_relationship_fields": ["patients.encounters"],
    "fields_to_modify": [
      { "field_to_modify": "encounters.attending_provider",
        "value_type": "context_entity_value",
        "context_entity_field": "patients.mrn" }
    ]
  } }
```

- **`automation_target_relationship_fields`** (list) is the hop chain from
  the automation's `target_object` to `object_to_modify` — each entry
  accepts an `"object.field"` ref (resolved at apply time, like any other
  `field_ref`) or a raw field UUID. A single-hop convenience alias,
  `relationship_field_ref` (or `relationship_field_id`), is also accepted
  and folded into this list — but the list is the real wire key; don't
  confuse this with `object_to_modify` (a plain object reference).
- **`fields_to_modify`** items use the *same* per-item shape as
  `change_field_value`'s flat form (`field_to_modify`/`field_ref`,
  `specific_field_value`, `related_object`, `related_object_field`,
  `field_value_mappings`, `variable`), with one dialect swap and one scoping
  exception:
  - **`value_type` replaces `change_type`.** A standalone `change_field_value`
    step's item uses `change_type`; a `modify_related_entities` item uses
    `value_type` for the identical concept (e.g. `"specific_value"`,
    `"context_entity_value"`, `"variable"`). Using the wrong key for the
    step you're in doesn't error clearly — the API instead complains
    `"specific_field_value should be passed"`, which reads like an unrelated
    problem.
  - **`field_to_modify`/`field_ref` names a field on `object_to_modify`**
    (the *related* object) — but **`context_entity_field` names a field on
    the automation's own `target_object`** (the *triggering* record, i.e.
    the "context entity"). A bare (undotted) ref for either resolves against
    the object that field actually lives on.
- **`field_value_mappings`** — for copying a status/dropdown/radio field
  between two objects whose option UUIDs differ (a very common pattern,
  e.g. mirroring a risk-level field): `[{"source_values": [<uuid>],
  "target_values": [<uuid>], "order": 0}, ...]`. Each entry maps one source
  option UUID (on the field being read) to one target option UUID (on
  `field_to_modify`); `order` controls evaluation order for multiple rules.

### LLM & extraction destinations

`call_llm`, `file_content_extraction`, and `audio_transcription` all write
results via the same `destinations` list shape. Two shapes, not three
mutually-exclusive options:

```json
{ "destinations": [
    { "field_ref": "patients.mrn" },
    { "variable": "extracted_mrn" },
    { "related_object_field": "encounters.reason_code_icd10",
      "relationship_field_ref": "risk_results.risk_result_encounter" }
] }
```

- **Same-object write**: `field_ref` (or a raw `field` UUID) names the
  destination directly on the automation's own `target_object`; `variable`
  writes into an automation variable instead. `field_ref` resolves like any
  other `"object.field"` ref.
- **Related-object write** (single relationship hop): `related_object_field`
  names the *destination* field on the related record — dotted
  `"object.field"` ref (resolved like `field_ref`) or a raw field UUID.
  Confirmed live 2026-07-27: the wire dialect then repurposes `field` as the
  **relationship hop** (the field on `target_object` that points at the
  related object), not a second destination — sending `related_object_field`
  alone 400s (`"Either field or variable should be provided"`), and pointing
  `field`/`field_ref` at the destination field itself 400s against the wrong
  object (`"field should belong to agentic workflow custom object"`). Name
  the hop explicitly with `relationship_field_ref` (or its alias
  `relationship_field_id`) — same convenience-alias pattern as
  `modify_related_entities`'s `relationship_field_ref`. Omit it and the
  planner auto-detects the hop when exactly one relationship field exists
  between `target_object` and the destination's object; with zero or more
  than one candidate it raises a clear `PlanError` rather than guessing.
  `field_ref`/`field` are also accepted as the hop (the shape the builder UI
  itself produces when you hand-pick "related field" for a destination), but
  `relationship_field_ref` reads clearer in a spec.

For a choice-type destination field (dropdown/radio/checkboxes/yesnomaybe),
`options` (the field's option UUIDs) auto-populates from live field metadata
when the destination field is named via `field_ref`/`related_object_field`
and `options` isn't given explicitly. `is_required` and
`confidence_threshold` (extraction only) default to `false`/`0.7`.

**Multiple destinations in one step** require `is_advanced: true` — without
it the server 400s with `"Multiple destinations are not allowed for
non-advanced LLM call actions"` even if every destination is otherwise
valid. Confirmed live: with `is_advanced: true`, one `call_llm` step can
write to a field on `target_object` *and* a field on a related object (two
different objects) in the same call.

**`model_name` + `business_plugin_app_id`** — run `kizen automations
llm-models` for the live catalog (wraps `GET
/api/automation2/automations/metadata`'s `llm.provider_model_details`):
every enabled `model_name` this business can use, which of
call_llm/condition-`llm_decision`/file_content_extraction/audio_transcription
each one supports, deprecation status + suggested replacement, and —
critically — the `business_plugin_app_id` each non-native model needs:

```
$ kizen automations llm-models
provider   model_name                   business_plugin_app_id                 call  decision  extract  transcribe
kizen      kizen/pro                    —                                       ✓      ✓        ✓         ·
gemini     gemini/gemini-2.5-flash      92dfef31-8b9b-49dc-b54b-3e47cd6b4523    ✓      ✓        ✓         ✓
anthropic  claude-sonnet-4-5-20250929   96bae762-ad8c-4431-bf21-95aaedadaa08    ✓      ✓        ✓         ·
```

`model_name` is a free string, unvalidated client-side — `gemini/...` and
`openai/...` models need the provider prefix; Claude models are bare (e.g.
`claude-3-7-sonnet-20250219`, no `claude/` prefix). **Any non-native
`model_name` (anything not `kizen/...`) needs `business_plugin_app_id` set
alongside it** — `model_name` alone 400s with `{'business_plugin_app_id':
['Business plugin app not found']}` even though `model_name` looks
sufficient on its own. `kizen/*` models don't need one (confirmed live:
`kizen/pro` works with no extra field).

**Getting `business_plugin_app_id` wrong looks the same as omitting it —
same 400, so it's easy to think the id itself is bad rather than the
source it came from.** Two endpoints return something that LOOKS like the
right id and isn't:
- `GET /api/external-integrations/bootstrap` returns each plugin's
  **catalog/app-definition id** — identical across every business (it's the
  "Gemini the product" id, not "your business's installed Gemini
  integration"). Sending this 400s the same way as omitting the field.
- `GET /api/external-integrations/business-plugin-apps` returns the
  correct per-business **installed instance** id (each entry's own `id`,
  distinct from the nested `plugin_app.id`), keyed by `plugin_app.api_name`
  matching the `model_name` prefix (`gemini`, `anthropic`, `open_ai`,
  `mistral`) — but requires a second join against the model list yourself.
- `GET /api/automation2/automations/metadata`'s
  `llm.provider_model_details[].plugin_app.id` is the same correct
  installed-instance id, already joined to each provider's model list —
  this is what `automations llm-models` reads.

Prefer `kizen automations llm-models` over hitting either endpoint by
hand — it already does the provider→id join and tells you which models
are usable for which step type in one shot.

**`merge_field_validation`** — how a blank merge field in `prompt`/
`html_prompt` is handled at run time. Same three-way enum on `call_llm`,
`file_content_extraction`, `audio_transcription`, and a condition step's
`llm_decision` block; confirmed live against the builder UI's own dropdown:

| Wire value | UI label |
|---|---|
| `error_if_required` | "Error if any merge field is blank" (server default when the key is omitted) |
| `default_to_unknown` | "Default to 'Unknown'" — fixed literal text, not a configurable string |
| `blank` | "Leave Blank" |

**`prompt`/`html_prompt` are kept in sync automatically** — same quirk as
`notify_member_via_text`'s `content`/`html_content`. Give plain `prompt`
text and the planner derives `html_prompt` (merge-field tokens rendered
into the UI's `kzn-merge-field` span markup); a plain-prompt-only step
still runs fine via the raw API, but its rich-text prompt editor renders
blank in the builder UI without this. Pass `html_prompt` explicitly to
override the derivation.

### Variable-comparison condition step

A `condition` step's `filter_config` normally uses the record-field filter
DSL (see `kizen docs show filters`), but comparing
an **automation variable** to a static value is a different clause shape,
`type: "variable"`, wrapped inside the same `query`/`filters` envelope:

```json
{ "step_type": "condition", "key": "check_score", "parent_key": null,
  "step_condition": {
    "type": "custom_filter",
    "filter_config": { "and": true, "query": [
      { "id": "query-0", "and": true, "filters": [
        { "type": "variable", "subtype": "automation_variable",
          "lhs_variable_name": "score", "condition": "<=",
          "rhs_value": "5.5", "rhs_value_type": "static",
          "description": "Variable Value 'score' Less Than Or Equal To Static Value 5.5",
          "view_model": [
            ["filter_type", { "vars": [
                ["fields_settings_search", true],
                ["custom_object_id", "<object-uuid>"],
                ["object_type", "standard"],
                ["client_tag_field_id", "257bb114-1b86-4761-99bd-95292de23f46"]
              ], "filter_type": "variable" }],
            ["lhs_variable", { "name": "score", "data_type": "number" }],
            ["condition", "less_than_or_equal_to"],
            ["value_type", "static"],
            ["value", "5.5"]
          ] }
      ] }
    ], "invalid": false }
  } }
```

- **`view_model` is required** — omit it and the condition renders empty/
  untyped in the Kizen UI editor, though it still executes correctly via API.
- **`rhs_value` is always a string**, even for a numeric comparison.
- **`condition` → `view_model` string mapping**: `<=`→`less_than_or_equal_to`,
  `>=`→`greater_than_or_equal_to`, `<`→`less_than`, `>`→`greater_than`,
  `=`→`equal_to`. For blank checks, `condition` is `is_blank`/`is_not_blank`
  directly (both keys use the same literal string in `view_model`) — this is
  a distinct token pair from the record-field DSL's single `is_blank`+bool
  form used elsewhere in `filter_config`.
- **`custom_object_id`** is the automation's target object UUID;
  **`client_tag_field_id`** (`257bb114-1b86-4761-99bd-95292de23f46`) is a
  constant that appears fixed across envs.

### Reading a related record's field into a variable

`initialize_variable`/`update_variable` sources support pulling a value
off a record related to the trigger record — the pattern has no field_ref
convenience; both fields are raw UUIDs **on the current automation's
target_object**, not `"object.field"` refs:

```json
{ "sources": [
    { "source_type": "field_value",
      "source_subtype": "primary_related_object_field",
      "relationship_field": "<uuid — the relationship field on target_object>",
      "field": "<uuid — the field on the RELATED object being read>" }
] }
```

Confirmed from a live capture: `relationship_field` is the hop (same
object as `automation_target_relationship_fields`, above); `field` is a
field UUID on the object that relationship points to, not on
`target_object` itself — look it up via `kizen objects get <related_object>`
first.

### Merge-field namespace tokens by step type

A merge field is not a bare `{{ <namespace>.<field_api_name> }}` token — the
planner (and Kizen's builder UI) always wraps it in a
`<span class="kzn-merge-field" data-merge-field-fallback-label="…"
data-merge-field-relationship="…" [data-merge-field-objectname="…"]>{{ … }}</span>`
marker; the bare token by itself is inert text. See
`src/kizen_builder/tools/merge_fields.py` for the span-building rules this
planner shares across step types, and `docs/specs/email-templates.md` for the
same convention in email template `Text` blocks and dashboard static text.

The namespace token varies **by which step type's content field it's in** —
this isn't one global convention:

| Step type | Namespace for the trigger record's own fields |
|-----------|------------------------------------------------|
| `notify_member_via_email`, `notify_member_via_text`, `send_related_contact_email`, `send_related_contact_text` | `entity_record.<field>` |
| `call_llm` (`prompt`), `initialize_variable`/`update_variable` ("static" sources) | `custom_objects.<field>` — a **literal token**, regardless of the target object's real api_name |

Other reserved namespaces (not real object fields, no API-queryable
catalog, never carry `data-merge-field-objectname`): `team_member.<field>`
(the notified team member's own fields), `business.<field>` (tenant
settings), `contact.<field>`, `automation_variable.<name>`,
`automation_history.<field>`, and `entity_record` pseudo-fields like
`link_url`/`created`/`estimated_close_date`. Any OTHER namespace is treated
as a real custom object's own api_name, and its span gets
`data-merge-field-objectname` holding that object's display name.

---

## Key quirks (the ones that bite)

- **Branching is `parent_key` + `parent_branch`, never `yes_step_ids`.** The
  OpenAPI `yes_step_ids`/`no_step_ids` fields return HTTP 500 — don't use them.
- **`condition` steps need `action_on_failure: notify_pause`** (the default
  `notify_continue` is rejected 400). The planner sets this automatically.
- **`initialize_variable` declaring a NEW variable must be a root-chain
  step — never nested under a `parent_branch`.** The server's own graph
  rule ("All `initialize_variable` steps must precede every other step",
  see the graph rules below) is stricter than it sounds: it's not enough for the
  step to come before its consumer, it must sit on the unbranched chain
  from the root (`parent_key` never crosses a `parent_branch` on the way
  back to `parent_key: null`) — confirmed via bisection, a plain `HTTP
  400` with no field-level detail otherwise. Only steps that *read* an
  already-declared variable (`call_llm` destinations, `update_variable`,
  etc.) may live inside a branch:

  ```
  initialize_variable (NEW variable)   ← root, un-branched
  └── condition
      ├── yes: stop_execution
      └── no: call_llm (writes into the variable declared above)   ← fine, branch-scoped CONSUMER
  ```

  Moving the same `initialize_variable` step to be the first step *inside*
  the `no` branch (declaring it only where it's used) 400s with no other
  change.
- **`stop_execution`'s "Action Options" dropdown is a required choice in
  the builder UI, and two of its five options don't stop anything.**
  Confirmed live (one automation with all 5 steps built through the UI,
  2026-07-27):

  | UI label | `action` wire value | what it actually does |
  |---|---|---|
  | Stop and mark as Failed | `stop_and_fail` | stops, run shows Failed |
  | Stop and mark as Successfully Completed | `stop_and_complete` | stops, run shows Completed |
  | Stop and mark as Cancelled | `stop_and_cancel` | stops, run shows Cancelled |
  | Pause and Error | `pause_and_error` | **pauses** (doesn't stop), shows Error |
  | Pause | `pause` | **pauses** (doesn't stop) |

  Pair any of these with `"notify": true` to also fire the corresponding
  notification (the UI's separate "Send Notification" toggle). The API
  still accepts an omitted/`null` `action` (a bare `{}` block, or no block
  at all) and reads back `null` — but nothing built through the UI leaves
  it unset, so pick one explicitly if the run's final status matters
  rather than relying on whatever `null` defaults to. Previously this
  entire option set was undocumented and untyped, so specs always left it
  unset even when e.g. "stop and mark as failed" was the intent.
- **`change_field_value.specific_field_value` is a bare scalar** (or a UUID
  string for dropdown/relation fields) — not `{value: …}`. The planner wraps
  it in the `actions: [...]` array.
- **`notify_member_via_email` content is a separate resource, not inline.** Its
  config points at an `AutomationMessage` by `id`. Create it correctly with
  `kizen messages create <automation> --template <name>` first, then reference
  that UUID — a message built from raw content shows as *unselected* in the UI.
- **`code_step.secrets` are env-specific** — the spec carries secret *names*; the
  target env must have each configured or the step fails at runtime. Surface
  this when authoring.
- **A step/trigger's `id` field controls whether it keeps its identity across
  an update.** PUT replaces the whole step set, so a step without `id` gets a
  fresh server-assigned one every time — which orphans that step's execution
  history against the old id (confirmed live 2026-08-10). Set `id` (copied
  from a live read, e.g. `kizen automations show`) on any step that isn't
  actually new, and it keeps the same id and history across the write.
- **`active` is the one runtime-state field the full replace does not
  clobber when the spec is silent.** An update PUT is a full replace, but
  `automations update` resolves an omitted/`null` `active` to the live
  automation's current value before building the payload — a spec that
  doesn't mention `active` never turns a running automation off. An
  explicit `true`/`false` still wins and is echoed in the `--dry-run`
  preview as a transition (`True → False (DEACTIVATES a live automation)`)
  rather than a bare value.
- **`condition.filter_config`** uses the filtering DSL (`{"all"|"any": [...]}`,
  single conditions wrapped in `{"all": [...]}`) — same as `saved-views.md`.
  Fields are **bare api_names**; **ops are per field type** (e.g. dropdown uses
  `is_any_of`/`not_any_of`, numbers use `>=`/`between` — *not* `in`/`gte`). Run
  `kizen filters ops [<field_type>]` for the authoritative list. Use a raw filter
  dict for clause types the DSL doesn't cover (variable comparisons — see
  "Variable-comparison condition step" above).

---

# Wire format & API behavior

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

Also: **automation updates need PUT, not PATCH.** PATCH refuses step/trigger
changes, and PUT requires the current `revision` as `last_revision`.

### Previewing an update before you send it

`kizen automations diff <api_name> --spec-file <path>` (stdin also accepted)
shows what `automations update` from that spec would actually change on the
live automation — trigger/step additions, removals, reparenting, and
config-field changes — without writing anything. It normalizes both sides
into the wire (PUT) dialect above and matches steps/triggers by `id` first,
falling back to position for a spec with no `id`s at all (see "A
step/trigger's `id` field controls..." above for what setting `id` does).

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

### Read→write transforms

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

`automation_variable_overrides` items carry a fixed key set on read, most null
per entry — `value_source` says which one is live: `context_entity_field`,
`automation_variable`, `specific_value`, and `blank` are confirmed live
2026-09-01 (`sets_variable_overrides`/`Receives Variable Overrides`, five
overrides on one target automation, incl. one `specific_value` and one
`blank`). Four more sibling keys are real on the wire but unconfirmed —
null in every capture so far: `automation_entity_variable`,
`automation_entity_variable_field`, `relationship_field`,
`related_record_field`. The translator raises rather than guesses at their
`value_source` names or unwrap shape.

### Graph rules the server enforces

Validated client-side before every PUT, because the server's own enforcement is
inconsistent:

- Exactly one root step; no dangling `parent_key`; `parent_yes_no` only under a
  condition or goal; no parent cycles; `go_to` targets must exist.
- **`go_to` may not target an `initialize_variable` step** (400).
- **All `initialize_variable` steps must precede every other step** (400) — see
  the root-chain rule in "Key quirks" above, which is the form this actually
  bites in.
- Condition steps need `action_on_failure: notify_pause` (the builder coerces).

### Unlisted step types

A step type with no dedicated builder falls through a generic pass-through that
strips volatile keys. Some survive it — `http_request` round-trips clean, and
`stage_updated` (a trigger) does too, since its read shape is
write-compatible. A 400 or 500 on PUT means the type needs a real builder.

## Wire keys that differ from the obvious guess

Beyond the step/trigger tables above:

| where | the key |
|---|---|
| `field_updated` trigger | `field_id` (UUID). This spec's `field_ref: "object.field"` resolves to it. **`from_value`/`to_value` write as bare UUIDs** — an expanded dict is silently stored as null |
| `on_or_around_date` trigger | `field_id` |
| `scheduled_activity_overdue` trigger | `activity` writes as `{"id": uuid}` |
| `schedule` trigger | `trigger_schedule: {rrule, is_advanced}` — matches a live read exactly, nothing to unwrap |
| `file_content_extraction` | input key is `input_field` (UUID); `input_field_ref` resolves |
| `business_plugin_app_id` | a bare UUID string — **do not** wrap in `{id: …}` |
| `change_field_value` (choice/status) | `specific_field_value` is the bare option UUID string |
| `start_automation` | `automation_ids`, `relationship_field_ids` |
| `archive_record` | `relationship_field_ids`, `automation_variable_name` |
| `assign_team_member` | the `*_id` dialect: `role_id`, `employee_ids`, `field_id`, `related_field_id`. Its `type` is its own six-value enum — see below |
| `go_to_automation_step` | `step_key`, pointing at a synthesized key |

The `manual` trigger is prepended to every spec automatically if absent.

## Four fields that break the bare-scalar convention

This codebase's usual "reads expand, writes take a bare scalar" rule is **a good
prior, not a universal law**. All four below were confirmed against
`GET /api/docs/schema` — the *full* schema, which covers automation2 in depth,
unlike `/api/docs/public/schema` — and/or a live create. **When wiring a new
step type, check the schema before assuming.**

### `search_records`

`custom_object`, `filter_groups[]`, and `destination_variable` are each an
`{"id": uuid}` / `{"name": str}` **object** on write — not the bare UUID/name
string most other steps use (contrast `modify_related_entities`'s
`object_to_modify`, `archive_record`'s `automation_variable_name`). **Sending
bare strings 500s.**

`filter_config` (only for `filter_type: custom_filter`) resolves against the
step's **own** `custom_object`, not the automation's `target_object` — which a
global automation doesn't have at all.

### `schedule_activity.association_configs`

Per-object record linkage, for activity types associated with more than one
custom object. Write keys are **flat** —
`custom_object_id`/`relationship_field_id`/`automation_variable_name`, bare
UUIDs and strings — **not** the nested `custom_object: {id}` shape a live read
returns, which the server silently ignores.

`association_source` is `none`/`context_record`/`related_field`/
`record_variable`/`variable_related_field` — **not** `relationship_field`/
`automation_variable`, which 400 with *"… is not a valid choice"*. Two more
confirmed rules:

- **`context_record` is only valid when that entry's `custom_object_id` IS the
  automation's own `target_object`** — otherwise 400: *"context_record
  custom_object_id must match the … workflow's custom object."* The server
  **auto-adds this entry itself** for the automation's own object, so don't send
  it, and don't count it when sizing the list you author — it appears as an extra
  item in the live GET afterward.
- **`related_field` requires a relationship field that exists ON THE ASSOCIATED
  OBJECT itself** — passing one from the automation's own `target_object` 400s
  with *"Relationship field … not found on source object"*. Resolution rules
  beyond that are unconfirmed.

### `schedule_activity.assigned_to`

Backs all 11 "Assign To" options in the UI (round_robin_all/_role/_team_members,
owner, team_member, team_selector_field, last_active(_role), specific_role,
team_member_from_variable, role_from_variable). Wire keys are
`role_id`/`employee_id`/`employee_ids`/`field_id` — the same `*_id` dialect
`assign_team_member` uses, and a live 400 caught the builder emitting bare
`role`/`employee`/`field` here.

But **`variable` must be an `{"id"|"name": …}` object** — not the bare name
string this codebase's other variable references use. `assign_team_member` has a
smaller enum with no variable-based options, so it doesn't share this quirk.

### `assign_team_member.type`

A **third**, six-value enum — not the `assigned_to.assignment_type` set above
and not the `team_member` selector's set on notify/send steps. Confirmed live
2026-08-06, one create per value:

| `type` | needs |
|---|---|
| `team_member` | `employee_id` — the **singular** key; `employee_ids` 400s with *"employee_id … is required"* |
| `round_robin_role` | `role_id` |
| `round_robin_team_members` | `employee_ids` |
| `round_robin_all` | — |
| `team_selector_field` | `field_id` (or `field_ref`) |
| `related_team_selector_field` | `related_field_id` |

`owner`, `last_active`, `last_active_role` and `employees` are all valid on
*other* selectors and all 400 here with *"… is not a valid choice"*.

### `move_to_folder` / `automations move`

The write dialect is a bare, nullable `folder_id` — **not** the
`folder: {id, name}` shape a live read returns. The `folder: {…}` payload PUTs
fine (200, revision bumps) and is **silently ignored**.

And **`folder_id: null` — the obvious way to say "move to root" — 400s** with
*"This field may not be null."* Root is itself a real, listable folder
(`<business_root>`, always present in `kizen automations folders list`), not an
absent value. `kizen automations move <api>` with no `--folder` resolves to that
folder's id.

## Converging branches

To bring several branches back to one downstream step: set that step's
`parent_key` to the **YES** branch's last step, then add a
`go_to_automation_step` whose parent is the **NO** branch's last step and whose
`step_key` points at the downstream step. That's the standard convergence
pattern — there is no join node.

## Debugging

### A 400 from plan-apply

The error is nested: `{step_key: {action_block_name: {field_name: [message]}}}`.
Work inside-out — which step, which config block, which wire field. A
"not a valid choice" rejection for `create_related_entity`,
`notify_member_via_text`, or `on_or_around_date` now carries the CLI's known
valid values inline, sourced from
`tools/planners/automations.py::KNOWN_ENUM_CHOICES` /
`KNOWN_ENUM_CHOICES_TRIGGERS` — check the registry for what else this repo
knows about a field before retrying blind. Common causes:

- `null` for a non-nullable field (often a model default leaking through).
- `action_on_failure: notify_continue` on a condition step — use `notify_pause`.
- `filter_config` structure wrong: check the query group `id`, the `filters`
  key, and that no `value` is null.
- A destination field of the wrong type — `wysiwyg`, `files`, and `timezone`
  fields can't be extraction destinations.

### A 500 from the PUT

Usually one of two things:

- **Wrong branching wire key** — the spec used `yes_step_ids` instead of
  `parent_key` + `parent_yes_no`.
- **A corrupt automation server-side.** If the 500 persists regardless of
  payload changes, the automation itself may be in a bad state: the tell is that
  it *executes* correctly when triggered but every PUT returns 500. That needs a
  server-side repair, not a payload change — escalate to Kizen.

## See also

- `kizen docs show automation-step` — editing or adding **one** step, and the
  GET→translate→validate→PUT loop those verbs run.
- `kizen docs show automation-runtime` — starting a run, watching it, pausing,
  and the debug verbs.
- `kizen docs show filters` — condition and `search_records` filter shapes,
  including variable comparisons.
- `kizen docs show code-steps` — writing and unit-testing the Python in a
  `code_step`.
- `kizen docs show email-templates` — the `notify_member_via_email` message
  resource and merge-field namespaces.

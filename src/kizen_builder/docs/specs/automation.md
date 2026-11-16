# Spec shape: `AutomationDef` — automations

**Consumed by:** `kizen automations create|update --spec-file <f>` (also stdin).
An automation is triggers + a step graph, sent as one atomic POST/PUT.

**Writing one:** `kizen automations step-types skeleton --object <api_name>`
prints a minimal automation. `step-types list` names every wired type, `show
<type>` prints one's config block with entries to copy, and `concepts`
explains the ideas the blocks share. This page holds what those don't: the
graph, worked patterns, and what steps do at run time.

**Checking one:** `--dry-run` reports every spec problem at once, before any
live call. Each finding names the step key, the dotted path and the `kizen`
command that lists the allowed values; warnings don't block. The plan lists
what it defaulted, such as `api_name` from the name and `order` from position.
`kizen automations diff <api_name> --spec-file <f>` shows what an update would
change.

```bash
kizen automations create --spec-file auto.json --dry-run
```

---

## `AutomationDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** |
| `api_name` | string | `^[A-Za-z0-9_]+$`. Left out, it defaults to `name` slugified (`Follow Up` → `follow_up`). |
| `type` | enum | `record_based` (default) or `global`. |
| `target_object` | api_name | **Required for `record_based`.** |
| `active` | bool \| null | Left out on create, `false`: author inactive, then `automations activate`. Left out on update, the live value stays. |
| `folder` | string | Folder name or UUID (`kizen automations folders list`). Left out on update, the current folder stays. |
| `triggers` | `AutomationTriggerDef[]` | A `manual` trigger is added when the spec has none. |
| `steps` | `AutomationStepDef[]` | The graph, below. |

## The step graph

- Every step has a unique **`key`** and chains to its predecessor by
  **`parent_key`**. Exactly one step, the root, has `parent_key: null`.
- The **first** step inside a condition's or goal's yes/no branch sets
  **`parent_branch: "yes"`** (or `"no"`). The steps after it leave it unset.
- **`id`** is the server UUID from a live read, separate from `key`: see
  [What an update keeps](#what-an-update-keeps). Omit it for anything new.
- `order` numbers steps, and triggers, `0..N` across the whole automation,
  not per branch. `step-types` entries leave it out; the CLI numbers by
  position.

### Branch groups

To rejoin branches rather than repeat their tail, make the `condition`, `goal`
or `branch` step a **branch group initiator** (`is_branch_group_initiator:
true`) with one `merge_branches` child, which has no `parent_branch`. The steps
after the merge hang off it. Confirmed live 2026-09-28 (conditions and
branches) and 2026-09-29 (goals).

```json
{ "key": "check", "parent_key": null, "step_type": "condition", "order": 0,
  "is_branch_group_initiator": true,
  "step_condition": { "type": "custom_filter", "filter_config": { "all": [
    { "field": "account_seats", "op": ">=", "value": 10 } ] } } },
{ "key": "big", "parent_key": "check", "parent_branch": "yes",
  "step_type": "delay", "order": 1, "step_delay": { "minutes": 5 } },
{ "key": "small", "parent_key": "check", "parent_branch": "no",
  "step_type": "delay", "order": 2, "step_delay": { "minutes": 5 } },
{ "key": "rejoin", "parent_key": "check", "step_type": "merge_branches", "order": 3 },
{ "key": "after", "parent_key": "rejoin", "step_type": "stop_execution", "order": 4,
  "action_stop_execution": { "action": "stop_and_complete" } }
```

**Parallel branches** are the children of a `branch` step, none with a
`parent_branch`. Put a `branch` step in front of any fan-out: Kizen's
2026-09-28 migration inserted one ahead of every existing fan-out, and an
`update` from a spec without it removes it again.

A skipped condition or goal (`should_skip_execution: true`) takes the branch
`continue_with_branch` names. `error_notification_severity_level` (`error`,
`warning`, `info`, `inherit`) sets a step's own severity; a new step inherits.
Confirmed live 2026-09-28.

### Loops

A loop runs its body once per item of an array variable. It is a branch group
closed by `next_or_finish` instead of `merge_branches`. A spec names every
part, and `create` and `update` add none:

1. **The array:** an `initialize_variable` with `is_array: true`.
2. **Its item:** an `initialize_variable` with `sources: []`, declaring one
   variable of the array's type (for records, the same `data_subtype`). Each
   loop needs its own.
3. **The `loop` step**, naming both variables by name in `step_loop`.
4. **The body**, hanging off the loop.
5. **One `next_or_finish` child of the loop.** The steps after the loop hang
   off it.

[Find or create a Contact by email](#find-or-create-a-contact-by-email) has
all five. `steps add` of a loop adds its closer and item variable (`kizen docs
show automation-step`). Kizen's editor names a loop "For each item in
'<array>'" and its item step "Item Reference Variable", but a CLI-created one
reads back as "Loop" and "Initialize Variable", so set `description` to match.
Confirmed live 2026-10-27.

### Working with the records a search found

`search_records` writes an **entity array**, even when one record matches. An
array can be written only into a to-many relationship field: into a to-one it's
a 400 ("Variable can't be cased to a field"), and no step turns an array into
one record. To use a found record, loop over the array and use the item
variable in the body. `change_field_value` with `change_type: variable` sets a
relationship field to it, and `change_type: entity_variable_field` with
`related_object_field` (a field UUID on the found record's object) copies one
of its fields. Each pass sets the field again, so the last record wins: narrow
the filter to one record. Confirmed live 2026-11-02: a search that found two
records ran the loop twice, and the record ended linked to the second.

### Find or create a Contact by email

This links a support request to the Contact with the request's email, and
creates the Contact when there is none. The `support_requests` object and its
fields are in `kizen docs show form`.

- A filter value takes no merge fields, so the search compares the Contact's
  `email` to a string variable copied from the request.
- `create_related_entity` can't create a Contact (confirmed live 2026-10-30),
  so a code step posts to `/records/client_client/add`, and its `field`
  output links the new Contact.
- After the loop, `customer` is blank only when nothing was found. The
  condition's yes branch creates the Contact, and its empty no branch ends the
  run.

<!-- fixture: examples/support_desk/find_contact.automation.json#/triggers -->
```json
[ { "trigger_type": "new_entity_created", "order": 0,
    "trigger_new_entity_created": { "action": "create_only" } } ]
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps -->
```json
[ { "key": "found", "parent_key": null, "step_type": "initialize_variable", "order": 0,
    "description": "found_contacts = []",
    "action_initialize_variable": { "sources": [], "array_aggregation_mode": "add_all",
      "variable": { "name": "found_contacts", "data_type": "entity", "is_array": true,
                    "data_subtype": "<client_client_object_uuid>" } } },
  { "key": "found_item", "parent_key": "found", "step_type": "initialize_variable",
    "order": 1, "description": "Item Reference Variable",
    "action_initialize_variable": { "sources": [],
      "variable": { "name": "found_contacts_item", "data_type": "entity",
                    "data_subtype": "<client_client_object_uuid>" } } },
  { "key": "email", "parent_key": "found_item", "step_type": "initialize_variable",
    "order": 2,
    "action_initialize_variable": {
      "sources": [ { "source_type": "static", "source_subtype": "string",
                     "value": "{{ custom_objects.customer_email }}" } ],
      "variable": { "name": "customer_email", "data_type": "string" } } },
  { "key": "find", "parent_key": "email", "step_type": "search_records", "order": 3,
    "action_search_records": { "custom_object": "client_client",
      "filter_type": "custom_filter",
      "filter_config": { "all": [ { "field": "email", "op": "=", "variable": "customer_email" } ] },
      "destination_variable": "found_contacts",
      "destination_variable_resolution": "overwrite" } },
  { "key": "each", "parent_key": "find", "step_type": "loop", "order": 4,
    "description": "For each item in 'found_contacts'",
    "step_loop": { "array_variable": "found_contacts", "item_variable": "found_contacts_item" } },
  { "key": "link", "parent_key": "each", "step_type": "change_field_value", "order": 5,
    "action_change_field_value": { "field_ref": "support_requests.customer",
      "change_type": "variable", "variable": "found_contacts_item" } },
  { "key": "next", "parent_key": "each", "step_type": "next_or_finish", "order": 6 },
  { "key": "no_customer", "parent_key": "next", "step_type": "condition", "order": 7,
    "step_condition": { "type": "custom_filter",
      "filter_config": { "all": [ { "field": "customer", "op": "is_blank" } ] } } },
  { "key": "create_contact", "parent_key": "no_customer", "parent_branch": "yes",
    "step_type": "code_step", "order": 8,
    "action_code_step": {
      "script": "# Create a Contact for an email no Contact has yet; the output links it.\nemail = (inputs.customer_email or \"\").strip()\nif not email:\n    raise Exception(\"customer_email is blank: no Contact to create\")\nresp = kizen.api.post(\n    \"/records/client_client/add\", json={\"fields\": [{\"name\": \"email\", \"value\": email}]}\n)\nif resp.status_code != 201:\n    raise Exception(\n        f\"creating a Contact for {email} failed: {resp.status_code} {resp.text[:200]}\"\n    )\noutputs.customer = resp.json()[\"id\"]",
      "inputs": [ { "name": "customer_email", "input_type": "field",
                    "field_ref": "support_requests.customer_email", "data_type": "string" } ],
      "outputs": [ { "name": "customer", "output_type": "field",
                     "field_ref": "support_requests.customer" } ] } } ]
```

`<client_client_object_uuid>` is the `id` from `kizen objects get client_client
--json`. Kizen refuses a second Contact with the same email (400
"Entity record email must be unique", confirmed live 2026-10-30), so a run that
races another fails the step instead of linking a duplicate.

Confirmed live 2026-11-02, on a copy with the object renamed: a request whose
email matched a Contact was linked to it (the search found 1), and a new email
got a new Contact, linked (found 0, the create answered 201). Activate the
automation after creating it.

### Running once per record on a schedule

A job that does something to every matching record is two automations. A
**global** one with a `schedule` trigger finds the records, and a
`start_automation` step with `record_source: automation_variable` starts a
record-based one on each, with no loop:

<!-- fixture: examples/first_build/daily_deal_summary.automation.json#/triggers -->
```json
[ { "trigger_type": "schedule", "order": 0,
    "trigger_schedule": {
      "rrule": "DTSTART:20261102T130000Z\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
      "is_advanced": false } } ]
```

The schedule has no time-zone field: write `DTSTART` in UTC with the trailing
`Z`, as Kizen's builder saves it. Weekdays at 7am US/Central is
`DTSTART:20261102T130000Z`. It's a fixed UTC time, so across a daylight-saving
change the local time should move an hour. `DTSTART;TZID=…`
and a `DTSTART` without `Z` also save, but nothing shows which zone the
scheduler gives them. Set `description` yourself (`"Weekdays at 7am
Central"`): Kizen's own misreads a `DTSTART` rrule as "Every day starting
<save time>". Confirmed live 2026-10-30 for save and read-back only. No
scheduled run was observed, so the DST behaviour is RFC 5545's, unconfirmed.

<!-- fixture: examples/first_build/daily_deal_summary.automation.json#/steps -->
```json
[ { "key": "deals_to_refresh", "parent_key": null, "step_type": "initialize_variable",
    "order": 0,
    "action_initialize_variable": { "sources": [], "array_aggregation_mode": "add_all",
      "variable": { "name": "deals_to_refresh", "data_type": "entity", "is_array": true,
                    "data_subtype": "<deals_object_uuid>" } } },
  { "key": "find_deals", "parent_key": "deals_to_refresh", "step_type": "search_records",
    "order": 1,
    "action_search_records": { "custom_object": "deals", "filter_type": "all_records",
      "destination_variable": "deals_to_refresh",
      "destination_variable_resolution": "overwrite" } },
  { "key": "refresh_each", "parent_key": "find_deals", "step_type": "start_automation",
    "order": 2,
    "action_start_automation": { "record_source": "automation_variable",
      "automation_variable_name": "deals_to_refresh",
      "automation_ids": ["refresh_deal_brief"] } } ]
```

The started automation is an ordinary record-based one, here an AI brief built
from the deal's account (see [Reading a related record's field into a
variable](#reading-a-related-records-field-into-a-variable)):

<!-- fixture: examples/first_build/refresh_deal_brief.automation.json#/triggers -->
```json
[ { "trigger_type": "manual", "order": 0, "trigger_manual": {} } ]
```

<!-- fixture: examples/first_build/refresh_deal_brief.automation.json#/steps -->
```json
[ { "key": "account_industry", "parent_key": null, "step_type": "initialize_variable",
    "order": 0,
    "action_initialize_variable": {
      "sources": [ { "source_type": "field_value",
                     "source_subtype": "primary_related_object_field",
                     "relationship_field": "<deals.account_field_uuid>",
                     "field": "<accounts.industry_field_uuid>" } ],
      "variable": { "name": "account_industry", "data_type": "string" } } },
  { "key": "write_brief", "parent_key": "account_industry", "step_type": "call_llm",
    "order": 1,
    "action_call_llm": {
      "model_name": "kizen/lite",
      "merge_field_validation": "default_to_unknown",
      "prompt": "Write a two-sentence brief on this deal for its owner.\nDeal: {{ custom_objects.name }}\nAccount industry: {{ automation_variable.account_industry }}\nClose date: {{ custom_objects.close_date }}\nLatest call notes: {{ custom_objects.call_notes }}",
      "destinations": [ { "field_ref": "deals.ai_deal_brief" } ] } } ]
```

Build the target and activate it before creating the scheduled automation:
Kizen rejects a `start_automation` whose target is inactive. Confirmed live
2026-10-30: the manual run found two deals and started the target once on
each, and both briefs were written.

---

## Triggers

- **`email_received`** on a custom object is a 400, "only client custom object
  is allowed" (confirmed live 2026-10-14); on Contacts it saves and activates
  (confirmed live 2026-11-02). The run gets the Contact, not the email: see
  `kizen docs show code-steps`, "Reading a Contact's newest inbound email".
- **`activity_logged`** and **`scheduled_activity_overdue`**: to branch on
  what was logged, see `kizen docs show activity`, "Branching on what someone
  logged" (confirmed live 2026-10-30).
- **`form_submitted`** and **`survey_submitted`**: a form on another object is
  a 400 that reads "Form with ID '…' does not exist." (confirmed live
  2026-11-02). To tie a form's answers to the record, see `kizen docs show
  form`.
- **`stage_updated`** also fires for a move made by another automation.
  Without a `description` it gets the UI's text ("Stage Update from 'Any
  Stage' to 'Closed Won'"). Confirmed live 2026-10-30.
- **A goal step** waits on the same trigger types under `step_goal.triggers`,
  e.g. `email_received` for a reply. It counts only an event that happens while
  the run waits at it, so a `stage_updated` goal misses a move made before the
  run got there and stays pending (confirmed live 2026-10-30).

### Webhook trigger auth (confirmed live 2026-10-27)

- Every `auth_type` but `kizen_api` adds `?business_id=<uuid>` to
  `webhook_url`.
- `http_header`: a caller sends every header, each matching exactly, and no
  name may repeat. Text around a `{{ integration_secret.<api_name> }}` or
  `{{ business_variable.<name> }}` reference is sent as written, so `Bearer {{
  integration_secret.token }}` expects `Bearer <secret>`. A literal value is
  stored in plain text, and `show --json` prints it.
- `basic_auth`: `secret: {api_name}` names an integration secret whose value
  is `user:pass`. Callers send `Authorization: Basic <base64 of user:pass>`.
- `none` accepts any request. Kizen API headers don't get past `http_header`
  or `basic_auth`; a failed check answers 401 `Invalid webhook credentials.`
- `automations duplicate` keeps each webhook's auth and name. The copy's URL
  uses the copy's api_name.

### Date trigger (`on_or_around_date`)

This fires three days before each deal's close date, at 9 am:

<!-- fixture: examples/first_build/closing_soon.automation.json#/triggers/0 -->
```json
{ "trigger_type": "on_or_around_date", "order": 0,
  "trigger_on_or_around_date": { "field_ref": "deals.close_date",
    "date_offset": "days_before", "date_offset_days": 3,
    "time": "09:00", "period": "am" } }
```

9 pm is `"09:00"` with `"pm"`: `"21:00"` and `"09:00:00"` are each a 400.
Left out, `date_offset_days` is `0` (1 or more with `days_before` or
`days_after`), `every_year` `false` and `past_date_action`
`proceed_down_met_path`. Confirmed live 2026-10-30: four
configs saved and read back as sent, with those defaults. A date trigger fires
only on its date, so no run of one was observed.

## Steps

### Moving a record to a stage

There's no move-stage step: set the pipeline's `stage` with
`change_field_value`, by option label or UUID. Kizen's API lists
`update_pipeline_status`, but its builder doesn't offer one and the CLI
refuses it. The move writes the same timeline entry and stage history as a
manual move (confirmed live 2026-10-30). To react to a move, use the
`stage_updated` trigger.

### `plugin_code_step`

- `plugin_app_api_name` is the **installed** plugin's api_name. A preview
  install is `<api_name>_preview_<branch>` (confirmed live 2026-10-05). `GET
  /external-integrations/bootstrap` lists each install's steps under
  `automation_action_configs` (confirmed live 2026-10-14).
- Kizen checks the names and the bindings against the manifest. An api_name
  with no install, a step the plugin doesn't ship, and a missing required
  input or output are each a 400 naming the key (confirmed live 2026-10-14).
- What a step receives through a `business_plugin_config` input isn't
  confirmed live.

### `create_related_entity`

Left out, `context_entity_field_resolution` is `add_only` when
`context_entity_field` is to-many, and `overwrite` otherwise. On a to-many
field, `overwrite` replaces the list with the new record, unlinking every
record already related (confirmed live 2026-11-16). `--dry-run` lists the
planned value under `defaults`.

### `modify_related_entities`

Updates fields on records related to the trigger record:

```json
{ "step_type": "modify_related_entities", "key": "sync_encounter",
  "parent_key": null,
  "action_modify_related_entities": {
    "object_to_modify": "encounters",
    "automation_target_relationship_fields": ["patients.encounters"],
    "fields_to_modify": [
      { "field_to_modify": "encounters.attending_provider",
        "value_type": "context_entity_value",
        "context_entity_field": "patients.mrn" } ] } }
```

- `automation_target_relationship_fields` is the hop chain from
  `target_object` to `object_to_modify`; `relationship_field_ref` is a one-hop
  shorthand. With `record_source: automation_variable`, set
  `record_source_variables` to **non-array** entity variables (an array 400s)
  and the hop chain to `[]`. Confirmed live 2026-10-06.
- `fields_to_modify` items take `change_field_value`'s keys with `value_type`
  in place of `change_type`. The wrong one gets a 400 that reads like another
  problem: "specific_field_value should be passed".
- `field_to_modify` names a field on `object_to_modify`, but
  `context_entity_field` one on the automation's own `target_object`.
- `field_value_mappings` copies an option field between objects whose option
  UUIDs differ: `[{"source_values": [<uuid>], "target_values": [<uuid>],
  "order": 0}, …]`, tried in `order`.

### Extraction input

`file_content_extraction` and `audio_transcription` read a file field on the
record (`input_field_ref`), on a related record, or from a variable
(`input_value_source`). A related record's hop must be single-valued: a
to-many hop 400s with `Relationship field must be single-valued`. The builder
UI offers only fields on the record, and an automation with any other source
doesn't open in its editor: it returns to the automation list. Confirmed live
2026-10-14.

### AI steps

- **Destinations.** `call_llm`, `file_content_extraction` and
  `audio_transcription` write to a field on the record, a variable, or a field
  on a related record one hop away (confirmed live 2026-07-27). **Several
  destinations need `is_advanced: true`**: without it Kizen 400s "Multiple
  destinations are not allowed for non-advanced LLM call actions", on all
  three steps (confirmed live 2026-11-17). With it, one step can write the
  record and a related record (confirmed live).
- **The model.** `kizen automations llm-models` lists each `model_name` the
  business can use, the steps it supports, and what replaces a deprecated
  one. `gemini/…` and `openai/…` models need the prefix; Claude models are
  bare. Leave `business_plugin_app_id` out: the CLI fills it, so the step reads
  back like one saved in the UI (confirmed live 2026-10-30).
- **`merge_field_validation: default_to_unknown`** puts the fixed text
  "Unknown" in place of a blank merge field; `error_if_required`, a new
  step's default, fails the step instead (confirmed live against the builder
  UI).
- **MCP tools** (confirmed live 2026-10-27). Set `mcp_server` and
  `run_tools_as` together; a plugin's own service account can't run the
  tools, and a name two accounts share needs the UUID. A model without tool
  calling 400s with "The selected model does not support tool calling", and
  the model catalog has no flag for it. `null` on either removes both. To
  search a knowledge catalog, see `kizen docs show mcp-server`.

### Reading a related record's field into a variable

A `field_value` source with `source_subtype: primary_related_object_field`
reads a field off a related record. Both keys are raw UUIDs:
`relationship_field` is the relationship field on `target_object`, and
`field` the field on the object it points to (here `deals.account` and
`accounts.industry`, from `kizen objects get <object> --json`). A dropdown
arrives as its option label. Confirmed live 2026-10-30:

<!-- fixture: examples/first_build/log_call.automation.json#/steps/1 -->
```json
{ "key": "account_industry", "parent_key": "today", "step_type": "initialize_variable",
  "order": 1,
  "action_initialize_variable": {
    "sources": [ { "source_type": "field_value",
                   "source_subtype": "primary_related_object_field",
                   "relationship_field": "<deals.account_field_uuid>",
                   "field": "<accounts.industry_field_uuid>" } ],
    "variable": { "name": "account_industry", "data_type": "string" } } }
```

**To set a date field to today**, a `trigger_time` source holds the run's
start date; write the variable with `change_type: variable`. Confirmed live
2026-10-30:

<!-- fixture: examples/first_build/log_call.automation.json#/steps/0 -->
```json
{ "key": "today", "parent_key": null, "step_type": "initialize_variable", "order": 0,
  "action_initialize_variable": {
    "sources": [ { "source_type": "trigger_time" } ],
    "variable": { "name": "today", "data_type": "date" } } }
```

<!-- fixture: examples/first_build/log_call.automation.json#/steps/2 -->
```json
{ "key": "stamp_last_activity", "parent_key": "account_industry",
  "step_type": "change_field_value", "order": 2,
  "action_change_field_value": { "field_ref": "deals.last_activity_date",
    "change_type": "variable", "variable": "today" } }
```

### Merge-field namespace tokens by step type

The namespace for the trigger record's own fields depends on the step:

| Step type | Namespace |
|-----------|-----------|
| `notify_member_via_email`, `notify_member_via_text`, `send_related_contact_email`, `send_related_contact_text` | `entity_record.<field>` |
| `call_llm` (`prompt`), `schedule_activity` (`note`), `initialize_variable`/`update_variable` (static sources) | `custom_objects.<field>`, literally, whatever the object's api_name |

These render empty rather than fail (confirmed live 2026-10-30):

- `entity_record.<field>` in a `schedule_activity` note.
- A hop, `{{ custom_objects.<relationship>.<field> }}`, in a `call_llm`
  prompt, even with `default_to_unknown`.
- On a Contacts automation, `{{ contact.id }}`, `{{ contact.email }}` and `{{
  entity_record.id }}` in a static variable source save, but the variable
  arrives empty (`{{ custom_objects.id }}` there is a 400). Bind the field as
  a code step's `field` input instead.

To use a related record's field, copy it into a variable first, then write `{{
automation_variable.<name> }}`. Kizen sent this prompt with both values filled
in (confirmed live 2026-10-30):

<!-- fixture: examples/first_build/log_call.automation.json#/steps/3 -->
```json
{ "key": "rate_health", "parent_key": "stamp_last_activity", "step_type": "call_llm",
  "order": 3,
  "action_call_llm": {
    "model_name": "kizen/lite",
    "merge_field_validation": "default_to_unknown",
    "prompt": "Rate this deal's health as On Track, Needs Attention or At Risk. Answer with the rating only.\nAccount industry: {{ automation_variable.account_industry }}\nLatest call notes: {{ custom_objects.call_notes }}",
    "destinations": [ { "field_ref": "deals.health" } ] } }
```

The other namespaces (`team_member`, `business`, `automation_history`, …) and
the `kzn-merge-field` span each token sits in are in `kizen docs show
email-templates`.

### Email steps: build order

A `notify_member_via_email` step points at an automation message, and a
message belongs to one automation, so the order is fixed:

1. **A template.** A blank business may have none (`kizen messages templates
   list`).
2. **The automation, without the email step.** `"steps": []` saves.
3. **The message**, from the template: `kizen messages create <automation>
   --template <name>` (`kizen docs show email-templates`).
4. **The step**, with that id as `email_template_id`, through `automations
   update`:

<!-- fixture: examples/first_build/closing_soon.automation.json#/steps/0 -->
```json
{ "key": "tell_owner", "parent_key": null, "step_type": "notify_member_via_email",
  "order": 0,
  "action_notify_member_via_email": {
    "team_member": { "type": "owner" },
    "email_template_id": "<closing_soon_message_uuid>" } }
```

Confirmed live 2026-10-30, in this order. A `team_selector_field` recipient
works too: the CLI sends the field's UUID as `field_id` (confirmed live
2026-11-16). A message can't be deleted and outlives its automation, so a
recreated automation needs new ones (`kizen docs show email-templates`).

### Stopping a run

Two of `stop_execution`'s five `action`s don't stop the run: `pause_and_error`
pauses and shows Error, and `pause` pauses. `stop_and_fail`,
`stop_and_complete` and `stop_and_cancel` stop it as Failed, Completed or
Cancelled. `"notify": true` also sends the matching notification. Confirmed
live 2026-07-27, against the builder UI's "Action Options". A saved `null`
action never finishes and the editor shows "Unknown" (confirmed live
2026-10-30); `translate`, `steps get` and `roundtrip` resend it as it is, so
fix one with `steps edit`.

## Variables

- A variable the UI creates inline from a step's picker is an ordinary
  root-chain `initialize_variable` with `sources: []` (confirmed live
  2026-09-28).
- A static array value is a `static` source with `source_subtype`
  (`string`/`uuid`), a list `value` and `array_separator`. Give `value` as a
  list: a single string such as `"a\nb"` loses its separator on read-back, and
  `diff` then shows a change (confirmed live 2026-09-29).
- **`value_resolution` on `update_variable`** says how the sources combine
  with an array's current value. Confirmed live 2026-10-14, from `[a]` with
  `deduplicate_array: false`:

  | `value_resolution` | sources `[b]` | sources empty |
  |---|---|---|
  | `add_only` | `[a, b]` (and `[a, c]` gives `[a, a, c]`) | not observed |
  | `overwrite` | `[b]` | `[]` |
  | `overwrite_except_null` | `[b]` | `[a]`: kept |
  | null / omitted | `[b]` | `[]` |

  An update that leaves it out keeps the stored value, so deleting the key
  doesn't clear it: set `overwrite`. The builder UI neither shows nor sets it
  (as of 2026-10-14).

## What an update keeps

A step or trigger keeps its identity, run history and the settings below only
when it carries its `id` from a live read (`kizen automations show`). Without
one it's new: it gets a fresh id, orphaning the old one's history (confirmed
live 2026-08-10), and Kizen's defaults for whatever the spec leaves out.

An update replaces the whole automation, so `automations update` fills in
what the spec leaves out from the live one: `active` (a spec that doesn't
mention it never turns a running automation off), `folder`,
`skip_non_working_days` and webhook auth; and on a step or trigger whose `id`
matches, its descriptions, skip and failure settings, and per-type settings a
Kizen PUT would reset, such as an LLM step's `merge_field_validation` and MCP
server, a stop's `action`, and a `schedule_activity`'s `target_variable`.
`--dry-run` names them as `keeps_live=[<key>, …]`. An explicit value always
wins, `""` and `false` included; `description: ""` gets the type's default
text.

## Scheduling activities and assigning owners

### When a scheduled activity is due

This schedules a Follow Up for the deal's owner two working days after the
run:

<!-- fixture: examples/first_build/log_call.automation.json#/steps/5 -->
```json
{ "key": "schedule_follow_up", "parent_key": "no_conversation", "parent_branch": "yes",
  "step_type": "schedule_activity", "order": 5,
  "action_schedule_activity": {
    "activity_type": "follow_up",
    "schedule": { "type": "with_delay",
                  "with_delay": { "delay_type": "day", "delay_value_type": "static",
                                  "delay_value": 2 },
                  "skip_non_working_days": true },
    "assigned_to": { "assignment_type": "owner" },
    "note": "Try again. Last call: {{ custom_objects.call_notes }}" } }
```

- Started on a Friday at 19:48 UTC, it was due the next Tuesday at 19:48 UTC:
  a `day` delay with `skip_non_working_days` counts working days and keeps the
  run's time of day. Confirmed live 2026-10-30.
- An `on_or_around_date` schedule `days_after` by 1 at `"10:00"`, on a record
  starting 2026-11-16, was due 2026-11-17 at 10:00 in the business's time
  zone. `on_day_and_time` takes the time from the field and ignores
  `time_offset`. Confirmed live 2026-11-16.

### `schedule_activity.target_variable`

`target_variable` receives the new activity's id. With
`variable_field_resolution: update_if_blank`, a variable already holding a
value keeps it, and the activity is still scheduled (confirmed live
2026-10-30, including runs). If an update removes the bound variable, also send
`"target_variable": null`, or Kizen refuses the PUT: `variable '<name>' does
not exist.`

### `schedule_activity.association_configs`

For an activity type tied to more than one object, `context_record` is valid
only on the entry for the automation's own `target_object`, and `related_field`
must be a relationship field on the associated object itself (one from
`target_object` 400s, *"Relationship field … not found on source object"*). A
block copied from `steps get` re-applies unchanged (confirmed live 2026-10-14).

### `assign_team_member`

The plan checks each `type`'s keys, as Kizen does (confirmed live 2026-10-16),
but a complete selector doesn't mean a run assigns anyone. Confirmed live
2026-08-13:

- **It writes only this record's `owner`**, never a related record's.
  `field_id` and `related_field_id` name where the owner is copied from.
- **`team_member` reports `completed` and leaves `owner` unchanged**, on two
  objects and on never-touched records. It's platform behaviour; no spec fixes
  it. The `round_robin_*` types weren't tested.
- **`related_team_selector_field` works on a record's first run.**
- **A re-run on a record the automation already processed silently no-ops**,
  even when the source changed. Test against a fresh record each time.
- **`updated` bumps on every run**, assigned or not. Read `owner` back.

An empty source fails the step, not the run: `detailed_log.reasons` says *"No
employee found for team_selector_field: <uuid>"* or *"No value set for
related_field_id: <uuid>"* (confirmed live 2026-10-16). To set an owner
reliably, use `kizen records update <object> <id> -f owner=<uuid>` or a
`change_field_value` step on `owner`.

## Debugging

**A 400 from plan-apply.** Most spec mistakes never get this far: `--dry-run`
lists them first, as `spec error:` lines on stderr, or with `--json` as `{"ok":
false, "errors": [...], "warnings": [...]}`. The server's error is nested,
`{step_key: {action_block_name: {field_name: [message]}}}`, so work
inside-out. Common causes: `null` for a non-nullable field; a `filter_config`
with a wrong group `id`, no `filters` key or a null `value`; a `wysiwyg`,
`files` or `timezone` field as an extraction destination.

**A 500 from every PUT**, whatever the payload, while the automation still
*executes* when triggered, means it's in a bad state. That needs a server-side
repair, so escalate to Kizen.

## See also

- `kizen docs show automation-step`: editing or adding **one** step.
- `kizen docs show automation-runtime`: starting, watching and pausing runs.
- `kizen docs show filters`: condition and `search_records` filter shapes.
- `kizen docs show code-steps`: writing and testing a `code_step`'s Python.
- `kizen docs show email-templates`: the message resource and merge fields.
- `kizen docs show activity`: activity types and branching on what was logged.

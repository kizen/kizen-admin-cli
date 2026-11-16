# Spec shape: `AutomationDef` — automations

**Consumed by:** `kizen automations create|update --spec-file <f>` (also stdin).
An automation is triggers + a step graph, sent as one atomic POST/PUT.

**Writing one:** `kizen automations step-types skeleton --object <api_name>`
prints a minimal automation to build on. `kizen automations step-types show
<type>` prints one step's or trigger's config block, with entries to copy and
the keys each choice needs; `step-types list` names every wired type. This
page holds what those don't: the graph rules, worked patterns, and what
steps do at run time.

**Checking one:** `--dry-run` reports every spec problem at once, before any
live call: a wrong choice or unknown key (with a "did you mean"), a
placeholder left in, a variable no `initialize_variable` declares, and every
graph rule broken. Each finding names the step key, the dotted path and the
`kizen` command that lists the allowed values. `kizen automations diff
<api_name> --spec-file <f>` shows what an update would change. Warnings (a choice missing the
key it usually needs, step orders with gaps) print with the plan and don't
block it. Leave out `api_name` and it defaults to the name, slugified; leave
out a step's `order` and it defaults to the step's position. The plan
preview lists what was defaulted. `steps add` and `steps edit` run the same
per-step checks against the automation's live variables.

> This is the richest spec. The graph rules below (one root, `parent_key`
> chaining, `parent_branch` on branch entries) are enforced at plan time.
> This file covers the shape, the wired types, and the common patterns; if a
> step or trigger's field list isn't fully covered here, `--dry-run` reports
> the exact field Kizen rejected.

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
      "step_type": "stop_execution", "order": 2,
      "action_stop_execution": { "action": "stop_and_complete" } }
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
| `api_name` | string | `^[A-Za-z0-9_]+$`. Left out, it defaults to `name` slugified (`Follow Up` → `follow_up`). |
| `type` | enum | `record_based` (default) or `global`. |
| `target_object` | api_name | **Required for `record_based`.** Resolved to `custom_object_id`. |
| `active` | bool \| null | On create, an omitted/`null` value resolves to `false` — prefer authoring inactive, then `automations activate`. On update, an omitted/`null` value **preserves whatever the live automation already is**; only an explicit `true`/`false` changes it. |
| `folder` | string | Folder name or UUID (`kizen automations folders list`). On update, leaving it out keeps the current folder. A name two folders share is refused; give the UUID. |
| `triggers` | `AutomationTriggerDef[]` | A `manual` trigger is added when the spec has none. |
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
- `order` (int ≥ 0) orders steps for display, across the whole automation,
  not per branch; leave it out and the CLI numbers by position in `steps`.
  `step-types` entries leave it out. **Triggers need it too** when there is
  more than one: two unordered triggers both send 0, and the dry-run refuses
  them. Sequential `0..N` on both `triggers` and `steps`, no gaps or
  duplicates.
- Cycles are rejected; `go_to_automation_step.step_key` must reference a real key.
- To rejoin branches, use a branch group (below) rather than duplicating the
  tail of each branch.

### Branch groups

A step whose branches rejoin is a **branch group initiator**: a `condition`,
`goal` or `branch` step with `is_branch_group_initiator: true`. It has exactly
one `merge_branches` child, which carries no `parent_branch`, and the steps
after the merge hang off the merge step. `branch` and `merge_branches` take no
config block. `--dry-run` checks the group against the server's rules.
Confirmed live 2026-09-28 (conditions and
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
`parent_branch`. Set `is_branch_group_initiator: true` and add a
`merge_branches` child to rejoin them. Lanes can be `branch` steps themselves,
merged or not, and a `branch` can follow a merge. Put a `branch` step in front
of any fan-out: a plain step with two or more children still saves, but the
plan preview warns about it. A group's closer (`merge_branches` or
`next_or_finish`) isn't a lane, so it doesn't count toward that warning.
Kizen's 2026-09-28 migration inserted a `branch` step ahead of every existing
fan-out, and an `update` from a spec without one removes it again. Confirmed
live 2026-09-28.

**Skipping a condition or goal** (`should_skip_execution: true`) needs
`continue_with_branch: "yes"` or `"no"`, the branch the run takes past it. The
spec is rejected without it. Confirmed live 2026-09-28.

`error_notification_severity_level` (`error`, `warning`, `info`, `inherit`)
sets a step's own severity. Omitted, a new step inherits and an `update`
keeps an existing step's live severity. Confirmed live 2026-09-28.

**Field references — two forms, don't mix them up:**
- **Action-block `field_ref`** (e.g. `change_field_value`, `call_llm` destinations,
  a team-member selector's `team_selector_field`) uses the object-qualified `"<object_api>.<field_api>"`, resolved to this
  business's UUID at apply time. A bare `field_id` UUID also works.
- **Condition `filter_config` `field`** uses the **bare field api_name**
  (`account_region`), *not* object-qualified — it's the filter DSL, same as
  `records list --filter` and saved views.

### Loops

A loop runs its body once per item of an array variable. Loops are branch
group initiators, closed by `next_or_finish` instead of `merge_branches`. A
spec names all six parts; `create` and `update` add none of them. `steps add`
of a loop adds its closer and item variable (see `kizen docs show
automation-step`):

1. **The array variable:** an `initialize_variable` step with `is_array: true`.
2. **Its item variable:** an `initialize_variable` step with `sources: []`,
   declaring a single variable of the array's type (for records, the same
   `data_subtype`). It needs no sources, though the server accepts one. Each
   loop needs its own.
3. **The `loop` step.** `step_loop` names both variables by **name**; their ids
   are a 400. The CLI always sends a loop as `is_branch_group_initiator: true`,
   because the server stores true even when false is sent.
4. **The body**, hanging off the loop.
5. **One `next_or_finish` child of the loop**, which closes the body. The steps
   after the loop hang off it.

Both variable steps go at the front of the automation, like every
`initialize_variable`. Confirmed live 2026-10-27.

```json
{ "name": "Visit every account", "api_name": "visit_every_account",
  "type": "global",
  "steps": [
    { "key": "accounts", "parent_key": null, "step_type": "initialize_variable",
      "order": 0, "description": "accounts = []",
      "action_initialize_variable": { "sources": [], "array_aggregation_mode": "add_all",
        "variable": { "name": "accounts", "data_type": "entity", "is_array": true,
                      "data_subtype": "<accounts object uuid>" } } },
    { "key": "status", "parent_key": "accounts", "step_type": "initialize_variable",
      "order": 1, "action_initialize_variable": { "sources": [],
        "variable": { "name": "status", "data_type": "string" } } },
    { "key": "account", "parent_key": "status", "step_type": "initialize_variable",
      "order": 2, "description": "Item Reference Variable",
      "action_initialize_variable": { "sources": [],
        "variable": { "name": "accounts_item", "data_type": "entity",
                      "data_subtype": "<accounts object uuid>" } } },
    { "key": "find", "parent_key": "account", "step_type": "search_records",
      "order": 3, "action_search_records": { "custom_object": "accounts",
        "filter_type": "all_records", "destination_variable": "accounts" } },
    { "key": "each", "parent_key": "find", "step_type": "loop", "order": 4,
      "description": "For each item in 'accounts'",
      "step_loop": { "array_variable": "accounts", "item_variable": "accounts_item" } },
    { "key": "visit", "parent_key": "each", "step_type": "update_variable", "order": 5,
      "action_update_variable": { "variable": { "name": "status", "data_type": "string" },
        "sources": [ { "source_type": "static", "source_subtype": "string",
                       "value": "visited" } ] } },
    { "key": "next", "parent_key": "each", "step_type": "next_or_finish", "order": 6 },
    { "key": "done", "parent_key": "next", "step_type": "update_variable", "order": 7,
      "action_update_variable": { "variable": { "name": "status", "data_type": "string" },
        "sources": [ { "source_type": "static", "source_subtype": "string",
                       "value": "done" } ] } }
  ] }
```

- **Array types:** strings, UUIDs and records (`data_type` `string`, `uuid` or
  `entity`). Kizen rejects an array of any other type at the
  `initialize_variable` step. A `search_records` destination can be the loop's
  array, as above. Confirmed live 2026-10-27.
- **Descriptions:** a CLI-created loop reads back as "Loop" and its item step as
  "Initialize Variable". The UI writes "For each item in '<array>'" and "Item
  Reference Variable". Set `description` to match the UI. Confirmed live
  2026-10-27.
- **`diff`** ignores `is_array_item_variable` and `source_array_variable`, which
  the server adds to the item variable from the loop. It reads a null key in a
  top-level or `initialize_variable` variable definition as absent. Confirmed
  live 2026-10-27.
- **Body lanes:** a body can be empty or have two lanes. Two lanes get the
  fan-out warning, like any plain fan-out. Confirmed live 2026-10-27.
- **Nesting:** a loop can sit in another loop's body, up to three levels, and a
  nested loop needs a different array from every loop around it. Kizen's
  builder refuses both, so the CLI refuses them at plan time (`--dry-run` shows
  it). The API accepts both: six levels, and one array at two levels, each
  confirmed live 2026-10-27.

### Working with the records a search found

A `search_records` step writes an **entity array** variable, even when the
filter matches one record. An array can be written only into a to-many
relationship field; into a to-one relationship it's a 400 ("Variable can't be
cased to a field"), and no step turns an array into a single record.

To use one found record, loop over the array and work with the loop's item
variable inside the body:

- `change_field_value` with `change_type: variable` and the item variable sets
  a relationship field to that record.
- `change_type: entity_variable_field` with the item variable and
  `related_object_field` (a field UUID on the found record's object) copies one
  of its fields onto this record.

Every pass of the loop sets the field again, so when the search finds several
records the last one wins. Narrow the filter until it matches one record.

This links each new hire to the playbook named "Sales Onboarding", and copies
the playbook's text. Both variable steps sit at the front, as the loop section
above requires:

```json
{ "key": "find", "parent_key": "v_item", "step_type": "search_records", "order": 2,
  "action_search_records": { "custom_object": "playbooks",
    "filter_type": "custom_filter",
    "filter_config": { "all": [ { "field": "name", "op": "=", "value": "Sales Onboarding" } ] },
    "destination_variable": "playbooks", "destination_variable_resolution": "overwrite" } },
{ "key": "each", "parent_key": "find", "step_type": "loop", "order": 3,
  "description": "For each item in 'playbooks'",
  "step_loop": { "array_variable": "playbooks", "item_variable": "playbooks_item" } },
{ "key": "link", "parent_key": "each", "step_type": "change_field_value", "order": 4,
  "action_change_field_value": { "field_ref": "hires.hire_playbook",
    "change_type": "variable", "variable": "playbooks_item" } },
{ "key": "copy", "parent_key": "link", "step_type": "change_field_value", "order": 5,
  "action_change_field_value": { "field_ref": "hires.hire_copied",
    "change_type": "entity_variable_field", "variable": "playbooks_item",
    "related_object_field": "<playbooks.pb_text field uuid>" } },
{ "key": "next", "parent_key": "each", "step_type": "next_or_finish", "order": 6 }
```

Confirmed live 2026-11-02: the search found one playbook, and the loop's one
pass linked it and copied its text. With the filter widened to `contains`
"Sales", it found two, the loop ran twice, and the hire ended linked to the
second. To read the linked record's fields in a later step, see
[Reading a related record's field into a variable](#reading-a-related-records-field-into-a-variable).

### Find or create a Contact by email

A support request arrives with the customer's email address. This links the
request to the Contact with that email, and creates the Contact when there is
none. It is the search-and-link pattern above, with three differences:

- **The search compares to a variable, not a merge field.** Kizen doesn't fill
  `{{ … }}` in a filter value. It stores the text and matches nothing, so the
  CLI refuses one in a condition or `search_records` filter spec. Copy the
  field into a string variable and compare with `"variable"` (see
  `kizen docs show filters`).
- **The Contact is created in a code step.** `create_related_entity` can't
  create a Contact: Kizen refuses it with a 400 (confirmed live 2026-10-30).
  The script posts to `/records/client_client/add`, and its `field` output
  links the new Contact.
- **Kizen refuses a second Contact with the same email**, with a 400 "Entity
  record email must be unique". Confirmed live 2026-10-30.

The object and its fields, applied as in `kizen docs show examples`:

<!-- fixture: examples/support_desk/support_requests.object.json -->
```json
{
  "api_name": "support_requests",
  "name": "Support Requests",
  "entity_name": "Support Request",
  "description": "A request to the support desk, from a customer's email address.",
  "categories": [
    { "api_name": "request_details", "name": "Request Details" }
  ]
}
```

<!-- fixture: examples/support_desk/support_requests.fields.json -->
```json
{
  "category": "Request Details",
  "fields": [
    {
      "name": "Customer Email",
      "api_name": "customer_email",
      "field_type": "email"
    },
    {
      "name": "Customer",
      "api_name": "customer",
      "field_type": "relationship",
      "relation": {
        "target_object": "client_client",
        "relation_type": "many_to_one",
        "related_name": "Support Requests"
      }
    },
    {
      "name": "Subject",
      "api_name": "subject",
      "field_type": "text"
    },
    {
      "name": "Message",
      "api_name": "message",
      "field_type": "longtext"
    },
    {
      "name": "Channel",
      "api_name": "channel",
      "field_type": "dropdown",
      "options": ["Web Form", "Email", "Chat"]
    }
  ]
}
```

The automation runs when a request is created:

<!-- fixture: examples/support_desk/find_contact.automation.json#/triggers -->
```json
[ { "trigger_type": "new_entity_created", "order": 0,
    "trigger_new_entity_created": { "action": "create_only" } } ]
```

The three variable steps sit at the front. `customer_email` copies the
request's email from a merge field, which a variable source does fill:

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/0 -->
```json
{ "key": "found", "parent_key": null, "step_type": "initialize_variable", "order": 0,
  "description": "found_contacts = []",
  "action_initialize_variable": { "sources": [], "array_aggregation_mode": "add_all",
    "variable": { "name": "found_contacts", "data_type": "entity", "is_array": true,
                  "data_subtype": "<client_client_object_uuid>" } } }
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/1 -->
```json
{ "key": "found_item", "parent_key": "found", "step_type": "initialize_variable",
  "order": 1, "description": "Item Reference Variable",
  "action_initialize_variable": { "sources": [],
    "variable": { "name": "found_contacts_item", "data_type": "entity",
                  "data_subtype": "<client_client_object_uuid>" } } }
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/2 -->
```json
{ "key": "email", "parent_key": "found_item", "step_type": "initialize_variable",
  "order": 2,
  "action_initialize_variable": {
    "sources": [ { "source_type": "static", "source_subtype": "string",
                   "value": "{{ custom_objects.customer_email }}" } ],
    "variable": { "name": "customer_email", "data_type": "string" } } }
```

`<client_client_object_uuid>` is the `id` from `kizen objects get client_client
--json`. The search compares the Contacts `email` field to the variable, and
the editor shows it as "Email Equals Variable 'customer_email'":

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/3 -->
```json
{ "key": "find", "parent_key": "email", "step_type": "search_records", "order": 3,
  "action_search_records": { "custom_object": "client_client",
    "filter_type": "custom_filter",
    "filter_config": { "all": [ { "field": "email", "op": "=", "variable": "customer_email" } ] },
    "destination_variable": "found_contacts",
    "destination_variable_resolution": "overwrite" } }
```

The loop links the Contact the search found. Each pass sets `customer` again,
so if the search ever found several Contacts, the last one would be linked:

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/4 -->
```json
{ "key": "each", "parent_key": "find", "step_type": "loop", "order": 4,
  "description": "For each item in 'found_contacts'",
  "step_loop": { "array_variable": "found_contacts", "item_variable": "found_contacts_item" } }
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/5 -->
```json
{ "key": "link", "parent_key": "each", "step_type": "change_field_value", "order": 5,
  "action_change_field_value": { "field_ref": "support_requests.customer",
    "change_type": "variable", "variable": "found_contacts_item" } }
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/6 -->
```json
{ "key": "next", "parent_key": "each", "step_type": "next_or_finish", "order": 6 }
```

After the loop, `customer` is still blank only when nothing was found. The
condition's yes branch creates the Contact. Its no branch is empty, so the run
ends there:

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/7 -->
```json
{ "key": "no_customer", "parent_key": "next", "step_type": "condition", "order": 7,
  "step_condition": { "type": "custom_filter",
    "filter_config": { "all": [ { "field": "customer", "op": "is_blank" } ] } } }
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps/8 -->
```json
{ "key": "create_contact", "parent_key": "no_customer", "parent_branch": "yes",
  "step_type": "code_step", "order": 8,
  "action_code_step": {
    "script": "# Create a Contact for an email no Contact has yet; the output links it.\nemail = (inputs.customer_email or \"\").strip()\nif not email:\n    raise Exception(\"customer_email is blank: no Contact to create\")\nresp = kizen.api.post(\n    \"/records/client_client/add\", json={\"fields\": [{\"name\": \"email\", \"value\": email}]}\n)\nif resp.status_code != 201:\n    raise Exception(\n        f\"creating a Contact for {email} failed: {resp.status_code} {resp.text[:200]}\"\n    )\noutputs.customer = resp.json()[\"id\"]",
    "inputs": [ { "name": "customer_email", "input_type": "field",
                  "field_ref": "support_requests.customer_email", "data_type": "string" } ],
    "outputs": [ { "name": "customer", "output_type": "field",
                   "field_ref": "support_requests.customer" } ] } }
```

That step's `script`, unescaped:

```python
# Create a Contact for an email no Contact has yet; the output links it.
email = (inputs.customer_email or "").strip()
if not email:
    raise Exception("customer_email is blank: no Contact to create")
resp = kizen.api.post(
    "/records/client_client/add", json={"fields": [{"name": "email", "value": email}]}
)
if resp.status_code != 201:
    raise Exception(
        f"creating a Contact for {email} failed: {resp.status_code} {resp.text[:200]}"
    )
outputs.customer = resp.json()["id"]
```

A 4xx comes back as a response rather than raising, so the script checks the
status itself. If another run has just created a Contact with that email,
Kizen answers 400 "Entity record email must be unique" and the step fails
instead of linking a duplicate. For what else a script can read and write, see
`kizen docs show code-steps`.

Confirmed live 2026-11-02, on a copy of these files with the object renamed: a
request whose email matched a Contact was linked to it (the search found 1),
and a request with a new email got a new Contact, linked (the search found 0,
the create answered 201). Activate the automation after creating it.

### Running once per record on a schedule

A nightly or weekday job that does something to every matching record is two
automations. A **global** automation with a `schedule` trigger finds the
records, and a `start_automation` step starts a record-based automation on each
one. `start_automation` takes the search result's array directly
(`record_source: automation_variable`), so no loop is needed:

<!-- fixture: examples/first_build/daily_deal_summary.automation.json#/triggers -->
```json
[ { "trigger_type": "schedule", "order": 0,
    "trigger_schedule": {
      "rrule": "DTSTART:20261102T130000Z\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
      "is_advanced": false } } ]
```

The schedule trigger has no time-zone field. Write `DTSTART` in UTC with the
trailing `Z`, the form Kizen's own builder saves, converted from the business's
time zone (in Kizen's business settings). For "every weekday at 7am" in a
US/Central business that is `DTSTART:20261102T130000Z` (7am CST) with
`RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR`. Treat it as a fixed UTC time: across
a daylight-saving change the local run time should move an hour (13:00Z is 8am
CDT). `DTSTART;TZID=US/Central:…` and a `DTSTART` without `Z` also save, but
nothing shows which zone the scheduler gives them, so don't use them. With no
`description` in the spec, Kizen writes one that misreads a `DTSTART` rrule
("Every day starting <save time>"), and `show` prints that, so set
`description` yourself (`"Weekdays at 7am Central"`). Confirmed live 2026-10-30
for save and read-back only. No scheduled run was observed, so the DST
behaviour is RFC 5545's, unconfirmed.

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

`<deals_object_uuid>` is the `id` from `kizen objects get deals --json`. The
started automation is an ordinary record-based one, here writing an AI brief
from the deal's account (see
[Reading a related record's field into a variable](#reading-a-related-records-field-into-a-variable)):

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

**The started automation must be active first.** Kizen rejects a POST or PUT
carrying a `start_automation` whose target is inactive with "Automation not
found", whatever the record source. So `create`, `update`, `steps add` and
`steps edit` refuse it at plan time, naming the target and the `kizen
automations activate` command to run. `show`, `diff`, `roundtrip`, `activate`
and `deactivate` warn instead, because Kizen also refuses a PUT of an
automation whose target was deactivated later. Build the target, activate it,
then create the scheduled one. Confirmed live 2026-10-30: the manual run found
two deals and started the target once on each, and both briefs were written.

---

## Triggers

`kizen automations step-types list` names the wired triggers, and `step-types
show <type>` prints one's `trigger_<type>` block (`email_received`'s is
`trigger_email_received_from_contact`). Any other trigger type is a plan error.

- **`email_received`** works on Contact automations only: on a custom object
  Kizen 400s "only client custom object is allowed" (confirmed live
  2026-10-14). The run gets the Contact, not the email; to read the email, see
  `kizen docs show code-steps`, "Reading a Contact's newest inbound email".
  Saving it and `kizen automations activate` both work (confirmed live
  2026-11-02).
- **`schedule`** is the recurring trigger, and the only time-based one a global
  automation can use. Its `rrule` is a full RFC 5545 string with `DTSTART` in
  UTC: see [Running once per record on a schedule](#running-once-per-record-on-a-schedule).
- **`activity_logged`** and **`scheduled_activity_overdue`** name an activity
  type (`kizen activities list`). A workflow reads what was logged through the
  record fields the activity's fields are linked to: `kizen docs show
  activity`, "Branching on what someone logged". Confirmed live 2026-10-30.
- **`form_submitted`** and **`survey_submitted`** run on any object, but the
  form or survey must be on the automation's `target_object`. Otherwise Kizen
  400s with "Form with ID '…' does not exist.", though the form exists
  (confirmed live 2026-11-02). To tie a form's answers to the record, see
  `kizen docs show form`.
- **`stage_updated`** fires when a record on a pipeline object moves between
  stages. A null or omitted `from_stage`/`to_stage` means any stage, so any
  move is `"trigger_stage_updated": {}`. `fire_on_create` also fires it for a
  record created in `to_stage`, and a move made by another automation fires it
  too. Without a `description`, the trigger gets the UI's text ("Stage Update
  from 'Any Stage' to 'Closed Won'"). Confirmed live 2026-10-30.

**A goal step** waits on the same trigger types and blocks, under
`step_goal.triggers`, e.g. `email_received` to wait for a reply. Its
`wait_type` is `never`, `delay` or `specific_datetime`; `wait_forever` is a
400 (confirmed live 2026-10-14). A goal counts only an event that happens while
the run waits at it, so a `stage_updated` goal misses a move made before the
run got there and stays pending (confirmed live 2026-10-30).

### Webhook trigger auth (confirmed live 2026-10-27)

```json
"trigger_webhook": {
  "name": "partner_feed",
  "auth_type": "http_header",
  "headers": [{"name": "Authorization", "value": "Bearer {{ integration_secret.partner_token }}"}]
}
```

- `auth_type` is `kizen_api` (the default), `http_header`, `basic_auth` or
  `none`. Every type but `kizen_api` adds `?business_id=<uuid>` to
  `webhook_url`.
- With `http_header`, a caller must send every header, each matching exactly,
  and no header name may repeat. Put `{{ integration_secret.<api_name> }}` or
  `{{ business_variable.<name> }}` in the value. Text around the reference is
  sent as written, so `Bearer {{ integration_secret.token }}` expects `Bearer
  <secret>`. A literal value works, but it's stored in plain text and `show
  --json` prints it.
- `basic_auth` takes `secret: {api_name}`, an integration secret whose value
  is `user:pass`. Callers send `Authorization: Basic <base64 of user:pass>`.
- `none` accepts any request. Kizen API headers don't get past `http_header`
  or `basic_auth`; a failed check answers 401 `Invalid webhook credentials.`
- `automations duplicate` keeps each webhook's auth and name. The copy's URL
  uses the copy's api_name.

### Date trigger (`on_or_around_date`)

Fires on a date field of the record, or a number of days before or after it,
at a time of day. This one fires three days before each deal's close date, at
9 am:

<!-- fixture: examples/first_build/closing_soon.automation.json#/triggers/0 -->
```json
{ "trigger_type": "on_or_around_date", "order": 0,
  "trigger_on_or_around_date": { "field_ref": "deals.close_date",
    "date_offset": "days_before", "date_offset_days": 3,
    "time": "09:00", "period": "am" } }
```

- `time` is `hh:mm` on a 12-hour clock, with `period` `am` or `pm`.
  `"09:00:00"` and `"21:00"` are each a 400, so write 9 pm as `"09:00"` with
  `"pm"`. `on_day_and_time` takes the time from the field instead.
- Left out, `date_offset_days` is `0` (1 or more with `days_before` or
  `days_after`), `every_year` `false`,
  `skip_non_working_days` `null` and `past_date_action`
  `proceed_down_met_path` (the others are `proceed_down_not_met_path` and
  `wait_for_not_met_path_delay`).

Confirmed live 2026-10-30: four configs saved and read back as sent, with
those defaults filled in. A date trigger fires only on its date, so no run of
one was observed.

## Steps

`kizen automations step-types list` names the wired step types, and
`step-types show <type>` prints one's block, with entries to copy. `branch`,
`merge_branches`, `loop` and `next_or_finish` are under
[Branch groups](#branch-groups) and [Loops](#loops); `code_step`'s Python side
is in `kizen docs show code-steps`.

### Setting a field, or moving a record to a stage

`change_field_value.specific_field_value` is a bare value, not `{value: …}`.
An option field (dropdown, radio, status, checkboxes, choices, yesnomaybe, a
pipeline's `stage`) takes option labels or UUIDs, and the CLI sends UUIDs.
That is also how an automation moves a record:

```json
{"step_type": "change_field_value",
 "action_change_field_value": {"field_ref": "stage", "specific_field_value": "<stage name>"}}
```

There's no move-stage step: Kizen's API lists `update_pipeline_status`, but
its builder doesn't offer one, and the CLI refuses it. The move writes the same
timeline entry and stage history as a manual move (confirmed live 2026-10-30).
To react to a move, use the `stage_updated` trigger.

### `plugin_code_step`

Runs a step from an installed plugin's manifest:

```json
{ "step_type": "plugin_code_step", "key": "summarize", "parent_key": null,
  "action_plugin_code_step": {
    "plugin_app_api_name": "example_plugin",
    "action_step_api_name": "summarize_records",
    "notify_plugin_developer_on_failure": false,
    "inputs":  [ { "name": "heading", "input_type": "field",
                   "field_ref": "client_client.first_name" },
                 { "name": "settings", "input_type": "business_plugin_config" } ],
    "outputs": [ { "name": "records_json", "output_type": "variable",
                   "variable_name": "context_text" } ],
    "secrets": ["api_key"] } }
```

- `plugin_app_api_name` is the **installed** plugin's api_name, as
  `business-plugin-apps` lists it. A preview install is
  `<api_name>_preview_<branch>` (confirmed live 2026-10-05).
  `action_step_api_name` names the step in that plugin's manifest. `GET
  /external-integrations/bootstrap` lists each install's steps under
  `automation_action_configs` (confirmed live 2026-10-14).
- Kizen checks the names and the bindings against the manifest. An api_name
  with no install (a catalog plugin that isn't installed included), a step
  the plugin doesn't ship, and a missing required input or output are each a
  400 naming the key (confirmed live 2026-10-14).
- `inputs`, `outputs` and `secrets` take the `code_step` shapes. `inputs` also
  takes `input_type: business_plugin_config`, for plugin steps only. What
  the step receives through it isn't confirmed live.
- `preview_user_id` is the user whose preview of a business app the step
  runs. Null, the default, runs the installed version. A non-null id on a
  plugin that isn't a business app is a 400, "Previews are only available
  for business apps" (confirmed live 2026-11-11). An `update` keeps the live
  value when the spec leaves it out; `null` unpins it.
- `activate`, `deactivate`, `steps` edits to other steps and `roundtrip
  --execute` keep a plugin step's inputs, outputs and secrets intact
  (confirmed live 2026-10-14).

### `create_related_entity`

Every key, choice and common shape: `kizen automations step-types show create_related_entity`.

A step that leaves out `context_entity_field_resolution` gets `add_only`
when `context_entity_field` is to-many, and `overwrite` otherwise. On a
to-many field, `overwrite` replaces the field's list with the new record, so
every record already related is unlinked; `add_only` keeps them (confirmed
live 2026-11-16). `--dry-run` lists the planned value under `defaults`. An
`update` keeps a live step's value.

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
        "context_entity_field": "patients.mrn" }
    ]
  } }
```

- `automation_target_relationship_fields` is the hop chain from
  `target_object` to `object_to_modify`, each an `"object.field"` ref or a
  field UUID. `relationship_field_ref` is a one-hop shorthand for it.
- `record_source` is `related_object_field` (the default, which needs the hop
  chain) or `automation_variable`. For `automation_variable`, set
  `record_source_variables` to the names of **non-array** entity variables
  (an array variable 400s) and `automation_target_relationship_fields: []`.
  Confirmed live 2026-10-06.
- `fields_to_modify` items take `change_field_value`'s keys, with
  `value_type` in place of `change_type`. The wrong one gets a 400 that
  reads like another problem: "specific_field_value should be passed".
- `field_to_modify` names a field on `object_to_modify`, but
  `context_entity_field` names one on the automation's own `target_object`.
- `field_value_mappings` copies an option field between two objects whose
  option UUIDs differ: `[{"source_values": [<uuid>], "target_values":
  [<uuid>], "order": 0}, …]`, one source option to one target option, tried
  in `order`.

### Extraction input

`file_content_extraction` and `audio_transcription` read their file from
exactly one of `input_field_ref` and `input_value_source`:

```json
{ "input_field_ref": "claims.claim_attachment" }
{ "input_value_source": { "type": "related_object_field",
                          "relationship_field": "claims.policy",
                          "field": "policies.policy_document" } }
{ "input_value_source": { "type": "automation_variable", "variable": "claim_file" } }
{ "input_value_source": { "type": "context_entity_field", "field": "claims.claim_attachment" } }
```

- `input_field_ref` is a file field on the automation's own record.
- `related_object_field` reads a file field on a related record:
  `relationship_field` is the hop on `target_object`, `field` the file field
  on the related object. **The hop must be single-valued**; a to-many hop
  400s with `Relationship field must be single-valued`.
- `automation_variable` reads the file from the variable `variable` names.
- `steps get` prints the input in a form that re-applies as it is.

Confirmed live 2026-10-14. The builder UI offers only fields on the record as
a source, and an automation with a related-record or variable source does not
open in the UI editor: it returns to the automation list.

### LLM & extraction destinations

`call_llm`, `file_content_extraction` and `audio_transcription` write their
results through a `destinations` list:

```json
{ "destinations": [
    { "field_ref": "patients.mrn" },
    { "variable": "extracted_mrn" },
    { "related_object_field": "encounters.reason_code_icd10",
      "relationship_field_ref": "risk_results.risk_result_encounter" }
] }
```

- `field_ref` names a field on the automation's own `target_object`, and
  `variable` an automation variable.
- `related_object_field` names a field on a related record, and
  `relationship_field_ref` the relationship field on `target_object` that
  reaches it (one hop, confirmed live 2026-07-27). Leave the hop out and the
  CLI uses the only relationship field between the two objects, or refuses
  when there are none or several.
- A choice-type destination gets its `options` from the field. `is_required`
  and `confidence_threshold` (extraction only) default to `false` and `0.7`.
- **Several destinations in one step need `is_advanced: true`.** Without it
  Kizen 400s "Multiple destinations are not allowed for non-advanced LLM call
  actions", on `file_content_extraction` and `audio_transcription` too
  (confirmed live 2026-11-17). With it, one `call_llm` can write a field on `target_object` and a
  field on a related object in the same call (confirmed live).

**The model.** `kizen automations llm-models` lists every `model_name` the
business can use, which LLM steps each supports, and what replaces a
deprecated one. `gemini/…` and `openai/…` models need the prefix; Claude
models are bare (`claude-3-7-sonnet-20250219`). Leave `business_plugin_app_id`
out: the CLI fills it from that catalog for every model, Kizen AI included, so
the step reads back like one saved in the Kizen UI (confirmed live
2026-10-30).

**`merge_field_validation`** says what a blank merge field in the prompt does
at run time. It's the same on `call_llm`, `file_content_extraction`,
`audio_transcription` and a condition's `llm_decision`, confirmed live against
the builder UI's dropdown:

| Wire value | UI label |
|---|---|
| `error_if_required` | "Error if any merge field is blank" (the default for a new step) |
| `default_to_unknown` | "Default to 'Unknown'": fixed text, not configurable |
| `blank` | "Leave Blank" |

Give `prompt`, and the CLI derives the `html_prompt` the UI's editor shows. An
explicit `html_prompt` wins.

**Tools (MCP server).** A `call_llm` step can let the model call an MCP
server's tools. Confirmed live 2026-10-27:

| Key | Value |
|---|---|
| `mcp_server` | the server's `api_name` or UUID |
| `run_tools_as` | the service account's `name` or UUID |
| `max_sequential_turns` | 1–32767, default 25 |
| `tool_failure_policy` | `never_fail`, the only value Kizen accepts |

- Set `mcp_server` and `run_tools_as` together. A plugin's own service account
  can't run the tools, and a name two accounts share needs the UUID.
- A model without tool calling 400s with "The selected model does not support
  tool calling". The model catalog has no flag for it.
- To remove the server, set `mcp_server` (or `run_tools_as`) to `null`: both
  go.

#### Searching a knowledge catalog

An AI step searches the business's knowledge catalogs through a server that
serves the catalog tools. Confirmed live 2026-11-02:

- **The server** is the system `readonly` server, or any server whose
  `tool_names` include `listCatalogs`, `searchCatalog` and `getDocument` (see
  `kizen docs show mcp-server`).
- **`run_tools_as`** is Kizen's own automation account, named "Agentic
  Workflow Code" where this was run. If that name isn't valid on a business,
  `--dry-run` names the accounts that are.
- **`kizen/pro`** supports tool calling.
- `listCatalogs` returns the default catalog, "Business Knowledge", api_name
  `business_knowledge` (confirmed live 2026-10-30). `searchCatalog` takes
  `query`, `n` and `catalog`; the model may leave `catalog` null.
- **To see what each search returned**, run `kizen automations runs view
  <execution_id> --json`. The step's `detailed_log.tool_calls` lists every call
  with its `arguments` and `result`, and `tool_summary` counts them.

This drafts an answer to a support request from the catalog, started by hand on
a request. The prompt reads the request's `subject` field:

<!-- fixture: examples/support_desk/draft_reply.automation.json#/triggers -->
```json
[ { "trigger_type": "manual", "order": 0, "trigger_manual": {} } ]
```

<!-- fixture: examples/support_desk/draft_reply.automation.json#/steps/0 -->
```json
{ "key": "answer", "parent_key": null, "step_type": "initialize_variable", "order": 0,
  "action_initialize_variable": { "sources": [],
    "variable": { "name": "draft_answer", "data_type": "string" } } }
```

<!-- fixture: examples/support_desk/draft_reply.automation.json#/steps/1 -->
```json
{ "key": "draft", "parent_key": "answer", "step_type": "call_llm", "order": 1,
  "action_call_llm": {
    "model_name": "kizen/pro",
    "mcp_server": "readonly",
    "run_tools_as": "Agentic Workflow Code",
    "merge_field_validation": "default_to_unknown",
    "prompt": "Search the knowledge catalog with searchCatalog for this support request, then draft a short answer from what you find. End with the titles of the documents you used, or say that none matched.\nRequest subject: {{ custom_objects.subject }}",
    "destinations": [ { "variable": "draft_answer" } ] } }
```

Confirmed live 2026-11-02, on a copy of this file: the model called
`searchCatalog` twice and wrote its draft to `draft_answer`.

### An AI step reads an emailed reply

The `email_received` trigger hands the run the Contact but not the email, so a
code step reads the newest inbound email from the Contact's timeline into a
variable, and the AI step's prompt quotes that variable. The script, the calls
it makes and the inboxes the trigger watches are in `kizen docs show
code-steps`, "Reading a Contact's newest inbound email".

The automation runs on Contacts, when an email arrives or by hand:

<!-- fixture: examples/support_desk/read_reply.automation.json#/triggers -->
```json
[ { "trigger_type": "manual", "order": 0, "trigger_manual": {} },
  { "trigger_type": "email_received", "order": 1,
    "trigger_email_received_from_contact": { "receiving_inbox": "any" } } ]
```

Two string variables hold the body and the summary:

<!-- fixture: examples/support_desk/read_reply.automation.json#/steps/0 -->
```json
{ "key": "body", "parent_key": null, "step_type": "initialize_variable", "order": 0,
  "action_initialize_variable": { "sources": [],
    "variable": { "name": "email_body", "data_type": "string" } } }
```

<!-- fixture: examples/support_desk/read_reply.automation.json#/steps/1 -->
```json
{ "key": "summary", "parent_key": "body", "step_type": "initialize_variable", "order": 1,
  "action_initialize_variable": { "sources": [],
    "variable": { "name": "reply_summary", "data_type": "string" } } }
```

The code step's input is the Contact's `email` field, because `{{ contact.id
}}` and the other Contact merge fields arrive empty (see [Merge-field namespace
tokens by step type](#merge-field-namespace-tokens-by-step-type)). Its output
writes the variable:

<!-- fixture: examples/support_desk/read_reply.automation.json#/steps/2 -->
```json
{ "key": "read_email", "parent_key": "summary", "step_type": "code_step", "order": 2,
  "action_code_step": {
    "script": "# Read the newest email this Contact sent to a connected inbox, as plain text.\nimport html\nimport re\n\n\ndef ok(resp, what):\n    if resp.status_code != 200:\n        raise Exception(f\"{what} failed: {resp.status_code} {resp.text[:200]}\")\n    return resp.json()\n\n\nemail = (inputs.contact_email or \"\").strip()\nif not email:\n    raise Exception(\"the Contact has no email to look it up by\")\nmatch = {\"type\": \"fields\", \"subtype\": \"non_custom\", \"field\": \"email\"}\nquery = [{\"and\": True, \"filters\": [{**match, \"condition\": \"=\", \"value\": email}]}]\nfound = ok(\n    kizen.api.post(\n        \"/records/client_client/search\",\n        json={\"query\": query, \"and\": True, \"field_names\": [\"email\"]},\n    ),\n    \"searching Contacts\",\n)[\"results\"]\nif len(found) != 1:\n    raise Exception(f\"{len(found)} Contacts have email {email}, not 1\")\n\nevents = ok(\n    kizen.api.get(\n        f\"/records/client_client/{found[0]['id']}/timeline\", params={\"size\": 50}\n    ),\n    \"reading the timeline\",\n)[\"events\"]\ninbound = [e for e in events if e[\"type_name\"] == \"integrated_inbox_email_received\"]\noutputs.email_body = \"\"\nif not inbound:\n    outputs.log(f\"no inbound email in the Contact's last {len(events)} timeline events\")\nelse:\n    data = max(inbound, key=lambda e: e[\"created\"])[\"data\"]\n    sid = data.get(\"synced_email_id\") or data.get(\"syncedEmailId\")\n    if not sid:\n        raise Exception(f\"no synced email id in the event's data: {sorted(data)}\")\n    body = ok(kizen.api.get(f\"/synced-email/{sid}/full_response\"), f\"reading {sid}\")\n    text = body.get(\"text_plain\") or \"\"\n    if not text.strip():\n        markup = body.get(\"text_html\") or \"\"\n        markup = re.sub(r\"(?is)<(script|style).*?</\\1>\", \"\", markup)\n        markup = re.sub(r\"(?i)<br\\s*/?>|</p>|</div>|</li>\", \"\\n\", markup)\n        text = html.unescape(re.sub(r\"<[^>]+>\", \"\", markup))\n    outputs.email_body = text.strip()\n    outputs.log(f\"read email {sid}: {len(outputs.email_body)} characters\")",
    "inputs": [ { "name": "contact_email", "input_type": "field",
                  "field_ref": "client_client.email", "data_type": "string" } ],
    "outputs": [ { "name": "email_body", "output_type": "variable",
                   "variable_name": "email_body", "data_type": "string" } ] } }
```

The prompt reads the body as `{{ automation_variable.email_body }}`.
`default_to_unknown` lets the step run when no email was found and the body is
empty; Kizen sent the prompt with `unknown` in its place (confirmed live
2026-11-02):

<!-- fixture: examples/support_desk/read_reply.automation.json#/steps/3 -->
```json
{ "key": "summarize", "parent_key": "read_email", "step_type": "call_llm", "order": 3,
  "action_call_llm": {
    "model_name": "kizen/lite",
    "merge_field_validation": "default_to_unknown",
    "prompt": "Summarize this customer's email reply in one sentence. If the reply is Unknown or empty, answer: No reply to summarize.\nReply: {{ automation_variable.email_body }}",
    "destinations": [ { "variable": "reply_summary" } ] } }
```

Confirmed live 2026-11-02, on a copy of this file: it saved, activated, and a
run started by hand on a Contact with no inbound email completed. The code step
logged that it found no inbound email, and `reply_summary` was set to "No
reply to summarize."

### Conditions

A condition's `filter_config` uses the filter DSL (`kizen docs show
filters`): fields are **bare api_names**, and ops depend on the field type
(`kizen filters ops [<field_type>]`). Every shape, a variable compared to a
static value and a field compared to a variable included, is in `kizen
automations step-types show condition`. Kizen's editor lays out a step's rules
only when every rule carries its layout, and the plan warns about each rule
saved without one.

### Reading a related record's field into a variable

A `field_value` source with `source_subtype: primary_related_object_field`
reads a field off a related record. Both keys are raw UUIDs, not
`"object.field"` refs: `relationship_field` is the relationship field on
`target_object`, and `field` the field on the object it points to.

This reads the industry of the deal's account. The tokens are the `id`s of
`deals.account` and `accounts.industry` in `kizen objects get deals --json` and
`kizen objects get accounts --json`. A dropdown arrives as its option label
(`Insurance`). Confirmed live 2026-10-30:

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

**Set a date field to today** with a `trigger_time` source, which holds the
run's start date, then write the variable with `change_type: variable`.
Confirmed live 2026-10-30:

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

A merge field is not a bare `{{ <namespace>.<field_api_name> }}` token — the
planner (and Kizen's builder UI) always wraps it in a
`<span class="kzn-merge-field" data-merge-field-fallback-label="…"
data-merge-field-relationship="…" [data-merge-field-objectname="…"]>{{ … }}</span>`
marker; the bare token by itself is inert text.
`kizen docs show email-templates` has the same convention for email `Text`
blocks and dashboard static text.

The namespace token varies **by which step type's content field it's in** —
this isn't one global convention:

| Step type | Namespace for the trigger record's own fields |
|-----------|------------------------------------------------|
| `notify_member_via_email`, `notify_member_via_text`, `send_related_contact_email`, `send_related_contact_text` | `entity_record.<field>` |
| `call_llm` (`prompt`), `schedule_activity` (`note`), `initialize_variable`/`update_variable` ("static" sources) | `custom_objects.<field>` — a **literal token**, regardless of the target object's real api_name |

Two tokens render empty rather than fail (confirmed live 2026-10-30):

- `entity_record.<field>` in a `schedule_activity` note.
- A hop to a related record, `{{ custom_objects.<relationship>.<field> }}`, in
  a `call_llm` prompt, even with `merge_field_validation: default_to_unknown`.

To use a related record's field, copy it into a variable first (see
[Reading a related record's field into a variable](#reading-a-related-records-field-into-a-variable)),
then write `{{ automation_variable.<name> }}`. This prompt reads the deal's own
call notes and its account's industry, and Kizen sent it with both filled in
(confirmed live 2026-10-30):

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

The destination is a dropdown, so the CLI fills its `options` and the model's
answer sets one of them.

On a Contacts automation, `{{ contact.id }}`, `{{ contact.email }}` and
`{{ entity_record.id }}` in a static variable source save, but the variable
arrives empty (`None` in a code step), and `{{ custom_objects.id }}` there is a
400 on save. To use the Contact's own field in a code step, bind it as a
`field` input instead (confirmed live 2026-10-30).

Other reserved namespaces (not real object fields, no API-queryable
catalog, never carry `data-merge-field-objectname`): `team_member.<field>`
(the notified team member's own fields), `business.<field>` (tenant
settings), `contact.<field>`, `automation_variable.<name>`,
`automation_history.<field>`, `integration_secret.<api_name>` and
`business_variable.<name>` (webhook header values; a secret's label is its
description), and `entity_record` pseudo-fields like
`link_url`/`created`/`estimated_close_date`. Any OTHER namespace is treated
as a real custom object's own api_name, and its span gets
`data-merge-field-objectname` holding that object's display name.

### Email steps: build order

A `notify_member_via_email` step points at an automation message, and a
message belongs to one automation. So the order is fixed:

1. **A template.** A blank business may have none: `kizen messages templates
   list`, then `kizen messages templates create --spec-file <f>` (`kizen docs
   show email-templates`).
2. **The automation, without the email step.** An automation with `"steps":
   []` saves.
3. **The message:** `kizen messages create <automation> --template <name> --yes
   --json | jq -r '.results[0].server_uuid'`. A message built from raw content
   instead shows as *unselected* in the UI.
4. **The step**, with that id as `email_template_id`, through `kizen
   automations update --spec-file`:

<!-- fixture: examples/first_build/closing_soon.automation.json#/steps/0 -->
```json
{ "key": "tell_owner", "parent_key": null, "step_type": "notify_member_via_email",
  "order": 0,
  "action_notify_member_via_email": {
    "team_member": { "type": "owner" },
    "email_template_id": "<closing_soon_message_uuid>" } }
```

- `send_related_contact_email` takes the message as `email: {"id": <id>}`.
- To email whoever a team-selector field names, set `team_member` (or
  `cc_team_member`) to `{"type": "team_selector_field", "field_ref":
  "<object>.<field>"}`. The CLI sends the field's UUID as `field_id`
  (confirmed live 2026-11-16).
- **Messages outlive their automation.** Deleting the automation leaves its
  messages bound to the deleted id, and Kizen answers a `DELETE` of one with
  405. A recreated automation can't use them, so it needs new ones (confirmed
  live 2026-10-30).

Confirmed live 2026-10-30, in this order.

### Stopping a run

`stop_execution`'s `action` sets how the run ends, and two of the five don't
stop it. Confirmed live 2026-07-27, against the builder UI's "Action Options":

| `action` | UI label | What it does |
|---|---|---|
| `stop_and_fail` | Stop and mark as Failed | stops, run shows Failed |
| `stop_and_complete` | Stop and mark as Successfully Completed | stops, run shows Completed |
| `stop_and_cancel` | Stop and mark as Cancelled | stops, run shows Cancelled |
| `pause_and_error` | Pause and Error | **pauses**, shows Error |
| `pause` | Pause | **pauses** |

`"notify": true` also sends the matching notification. A step without
`action` gets `stop_and_complete`. Kizen accepts a null `action`, but that
step never finishes and the editor shows it as "Unknown" (confirmed live
2026-10-30). `translate`, `steps get` and `roundtrip` show and resend a saved
`null` as it is; fix one with `steps edit`.

## Variables

- **Declare every variable at the front.** Each new variable is an
  `initialize_variable` step on the unbranched chain from the root, before any
  other step. Steps inside a branch can read and update a variable but not
  declare one. A variable the UI creates inline from a step's picker is the
  same: a root-chain `initialize_variable` with `sources: []` (confirmed live
  2026-09-28).
- **An `entity` variable** names its records' object in `data_subtype`, as an
  api_name or UUID.
- **Arrays.** A `string`, `uuid` or `entity` variable can be an array: set
  `is_array: true` on the variable, plus `array_aggregation_mode` (`add_all`
  or `add_first_non_blank`) and `deduplicate_array` (bool) on the step. In the
  builder those are "Choose Source Handling" (*Add All Sources* / *First
  Non-Blank Source Only*) and "Unique Values Only" (confirmed live
  2026-10-14). A static array value is a `static` source with
  `source_subtype` (`string`/`uuid`), a list `value` and `array_separator`.
  Give `value` as a list: a single string such as `"a\nb"` loses its
  separator on read-back, and `diff` then shows a change (confirmed live
  2026-09-29).
- **`value_resolution` on `update_variable`** says how the sources combine
  with an array variable's current value. It works on array variables and on
  `update_variable` only. Confirmed live 2026-10-14, starting from `[a]` with
  `deduplicate_array: false`:

  | `value_resolution` | sources `[b]` | sources empty |
  |---|---|---|
  | `add_only` | `[a, b]` (and `[a, c]` gives `[a, a, c]`) | not observed |
  | `overwrite` | `[b]` | `[]` |
  | `overwrite_except_null` | `[b]` | `[a]`: kept |
  | null / omitted | `[b]` | `[]` |

  An update that leaves it out keeps the stored value, so deleting the key
  from a spec doesn't clear it: set `overwrite`, which behaves like null. The
  builder UI has no control for it and doesn't show a stored value (as of
  2026-10-14).

## What an update keeps

A step or trigger keeps its identity, its run history and the settings below
only when it carries its `id` from a live read (`kizen automations show`).
Without one it's new: it gets a fresh id, orphaning the old one's history
(confirmed live 2026-08-10), and Kizen's defaults for whatever the spec leaves
out.

An update replaces the whole automation, so `automations update` fills in what
the spec leaves out from the live one:

- `active` (a spec that doesn't mention it never turns a running automation
  off), `folder`, `skip_non_working_days` and each webhook trigger's auth.
- On a step or trigger whose `id` matches: its descriptions, its skip and
  failure settings, and the per-type settings a Kizen PUT would otherwise
  reset, such as an LLM step's `merge_field_validation` and MCP server, a
  stop's `action`, a plugin step's `preview_user_id`, and a
  `schedule_activity`'s `target_variable`.

`--dry-run` names those steps and triggers as `keeps_live=[<key>, …]`. An
explicit value always wins, `""` and `false` included; `description: ""`
gets the type's default text.

## Scheduling activities and assigning owners

### `schedule_activity.note` and `html_note`

Kizen stores the two separately, and a PUT without `html_note` clears it
while `note` survives. Set both when you want the note in both forms.
Confirmed live 2026-10-06. The note's merge fields use the `custom_objects`
namespace (see [Merge-field namespace tokens by step type](#merge-field-namespace-tokens-by-step-type)).

### When a scheduled activity is due

`schedule.type` is `immediately`, `with_delay` or `on_or_around_date`, the
last two with a block of the same name; `kizen automations step-types show
schedule_activity` lists each block's choices and defaults.
`skip_non_working_days` sits on `schedule` itself. This step schedules a
Follow Up for the deal's owner two working days after the run:

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

- Started on a Friday at 19:48 UTC, the step above was due the next Tuesday at
  19:48 UTC: a `day` delay with `skip_non_working_days` counts working days
  and keeps the run's time of day.
- A `specific_time` takes `time` on a 24-hour clock: `"21:00"` saves, unlike
  the date trigger's 12-hour `time`.
- `{"field_id": "<start date field uuid>", "date_offset": "days_after",
  "date_offset_days": 1, "time_offset": "specific_time", "time": "10:00"}` on a
  record starting 2026-11-16 was due 2026-11-17 at 10:00 in the business's
  time zone. `on_day_and_time` takes the time from the field and ignores
  `time_offset`. Confirmed live 2026-11-16.

Confirmed live 2026-10-30.

### `schedule_activity.target_variable` and `variable_field_resolution`

`target_variable` names a String or UUID automation variable that receives
the scheduled activity's id. `variable_field_resolution` is `overwrite` (the
default) or `update_if_blank`, which leaves a variable that already holds a
value alone; the activity is still scheduled. Confirmed live 2026-10-30,
including runs: `overwrite` replaced a seeded value with the new activity's
id, and `update_if_blank` kept it.

An explicit `"target_variable": null` unbinds the variable. If an update
removes the bound variable from `variables`, send that `null` too: otherwise
the kept name points at a variable that no longer exists, and Kizen refuses
the PUT with `HTTP 400 … variable '<name>' does not exist.`

### `schedule_activity.association_configs`

For an activity type tied to more than one object, these rules bite:

- **`context_record` is only valid on the entry for the automation's own
  `target_object`**; elsewhere it's a 400.
- **The server adds one entry per object the activity type is associated
  with**, `context_record` for the automation's own object and `none` for the
  others, so you needn't author them. A block copied from `steps get` already
  carries them and re-applies unchanged (confirmed live 2026-10-14).
- **`related_field` must be a relationship field on the associated object
  itself**; one from the automation's own `target_object` 400s with
  *"Relationship field … not found on source object"*. Resolution rules
  beyond that are unconfirmed.

### `assign_team_member`

Every `type` and the keys each needs: `kizen automations step-types show
assign_team_member`. The plan checks them, and Kizen enforces the same rules
(confirmed live 2026-10-16).

**A complete selector only means the keys are right.** It doesn't mean a run
assigns anyone:

- **The step writes only this record's `owner`.** It never writes to a related
  record. `field_id` and `related_field_id` name the source the owner is copied
  from (confirmed live 2026-08-13).
- **`team_member` reports `completed` and leaves `owner` unchanged.** `updated`
  still bumps. This was reproduced on two objects, on never-touched records
  (confirmed live 2026-08-13). It is platform behaviour, and no spec change
  fixes it. The `round_robin_*` types weren't tested.
- **The selector types work on a record's first run.**
  `related_team_selector_field` copied the far record's selector into this
  record's `owner` (confirmed live 2026-08-13). Whether `field_id` may name a
  team-selector field other than the far object's `owner` is unconfirmed.
- **A re-run on a record the automation already processed silently no-ops**,
  even when the source changed (confirmed live 2026-08-13). It looks like a
  per-(automation, record) guard. Test against a fresh record each time.
- **An empty source fails the step, not the run.** The step's
  `detailed_log.reasons` says *"No employee found for team_selector_field:
  <uuid>"*, or *"No value set for related_field_id: <uuid>"* when the hop is
  empty. The run still reports `completed` (confirmed live 2026-10-16).
- **`updated` bumps on every run, assigned or not**, so it is no evidence
  either way. Read `owner` back.

The reliable way to set an owner today is `kizen records update <object> <id>
-f owner=<uuid>`, or a `change_field_value` step on `owner`.

## Debugging

### A 400 from plan-apply

Most spec mistakes never get this far: `--dry-run` lists them first, as
`spec error:` lines on stderr, or with `--json` as
`{"ok": false, "errors": [...], "warnings": [...]}` on stdout. Each finding
has `message`, `path`, and where they apply `step`, `index`, `got`,
`allowed`, `suggestion` and `hint` (the command to run).

The server's error is nested: `{step_key: {action_block_name: {field_name: [message]}}}`.
Work inside-out: which step, which config block, which wire field. Common
causes:

- `null` for a non-nullable field.
- `filter_config` structure wrong: check the query group `id`, the `filters`
  key, and that no `value` is null.
- A destination field of the wrong type: `wysiwyg`, `files` and `timezone`
  fields can't be extraction destinations.

### A 500 from the PUT

If the 500 persists whatever the payload, the automation itself may be in a
bad state: the tell is that it *executes* correctly when triggered but every
PUT returns 500. That needs a server-side repair, not a payload change, so
escalate to Kizen.

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
- `kizen docs show activity` — the activity types `activity_logged` and
  `schedule_activity` name, and branching on what someone logged.

# Automation patterns

Worked automations that combine several steps. The graph rules are in `kizen
docs show automation`; each step's own rules print with `kizen automations
step-types show <type>`.

## Finding one record, or every record a search finds

Use this when a step needs a record on another object.

**One record:** `find_single_record` searches, sorts by `sort_field`, and
writes the first match to a variable or a field: a relationship field on the
record, on a related record, or on a record variable's record.
With `when_no_records_found: blank` the run continues when nothing matches,
and under the default `conflict_resolution: overwrite` it clears the
destination; `update_if_blank` and `overwrite_except_null` keep what was there.
`when_multiple_records_found` picks between the first match, an error and a
blank. `kizen automations step-types show find_single_record` has
the shapes.

**Every record:** `search_records` writes an **entity array**, even when one
record matches. An array can be written only into a to-many relationship
field: into a to-one it's a 400 ("Variable can't be cased to a field"). To act
on each record, loop over the array and use the item variable in the body.
`change_field_value` with `change_type: variable` sets a relationship field to
it, and `change_type: entity_variable_field` with `related_object_field` (a
field UUID on the found record's object) copies one of its fields. Confirmed
live 2026-11-02: a search that found two records ran the loop twice.

## Find or create a Contact by email

Use this to link a record to the Contact with a given email, creating the
Contact when there is none.

The example uses a support request; the `support_requests` object and its
fields are in `kizen docs show form`.

- A filter value takes no merge fields, so the search compares the Contact's
  `email` to a string variable copied from the request.
- `find_single_record` links the Contact it finds through the request's
  `customer` field. With `when_no_records_found: blank` a request with a new
  email continues with `customer` blank.
- `create_related_entity` can't create a Contact (confirmed live 2026-10-30),
  so on the condition's yes branch a code step posts to
  `/records/client_client/add`, and its `field` output links the new Contact.
  The empty no branch ends the run.

<!-- fixture: examples/support_desk/find_contact.automation.json#/triggers -->
```json
[ { "trigger_type": "new_entity_created", "order": 0,
    "trigger_new_entity_created": { "action": "create_only" } } ]
```

<!-- fixture: examples/support_desk/find_contact.automation.json#/steps -->
```json
[ { "key": "email", "parent_key": null, "step_type": "initialize_variable", "order": 0,
    "action_initialize_variable": {
      "sources": [ { "source_type": "static", "source_subtype": "string",
                     "value": "{{ custom_objects.customer_email }}" } ],
      "variable": { "name": "customer_email", "data_type": "string" } } },
  { "key": "find", "parent_key": "email", "step_type": "find_single_record", "order": 1,
    "action_find_single_record": { "custom_object": "client_client",
      "filter_type": "custom_filter",
      "filter_config": { "all": [ { "field": "email", "op": "=", "variable": "customer_email" } ] },
      "sort_field": "email", "sort_direction": "asc",
      "when_no_records_found": "blank",
      "destination": { "type": "context_entity_field", "field": "support_requests.customer" } } },
  { "key": "no_customer", "parent_key": "find", "step_type": "condition", "order": 2,
    "step_condition": { "type": "custom_filter",
      "filter_config": { "all": [ { "field": "customer", "op": "is_blank" } ] } } },
  { "key": "create_contact", "parent_key": "no_customer", "parent_branch": "yes",
    "step_type": "code_step", "order": 3,
    "action_code_step": {
      "script": "# Create a Contact for an email no Contact has yet; the output links it.\nemail = (inputs.customer_email or \"\").strip()\nif not email:\n    raise Exception(\"customer_email is blank: no Contact to create\")\nresp = kizen.api.post(\n    \"/records/client_client/add\", json={\"fields\": [{\"name\": \"email\", \"value\": email}]}\n)\nif resp.status_code != 201:\n    raise Exception(\n        f\"creating a Contact for {email} failed: {resp.status_code} {resp.text[:200]}\"\n    )\noutputs.customer = resp.json()[\"id\"]",
      "inputs": [ { "name": "customer_email", "input_type": "field",
                    "field_ref": "support_requests.customer_email", "data_type": "string" } ],
      "outputs": [ { "name": "customer", "output_type": "field",
                     "field_ref": "support_requests.customer" } ] } } ]
```

Kizen refuses a second Contact with the same email (400 "Entity record email
must be unique", confirmed live 2026-10-30), so a run that races another fails
the step instead of linking a duplicate.

Confirmed live 2026-11-25, on a copy with the object renamed: a request whose
email matched a Contact was linked to it, and a new email got a new Contact,
linked. Activate the automation after creating it.

## Reading a related record's field into a variable

Use this when a step needs a field from a related record: a merge field can't
hop, so copy the value into a variable first.

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

Then write `{{ automation_variable.<name> }}`. Kizen sent this prompt with
both values filled in (confirmed live 2026-10-30):

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

## Running once per record on a schedule

Use this for a job that does something to every matching record on a
schedule.

A job that does something to every matching record is two automations. A
**global** one with a `schedule` trigger finds the records, and a
`start_automation` step with `record_source: automation_variable` starts a
record-based one on each, with no loop. The rrule is UTC: `kizen automations
step-types show schedule`.

<!-- fixture: examples/first_build/daily_deal_summary.automation.json#/triggers -->
```json
[ { "trigger_type": "schedule", "order": 0,
    "trigger_schedule": {
      "rrule": "DTSTART:20261102T130000Z\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
      "is_advanced": false } } ]
```

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

## Single-step examples

Entries the step-type cards point at. Each step's rules are on its card.

### A date trigger

This fires three days before each deal's close date, at 9 am:

<!-- fixture: examples/first_build/closing_soon.automation.json#/triggers/0 -->
```json
{ "trigger_type": "on_or_around_date", "order": 0,
  "trigger_on_or_around_date": { "field_ref": "deals.close_date",
    "date_offset": "days_before", "date_offset_days": 3,
    "time": "09:00", "period": "am" } }
```

### An email step

The fourth step of the build order on `kizen automations step-types show
notify_member_via_email`:

<!-- fixture: examples/first_build/closing_soon.automation.json#/steps/0 -->
```json
{ "key": "tell_owner", "parent_key": null, "step_type": "notify_member_via_email",
  "order": 0,
  "action_notify_member_via_email": {
    "team_member": { "type": "owner" },
    "email_template_id": "<closing_soon_message_uuid>" } }
```

### A scheduled follow-up

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

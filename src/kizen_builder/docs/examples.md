# Worked example: object → activity → automation → dashboard

Every other topic under `kizen docs show` covers **one** surface. The gap this
topic closes is the connective tissue *between* surfaces: how a field's UUID
flows into a dashlet's `config`, how an object api_name gets pluralized on
create and has to be read back before the next command can use it, how an
activity type's UUID differs from the custom-object UUID it's logged against.
Every command below was run for real against a disposable Kizen business;
every finding below carries `confirmed live 2026-08-13`.

This is one example, not a catalog: a **field-service ticketing** domain,
picked because it's structurally boring and carries no resemblance to any real
customer's data. Four entities, wired together in the order you'd actually
build them:

1. a custom object (`service_ticket`) with fields
2. an **activity type** (`Site Visit`) logged against it
3. an automation with a branch, a merge, and a `code_step`
4. a dashboard, generated the way `kizen docs show dashboard` recommends

Each JSON block below is a byte-for-byte copy of a committed fixture under
`tests/fixtures/examples/service_ticket/` — `tests/test_examples_doc_matches_fixtures.py`
fails if this doc and that fixture ever diverge. `tests/drift/test_worked_examples.py`
(marked `pytest.mark.drift`) applies the same fixtures against a real
environment end to end.

Every annotation below names the surface topic that owns the underlying fact
rather than restating it — `kizen docs show <topic>` for the full story.

---

## 1. The object

`objects create`/`categories create` are flag-driven (`kizen docs show
objects`) — there's no spec file for either, so the "spec" here is the flags
themselves, captured once as a fixture so the doc can't quietly drift from
what's actually run:

<!-- fixture: examples/service_ticket/object.json -->
```json
{
  "api_name": "service_ticket",
  "name": "Service Tickets",
  "entity_name": "Service Ticket",
  "description": "A field-service work order. The worked example's primary object.",
  "categories": [
    { "api_name": "ticket_details", "name": "Ticket Details" }
  ]
}
```

```bash
kizen objects create --api-name service_ticket --name "Service Tickets" \
  --entity-name "Service Ticket" \
  --description "A field-service work order. The worked example's primary object." \
  --dry-run
kizen objects create --api-name service_ticket --name "Service Tickets" \
  --entity-name "Service Ticket" \
  --description "A field-service work order. The worked example's primary object." \
  --yes
```

**Kizen pluralizes the api_name on create — `confirmed live 2026-08-13`.**
`service_ticket` came back as `service_tickets`. This is `objects.md`'s own
documented gotcha ("Kizen rewrites api_names... always read back after
writing"), and it's exactly the kind of thing this example exists to make
concrete rather than abstract: **every command from here on uses
`service_tickets`**, read back via `kizen objects get service_tickets`, not
the name the create command was given.

```bash
kizen categories create service_tickets --api-name ticket_details --name "Ticket Details" --yes
```

### Fields

<!-- fixture: examples/service_ticket/fields.json -->
```json
{
  "category": "Ticket Details",
  "fields": [
    {
      "name": "Priority",
      "api_name": "priority",
      "field_type": "dropdown",
      "options": ["Low", "Medium", "High"]
    },
    {
      "name": "Status",
      "api_name": "ticket_status",
      "field_type": "dropdown",
      "options": ["Open", "In Progress", "Resolved"]
    },
    {
      "name": "Scheduled Date",
      "api_name": "scheduled_date",
      "field_type": "date"
    },
    {
      "name": "Escalation Ticket",
      "api_name": "escalation_ticket",
      "field_type": "relationship",
      "relation": {
        "target_object": "service_tickets",
        "relation_type": "one_to_one",
        "related_name": "Original Ticket"
      }
    }
  ]
}
```

```bash
kizen fields create service_tickets --spec-file fields.json --dry-run
kizen fields create service_tickets --spec-file fields.json --yes
```

Three findings from actually running this, none of them cosmetic:

- **`escalation_ticket`'s `relation.target_object` is `service_tickets`, not
  `service_ticket`** — the self-reference has to use the server's derived
  name from the step above, not the name this fixture asked to create. Send
  the pre-pluralization name here and `fields create` 404s with "No
  CustomObject matches the given query" — a confusing error for a field that
  otherwise looks correct, `confirmed live 2026-08-13`.
- **`status` is a reserved field api_name and 400s on create** — `field.md`'s
  reserved-name list (`name`, `owner`, `created`, `updated`, `id`, plus
  contact-ish names) is explicitly non-exhaustive; `status` joins it,
  `confirmed live 2026-08-13`. This fixture uses `ticket_status`, following
  `field.md`'s own rule of thumb (prefix with the object's api_name).
- **A relationship field that targets its own object still gets a mirrored
  inverse field** — `field.md` documents the mirror for two *different*
  objects; a self-relation is not a special case. Creating `escalation_ticket`
  (`one_to_one`, self) auto-adds a second field, `original_ticket`, also
  `one_to_one`, also pointing at `service_tickets`. That auto-added field is
  what the automation step below writes into — see "Where the UUID comes
  from" there.

Read the object back to get every field's UUID and every option's UUID in one
call — `kizen docs show objects`, "`objects get` is the lookup you want":

```bash
kizen objects get service_tickets -o json
```

---

## 2. The activity type

`Site Visit` logs a field visit against a ticket. This is the leg that adds
three cross-entity UUID references — the reason activities are in this
example at all.

<!-- fixture: examples/service_ticket/activity.json -->
```json
{
  "name": "Site Visit",
  "association_mode": "selected_objects_associated",
  "selected_object_ids": ["<service_tickets_object_uuid>"],
  "fields": [
    { "name": "Work Performed", "field_type": "longtext", "order": 0 },
    { "name": "Outcome", "field_type": "dropdown", "order": 1,
      "options": ["Resolved", "Follow-up Needed", "Cancelled"] },
    { "name": "Priority Snapshot", "field_type": "activity_custom_field",
      "order": 2, "custom_object_field": "<service_tickets.priority_field_uuid>" }
  ]
}
```

**Don't name a custom field `Notes`.** Every activity type auto-adds its own
default `notes` field (wysiwyg, rich-text) — a custom `longtext` field also
named `Notes` shows up as a second, plain-textarea field with the identical
label on the Complete Activity form, `confirmed live 2026-08-13`. This
fixture's `longtext` field is named `Work Performed` instead, for exactly
that reason.

Two of the three cross-entity references in this fixture are placeholder
tokens, not committed UUIDs, because the values only exist once the object
above has been created and read back:

- `<service_tickets_object_uuid>` — `service_tickets`' own `id`, from
  `objects get service_tickets`. **This is a UUID, not the api_name** —
  `activity.md`'s own gotcha ("`object_ids` are UUIDs, not api_names — get
  them from `kizen objects get`") is exactly this reference.
- `<service_tickets.priority_field_uuid>` — the `priority` field's `id`, from
  the same `objects get` call. This is the second, opposite-direction UUID
  hop `activity.md` calls out: an `activity_custom_field` mirrors an existing
  object field via a bare field UUID (CLI: `--linked-field
  service_tickets.priority`), not an object-qualified `field_ref`.

**Sending `selected_object_ids` on `activities create --spec-file` 400s —
`confirmed live 2026-08-13`.** The error is `associated_objects: This field
cannot be empty when association mode is set to 'selected_objects_associated'`.
This is the create-time edge of a gotcha `activity.md` already documents for
*updates* ("you must send `associated_objects`... NOT the documented
`selected_object_ids`/`custom_object_ids`, which are ignored... The CLI's
`--object <api_name>` flag builds this shape") — but `activities create` has
no `--object` flag at all, only `activities update` does, so there is no
single-step way to create an activity type with its object association via
`--spec-file`. Worth its own follow-up item (an `--object`/association flag
on `activities create`); not fixed here. The working sequence is
create-without-association, then update:

```bash
kizen activities create --spec-file activity.json --dry-run     # fields only; drop selected_object_ids first
kizen activities create --spec-file activity.json --yes
kizen activities update <site_visit_api_name> --object service_tickets --dry-run
kizen activities update <site_visit_api_name> --object service_tickets --yes
```

**`<site_visit_api_name>` is a placeholder you fill from the `create` output
above, not literally `site_visit`.** Kizen derives the api_name from the
display name when none is given, and never reissues a name once any activity
type (even a deleted one) has held it in a business — so a fresh `Site Visit`
only comes back as the clean `site_visit` in a business that has never had
one before; every later create in the same business gets a randomized suffix
instead. `confirmed live 2026-08-13`, three separate rebuilds against the
same `cli-testing` business produced three different values —
`site_visit`, `site_visit_sk6iwzxh`, `site_visit_umy0j0yh` — none
predictable ahead of time. Every `<site_visit_api_name>` below stands for
whatever `kizen activities create`'s own output or `kizen activities list`
actually returned for your run — read it back and substitute the real value.
(`automation.json`'s internal step key `schedule_site_visit` is unrelated —
that name is chosen by the fixture author, not derived by Kizen, and never
changes.)

Read it back to confirm all three references landed — the third is the
dashboard leg, below:

```bash
kizen activities get <site_visit_api_name> -o json
```

`custom_objects` shows the object association; the `Priority Snapshot`
field's `custom_object_field` expands to the source object + field
(`linked_field: "service_tickets.priority"`); and Kizen auto-adds a default
`notes` (wysiwyg) field to every activity type, exactly as `activity.md`
documents, `confirmed live 2026-08-13`.

---

## 3. The automation

One automation, one branch, one merge, one `code_step` — `automation.md`'s
graph rules (one root, `parent_key` chaining, `parent_branch` on the first
step of each branch, merge via `go_to_automation_step`) in the smallest shape
that exercises all of them.

<!-- fixture: examples/service_ticket/automation.json -->
```json
{
  "name": "Escalate High-Priority Tickets",
  "api_name": "escalate_high_priority_tickets",
  "type": "record_based",
  "target_object": "service_tickets",
  "active": false,
  "triggers": [
    { "trigger_type": "new_entity_created", "order": 0,
      "trigger_new_entity_created": { "action": "create_only" } }
  ],
  "steps": [
    { "key": "check_priority", "parent_key": null, "step_type": "condition", "order": 0,
      "step_condition": {
        "type": "custom_filter",
        "filter_config": { "all": [
          { "field": "priority", "op": "is_any_of", "value": ["High"] }
        ] }
      } },
    { "key": "create_followup", "parent_key": "check_priority", "parent_branch": "yes",
      "step_type": "create_related_entity", "order": 1,
      "action_create_related_entity": {
        "target_object": "service_tickets",
        "target_custom_object": "service_tickets",
        "new_entity_name": "Escalation for {{ entity_record.name }}",
        "new_entity_name_html": "<p>Escalation for {{ entity_record.name }}</p>",
        "new_entity_owner_type": "assign_from_context_record",
        "context_entity_field": "<service_tickets.escalation_ticket_field_uuid>"
      } },
    { "key": "log_escalation", "parent_key": "create_followup",
      "step_type": "code_step", "order": 2,
      "action_code_step": {
        "inputs": [ { "name": "ticket_priority", "field_ref": "service_tickets.priority" } ],
        "outputs": [],
        "script": "outputs.log(f\"Escalation follow-up created for a {inputs.ticket_priority} priority ticket\")"
      } },
    { "key": "finish", "parent_key": "log_escalation",
      "step_type": "stop_execution", "order": 3,
      "action_stop_execution": { "action": "stop_and_complete" } },
    { "key": "schedule_site_visit", "parent_key": "check_priority", "parent_branch": "no",
      "step_type": "schedule_activity", "order": 4,
      "action_schedule_activity": {
        "activity_type_id": "<site_visit_activity_type_uuid>",
        "schedule": { "type": "immediately" },
        "assigned_to": { "assignment_type": "owner" },
        "note": "Follow-up Site Visit for a non-urgent ticket."
      } },
    { "key": "continue_after_schedule", "parent_key": "schedule_site_visit",
      "step_type": "go_to_automation_step", "order": 5,
      "action_go_to_automation_step": { "step_key": "finish" } }
  ]
}
```

Two placeholder tokens, both filled in from the previous two sections:

- `<service_tickets.escalation_ticket_field_uuid>` — the `escalation_ticket`
  field's `id` from `objects get service_tickets`. This is
  `create_related_entity`'s `context_entity_field`: the field on **this
  automation's own `target_object`** that gets set to point at the record
  this step creates. `automation.md` already documents `context_entity_field`
  for `modify_related_entities` with the same "lives on `target_object`" rule
  — but not this write-destination usage on `create_related_entity`, which is
  what the working example captures, `confirmed live 2026-08-13`: after the
  step runs, `context_entity_field` holds the new record's id.
- `<site_visit_activity_type_uuid>` — the `Site Visit` activity type's `id`
  from `activities get <site_visit_api_name>`, used by `schedule_activity`'s
  `activity_type_id`.

**A schema gap, `confirmed live 2026-08-13`: `create_related_entity`'s
declared spec field is dead.** The model documents `target_object` as the
key to set ("api_name of the target custom object... resolved to UUID from
state at apply time") and `--dry-run` accepts a spec that sets only
`target_object` — plan-time validation checks the declared model, and the
declared model is satisfied. But the **builder that turns the spec into a
wire payload reads `target_custom_object`, an entirely different,
undeclared key**, not `target_object`. Sending only `target_object` passes
`--dry-run` and then 400s on apply: `target_custom_object: This field is
required.` This fixture sends **both** keys for exactly this reason — the
same workaround already used in this repo's own drift-test fixtures
(`tests/drift/test_roundtrip_automations.py`'s `drift_related_steps`). This is
a doc/CLI gap worth its own follow-up item — either wire the declared
`target_object` field into the builder or drop it from the model in favor of
`target_custom_object` — and is not fixed here: this item is docs, fixtures,
and tests only.

`condition.filter_config` uses the bare field api_name and an option
**label** (`"High"`), not a UUID — `kizen docs show filters` resolves it at
apply time, same DSL as `records list --filter`. `code_step`'s
`inputs[].field_ref` is `"service_tickets.priority"` — see `kizen docs show
automation`, "field_ref resolves at apply time," which is why this spec is
portable across environments rather than hand-carrying a UUID.

```bash
kizen automations create --spec-file automation.json --dry-run
kizen automations create --spec-file automation.json --yes
kizen automations activate escalate_high_priority_tickets --yes
```

`kizen automations show escalate_high_priority_tickets` renders the graph —
a `manual` trigger is auto-prepended (documented in `automation.md`'s "wire
keys that differ from the obvious guess" section). The render below is the
literal captured output, `confirmed live 2026-08-13`:

```
rev 2, active, for service_tickets
Escalate High-Priority Tickets  (escalate_high_priority_tickets)
├── triggers (2)
│   ├── t0_manual  Manual
│   └── t1_new_entity_created  New Entity Created
└── s00_condition  Condition
    ├── yes
    │   └── s01_create_related_entity  Action: Create Related Entity
    │       └── s02_code_step  Action: Code Step
    │           └── s03_stop_execution  Stop Execution
    └── no
        └── s04_schedule_activity  Action: Schedule Activity
            └── s05_go_to_automation_step → s03_stop_execution  Action: Go to Automation Step
```

`kizen automations roundtrip escalate_high_priority_tickets --execute`
confirmed zero semantic drift for every step/trigger type this automation
uses, `confirmed live 2026-08-13`.

---

## 4. The dashboard

The dashboard leg never becomes hand-written JSON. Every dashlet's `config` is
generated by `dashboards dashlet-config`, the same library builders
`dashboards create` calls — see `kizen docs show dashboard`, "Authoring a
dashlet config." There is nothing here for the doc to drift from.

**One `--field` bakes in one column — call it once per column the table
should actually show.** `table_of_records`'s `fe_extra_info.columns` takes a
list, but `dashlet-config` resolves and bakes in exactly one `col()` entry per
invocation; running it once with a single `--field` (e.g. only
`ticket_status`) produces a table with a single column, `confirmed live
2026-08-13`. An "open tickets" table worth looking at needs the ticket's own
`name`, `priority`, `ticket_status`, and `scheduled_date` — call
`dashlet-config` once per field and merge each call's one-entry `columns` list
into the dashlet's `fe_extra_info.columns` before wrapping and applying:

```bash
# dashlet 1: an open-tickets table with four real columns, wrapped in a full
# DashboardDef — merge the four calls' single-entry `columns` lists into one
# dashlet's `fe_extra_info.columns` before creating
kizen dashboards dashlet-config --type table_of_records --object service_tickets --field name
kizen dashboards dashlet-config --type table_of_records --object service_tickets --field priority
kizen dashboards dashlet-config --type table_of_records --object service_tickets --field ticket_status
kizen dashboards dashlet-config --type table_of_records --object service_tickets --field scheduled_date --full > dashboard.json
kizen dashboards create --spec-file dashboard.json --dry-run
kizen dashboards create --spec-file dashboard.json --yes
```

Custom-object dashlets (`table_of_records`, `field_breakdown`) are
**homepage-only** — `dashlet-config --full` picks `type: "homepage"` for
this reason; see `kizen docs show dashboard`'s `⚠️` section.

```bash
# dashlets 2 and 3: a status breakdown, and the activity leg's dashlet — added
# via `dashboards update` rather than recreating the dashboard
kizen dashboards dashlet-config --type field_breakdown --object service_tickets --field ticket_status
kizen dashboards dashlet-config --type scheduled_activities --object <site_visit_api_name>
kizen dashboards update service_ticket_ops --spec-file dashboard_update.json --dry-run
kizen dashboards update service_ticket_ops --spec-file dashboard_update.json --yes
```

**The third dashlet's `--object` is the activity *type*, not a custom
object** — `dashboard.md`'s own warning ("`object_id` is an activity *type*
UUID, not a custom object") made concrete: `<site_visit_api_name>` here
resolves to the activity type's UUID, the same one `schedule_activity` used
above, not `service_tickets`'.

```bash
kizen dashboards get service_ticket_ops
```

The literal captured output, `confirmed live 2026-08-13`:

```
Service Ticket Ops  (service_ticket_ops, homepage, id=c29dbd46-f3f0-487b-8a4d-c86628df06ef)
                                                   Dashlets                                                   
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ name                 ┃ report_type          ┃ chart_type ┃   pos    ┃ id                                   ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Open Tickets         │ table_of_records     │ table      │ 0,0 12×3 │ cfe5a01e-a538-444b-899e-69fed7c34700 │
│ Tickets by Status    │ field_metrics        │ donut      │ 0,3 6×3  │ 73aeef37-2d39-4e99-9a17-77de0eba1860 │
│ Upcoming Site Visits │ scheduled_activities │            │ 6,3 6×3  │ d06547b7-83cc-4a60-9cf2-fd76674ad861 │
└──────────────────────┴──────────────────────┴────────────┴──────────┴──────────────────────────────────────┘
```

---

## 5. Firing it, and the CLI/UI boundary

Creating a `High`-priority ticket exercises the yes-branch end to end:

```bash
kizen records create service_tickets --field name="Broken water heater" \
  --field priority=High --field ticket_status=Open --yes
kizen automations runs list escalate_high_priority_tickets
kizen automations runs view <execution_id>
```

The run completes `check_priority` → `create_related_entity` →
`code_step` → `stop_execution`. Reading the original ticket back shows
`escalation_ticket` now pointing at the new record — the field
`context_entity_field` referenced, set automatically:

```bash
kizen records get service_tickets <ticket_id> -o json
```

A `Low`-priority ticket instead exercises the no-branch and the merge:
`check_priority` → `schedule_activity` → `go_to_automation_step`, landing on
the same `stop_execution` the yes-branch reaches directly. `kizen activities
scheduled list` reads the scheduled `Site Visit` back.

**Logging an activity instance is a browser-only step — the doc doesn't run
this part headless.** `activity.md` is explicit: "Logging/scheduling of
instances stays in the UI," and `cli/activities_instances.py` exposes only
`get`/`list` on both logged and scheduled instances — there is no
`activities logged create`. Wiring an `activity_logged` trigger, a
`schedule_activity` step (used above), or a `scheduled_activity_overdue`
trigger into an automation is fully CLI-wired; **firing** an
`activity_logged` trigger specifically requires logging one instance by hand
in the Kizen UI first.

The read side is still real: after logging one `Site Visit` instance by hand
against a ticket in the browser,

```bash
kizen activities logged list <site_visit_api_name>
```

reads it back. Until an instance has actually been logged, this returns zero
rows — the honest state of an unused read path, not a broken one.

---

## Cleanup

Everything created in this walkthrough is disposable. Delete newest-first —
automation, activity type, dashboard (no `dashboards delete` CLI command
exists today, worth its own follow-up item; use `kizen automations
delete`/`kizen activities delete` for the other two), then the ticket
records, then the object last (`objects delete` refuses while records
exist):

```bash
kizen automations delete escalate_high_priority_tickets --yes
kizen activities delete <site_visit_api_name> --yes
kizen records list service_tickets --limit 100000 --output csv > tickets.csv
kizen records archive service_tickets --spec-file tickets.csv --yes
kizen objects delete service_tickets --yes
```

## See also

- `kizen docs show objects` / `field` — object and field spec shapes, the
  pluralization gotcha, reserved api_names.
- `kizen docs show activity` — `ActivityDef`/`ActivityFieldDef`, the
  UUID-vs-api_name associations gotcha, `activity_custom_field`.
- `kizen docs show automation` / `automation-step` — the step graph rules,
  `create_related_entity`, `schedule_activity`, `code_step` input binding.
- `kizen docs show dashboard` — `dashlet-config`, the homepage-only
  restriction, per-`entity_type` config shapes.
- `kizen docs show filters` — the filter DSL used by the condition step.
- `kizen docs show code-steps` — writing and testing the `code_step` script.

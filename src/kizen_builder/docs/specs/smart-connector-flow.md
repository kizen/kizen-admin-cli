# Spec shape: smart-connector flow — execution variables + load steps

**Consumed by:**
- `kizen smart-connectors configure-flow [<connector>] --spec-file <f>` — the
  execution variables and load steps that turn a connector's SQL output into
  records.

A connector's SQL produces one or more **output tables**. This spec says what to
do with their columns: declare an **execution variable** per column you want to
use, then one **load step** per Kizen object you want to write to, mapping
variables onto fields.

Everything refers to things by name — object api_names, field api_names, variable
names — and is resolved to UUIDs against live state before anything is written.

> **Publish the SQL first.** Kizen validates every variable's `scope` and
> `data_source` against the connector's recognized output columns (`headers`),
> and only a publish refreshes them. After changing what the SQL outputs, run
> `push --publish`, then `configure-flow`. See `kizen docs show
> smart-connectors` (Lifecycle).

---

## Quick example — one object

```json
{
  "connector": "nightly_order_import",
  "execution_variables": [
    { "name": "order_number", "data_type": "string" },
    { "name": "customer_email", "data_type": "email" },
    { "name": "placed_at", "data_type": "datetime", "input_format": "%Y-%m-%d %H:%M" }
  ],
  "loads": [
    {
      "custom_object": "orders",
      "matching_rules": [
        { "field": "order_number", "variable": "order_number" }
      ],
      "field_mapping_rules": [
        { "field": "name", "variable": "order_number" },
        { "field": "customer_email", "variable": "customer_email" },
        { "field": "placed_at", "variable": "placed_at" }
      ]
    }
  ]
}
```

Start from `kizen smart-connectors suggest-variables <connector> --spec`, which
emits the `execution_variables` block with data types and formats already
inferred from the reference file.

---

## Two objects, linked by a relationship field

A load step can expose the Kizen id of the record it just matched or created.
A **later** step references that variable to fill a relationship field, so one
connector builds a linked graph of records:

```json
{
  "connector": "nightly_order_import",
  "execution_variables": [
    { "name": "order_number", "data_type": "string" },
    { "name": "sku", "data_type": "string" },
    { "name": "quantity", "data_type": "number", "output_format": "no_comma" }
  ],
  "loads": [
    {
      "custom_object": "orders",
      "order": 0,
      "matching_rules": [{ "field": "order_number", "variable": "order_number" }],
      "field_mapping_rules": [{ "field": "name", "variable": "order_number" }],
      "exposes_variable": "matched_order"
    },
    {
      "custom_object": "order_lines",
      "order": 1,
      "matching_rules": [{ "field": "sku", "variable": "sku" }],
      "field_mapping_rules": [
        { "field": "name", "variable": "sku" },
        { "field": "quantity", "variable": "quantity" },
        { "field": "order_rel", "variable": "matched_order" }
      ]
    }
  ]
}
```

`order_rel` is a relationship field on `order_lines` pointing at `orders`; it
resolves to the actual record the first step wrote for that row.

References only go **backwards** — a step can't reference a variable a later step
exposes, and the CLI rejects that at plan time rather than saving something that
can't work.

---

## Top level

| Key | Type | Notes |
|-----|------|-------|
| `connector` | string | Connector api_name or UUID. Optional — the CLI argument wins when both are given. |
| `execution_variables` | `ExecutionVariable[]` | Optional. **Saving replaces the connector's existing set.** A re-declared variable is updated in place: the CLI sends it by id, so its uuid and every rule that references it survive. Anything live but not re-declared is dropped (the plan lists them), and a spec rule can't reference it. The drop is the save's **last** write, and the rules still pointing at a dropped variable are removed with it. Omit the key entirely to leave the live set alone and only write load steps. Confirmed live 2026-09-25. |
| `loads` | `LoadStep[]` | **Required**, at least one. One entry per object written to. The spec is the whole flow: each entry updates the live step with the same `custom_object` and `scope` in place (by id, several with one key pair in `order` order), an entry with no live counterpart is created, and **a live step the spec no longer lists is deleted** along with its rules. The plan lists all three. An updated step keeps the automations picked for it in the UI (new-record and other-match lists). Confirmed live 2026-09-25. |

## `ExecutionVariable`

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** What matching / field-mapping rules refer to. |
| `data_source` | string | Output column to read. Defaults to `name`. |
| `data_type` | enum | `string` (default), `boolean`, `date`, `datetime`, `email`, `number`, `phone_number`, `uuid`. These are the *connector's* type names — an integer or decimal Kizen field is fed by a `number`. |
| `scope` | string | Output table the column lives in. Defaults to the connector's only output table when it has exactly one. |
| `is_array` | bool | Split one value into several — needed for multi-select (`checkboxes`) fields. Requires `array_delimiter`. |
| `array_delimiter` | string | One of `,` `;` `\|` `\t` `{}` `[]`. |
| `input_format` | string | How to parse the incoming text: `yes_no` / `true_false` / `on_off` / `one_zero` / `t_f` / `checked_unchecked` for booleans, a strftime pattern or `unix_epoch` / `excel_epoch` for dates. |
| `output_format` | string | How to hand the value to Kizen (e.g. `no_comma` for numbers). |
| `required` | bool | Fail the row when the column is empty. |
| `value` | string | A literal, for a variable with no `data_source`. |
| `display_order` | int | Defaults to the order listed here. |

Run `kizen smart-connectors metadata` for the authoritative list of each data
type's legal `input_format` / `output_format` values.

## `LoadStep`

| Key | Type | Notes |
|-----|------|-------|
| `custom_object` | string | **Required.** Object api_name (or UUID) to write to. |
| `matching_rules` | `MatchingRule[]` | **Required**, at least one. How to tell whether the row is an existing record. |
| `field_mapping_rules` | `FieldMappingRule[]` | **Required**, at least one. Which variable writes to which field. |
| `scope` | string | Output table feeding this step. Defaults to the connector's only output table when it has exactly one. |
| `order` | int | Execution order (0-based). Defaults to the order listed here. Must be unique across the spec's steps: Kizen rejects a flow in which two steps share one, so the plan refuses it. Confirmed live 2026-09-25. |
| `type` | `"csv_load"` | The only load type. Default. |
| `exposes_variable` | string | Name for the uuid variable carrying this step's matched/created record id, for a later step to reference. On a step that already exposes one, a new name renames it and keeps its uuid; leaving the key out keeps the live one. A name already held by a live execution variable or by a *different* live step is refused at plan time, since Kizen rejects a reused name even when its holder is deleted in the same save. |
| `automation_trigger_config` | string | Whether automations fire for records this step writes (e.g. `fire_all`). Server default when unset. |

## `MatchingRule`

Rules are tried in order. The defaults are the ordinary upsert.

| Key | Type | Notes |
|-----|------|-------|
| `variable` | string | **Required.** Variable holding the value to match on — including one another load step exposes. |
| `field` | string | Field api_name (or UUID) to match on. Required unless `is_match_by_kizen_id`. |
| `is_match_by_kizen_id` | bool | Match on the literal Kizen record id instead of a field. |
| `no_match_action` | enum | `create_new` (default), `next_rule_ignore_previous`, `do_not_upload`. |
| `single_match_action` | enum | `update_current` (default), `next_rule`, `next_rule_ignore_previous`, `do_not_upload`. |
| `multiple_match_action` | enum | `do_not_upload` (default), `next_rule`, `next_rule_ignore_previous`. |
| `match_archive_action` | enum | `create_new` (default), `unarchive_and_update`, `unarchive_only`, `next_rule`, `next_rule_ignore_previous`, `do_not_upload`. |
| `order` | int | Defaults to the order listed here. |

## `FieldMappingRule`

| Key | Type | Notes |
|-----|------|-------|
| `field` | string | **Required.** Field api_name (or UUID) on this step's object. |
| `variable` | string | The variable to write. Use for the ordinary 1:1 case. |
| `variables` | string[] | Several variables concatenated into one field. Exactly one of `variable` / `variables` is required. |
| `conflict_resolution` | enum | `overwrite`, `only_update_blank`, `only_add_options`, `overwrite_except_null`. **Leave unset** unless you need it — see gotchas. |
| `can_create_field_options` | bool | Let an unrecognized value add a new option to a dropdown/checkboxes field instead of failing the row. |
| `display_order` | int | Defaults to the order listed here. |

---

## Field types that work from plain text

All confirmed end-to-end from CSV text values:

- `text`, `longtext`, `email` — as-is.
- `dropdown` / `radio` / `checkboxes` — matched by **option label text**, resolved
  to the option's UUID. Multi-select needs the variable's `is_array` +
  `array_delimiter`.
- `datetime` / `date` — parsed with `input_format`, timezone-converted to the
  business timezone.
- `integer` / `decimal` / `money` — a `number` variable; `output_format: no_comma`
  for values written with thousands separators.
- `checkbox` — `"Yes"` / `"No"` text via a `boolean` variable with
  `input_format: yes_no`.
- relationship — a variable holding a Kizen record id, i.e. another step's
  `exposes_variable`.

## Gotchas

- **Every load step needs a mapping for its object's own `name` field.** Kizen
  requires it; the CLI checks before writing.
- **`data_source` names a column of the generated output sample** — what your SQL
  selects, not what the reference file contains. Inventing output columns in SQL
  is fine (a webhook connector maps fields pulled out of a JSON body this way);
  what isn't fine is inventing them without publishing, since `publish` is the
  only thing that refreshes the column list Kizen validates against. A
  `data_source` rejected for a column you can see in your SQL means that SQL
  isn't published yet: `push --publish`.
- **`conflict_resolution` 400s with "not valid for this field type"** on plain
  text/email fields. Omitting it lets the server default to `only_update_blank`,
  which is what you'd pick anyway.
- **The last matching rule can't fall through.** `multiple_match_action:
  next_rule` on the final rule is rejected — there is no next rule.
- **The wire format is asymmetric** (and this spec papers over it): a matching
  rule takes one `variable`, a field mapping takes a plural `variables` list.
  Write `variable` for the 1:1 case either way.
- **Load steps are saved in rounds**, not one call, when a step references a
  record id an earlier step creates — that variable only gets a UUID once the
  earlier step exists. The CLI handles the rounds and reports how many it needed.
  Re-running a spec updates the connector in place, usually in one round, since
  the live steps' exposed variables already have uuids. Every round sends every
  live step, the ones not yet saved as they were, so no round removes a rule the
  spec keeps. Confirmed live 2026-09-25.
- **A save is several PATCHes, and only each one is all-or-nothing.** Kizen checks
  a PATCH in full before writing any of it, but a later one can still fail after
  an earlier one landed. When that happens, `configure-flow` re-reads the
  connector and reports which write failed, each load step's state as the
  re-read shows it (`updated`, `previous config`, `created`, `not created yet`,
  `deleted`, `not deleted yet`, or `unknown` when the re-read fails too) with
  its matching/mapping rule counts before, now, and in the spec, and the
  connector's `status` (an `operational` one is live in that state). A write
  that timed out but landed shows as landed. Re-running the spec picks up from
  there. With `--json` the report is the JSON on stdout. Exit code 1.
- **`configure-flow` doesn't make the connector run.** A live run also needs a
  published script (`push --publish`) and `status: operational`
  (`smart-connectors activate`) — without the latter a live run sits queued
  forever with no error.

---

# Writing to multiple objects in one connector

One connector can have several `flow.loads` entries, each targeting a different
`custom_object`, executed in `order`. That's how you populate a relationship
field pointing at a record the same run just created.

Once a load step has valid `matching_rules` + `field_mapping_rules`, it exposes
an **`execution_variable`** (`type: "related_object_upload"`,
`data_type: "uuid"`) holding the Kizen id of the record it matched or created
for that row. A **later** load step's `matching_rules` / `field_mapping_rules`
can reference that variable's id to populate a `many_to_one` relationship field —
confirmed live, it resolves to the actual record.

**On a brand-new connector this `execution_variable` isn't always
auto-populated** the first time load steps are saved. If it comes back `null`,
set it explicitly in the same PATCH and the server assigns a real id you can
reference from the next step:

```json
{"name": "<anything>", "data_type": "uuid", "scope": "<the load step's scope>"}
```

This is also why load steps save in **rounds** rather than one call: the
variable only gets a uuid once the earlier step exists, and each round has to
re-send the already-saved steps **with their server ids intact** or they're
recreated and that uuid goes stale.

## Field types confirmed working from plain text

All end-to-end via `field_mapping_rules`, from plain CSV text values:

| type | how the text is interpreted |
|---|---|
| `dropdown` / `checkboxes` | matched by **option label text**, auto-resolved to the option UUID. Multi-select `checkboxes` need the variable's `is_array: true` plus `array_delimiter` |
| `datetime` | auto timezone-converted |
| `integer` | as written |
| `checkbox` | `"Yes"`/`"No"` text via `input_format: yes_no` |
| `longtext` | as written |
| `relationship` | via the `execution_variable` mechanism above |

## See also

- `kizen smart-connectors suggest-variables <connector> --spec` — a starting spec.
- `kizen smart-connectors metadata` — the authoritative enum catalog.
- `kizen docs show smart-connectors` — the API, the create→execute path, the
  local `pull`/`run`/`push` loop, and per-connector-type file shapes.

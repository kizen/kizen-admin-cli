# Filters — one DSL, six surfaces

Kizen filters show up in six unrelated-looking places, and they are all the
same structure underneath. Learn it once here; each surface doc only records
which key holds it.

There are **two layers**, and knowing which one you're writing prevents most
filter bugs:

| layer | what it is | where you write it |
|---|---|---|
| **The DSL** | `{"all"\|"any": [{field, op, value}]}` (or `variable` in place of `value`, below) — friendly, field api_names, option *labels* | `--filter` / `--filter-file`, and `filter_config` in a spec file |
| **The wire** | `{"and", "query": [...], "invalid"}` — query groups, `"custom"::<uuid>` field refs, option UUIDs | what Kizen stores; what you get back from a read |

The CLI resolves the DSL to the wire form against live schema at plan time.
Read a filter back and you get the wire form — so a filter copied out of a live
read is *already* wire-shaped and should be passed through as a raw dict, not
re-run through the DSL.

## Where filters appear

| surface | the key | notes |
|---|---|---|
| `records list --filter` | search body `query` | `kizen docs show records` |
| Filter groups (segments) | `config` | `kizen docs show saved-views` |
| Quick filters | `filters` | same shape as filter groups |
| Automation `condition` step | `step_condition.filter_config` | `kizen docs show automation` |
| Automation `search_records` step | `filter_config` | resolved against the step's **own** `custom_object`, not the automation's `target_object` |
| Dashlets | `config.custom_filters` | an empty filter is a specific literal, not `{}` — `kizen docs show dashboard` |

## The DSL

```json
{ "all": [
    { "field": "account_region", "op": "is_any_of", "value": ["North", "West"] },
    { "field": "account_seats",  "op": ">=",        "value": 10 }
] }
```

- Top level is `{"all": [...]}` (AND) or `{"any": [...]}` (OR).
- `field` is the **bare field api_name**. Option labels are resolved to UUIDs
  against the target object for you.
- **Ops are per field type.** Dropdown/status use `is_any_of`/`not_any_of`,
  numbers use `>=`/`between`, text uses `contains`/`starts_with`. There is
  **no `in` and no `gte`**. Run `kizen filters ops [<field_type>]` for the
  authoritative list per type — that command is generated from the same table
  the resolver uses, so it can't drift.
- **A single condition must still be wrapped** in `{"all": [...]}`. A bare
  `{"field": ...}` is not accepted anywhere — not in `--filter`, not in a
  condition step.
- A variable compared to a static value is a clause type the DSL doesn't
  cover (below): pass a raw wire dict. A **field compared to an array
  variable** is in the DSL: give `"variable"` instead of `"value"`, in a
  condition step only (below).

## The wire format

```json
{
  "and": true,
  "query": [
    { "id": "query-0", "and": true, "filters": [
        { "type": "fields_v2", "field": "\"custom\"::<field-uuid>",
          "subtype": "custom", "condition": "is_blank", "value": false },
        { "type": "fields_v2", "field": "\"custom\"::<another-uuid>",
          "subtype": "custom", "condition": "is_any_of",
          "value": ["<option-uuid-1>", "<option-uuid-2>"] } ] },
    { "id": "query-1", "and": false, "filters": [
        { "type": "fields", "field": "display_name",
          "subtype": "non_custom", "condition": "=", "value": "some text" } ] }
  ],
  "invalid": false
}
```

Rules:

- Top level is exactly `{and, query, invalid}`.
- Each query group is `{id, and, filters[]}`. **`id` is required** and
  conventionally `"query-0"`, `"query-1"`, … `and: true` means AND *within* the
  group, `and: false` means OR within it.
- Multiple query groups are ANDed together when the top-level `"and": true`.
- **Custom fields:** `type: "fields_v2"`, `field: '"custom"::<uuid>'`,
  `subtype: "custom"`.
- **Non-custom / system fields:** `type: "fields"`, `field: "<api_name>"`,
  `subtype: "non_custom"`.
- **`value` may never be null** — use `false` for a blank check, `""` for an
  empty-text check.
- **Automation rules carry the editor's layout.** A condition or
  `search_records` rule the CLI compiles from a filter spec also carries a
  `view_model`: the layout the Kizen workflow editor reads to show the rule.
  Other surfaces don't use it; UI-built filter groups carry none (confirmed
  live 2026-10-30). [The table below](#which-automation-rules-carry-the-editors-layout)
  lists the rule kinds it's written for, on every object, Contacts included.
  The editor uses a step's layouts only when every rule in the step has
  one. Otherwise it rebuilds each rule from its keys, which shows most rules but not one on a default field such as `created` or `owner`
  (no field shown), and not a list of options, stages or records. The
  `automations create` and `update` dry run warns about each field rule in a
  condition or search step that has no `view_model`, raw ones included, and
  about a step whose other layouts the editor will ignore for that reason; a
  raw rule that carries its own `view_model` doesn't warn. `steps add`
  and `steps edit` write the same layout but don't warn. A rule without one
  still runs. `update` keeps the card text (`description`) the editor saved
  on a rule the spec leaves as it is.

### Which automation rules carry the editor's layout

The CLI writes the layout for the kinds in the "laid out" column. Unless
the inferred list below names it, each was saved in the editor and read
back (confirmed live 2026-10-30), and the CLI writes the same layout. Every
other op on that field type is not yet captured; the dry run warns. A kind
that the editor can show without a layout still counts as not captured,
because one bare rule makes the editor drop every layout in its step. Ops
are the filter spec's.

| field type | laid out | not yet captured; the dry run warns |
|---|---|---|
| text | `=`, `!=`, `contains` | `starts_with`, `ends_with`, `not_contains`, `not_starts_with`, `not_ends_with`, `is_blank`, `not_blank` |
| email | `=`, `contains` | `!=`, `starts_with`, `ends_with`, `not_contains`, `not_starts_with`, `not_ends_with`, `is_blank`, `not_blank` |
| dropdown | `=`, `!=`, `is_any_of`, `not_any_of`, `is_blank` | `not_blank` |
| status, yesnomaybe | `=` | `!=`, `is_any_of`, `not_any_of`, `is_blank`, `not_blank` |
| checkboxes | `=` and `contains` (both the editor's "Contains"), `not_any_of` | `!=`, `is_any_of`, `has_any`, `not_contains`, `is_blank`, `not_blank` |
| checkbox | `is_checked` (`=` true) | `not_checked` (`=` false) |
| rating | `>=`, `is_any_of` | `=`, `!=`, `<`, `<=`, `>`, `not_any_of`, `is_blank`, `not_blank` |
| integer | `>=` | `=`, `!=`, `<`, `<=`, `>`, `between`, `is_blank`, `not_blank` |
| decimal | `<` | `=`, `!=`, `<=`, `>`, `>=`, `between`, `is_blank`, `not_blank` |
| date | `between` | `=`, `!=`, `<`, `<=`, `>`, `>=`, `month_equals`, `is_blank`, `not_blank` |
| datetime (`created` too) | `<`, `>=` | `=`, `!=`, `<=`, `>`, `between`, `month_equals`, `is_blank`, `not_blank` |
| team_selector (`owner` too) | `is_me`, `is_any_of` | `=`, `!=`, `not_any_of`, `is_blank`, `not_blank` |
| relationship | Contains (`is_any_of`), `is_blank`, `not_blank` | Does Not Contain (`not_any_of`), `contains`, `not_contains` |
| stage | `=`, `!=`, `is_any_of`, `not_any_of` | none |
| money, radio, timezone, phonenumber, longtext, files, dynamictags | none | every op |

Laid out but inferred, not saved in the editor. Each takes the layout
captured for the same field type on another field or object:

| inferred kind | layout taken from |
|---|---|
| `created` `<` | a custom date-time field's `<` |
| `>=` on a custom date-time field | `created`'s `>=` |
| `owner` `is_any_of` | a custom team-member field's `is_any_of` |
| `is_me` on a custom team-member field | `owner`'s `is_me` |
| email `=`, `contains` on any object but Contacts | Contacts' `email` |
| every row but email on Contacts | the same row on a custom object, with the Contacts field URL |
| to-many relationship Contains (`is_any_of`) | a to-one relationship's Contains |

They carry a layout rather than none because a bare rule makes the editor
rebuild the whole step, which can't show `created`, `owner` or a list of
records, and drops the captured layouts beside it. They don't warn.

- **A relationship field has two operators in the editor: Contains and
  Does Not Contain.** There is no "Is Any Of" on a relationship. Contains is
  spec op `is_any_of`, wire `is_any_of`. Does Not Contain is spec op
  `not_any_of`, wire `is_none_of`; that wire value is read from the editor's
  code, not from a saved rule. The spec's `contains`/`not_contains` write
  the `contains` token, which the editor never writes.
- **Laid-out number rules send the value as a string** (`"90"`), as the
  editor writes it: integer `>=` and decimal `<`. Every other number rule,
  money included, still sends the number. Other filter surfaces always keep
  the number. Kizen compares the string form numerically (tested
  2026-10-30).
- The stage-time rules (Python DSL only) are laid out for `time_in_stage`
  with `more_than`, `entered_stage` with `earlier_than` and `left_stage` with
  `later_than_or_on`. Their other comparisons aren't captured.

### Record-field operators

| operator | value | meaning |
|---|---|---|
| `is_blank` | `true` | field is empty |
| `is_blank` | `false` | field has a value |
| `=` | `"<string>"` | equals |
| `>` | `"<ISO date>"` | later than (dates) |
| `is_any_of` | `["<uuid>", …]` | option is one of (choice fields) |

**There is only one blank-check token for record fields — `is_blank`,
distinguished by its boolean value.** There is no separate `is_not_blank` here.
This matches what the records-search UI itself sends and what the CLI's own DSL
emits.

A legacy dashlet-filter helper once emitted a distinct `is_not_blank` token for
"has a value", and that was confirmed working against dashlets at the time — so
Kizen may accept both spellings for `fields_v2`. It is not what the modern UI
or this CLI produce; treat `is_not_blank` on a *record field* as
legacy/unconfirmed and use `is_blank` + `false`.

Do not confuse that with the genuinely distinct `is_not_blank` token used by
**variable** conditions, immediately below — a different clause type with a
different token set.

## Variable-comparison clauses (automations only)

Comparing an automation variable to a static value is a different `type`, and
the DSL doesn't cover it — write the raw dict:

```json
{
  "type": "variable",
  "subtype": "automation_variable",
  "lhs_variable_name": "<variable_name>",
  "condition": ">=",
  "rhs_value": "5.5",
  "rhs_value_type": "static",
  "description": "Variable Value '<variable_name>' More Than Or Equal To Static Value 5.5",
  "view_model": [
    ["filter_type", { "vars": [
        ["fields_settings_search", true],
        ["custom_object_id", "<object-uuid>"],
        ["object_type", "standard"],
        ["client_tag_field_id", "<client-tag-field-uuid>"]
      ], "filter_type": "variable" }],
    ["lhs_variable", { "name": "<variable_name>", "data_type": "number" }],
    ["condition", "more_than_or_equal_to"],
    ["value_type", "static"],
    ["value", "5.5"]
  ]
}
```

**`view_model` is required.** Without it the condition block appears
empty/untyped in the Kizen UI editor — it still *executes* correctly via API,
so this fails silently in the direction that matters least and most annoyingly.

Operator mapping (the `condition` key vs its `view_model` string):

| API `condition` | `view_model` string | label |
|---|---|---|
| `>=` | `more_than_or_equal_to` | More Than Or Equal To |
| `=` | `equals` | Equals |
| `=` on a boolean | `is_yes` (`rhs_value: true`) / `is_no` (`false`) | Is Yes / Is No |
| `<=` | `less_than_or_equal_to` (not confirmed) | Less Than Or Equal To |
| `<` | `less_than` (not confirmed) | Less Than |
| `>` | `greater_than` (not confirmed) | Greater Than |

`>=`, `=` and the boolean pair are from UI-built clauses. A boolean rule's
`view_model` stops after its `condition` entry, with no `value_type` or
`value`. The other three rows have not been seen in a UI-built clause.

For a blank check **on a variable**, `condition` is `is_blank` or
`is_not_blank` — a distinct pair of tokens from the record-field
`is_blank`+boolean form above, confirmed from real captured GET responses. Here
`view_model`'s `["condition", …]` entry uses that same string rather than the
`less_than`-style mapping.

`rhs_value` is always a string. `custom_object_id` is the automation's target
object. `client_tag_field_id` is per business, not a constant: two businesses'
contact `tags` field ids differed (confirmed live 2026-10-05). Copy it from the
`view_model` of an existing condition in the same business
(`kizen automations get <api_name> --raw`). If there is none, use the contact
`tags` field's id from `kizen objects get client_client`. That fallback is a
best guess.
Conditions were read in two businesses, one on 2026-10-05 and one on
2026-10-16. Record-field clauses mostly carried the `tags` id in both. Variable
clauses carried it in the second and a different id in the first. A condition
with no `view_model` still executes, so a wrong value in a raw clause probably
changes only how the builder UI shows it (not tested). A rule the CLI compiles
from a filter spec doesn't need it: the CLI writes the layout itself and
resolves the id per business (see [Comparing a field to an array
variable](#comparing-a-field-to-an-array-variable-condition-steps-only)).

## Comparing a field to an array variable (condition steps only)

The UI's "Variable" option on a rule compares a record field to an
automation variable instead of a literal. In the DSL, name the variable:

```json
{ "all": [
    { "field": "status", "op": "is_any_of", "variable": "allowed_statuses" }
] }
```

- `variable` replaces `value`; give one or the other.
- Ops: `is_any_of` or `not_any_of`. Labels vary by field type: checkboxes
  and relationship fields say "Contains Any Of" / "Contains None Of".
- The variable must be declared in the same automation, by an
  `initialize_variable` step or as a `search_records` destination (an entity
  array of the searched object). The planner checks its kind against the
  field. The CLI writes the UI's full `view_model`; without it the UI can't
  open the step.
- For a dropdown, the array can hold option names or option UUIDs: a test
  run on 2026-10-14 matched with each. The other field types were not run.
- Every other filter surface rejects `"variable"`, `search_records`
  included.

Which variables the UI offers for each field type (confirmed live
2026-10-14):

| field type | variable kinds |
|---|---|
| `dropdown` | string array, UUID array |
| `checkboxes` | string array, UUID array |
| `team_selector` | string array, UUID array, a single `employee` variable |
| `relationship` | string array, UUID array, entity array of the related object |

The wire clause is the field's usual clause with the variable's name as
`value` and `"value_type": "variable"`. The `condition` token is the same one
a literal list would get (checkboxes `not_any_of` is `!has_any`).

## Writing a condition step's filter, start to finish

1. `kizen objects get <object>` — field UUIDs are shown inline for every field,
   and every choice/status/yesnomaybe field's `options` array carries
   `{id, name, code}`. **No separate API call is needed for option UUIDs.**
2. Build the filter. Prefer the DSL and let the CLI resolve it; drop to the
   wire form only to compare a variable to a static value.
3. The planner sets `action_on_failure: notify_pause` for you — a condition
   step is rejected with 400 without it.
4. Wire the branches: each child step sets `parent_key` to the condition's key
   plus `parent_branch: "yes"` or `"no"`.

## See also

- `kizen filters ops [<field_type>]` — the authoritative per-type operator list.
- Each surface's own doc for the key its filter lives under (table above).

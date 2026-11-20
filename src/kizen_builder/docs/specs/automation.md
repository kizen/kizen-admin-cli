# Spec shape: `AutomationDef` — automations

Build order: skeleton → step-types show <type> for each step → create --dry-run → create → use the step keys the write prints.

1. `kizen automations step-types skeleton --object <api_name>` prints a
   minimal automation. `step-types list` names every wired type; `--dry-run`
   refuses any other. Read these before authoring; don't grep the source.
2. `kizen automations step-types show <type>` prints one step's or trigger's
   config block: entries to copy, the keys each choice needs, the values to
   look up first, and its run-time notes. `step-types concepts` explains the
   ideas the blocks share.
3. `kizen automations create --spec-file <path> --dry-run` lists every spec
   problem at once, each with its step key, dotted path and the `kizen`
   command that lists the allowed values, then checks the spec against the
   environment: fields, activity types, team members. Warnings don't block.
   The plan lists what it defaulted, such as `api_name` from the name and
   `order` from position. Fix them all and dry-run again until the plan
   renders.
4. Re-run without `--dry-run` to apply, after the user approves the plan.
   `kizen automations diff <api_name> --spec-file <f>` shows what an update
   would change.
5. The live steps get synthesized keys (`s00_delay`, …), not the spec's
   `key`s. Before a `steps` verb, use the keys the write prints or `kizen
   automations show <api_name>` lists (`kizen docs show automation-step`).

**Consumed by:** `kizen automations create|update --spec-file <f>` (also
stdin). An automation is triggers + a step graph, sent as one atomic POST/PUT.
This page holds the graph and what an update keeps. Worked multi-step
automations are in `kizen docs show automation-patterns`.

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
- Kizen doesn't store step keys. After a write each step is keyed
  `s<NN>_<type>` by order, and the write prints the new keys; use those with
  `--parent` and `steps get`.

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
6. **Optionally, `exit_loop` steps in the body**, which end a loop early.
   `action_exit_loop.loop_to_exit` names the loop to end: the one it's in, or
   one around it. Omitted, it ends the innermost loop around the step
   (`kizen automations step-types show exit_loop`).

`kizen docs show automation-patterns`, "Find or create a Contact by email",
has all five. `steps add` of a loop adds its closer and item variable
(`kizen docs show automation-step`). Kizen's editor names a loop "For each
item in '<array>'" and its item step "Item Reference Variable", but a
CLI-created one reads back as "Loop" and "Initialize Variable", so set
`description` to match. Confirmed live 2026-10-27. A CLI-created exit loop
reads back as "Exit Loop"; the UI writes "Exit <array> Loop".

## Variables

- A variable the UI creates inline from a step's picker is an ordinary
  root-chain `initialize_variable` with `sources: []` (confirmed live
  2026-09-28).
- A static array value is a `static` source with `source_subtype`
  (`string`/`uuid`), a list `value` and `array_separator` (`comma` or
  `newline`). Give `value` as a list: a single string such as `"a\nb"` loses
  its separator on read-back, and `diff` then shows a change (confirmed live
  2026-09-29).
- **`value_resolution` on `update_variable`** says how the sources combine
  with an array's current value. Confirmed live 2026-10-14, from `[a]` with
  `deduplicate_array: false`:

  | `value_resolution` | sources `[b]` | sources empty |
  |---|---|---|
  | `add_only` | `[a, b]` (and `[a, c]` gives `[a, a, c]`) | not observed |
  | `overwrite` | `[b]` | `[]` |
  | `overwrite_except_null` | `[b]` | `[a]`: kept |
  | null (stored) | `[b]` (re-confirmed 2026-11-11) | `[]` |

  A new step (`create`, `steps add`, or one an `update` adds) that leaves it
  out on an array variable gets `add_only`, as Kizen's builder picks for a
  new step. A step with an `id` keeps its stored value, so deleting the key
  doesn't clear it: set `overwrite`. In the builder it is "Append or
  Replace": *Append Values to End of Array* is `add_only`, *Overwrite with
  These Sources (Clear if Blank)* is `overwrite`, and *Overwrite with These
  Sources (But Skip if Blank)* is `overwrite_except_null`. Observed
  2026-11-11.

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
server, a stop's `action`, a plugin step's `preview_user_id`, an
`update_variable`'s `value_resolution`, an `exit_loop`'s target loop, and a
`schedule_activity`'s `target_variable`.
`--dry-run` names them as `keeps_live=[<key>, …]`. An explicit value always
wins, `""` and `false` included; `description: ""` gets the type's default
text.

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

- `kizen docs show automation-patterns`: worked multi-step automations.
- `kizen docs show automation-step`: editing or adding **one** step.
- `kizen docs show automation-runtime`: starting, watching and pausing runs.
- `kizen docs show filters`: condition and `search_records` filter shapes.
- `kizen docs show code-steps`: writing and testing a `code_step`'s Python.
- `kizen docs show email-templates`: the message resource and merge fields.
- `kizen docs show activity`: activity types and branching on what was logged.

# Spec shape: single automation step (`steps edit` / `steps add`)

For surgically changing **one** step on an existing automation, without
re-sending the whole `AutomationDef`. Step keys come from
`kizen automations show <api>` and **re-synthesize after every apply**. Each
write prints the keys it leaves; use those, or re-run `show`, for the next step
operation.

**Consumed by:**
- `kizen automations steps edit <api> <key> --spec-file <f>` — a **patch**.
- `kizen automations steps add <api> (--parent <key>|--root) [--branch yes|no] [--leaf] --spec-file <f>` — a **new step**.

Step blocks are the same as in a whole automation: `kizen automations
step-types show <type>` prints one, and `kizen docs show automation` has the
graph rules. Only the delivery differs.

---

## `steps edit` — patch one step

Top-level keys in the JSON **replace** the step's keys. A config block
(`action_*` / `step_*`) is replaced **wholesale**, so start from the current
wire JSON and modify:

```bash
kizen automations steps get flag_big_north_accounts flag > step.json
# …edit step.json…
kizen automations steps edit flag_big_north_accounts flag --spec-file step.json --dry-run
```

```json
{
  "action_change_field_value": {
    "field_ref": "accounts.account_flagged",
    "specific_field_value": false
  }
}
```

- `key` and `type` are **immutable**.
- Re-parenting via `parent_key` / `parent_yes_no` **is** allowed and
  graph-validated.
- Config blocks accept the same authoring shapes as create specs — `field_ref`
  resolves against the live env.

## `steps add` — insert one step

The spec is a **single step** (`step_type` + its config block; `field_ref`
welcome). `kizen automations step-types show <type> --variant <name>` prints
one to start from. **Placement comes from the flags, not the spec:**

- `--parent <key>` — attach under that step (or `--root` to become the new first step).
- `--branch yes|no` — required when the parent is a `condition`/`goal`.
- `--leaf` — append as a leaf; **without it, the parent's existing children move
  under the new step** (the default "insert into the chain" behavior).

```json
{ "step_type": "delay", "step_delay": { "days": 1 } }
```

```bash
kizen automations steps add flag_big_north_accounts --parent check --branch yes \
  --spec-file delay.json --dry-run
```

`steps remove <key> [--cascade]` removes one step, or its whole subtree.

---

# How step editing actually works

**There is no per-step endpoint.** Every `kizen automations steps …` verb runs
the same loop:

GET → translate to write dialect → mutate one node in memory → graph-validate →
atomic PUT (with `last_revision`, so a concurrent edit fails loudly) → re-GET →
report the before/after semantic diff as evidence.

```bash
kizen automations show <name>                # step tree with synthesized keys (the handles)
kizen automations steps get <name> <key>     # one step's wire JSON — the starting point for an edit
kizen automations steps edit <name> <key> [--spec-file|stdin]
kizen automations steps add <name> --parent <key> [--branch yes|no] [--leaf] [--root]
kizen automations steps remove <name> <key> [--cascade]
kizen automations roundtrip <name> [--execute]   # translator fidelity check
```

Consequences worth knowing:

- **Keys re-synthesize after every apply** — they encode order + type, so an
  insert or remove mid-graph renames every later step. The write prints the
  keys it leaves (`keys now:`); use those, or re-run `show`, before the next
  step operation.
- **`edit` is a merge patch, but config blocks replace wholesale.** Send a
  complete config block, not a partial one. Two exceptions: a
  `stop_execution` block keeps the live `action` and `notify` it leaves
  out, and gets `stop_and_complete` when the step has no action; an
  `initialize_variable` keeps its variable's live `is_return_variable`,
  which the CLI can't change.
- **`add` inserts into the chain by default** — the parent's children move under
  the new step. `--leaf` appends without adopting them, `--branch yes|no` targets
  a condition/goal branch, `--root` puts the new step first. A
  `next_or_finish` or `merge_branches` child is never adopted, so `--parent
  <loop>` puts the new step at the head of the loop body, and `--parent
  <next_or_finish>` puts it right after the loop.
- **`add` of a `loop` adds its `next_or_finish` and its item variable too**, as
  Kizen's builder does. The spec is `{"step_type": "loop", "step_loop":
  {"array_variable": "<array>"}}`, and the array must already be declared by
  an `initialize_variable` step; the CLI never creates it. The item variable is
  always created, named `<array>_item` (`<array>_item_2`, `_3`… when the name
  is taken), with the array's type, in a new `initialize_variable` step after
  the other variable steps. A spec that sets `step_loop.item_variable` is
  refused. The loop's body starts empty, and the parent's existing children
  follow the `next_or_finish`, so they run once, after the loop (`--leaf`
  leaves them on the parent). A `next_or_finish` can't be added on its own.
  Confirmed live 2026-10-27, with `roundtrip --execute` clean after each add
  and remove.
- **`add` of an `exit_loop` goes in with `--leaf`.** The spec is
  `{"step_type": "exit_loop"}`, plus `"action_exit_loop": {"loop_to_exit":
  "<loop key>"}` to end a loop around the innermost one. Without `--leaf` the
  add is refused, because the steps it would adopt would never run.
- **`remove` splices:** children adopt the removed step's parent and inherit its
  branch position, and `go_to` references retarget to that parent. Removing a
  condition or goal that has children requires `--cascade`, which deletes the
  whole subtree. So does a loop with body steps, where the subtree is the body
  and everything after the loop. A loop with an empty body is removed with its
  `next_or_finish`, the steps after it move up, and its item variable step
  stays. A `next_or_finish` can't be removed, even with `--cascade`: remove its
  loop instead. Removing the last body step leaves an empty body, which is
  valid.
- **The post-apply diff is positional**, so a mid-graph change shows up as
  every later step shifting. Verify structure with `show` and treat the diff as
  the audit trail, not as a minimal changeset.
- **Step/trigger `id`s survive editing** — every verb echoes back the `id` it
  read from GET for every step/trigger, including the ones it isn't touching,
  so nothing outside the automation that stored a step UUID breaks, and
  Kizen's own execution-history view keeps showing history against the same
  steps (confirmed live 2026-08-10) instead of marking them deleted. Only the
  synthesized `key`s rotate — `go_to` references are re-keyed onto those, same
  as before.
- **A step setting the CLI doesn't model stops the loop before the PUT,**
  on any step, not only the one you're editing, rather than dropping the
  setting. The error names the step and the block; change that step in the
  Kizen UI.

## See also

- `kizen automations step-types show <type>` — one step type's block.
- `kizen docs show automation` — the graph rules and what an update keeps.
- `kizen docs show automation-runtime` — watching and controlling a run.

# Kizen CLI — command map

A map of what exists. Run `kizen <group> <cmd> --help` for authoritative
flags and behavior — this file can lag; `--help` is generated from the code.

Operating rules (the approval gate, when to pull live state, how mutations
work) are in `kizen docs show operating`. Wire formats and quirks live with
each entity — `kizen docs list` for the surface topics.

## Available commands

### Read (always safe)

```
kizen envs list                              # show env this directory resolves to
kizen objects list                           # list objects, custom and built-in (e.g. Contacts)
kizen objects get <api_name>                 # one object incl. categories, fields, option UUIDs; full stage detail for pipelines
kizen objects stages list <pipeline>         # a pipeline's stages: name, status, chance-to-close, order
kizen automations list                       # list automations (summary)
kizen automations get <api_name>             # one automation incl. triggers + steps
kizen automations show <api_name>            # step tree with synthesized step keys (handles for steps verbs)
kizen automations steps get <api> <key>      # one step's wire JSON (starting point for steps edit)
kizen automations roundtrip <api_name>       # translate + graph-validate (add --execute to PUT + drift-check)
kizen automations diff <api_name> --spec-file <path>  # preview what `update` from this spec would change — read-only
kizen automations llm-models                 # live model_name + business_plugin_app_id catalog (see kizen docs show automation)
kizen automations runs list <api_name>       # recent runs for an automation
kizen automations runs view <exec_uuid>      # one run: summary + step-by-step trace (per-step status/duration)
kizen automations runs view <exec_uuid> --no-steps   # summary only (status + record + start/finish)
kizen automations runs view <exec_uuid> --wait       # block until the run finishes (see kizen docs show automation-runtime)
kizen automations runs logs <exec_uuid>              # each step's detailed_log (code_step stdout/traceback, etc.)
kizen automations modification-history <api_name>    # who changed this automation, when, what changed
kizen automations failures <api_name>                # recent step-failure history
kizen dashboards list                        # list dashboards + homepages (mine)
kizen dashboards get <uuid|api_name>         # one dashboard + dashlet summary (--raw for full config)
kizen layouts list <object_api_name>         # record layouts on a custom object
kizen layouts get <object_api_name> [--name] # one layout's block/column structure
kizen records get <object> <uuid>            # one record with all field values
kizen records get <object> --name "<name>"   # look up one record by its name field (exact, errors if ambiguous)
kizen records list <object>                  # list records; table shows id + name, --json/-o csv show every field
kizen records list <object> --search <text>  # filter records by text
kizen records list <object> --filter '<json>'  # structured filter; {"all"|"any": [{"field","op","value"}]}
                                               # or raw {"query": [...]} groups; --filter-file for a path
kizen records list <object> --fields a,b,c   # fetch id, name, and those field api_names; shown as table columns too
kizen records related <uuid>                 # a record's related pipeline records (any object, no object arg)
kizen records field-values <uuid> <field>    # all values from a summarized relationship field
                                               # <field> is a UUID or "object_api_name.field_api_name"
kizen team search <name>                     # find team member UUIDs by name or email
kizen messages templates list                # email templates (source for `messages create --template`)

kizen smart-connectors list [--search <t>] [--type <t>] [--status <s>]  # ETL/data-ingestion connectors
kizen smart-connectors get <connector>                  # one connector: detail + draft/live SQL script ids
kizen smart-connectors metadata                         # connector-type / matching-rule catalog (raw)
kizen smart-connectors executions <connector>           # run history (most recent first)
kizen smart-connectors execution-sql <connector> <eid>  # the SQL used in one execution
kizen smart-connectors scripts <connector>              # the connector's draft + live SQL scripts
kizen smart-connectors events <connector-uuid>          # event history / audit trail (UUID only)

kizen filter-groups list <object> [--search <text>]     # per-object saved filters (segments)
kizen filter-groups get <object> <uuid|name>            # one filter group's config + sharing_settings
kizen quick-filters list <object> [--search <text>]     # per-object quick-filter chips
kizen quick-filters get <object> <uuid|name>            # one quick filter's config + sharing_settings
kizen columns list <object> [--search <text>]           # per-object saved column layouts
kizen columns get <object> <uuid|name>                  # one column template's config + sharing_settings

kizen roles list                             # list roles (name, user count, # perm groups, default flag)
kizen roles get <name|uuid>                  # one role: default flag, app permissions, attached groups + level counts
kizen permissions groups                     # list permission groups (raw)
kizen permissions group <name|uuid>          # one group as a sectioned permission map (UI-style sliders); --fields adds per-field rows; --raw for wire JSON
kizen permissions meta                        # the permissions catalog (sections, capabilities, defaults)

kizen activities list [--object <api_name>] [--search <text>]  # activity types
kizen activities get <api_name|uuid>         # one activity type: metadata, fields, visibility rules
kizen activities fields list <activity>      # fields on an activity type
kizen activities logged get <logged_uuid>    # one logged instance + field values (read-only)
kizen activities logged list <activity>      # logged instances of a type (read-only)
kizen activities scheduled list [--activity <a>] [--mine] [--completed/--pending]  # scheduled instances
kizen activities scheduled get <uuid>        # one scheduled instance (read-only)

kizen forms list [--search <text>]           # forms (kizen surveys mirrors 1:1 under /api/surveys)
kizen forms get <api_name|uuid>              # one form: metadata + fields
kizen surveys list [--search <text>]         # surveys (same shape as forms, different base path)
kizen surveys get <api_name|uuid>            # one survey: metadata + fields
# forms/surveys create/update/set-ui + fields ARE wired (see Mutate below and
# kizen docs show form). A form needs both fields AND a form_ui page layout to
# render/submit. Submissions/subscribers/page-view/upload are not covered yet.
```

### Runtime (state-changing, but confirm-free)

```
kizen automations start <api_name> --record <uuid>              # fire on one entity
kizen automations start <api_name> --record <uuid> \
    --var org_match=true --var llm_notes="known input"          # seed variables for the run
kizen automations start <api_name> --record <uuid> \
    --vars-json '{"org_match": true}'                           # same, from a JSON object
kizen automations start <api_name> --record <uuid> --wait --show-logs  # fire, block, and stream step status + each step's log once it finishes

kizen code test --script s.py \
    --input n=21:number --input who=world:string \
    --output doubled:number --output greeting:string \
    [--secret MY_API_KEY] [--runtime python-3-13]              # unit-test a code_step script in the sandbox
kizen code test --script s.py --inputs-file in.json --outputs-file out.json  # bulk inputs/outputs (JSON)
```

`automations start` is the one state-changing command outside the
plan/preview/confirm gate. That's deliberate: it triggers an *existing*
automation on a record (a runtime action), it doesn't create or alter schema,
so there's nothing to preview and no destructive blast radius. It prints the
new `execution_id` and the `kizen automations runs view <id>` command to
watch it. (A standing decision, not an oversight.)

`--record` is **optional**: global (record-less) automations start without it.
Record-based automations (those bound to a custom object) still require
`--record` and error clearly if it's missing.

- **`--var name=value` (repeatable) / `--vars-json '{name: value}'`** seed the
  automation's variables for that one run — the way to exercise it with a
  known input. Names are validated against the automation's declared variables
  (a typo errors with the valid list, rather than silently no-opping); values
  are sent as strings and the server coerces by each variable's `data_type`.
  `--var` wins over `--vars-json` on conflict.
- **`--record` is routed automatically**: contact (`client_client`)
  automations receive it as `client_id`, custom-object automations as
  `record_id` — you pass one id regardless of the object.

`code test` (`POST /api/coderunner/run`) runs a Python script in the same
secure Lambda sandbox `code_step` uses — standalone, no automation, no record,
nothing created in the env — so it's the primitive for unit-testing a
code-step script before wiring it into an automation. Confirm-free like
`automations start`. The script uses the code-step namespace (`inputs.<name>`
to read, `outputs.<name> = …` to write, `outputs.log("…")` to emit a debug
line — plain `print()` is NOT captured); `kizen.api` works inside with auth
auto-injected (paths relative to `/api`, e.g. `/custom-objects`).
Inputs/outputs are typed by a data_type **name** (`number`, `datetime`, …) or
its short code (`n`, `dt`, …) — both work, unknown defaults to `string`; it is
NOT a raw `field_type` (`integer` maps to `number` for you). Scalar values are
sent as strings and the server coerces by type. Read the script from `--script
<file>` or stdin; use `--inputs-file`/`--outputs-file` (JSON) for many
inputs/outputs, where individual `--input`/`--output` flags override same-named
file entries. Full author → test → wire-in workflow (and the confirmed code
table) in `kizen docs show code-steps`.

```
kizen automations runs pause <exec_uuid>                        # pause a running execution
kizen automations runs resume <exec_uuid>                        # resume a paused execution
kizen automations runs cancel <exec_uuid>                        # cancel an execution (irreversible)
kizen automations runs skip-and-resume <exec_uuid> --skip-step <step_uuid> [--branch yes|no]
    # resume an execution paused on a step failure, by skipping that step
kizen automations runs debug-rerun <exec_uuid> --step <step_uuid>      # re-run one step, no downstream steps scheduled
kizen automations runs debug-restart <exec_uuid> --step <step_uuid>    # restart from a step, downstream steps ARE scheduled
kizen automations runs debug-step <exec_uuid> --history <id> --action execute|skip|debug [--branch yes|no]
kizen automations runs debug-sendit <exec_uuid>                  # run a debug-mode execution to completion
```

These execution-control verbs are confirm-free for the same reason `start`
is: they act on an execution's own runtime state, not schema. Confirmed live
for pause/resume/cancel; the debug-* family is wired from the public API
schema but not live-exercised (needs a debug-mode execution with real
step/history ids — see `kizen docs show automation-runtime`).

### Mutate (plan → preview → confirm → apply, all in one command)

Every mutation verb builds a plan from live state, renders it, and
y/N-confirms before applying. `--dry-run` stops after the preview (safe to
run without approval). `--yes` skips the confirm — use it only after the
user approved the dry-run output in chat. `--json` emits machine-readable
output (the plan with `--dry-run`, apply results otherwise).

```
# create
kizen objects create --api-name X --name Y [--object-type standard|pipeline] [--pipeline] [...]
kizen categories create <object_api_name> --api-name X --name Y
kizen fields create <object_api_name> --api-name X --type text --category Y [...]   # one field
kizen fields create <object_api_name> [--spec-file path | < stdin]                  # many at once
kizen automations create [--spec-file path | < stdin]
kizen dashboards create [--spec-file path | < stdin]            # dashboard/homepage + dashlets
kizen layouts update <object_api_name> [--spec-file | < stdin]  # PUT-replace a record layout
kizen records create <object> --field api_name=value [...]      # one record (values resolved vs schema)
kizen records create <object> [--spec-file f.csv|f.json | < stdin]   # bulk create from CSV/JSON

# update — only the flags you set are changed; if no diff, op is "skip"
kizen objects update <api_name> [--object-name X] [--description Y]
kizen categories update <object_api_name> <current_name> --name <new>
kizen fields update <object_api_name> <field_api_name> [--name X] [--required] [...]
kizen automations update [--spec-file path | < stdin]
kizen dashboards update <uuid|api_name> [--spec-file | < stdin]   # metadata + dashlets (diff by id)
kizen records update <object> <uuid> --field api_name=value [...]    # one record
kizen records update <object> [--spec-file f.csv|f.json | < stdin]   # bulk update (each row needs an 'id')
kizen records upsert <object> <lookup_value> --field api_name=value [...]  # create-or-update by lookup_value
kizen records upsert <object> [--spec-file f.csv|f.json | < stdin]   # bulk upsert (each row needs 'lookup_value')
kizen records set-field <object> <uuid> [<uuid> ...] --field X --value Y [--resolution ...]
    # set one field to one value across many records in ONE call (bulk-change-field-value).
    # Id-targeted only — build the id list with `records list --filter` first;
    # no server-side bulk-by-filter wired up yet (see kizen docs show records).

kizen filter-groups create <object> --name X [--filter '<json>'|--filter-file f] [--hidden] [--owner uuid]
kizen filter-groups update <object> <uuid|name> [--name] [--filter ...] [--hidden/--visible] [--owner]
kizen quick-filters create <object> --name X [--filter '<json>'|--filter-file f] [--owner uuid]
kizen quick-filters update <object> <uuid|name> [--name] [--filter ...] [--owner]
kizen quick-filters apply-to-roles <object> <uuid|name> --role <name|uuid> [...]
kizen quick-filters apply-to-users <object> <uuid|name> --user <uuid> [...]
kizen columns create <object> --name X [--config-file f] [--owner uuid]     # configuration_json opaque, copy from `columns get --json`
kizen columns update <object> <uuid|name> [--name] [--config-file f] [--owner]
kizen columns apply-to-roles/apply-to-users/apply-to-permission-groups <object> <uuid|name> --role/--user/--group <name|uuid> [...]

# delete — objects, categories, records, fields (and field options)
kizen objects delete <api_name>                                # archive an object + its data
kizen categories delete <object_api_name> <category_name>
kizen filter-groups delete <object> <uuid|name>
kizen quick-filters delete <object> <uuid|name>
kizen columns delete <object> <uuid|name>
# activities (activity types / loggable definitions) — logging/scheduling stays in the UI
kizen activities create --name X [--api-name Y] [--association-mode ...] [--editable] [--spec-file f]
kizen activities update <activity> [--name] [--description] [--object <api_name> ...] \
    [--association-mode ...] [--visibility-rules-file f] [--spec-file f]
kizen activities delete <activity>
kizen activities fields create <activity> --name X --type text [--option ...]   # native activity field
kizen activities fields create <activity> --linked-field object_api.field_api    # surface an existing CO field
kizen activities fields create <activity> [--spec-file f]                        # bulk (either kind)
kizen activities fields update <activity> <field_api_name> [--name] [--required] [--hidden] [--order N]
kizen activities fields delete <activity> <field_api_name>
kizen activities fields options add <activity> <field> --option "Label" [...]
kizen activities fields options remove <activity> <field> <option> [--remap-to <other>]

# forms & surveys — full CRUD wired (surveys mirror forms under /api/surveys). Spec: kizen docs show form
kizen forms create [--spec-file f | < stdin]                           # FormDef (may include fields)
kizen forms update <form> [--spec-file f | < stdin]                    # FormDef changes
kizen forms set-ui <form> [--spec-file f | < stdin]                    # form_ui page layout (required to render)
kizen forms fields create <form> [--spec-file f | < stdin]             # FormFieldDef list
kizen forms delete <form> ; kizen forms duplicate <form> [--name X]
# a form needs BOTH fields and a form_ui layout to render/submit.
# submissions, subscribers, page-view, and upload endpoints are NOT covered yet — later slice

# smart connectors — local dev loop: pull → run → push (replaces the UI download/copy-paste cycle)
kizen smart-connectors pull <connector> [--dir path] [--live] [--force]   # build a local workdir (connector.sql + __config.json + data/)
kizen smart-connectors run [--dir path] [--dry-run]        # execute connector.sql locally via embedded ClickHouse; needs the 'connectors' extra
kizen smart-connectors add-input <file> [--dir path]       # normalize a CSV/Excel/ZIP into data/ + patch config (needs 'connectors' extra)
kizen smart-connectors push [--dir path] [--publish] [--dry-run] [--yes]  # write connector.sql back to the draft; --publish promotes it live
# `run`/`add-input` need `uv sync --extra connectors` (chdb). `push` previews a SQL diff and confirms
# (mutation gate) before writing; only --publish makes a live change. See kizen docs show smart-connectors.

# smart connectors — building one from scratch, in order. Spec: kizen docs show smart-connector-flow
kizen smart-connectors create <name> --object <api_name> [--type spreadsheet|webhook|schedule|activity|...]
                              [--cadence secs] [--activity-object <activity type>] [--sql-version 4.1.x]
kizen smart-connectors set-input <file> --connector <c> [--no-regenerate] [--force]  # upload the reference file + generate the SQL template
# → iterate on the SQL with pull → run → push
kizen smart-connectors generate-sample <connector> [--no-wait]   # server-side output sample; publish is blocked until this succeeds
kizen smart-connectors suggest-variables <connector> [--spec]    # infer execution variables from the file's columns (writes nothing)
kizen smart-connectors configure-flow [<connector>] --spec-file f   # execution variables + load steps (object/field/variable writes)
kizen smart-connectors activate <connector> [--status operational] # a LIVE run without this sits queued forever, silently
kizen smart-connectors start-flow <connector> [--live] [--force]   # queue a run; dry run unless --live
# `set-input` refuses to REPLACE an existing reference file: swapping one is broken server-side (the executor
# keeps reading the old file). Build a fresh connector instead.

# smart connectors — read from other Kizen objects (exposed to the SQL as a kizen.<object> view)
kizen smart-connectors seeds list <connector>
kizen smart-connectors seeds add <connector> --object <o> --group <saved filter group> [--field f ...]
kizen smart-connectors seeds remove <connector> --object <o>
# --group is a saved filter group / segment (`kizen filter-groups list <o>`), NOT a field category.
# `add` refreshes the script config so the view exists (your SQL is kept) — without that a seed does nothing.
# `pull` exports each seeded object's rows to data/ from the same filter group, so `run` hits the same joins.

# smart connectors — webhook connectors (triggered by a real inbound POST, never by start-flow)
kizen smart-connectors webhook-sample <path> --body '<json>' --employee <email|name|uuid>  # the reference file they need
kizen smart-connectors send-webhook <connector> --body '<json>|@file' [--query k=v]        # fires it for real
# create --type webhook pins sql_version 4.1.x (lower 500s) and set-input drops the generated
# `output.webhooks` debug table (no such object; it crashes sample generation). Inbound requests are
# BATCHED on the connector's cadence, so an execution appears within that window, not immediately.

kizen records delete <object> <uuid> [<uuid> ...]              # archives (does not erase); see docs show records
kizen records archive <object> <uuid> [<uuid> ...]              # same effect as delete, named for what it does
kizen records unarchive <object> <uuid> [<uuid> ...]            # reverse of archive/delete
kizen fields delete <object_api_name> <field_api_name>         # delete a field (drops its data everywhere)
kizen fields options add <object> <field> --option "Label" [...]          # add select-field options
kizen fields options remove <object> <field> <option> [--remap-to <other>] # remove one option

# pipeline stages (a sub-resource of pipeline-type objects, not field options)
kizen objects stages create <pipeline> --name X [--status open|won|lost|disqualified] [--pct N] [--order N]
kizen objects stages update <pipeline> <stage> [--name] [--status] [--pct] [--order]
kizen objects stages remove <pipeline> <stage> --move-to <stage>   # migrates the stage's records first
kizen records move <pipeline> <uuid> --stage <name>                # move a record between stages

# roles & permission groups (names accepted everywhere — never make someone type a UUID)
kizen roles create --name X [--group <name|uuid> ...] [--permission <flag> ...] [--default]
kizen roles update <name|uuid> [--name Y] [--group <name|uuid> ...] [--default/--no-default]  # --group REPLACES the set
kizen roles delete <name|uuid>
kizen permissions group-create --name X [--base default|clone] [--from <name|uuid>] [--settings-file f]
kizen permissions group-update <name|uuid> --settings-file f       # raise/lower controls on an EXISTING group; same op shapes as group-create
kizen permissions group-delete <name|uuid>

# patch one automation step (GET → translate → mutate node → validate → atomic PUT)
kizen automations steps edit <api> <key> [--spec-file path | < stdin]
kizen automations steps add <api> --parent <key> [--branch yes|no] [--leaf] [--spec-file | < stdin]
kizen automations steps remove <api> <key> [--cascade]

# notify_member_via_email step content (a separate resource, not inline — see below)
kizen messages create <api> --template <name|uuid>          # UUID → step's email_template_id
```

Step keys come from `kizen automations show` and re-synthesize after every
apply — always re-run `show` before the next step operation. The post-apply
output includes the before/after semantic diff as the audit trail. See
`kizen docs show automation` / `automation-step` ("GET and PUT are different
Editing") for the full model.

The canonical loop:

```
kizen fields create ... --dry-run     # render the plan for the user
# user approves in chat
kizen fields create ... --yes         # re-plans against live state and applies
```

When the spec comes from stdin, the command can't prompt — preview with
`--dry-run`, then re-run with `--yes` (or use `--spec-file`). A saved plan
(`--dry-run --json > plan.json`) can also be executed later with
`kizen apply [--plan-file path | < stdin] [--yes] [--json]`.

**Bulk field creation.** To build a multi-field object in one shot, pass
`fields create` a JSON spec (via `--spec-file` or stdin) instead of the
single-field flags — one plan/confirm/apply for the whole batch. The spec is
either a list of field objects or `{"category": "<default>", "fields": [...]}`;
each field is a `FieldDef` shape (`name`, `api_name`, `field_type`, plus the
type-specific block) and may carry its own `"category"` (per-field overrides
the spec-level default, which overrides the `--category` flag). Single-field
flag mode and bulk mode are mutually exclusive.

**Record data (create/update/delete).** Records are *data*, not schema, but
they run through the same plan → preview → confirm → apply loop as everything
else (so a bulk load is previewed and logged). Values authored as
`--field api_name=value` or CSV cells are resolved against the live object
schema: dropdown/status/radio option **labels** become option UUIDs,
relationship values become `{"id": <record_uuid>}`, and checkbox/number
strings are coerced by field type. A value that starts with `[` or `{` is
parsed as JSON (for multi-select lists or explicit refs). For full wire
control, a JSON spec record may carry a raw `"fields": [{name/id, value}, …]`
list, which is passed through untouched. Bulk input is CSV (header row =
field api_names; an `id` column targets updates; blank cells are skipped) or a
JSON list of objects. Useful for standing up test/reference data. Contacts use
`client_client` as the object identifier here too.

**Idempotent loads (`records upsert`).** Re-running a `records create` load
duplicates records — `records upsert` is the fix. It matches an existing
record by `lookup_value` (the object's name field; email for contacts) and
updates it in place, or creates one if nothing matches. Bulk spec rows carry
`lookup_value` instead of `id`. `--oncreate-unarchive` (`prompt|unarchive|
overwrite`) controls what happens on create when an archived record already
matches; `--onupdate-conflict overwrite` lets an update proceed past an
archived-record naming conflict. Omit both to keep the server's default
(conflict-raising) behavior. Note `lookup_value` is a single string matched
against the object's identifying field — there's no separate "which field to
match on" option.

**Field options & deletion.** `fields options add/remove` edit a select-type
field's options in place via Kizen's dedicated option endpoints (not a field
PATCH). `remove` drops the option and its data unless `--remap-to <other>` is
given, which reassigns affected records to another option first. `fields
delete` removes a field and its data across all records — irreversible, so
confirm intent.

**Pipeline stages.** On a pipeline-type object, `objects get` shows full stage
metadata (status, chance-to-close, order) in a `stages` block — richer than
the mirrored `stage` field's options (id/name only). Stages live at a
dedicated `/api/pipelines/{id}/stages` endpoint, not the object's field-option
list, so `fields options add/remove` **cannot** manage them — it detects a
stage-backed field and refuses rather than silently no-op'ing (this used to
report a fake success; see `kizen docs show objects`). Use `objects stages
create/update/remove` instead; `remove` requires `--move-to` since Kizen
always migrates the removed stage's records onto another one. `records move`
is the runtime counterpart — moves one record between stages.

**Relationship targets.** `objects get` resolves each relationship field's
target object to a readable api_name plus cardinality — the table `target`
column shows e.g. `clinics (many_to_one)`, and `relation_target` /
`relation_cardinality` are in the JSON/CSV output (no more hand-resolving the
`related_object` UUID).

Cross-env transport (`dev → uat → prod` etc.) is intentionally NOT
part of this repo — it lives in a separate project so the prod-touching
risk has its own guardrails. If a user asks to migrate, point them at
that other project.

### Setup

```
kizen init --profile <label>    # store creds centrally + pin this directory
kizen docs list                 # documentation topics
kizen --version                 # installed version
kizen upgrade --check           # is a newer version available? (quiet, cached)
kizen upgrade                   # update in place, however this was installed
```

`upgrade --check` is safe to run unconditionally at session start: it caches
its answer for a day and always exits 0, staying silent when it can't tell.
`upgrade` itself detects the install shape — editable checkout, `uv tool`,
`pipx`, or a direct VCS install — and runs the right commands for it;
`--dry-run` shows them without running anything.


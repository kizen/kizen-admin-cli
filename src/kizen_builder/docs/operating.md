# Operating a Kizen environment with the `kizen` CLI

How to work in an environment folder: the approval gate, when to pull live
state, how mutations are planned and applied, and the conventions that keep
a session from acting on stale assumptions.

Command syntax lives in `kizen --help`; the command map in
`kizen docs show commands`. Everything about one kind of entity — spec shape,
wire formats, quirks — is in that surface's own topic (`kizen docs list`).

## Operating model

**0. Read commands never need approval.** Run `kizen objects get/list`,
`kizen automations get/list`, `kizen records get/list`, `kizen team search`,
and all other read-only CLI commands freely without asking. Mutation verbs
(`create`/`update`) run with `--dry-run` are also safe — they only render
the plan. Approval is required before running a mutation verb without
`--dry-run` (that's the step that applies).

**1. One env per working directory.** Environment selection is *positional* —
the working directory is pinned to one profile via a committed `.kizen/profile`
file that records the expected `business_id`; the credentials themselves live
centrally in `~/.config/kizen/credentials.toml` (0600), keyed by profile name.
Commands resolve the profile from the pin automatically — no env label argument
— and refuse to run if the resolved profile's `business_id` doesn't match the
pin. A second environment is a second folder with its own pin. Profile-name
resolution order: `--profile`/`-p` > `$KIZEN_PROFILE` > `.kizen/profile` pin >
`$KIZEN_ENV` (legacy label); the credentials for that name come from the
central store. `kizen envs list` shows what the current directory resolves to.
`kizen init` asks which Kizen environment you're on — `go`, `fmo`, `staging`,
or `integration` — and resolves the name to its API host itself, so nothing
gets pointed at the wrong host by hand; free-text URL entry is still there for
self-hosted or one-off setups, reached with a deliberate `url` choice, not a
default.

**2. Pull live before reasoning.** Before planning any change, call the
relevant read commands to fetch the current state of the env. Don't assume
from prior turns or from any local notes — Kizen state can change between
sessions, and notes record intent, not reality.

**3. Plan, then apply.** The pattern is: run the mutation verb with
`--dry-run` → show the rendered plan to the user → wait for approval →
re-run without `--dry-run` (add `--yes` since approval already happened in
chat). Never apply without explicit approval, even for additive changes that
look safe.

**4. Use the CLI for exploration; Python only for production scripts.** When
investigating Kizen state — looking up fields, reading records, tracing
automation steps — always use `kizen` CLI commands. Do not write ad-hoc
Python scripts to probe the API. Raw Python is appropriate only for
production scripts that will run as Kizen code steps (use the `kizen.api`
wrapper available inside the sandbox — see `kizen docs show code-steps`) or
standalone Lambda functions (call the API directly). Endpoint patterns are in
each surface's topic — `kizen docs show records` for record CRUD, including the
`client_client` identifier contacts use.

**5. Keep the CLI current.** Run `kizen upgrade --check` at the start of a
session. It reports whether a newer version exists, caches the answer for a
day, and always exits 0 — offline or with no channel configured it just says
so quietly, so it's safe to run unconditionally. `kizen upgrade` then applies
it, using whatever is right for how this CLI was installed. Docs ship with the
package, so upgrading updates the instructions too.

**6. Confirm rendered output in the browser.** Build and re-apply changes
through the CLI — it's the source of truth for what got written and it's
where approval happens. But the CLI has no renderer: it cannot show you a
dashlet's chart, an email's HTML body, or a condition's resolved option
labels, so a write it reports as successful can still be wrong on screen.
Treat the two surfaces as one workflow rather than the browser being a
fallback for when something looks broken — open it to confirm anything a
human would look at: a dashboard, a dashlet, an email body, a condition or
filter's displayed labels.

## Where to find things

These sources, in this order — don't guess, and don't grep `src/` for any of them:

| You need… | Go to | How |
|-----------|-------|-----|
| **Command syntax** — what flags a command takes, what it does | `kizen <group> <cmd> --help` | Authoritative and always current (it's generated from the code). Read-only, no approval. Start here for any command. |
| **Everything about one kind of entity** — the spec shape a `--spec-file` wants, *and* its wire formats, endpoints and quirks | `kizen docs show <surface>` | One doc per surface: `objects`, `field`, `records`, `automation`, `automation-step`, `automation-runtime`, `activity`, `form`, `dashboard`, `layout`, `saved-views`, `permission-group`, `smart-connectors`, `smart-connector-flow`, `email-templates`, `files`. Each command's `--help` names its topic. |
| **Filters** — the DSL, the wire format, per-type operators | `kizen docs show filters` | One shape shared by `records list --filter`, saved views, condition steps, `search_records`, and dashlets. |
| **Writing a `code_step` script** — the namespace, how inputs are typed, the test loop | `kizen docs show code-steps` | Write → `kizen code test` → wire into an automation. |
| **Which topic do I want?** | `kizen docs show reference` | A router plus the conventions that hold across every surface. |
| **What commands exist** — the map of the surface | `kizen docs show commands` | A map, not a contract. `--help` is the authority; this can lag. |

Rule of thumb: `--help` tells you *how to call* a command; `kizen docs show
<surface>` tells you everything about the thing you're building — spec shape and
wire behavior in one place. **Don't grep for a quirk; open the surface's doc.**
`kizen docs list` is the index.


## Conventions

- **Never invent api_names.** When the user asks about an object or
  automation, run `kizen objects list` (or `kizen automations list`) and pick
  from what's actually there. `kizen objects list` includes built-in objects
  (Contacts) alongside custom ones. Contacts are also reachable via
  `kizen records list client_client` (or `kizen records get client_client <uuid>`).
  For contact field schemas and option UUIDs, use `kizen objects get client_client`.
- **Kizen is the source of truth, not your notes.** If a prior session's
  notes say field X was created but `kizen objects get` doesn't show it,
  Kizen wins — refresh and note the drift in your reply.
- **Show, don't paraphrase.** When relaying read results, prefer the actual
  command output (or a tight summary of it) over free-text descriptions. The
  user wants the ground truth.
- **Don't migrate from this repo.** Cross-env transport has its own
  project for blast-radius reasons. If the user asks to push something
  from dev to staging/prod, redirect them rather than scripting it here.


## Verifying rendered output

A write that the CLI reports as successful can still be wrong on screen.
Nothing in the CLI renders a chart, an HTML body, or a resolved label — it
plans and applies the underlying data, and stops there. Confirming the
rendered result needs a human in the browser, every time the change touches
something a human looks at:

- **Dashboard and dashlet layout and data.** A dashlet spec that plans clean
  and applies clean can still duplicate on screen (a repeat `dashboards
  update` without the existing dashlet `id`s creates instead of updating) or
  render its data wrong (a filter the CLI reported as a clean success can
  still break the dashlet that consumes it). Open the dashboard.
- **Email and other HTML body rendering.** The CLI validates and stores the
  template; it has no renderer for the HTML that gets sent. Open the
  template or a test send.
- **Condition and filter label display.** A condition step's option values
  are stored and evaluated correctly even when the automation builder UI
  shows their labels as blank or malformed — this is a known display bug in
  the Kizen product's builder UI, not a CLI limitation. Confirm the
  underlying filter is correct (e.g. with `automations get` or a negative
  test against the gate) rather than trusting what the builder UI displays
  for the labels.

**The record Timeline is the single best artifact for confirming an
automation actually did what it was supposed to** — it shows the automation's
provenance against the record the way nothing else does, and the CLI has no
equivalent view today. Open it even when nothing looks wrong, not only when
chasing a failure.

For how to confirm a run itself reached a terminal state, see
`kizen docs show automation-runtime` — that's where the run-verification
mechanic is documented, whatever form it currently takes.

## What's *not* in the repo (and why)

- **No spec YAML files.** Desired state lives in the conversation; mutations
  are constructed inline. (Slice 2 will accept Pydantic-equivalent dicts as
  tool input.)
- **No state file.** No local `.kizen-state/` mapping spec api_names to
  Kizen UUIDs. Kizen is the source of truth; tools fetch live each time.
- **No drift detection.** With no local state, "drift" reduces to "read the
  live env and reconcile in conversation."
- **No multi-spec merging.** Each tool call is one entity at a time.

## Mutation flow (plan-then-apply)

Every mutation goes through this loop:

1. You construct the desired state inline (a JSON dict for automations,
   or named flags for fields/objects).
2. Run the mutation verb with `--dry-run`. This pulls live state,
   validates against it (e.g. resolves field_refs, detects collisions),
   and renders the plan — including the exact wire payload for automation
   updates (`last_revision`, carried-over server fields).
3. Show the plan to the user (the rich-table output is good for chat).
4. **Wait for explicit approval.** Even small additive changes need a
   "go" before applying. Don't apply on assumption.
5. Re-run the same command without `--dry-run`, adding `--yes` since
   approval already happened in chat. The command re-plans against live
   state and applies.

Plans are **ephemeral** — there's no on-disk plan store. Each run re-plans
from live state, so an approved change applied later is still validated
against reality at apply time. For automation updates, the plan bakes in
`last_revision` — if someone edits the automation between plan and apply,
the PUT fails loudly instead of clobbering their change.

### Building automation specs inline

For `kizen automations create` / `update`, the JSON is an `AutomationDef`.
**The full spec — graph rules (`parent_key`/`parent_branch`), the wired triggers
and step types, `field_ref` resolution, condition `filter_config`, the GET→PUT
wire dialect, and the quirks that bite — lives in
`kizen docs show automation`.** Read that before authoring one; don't grep
`spec.py`. A few load-bearing reminders:

- One root (`parent_key: null`); branch entries under a `condition`/`goal` set
  `parent_branch: "yes"|"no"`; rejoin branches with a branch group rather
  than duplicating tails.
- `field_ref: "<object>.<field>"` resolves to a UUID at apply time (portable
  across envs); bare `field_id` UUIDs work but are env-bound.
- `stop_execution` needs no config block — the planner emits the empty one.
- A type not in the wired list raises `PlanError` — add a builder following
  `_STEP_BUILDERS` / `_TRIGGER_BUILDERS`. Config models are `extra="allow"`, so
  they round-trip the richer shapes returned by `kizen automations get`; a key
  the type's builder doesn't read is still a plan error.
- Condition `filter_config` uses the shared filter DSL — the same one
  `records list --filter` and saved views take. `kizen docs show filters` has
  both layers (the friendly DSL and the wire form it resolves to), the per-type
  operator rules, and the variable-comparison clause the DSL doesn't cover.

### Secrets in code steps are env-specific

`code_step.secrets` references API keys / connection strings configured
per-env. The secret NAMES go in the spec; the actual values live in
Kizen, attached to the env. If a code step references a secret that
isn't configured on the env, it'll fail at runtime — surface this to
the user when authoring any automation that uses secrets.

### Step/trigger identity across an update depends on `id`

Every PUT replaces the whole step set instead of merging. `kizen automations
steps add/edit/remove` and `roundtrip` always echo back the `id` of every
step/trigger they read from GET, so anything that isn't the one node you're
touching keeps its identity — and its execution history in the Kizen UI —
across the write (confirmed live 2026-08-10). A step/trigger built from a
hand-authored spec (`plan-update-automation`) only keeps its id if the spec
sets one explicitly; a spec author who didn't seed from a live read gets a
fresh id for that step, same as before.


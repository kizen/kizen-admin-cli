# Architecture

How the `kizen` CLI is put together: the layers, the rule that makes mutations
safe to approve, and where a change belongs. For the annotated source tree and
the test tiers see [`CONTRIBUTING.md`](../CONTRIBUTING.md); for the rules that
govern changes see [`CLAUDE.md`](../CLAUDE.md).

## What the tool is

`kizen` is a command-line client over Kizen's REST API — an API with no stable
public contract, whose wire formats were established empirically and are
recorded per surface in `src/kizen_builder/docs/specs/`. It exists so a Kizen
environment (custom objects, fields, automations, dashboards, layouts, roles,
records) can be built from declarative spec files instead of clicked together in
a UI. It's built to be driven by an *agent* as much as by a person: every
mutation verb is a plan → preview → confirm → apply round-trip, machine-readable
output is a flag away, and the operating manual an agent needs ships inside the
package and is served by `kizen docs show`.

## The four layers

```
                    spec file (JSON/CSV)
                            │
                            ▼
    models/spec/      Pydantic models — validate desired state
                            │
                            ▼
    tools/planners/   plan_* functions — read live state, diff, build ops
                            │         ← may READ via api/, never writes
                            ▼
                     Plan { id, env, summary, operations[] }
                            │
                            ▼
    cli/              render preview  ──► user confirms  (--dry-run stops here)
                            │
                            ▼
    tools/plans.py    apply_plan() — sequential dispatch, deferred-ref patching
                            │
                            ▼
    api/              thin httpx wrappers, one module per endpoint family
                            │
                            ▼
                        Kizen REST API

    config.py  ── credentials chokepoint ──►  every KizenClient
    docs.py    ── docs-tree chokepoint   ──►  kizen docs show
```

**`api/`** — one module per endpoint family (`custom_objects.py`,
`automations.py`, `records.py`, `permissions.py`, …). Each function builds a
path, hands a dict to `KizenClient`, and returns the parsed response. No
business logic, no spec awareness, no user-facing formatting. `api/client.py`
owns the two things everything else depends on: the three auth headers
(`X-API-KEY`, `X-BUSINESS-ID`, `X-USER-ID`, injected on every request from
`EnvConfig.auth_headers()`) and `KizenAPIError`, the single exception type every
non-2xx response normalizes into. One error shape means callers catch one thing;
one injection point means no endpoint module can forget the headers.

**`tools/`** — read functions and orchestration, and what the CLI mostly calls:
`list_objects()`, `get_object(api_name)`, `search_records()`, step-graph surgery
in `steps.py`, the apply orchestrator in `plans.py`. Tools resolve credentials
(`load_env_config()`), open a `KizenClient`, call `api/`, and normalize the
result into the shape the CLI and the planners want.

**`tools/planners/`** — the `plan_*` functions. Each takes a validated spec plus
live state and returns a `Plan`. A planner **reads** live state — that's how it
detects "field already exists", resolves an api_name to a UUID, or diffs a
desired automation against the deployed one — but **never mutates**. There is no
POST/PUT/PATCH/DELETE anywhere under `planners/`. The whole approval model rests
on that: running a `plan_*` function, or a command with `--dry-run`, cannot
change anything in Kizen, so a plan is always safe to generate and inspect
before deciding.

**`cli/`** — Typer wrappers. Argument parsing, spec-file and stdin reading, Rich
tables, exit codes, and the shared `_run_mutation()` flow. Building a request
body here means it belongs in a planner instead.

One module per Kizen surface, plus two underscore-prefixed modules holding what
the rest share: `_shared.py` (the root `app` and its callback, the two Rich
consoles, and the `--output`/`--json` options) and `_mutations.py`
(`_run_mutation` and the plan/result renderers). `_shared.py` imports no command
module, so everything else in the package can import it.

`cli/__init__.py` imports every module so its commands register, and **the order
of those imports sets the order `--help` lists things** — Typer renders in
registration order. CONTRIBUTING.md § "Extending the CLI" has the rule and the
`scripts/dump_cli_tree.py` diff that guards it.

## The plan/apply contract

1. A `plan_*` function validates the spec (`models/spec/`), fetches whatever
   live state it needs to resolve names to UUIDs and to reject impossible
   changes, and returns a `Plan`.
2. `cli/` renders the plan — one table row per operation — and asks for
   confirmation. `--dry-run` stops there; with `--dry-run --json` it emits the
   plan as JSON on stdout.
3. `apply_plan(plan)` walks the operations in order, dispatches each to `api/`,
   and returns an `ApplyResult` with one `OperationResult` per op.

Planning and applying are separate functions, separately invoked, with a human
decision between them. That separation *is* the approval gate: the preview a
user approves comes from code that provably cannot write, and what runs
afterwards is the same object that was previewed, rather than a re-derivation of
it.

Plans are JSON-serializable and held in conversation context. `plan_to_json` /
`plan_from_json` let a plan be saved from a `--dry-run --json` run and fed back
to `kizen apply` later, but the tool keeps no on-disk plan store. A plan
records the env label (`Plan.env`) and `business_id` it was built against, and
`kizen apply` refuses a plan whose `business_id` differs from the resolved
profile's. Replaying a saved plan sends its payloads as they were at plan time,
without re-validating them against live state. The plan's `id` is a short
content hash used for display and cross-referencing rather than as a lookup key.

### `PlanOperation` carries both `preview` and `payload`

Every operation holds two dicts: `preview`, a human summary of what changes (the
plan table shows this), and `payload`, the literal JSON request body that will
be sent. Keeping both lets a reviewer see the intent *and* audit the wire body
without running anything. That matters most where a payload is assembled by
merging live server state — automation updates, for instance — because the
previewed payload is exactly what gets PUT.

Two fields handle intra-plan ordering: `deferred_parent_object_key` and
`deferred_category_key` name an *earlier op in the same plan* whose server UUID
isn't known until that op runs, and `apply_plan` resolves them from
`results_by_key` and patches the payload before dispatch. That's what lets one
plan create an object and the fields inside it. A companion guard skips any op
whose parent failed — by deferred ref, or by dotted-key prefix like
`accounts.status_code` — and a failed op never aborts the batch, so the caller
gets a complete report instead of a traceback over a half-applied plan.

## The two chokepoints

**`config.py`** resolves the active environment's credentials. Every
`KizenClient` in the codebase is constructed from an `EnvConfig` it returned.
Going around it — reading `os.environ` directly, hand-building headers — loses
three things at once: the profile-resolution order, the `business_id` pin check
below, and the single place to reason about where secrets come from. A second
credential path is a second way to end up pointed at the wrong environment,
which is the failure this design exists to prevent.

**`docs.py`** resolves the packaged docs tree. The docs live inside the package
(`src/kizen_builder/docs/`) rather than at the repo root, so they version with
the install and an agent reading them from a wheel gets the docs matching the
code it's running. `docs_root()` raises `DocsUnavailable` rather than degrading
quietly: a missing tree means the wheel was built without its `package-data`
globs, which is a packaging bug and should be loud. Guide topics are listed in
`GUIDE_TOPICS`; surface docs under `specs/` are discovered from disk, so adding
one needs no code change — but does need a `package-data` glob, which
`tests/test_docs_packaging.py` enforces.

## Environment resolution

Which environment a command acts on is decided positionally, by the directory it
runs in. Individual commands take no environment argument. The root callback has
a global override (`--profile`, aliased `--env`), and even that cannot escape a
pinned directory — see the checksum rule below.

- **Storage is central.** All profiles live in one TOML file at
  `$XDG_CONFIG_HOME/kizen/credentials.toml` (falling back to
  `~/.config/kizen/credentials.toml`), keyed by profile name, written mode
  `0600`, never committed.
- **Selection is positional.** A working directory pins itself with a committed,
  non-secret `.kizen/profile` recording a profile name *and* the expected
  `business_id`. `find_pin()` walks up from the cwd to find it.
- **Resolution order** for the profile name, in `load_env_config()`: explicit
  `env_name` argument > the `--profile` CLI override > `$KIZEN_PROFILE` > the
  `.kizen/profile` pin > `$KIZEN_ENV`. A `.env` in the working directory is
  loaded into the environment first, so it can supply those variables.
- **The pin is a checksum, not a default.** Whichever path selected the profile,
  if a pin exists and records a `business_id`, the resolved credentials'
  `business_id` must equal it or `load_env_config()` raises `ConfigError` and
  refuses. `business_id` is the canonical identity of a Kizen environment; the
  profile name is a label. So `--profile other-env` inside a pinned directory
  fails rather than silently retargeting.

A pinned directory plus the identity checksum is what stops an agent from
drifting onto the wrong environment: there's no ambient "active profile" pointer
to flip. `kizen apply` holds a saved plan to the same check: the plan's
`business_id` must equal the resolved profile's, so a plan saved in one folder
can't be replayed into another environment.

## Where a new surface's code goes

A new kind of Kizen entity touches every layer:

1. `api/<surface>.py` — thin wrappers for its endpoints.
2. `models/spec/` — Pydantic models for its spec shape.
3. `tools/planners/<surface>.py` — `plan_*` functions returning `Plan`s.
4. `tools/plans.py` — add its op kinds to the `Kind` literal and a dispatch
   branch in `_execute`.
5. `tools/<surface>.py` — read/show tools, if it has any.
6. `cli/<surface>.py` — Typer commands, routed through `_run_mutation` for
   mutations, with an `epilog` pointing at its docs topic. Add it to the import
   list in `cli/__init__.py` at the position you want it to appear in
   `kizen --help`.
7. `src/kizen_builder/docs/specs/<surface>.md` — spec shape above the divider,
   wire format and quirks below it.

Which doc owns which fact is covered in [`CLAUDE.md`](../CLAUDE.md).

## Registry gates

Some surfaces gate their supported variants behind an explicit registry dict.
The registry, rather than the docs or the models, says what's actually wired:

- `_TRIGGER_BUILDERS` / `_STEP_BUILDERS` in `tools/planners/automations.py` map
  an automation trigger or step type to the function that builds its wire block.
  A type absent from the dict raises with the sorted list of supported types,
  whatever a spec model would accept. Each builder also declares the block keys
  it reads (`@honours`), and any other key in a spec block is a plan error.
  Adding one means writing the builder, declaring its keys and registering it;
  CONTRIBUTING.md has the checklist.
- Field types are gated the same way, split across two files: the `FieldType`
  literal and its type-specific config models in `models/spec/` (validated by
  `_validate_type_specific_config`), and payload construction in
  `_build_field_payload` in `tools/planners/fields.py`. `ActivityFieldType` and
  `FormFieldType` are separate literals following the same pattern, since
  Kizen's field type enums differ per surface.

## Testing strategy, and its limit

Every test is offline. `tests/conftest.py` fakes credentials via env vars
(`BASE_URL` points at a reserved-TLD host that `respx` intercepts where needed),
redirects `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` at temp dirs so no test reads
the developer's real credential store or upgrade cache, and disables the
directory-pin lookup by default. The `patch_live_lookups` fixture monkeypatches
the live-state seams the planners use — `get_object`, `list_automations`,
`get_automation`, `list_objects`, and `LiveContext`'s field/option lookups — to
serve from sanitized fixtures under `tests/fixtures/` captured from real API
responses. Tests marked `live` are skipped unconditionally by
`pytest_collection_modifyitems`; they record an expectation rather than running.
CI never touches a Kizen API.

**The limit, stated plainly: the suite proves the tool builds the payloads we
believe are correct, not that Kizen still accepts them.** Fixtures are
snapshots. If Kizen changes a wire format, every test stays green and the first
signal is a 400 in someone's terminal. Wire-format correctness rests on
empirical findings dated `confirmed live <date>`, plus the maintainer-run live
tier at `tests/drift/` (`KIZEN_DRIFT_PROFILE=<profile> uv run pytest -m drift`,
never CI, since CI has no environment to reach). That tier has three parts: a
schema diff of `GET /api/docs/schema` against a committed snapshot
(`test_schema_drift.py`); a round-trip half that creates real entities from the
actual planner payloads and reads them back, covering objects, fields, records,
filtering, permissions, and every wired automation step and trigger type
(`test_roundtrip_*.py`); and a fixture-fidelity check that diffs the offline
`tests/fixtures/*.json` key-shape against live truth
(`test_fixture_fidelity.py`). CONTRIBUTING.md § "Schema-drift and round-trip
checks" has the full breakdown.

## `vendor/`

`src/kizen_builder/vendor/connector_runtime/` is Kizen *product* source,
vendored from the smart-connector local dev package. `script_runner.py` is kept
byte-for-byte identical to upstream on purpose: a connector's SQL must behave
the same locally as in production, and a local edit silently breaks that
equivalence. To fix a local concern, parametrize from the calling `tools/`
layer, which is what the recorded deltas in `process_new_input_file.py` do.

Re-vendoring procedure, per-file deltas, and runtime dependencies (the optional
`connectors` extra) are in
[`vendor/connector_runtime/PROVENANCE.md`](../src/kizen_builder/vendor/connector_runtime/PROVENANCE.md).

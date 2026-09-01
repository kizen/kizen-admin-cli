# Contributing to Kizen Admin CLI

This is the source checkout of the `kizen` CLI. If you're here to *design a
Kizen solution* rather than change the tool, that work happens in an environment
folder pinned to a profile, and its instructions come from the CLI itself
(`kizen docs show operating`).

For the rules that govern changes here — doc ownership, changelog entries,
naming — see [CLAUDE.md](CLAUDE.md). This file covers how the project is built,
laid out, and tested.

## Setup

```bash
git clone https://github.com/kizen/kizen-admin-cli.git "Kizen Admin CLI"
cd "Kizen Admin CLI"
uv sync --extra dev  # creates .venv from the committed uv.lock
uv run pytest        # confirm green before changing anything
```

The `dev` extra carries pytest, respx, ruff, and mypy; a plain `uv sync` prunes
them and collection fails on the missing imports. CI syncs the same way, with
`--locked` added.

`uv sync` is the dev setup: it builds the checkout's own `.venv`. Users install
with `uv tool install --editable .`, which puts `kizen` on `PATH` globally; see
[README.md](README.md).

`uv.lock` is committed and `uv sync` respects it. CI syncs with `--locked`, so a
dependency change that arrives without a re-lock fails there.

To work on the smart-connector local loop, add the `connectors` extra:

```bash
uv sync --extra connectors     # adds chdb (~100 MB native wheel) + spreadsheet readers
```

CI omits it, so `test_smart_connectors_tools.py`'s execution test `importorskip`s
and is skipped there. If you touch `vendor/connector_runtime/` or
`tools/smart_connectors/run.py`, run that test locally with the extra installed —
nothing automated will catch a regression.

`vendor/connector_runtime/script_runner.py` is Kizen product source vendored
verbatim so local runs match production. To fix a local-only concern,
parametrize from the `tools/` layer instead of editing it. Its `PROVENANCE.md`
has the re-vendoring procedure for when Kizen ships a new dev package.

To bring an existing checkout up to date:

```bash
kizen upgrade --dry-run   # show what updating this checkout would run
kizen upgrade             # git pull --ff-only + dependency reinstall, in one step
```

## Tests

```bash
uv run pytest                  # the default suite: offline, no network, fast
uv run pytest -m wheel         # opt-in: builds a wheel, installs it, runs the CLI
```

Four tiers, in increasing cost:

| Tier | What it proves | Cost |
|---|---|---|
| Default suite | Unit + wire-format contracts. `conftest` fakes credentials and serves every live-state lookup from fixtures, so nothing reaches the network. | seconds |
| `test_docs_packaging.py` (always on) | Every `package-data` glob matches real files, and every shipped `.md` is matched by some glob. | milliseconds |
| Same file, `importorskip("build")` | Builds a wheel and inspects it with `zipfile` — the docs really are inside. | ~2s |
| `-m wheel` (deselected by default) | Installs the wheel into a clean venv and runs `kizen` from outside the checkout. | ~10s |

The `-m wheel` tier is deselected via `addopts` in `pyproject.toml` to keep the
default run fast and hermetic; it shells out to `build` and `pip`, the one place
a test reaches an index. CI runs both, plus a `build-smoke` job that installs the
wheel and invokes the CLI from a directory where nothing in `src/` is importable.
That job catches a wheel that installs cleanly and then can't find its own
documentation.

Tests marked `live` need a real environment; `conftest` skips them
unconditionally, so they record an expectation rather than running.

## Source tree

Regenerate this from `git ls-files 'src/kizen_builder/**/*.py'
'src/kizen_builder/*.py'` when it drifts — every non-`__init__.py` module should
have a line here.

```
src/kizen_builder/
  api/                           # Thin httpx wrappers around Kizen REST endpoints.
    client.py                    #   the shared httpx client + auth headers
    activities.py                #   activity-type (loggable definition) CRUD
    automations.py               #   automation CRUD (automation2 namespace)
    coderunner.py                #   POST /api/coderunner/run — code_step sandbox runner
    custom_objects.py            #   custom-object / category / field CRUD
    dashboards.py                #   dashboard + dashlet CRUD, sharing settings
    external_refs.py             #   name→UUID lookups for by-name spec references
    files.py                     #   raw file bytes: the S3 upload/download legs (not JSON)
    forms.py                     #   form/survey CRUD (base_path-shared with surveys)
    layouts.py                   #   record-layout CRUD (field/block arrangement)
    messages.py                  #   automation-scoped message resources (notify_* step content)
    permissions.py               #   roles, permission groups, permissions catalog
    pipelines.py                 #   pipeline-stage CRUD + record stage-move
    records.py                   #   record-level reads/writes (custom objects + built-ins)
    saved_views.py               #   filter groups / quick filters / column templates
    schema.py                    #   schema lookups backing the filtering DSL
    smart_connectors.py          #   smart-connector CRUD, scripts, executions
    team.py                      #   team-member reads
  tools/                         # Tool functions. What the CLI mostly calls.
    activities.py                #   list/get activity types + logged/scheduled reads
    automations.py               #   get/list/show/roundtrip/start, executions, step patching
    coderunner.py                #   run a code_step script standalone via /api/coderunner/run
    dashboards.py                #   list/detail, sharing helpers, config builders
    dashlet_templates.py         #   generate a ready-to-edit dashlet config by type
    envs.py                      #   list_envs()
    form_ui.py                   #   build a form/survey's visual, submittable page layout
    forms.py                     #   list/get forms and surveys (base_path-shared)
    layouts.py                   #   list/get, block helpers, inject_layout_ids
    messages.py                  #   automation message content (notify_* step templates)
    objects.py                   #   list_objects(), get_object(api_name)
    permission_builder.py        #   enumerate/build/mutate the full permission-group structure
    permissions.py               #   list/get roles, permission groups, permissions catalog
    plans.py                     #   Plan / PlanOperation models, apply_plan()
    records.py                   #   get_record(), search_records()
    saved_views.py               #   list/get filter groups, quick filters, column templates
    smart_connectors/            #   inspection + authoring + local pull/run/push dev loop
      __init__.py                #     re-export facade — `from ... import smart_connectors as sct`
      _common.py                 #     MARKER_NAME, execution-metadata keys, shared helpers
      inspection.py              #     list/get connectors, executions, scripts, events
      pull.py                    #     pull_connector() — assemble a local workdir
      run.py                     #     run_connector(), add_input() — local chdb execution
      push.py                    #     plan_push/apply_push — send the local script back
      webhooks.py                #     webhook sample build + send
      seeds.py                   #     list/add/remove seed tables
      authoring/                 #     plan_*/apply_* authoring surface
        _helpers.py              #       connector/object/field lookups, scope resolution
        create.py                #       plan/apply create connector
        set_input.py             #       plan/apply set-input (file upload, regeneration)
        configure_flow.py        #       plan/apply configure-flow (variables, load steps)
        start_flow.py            #       plan/apply start-flow
        sample.py                #       generate_output_sample()
        status.py                #       plan/apply set-status
        variables.py             #       suggest_execution_variables()
    steps.py                     #   step-graph surgery (find/edit/insert/remove, validation)
    team.py                      #   search_team()
    planners/                    #   plan_* tools — produce Plans, never mutate
      activities.py              #     plan_create/update/delete activity types + fields
      automations.py             #     plan_create_automation, plan_update_automation
      dashboards.py              #     plan_create_dashboard, plan_update_dashboard
      fields.py                  #     plan_create_field, plan_update_field
      forms.py                   #     plan_create/update/delete forms/surveys + fields
      layouts.py                 #     plan_update_layout
      messages.py                #     plan_create automation message content
      objects.py                 #     plan_create/update_object, plan_create/update_category
      permissions.py             #     plan_create/update/delete roles + permission groups
      pipeline_stages.py         #     plan_create/update/remove stages, record stage-move
      records.py                 #     plan_create/update/delete records (data, not schema)
      saved_views.py             #     plan_create/update/delete the three saved-view kinds
  filtering.py                   # Filter DSL (Field, All/Any, search/filter-config rendering).
  translate.py                   # Automation GET → PUT wire translation.
  models/spec/                   # Pydantic shapes for desired state (spec inputs);
                                  #   package, one module per cluster — see __init__.py.
  output.py                      # Shared table/json/csv output layer for read commands.
  cli/                           # Typer wrappers around tools/. The `kizen` command.
    __init__.py                  #   exports `app`; imports every module below, IN ORDER
    _shared.py                   #   the root app + callback, consoles, shared output options
    _mutations.py                #   plan → preview → confirm → apply (`_run_mutation`)
    docs.py                      #   kizen docs
    envs.py                      #   kizen envs
    objects.py                   #   kizen objects — reads + create/update/delete
    stages.py                    #   kizen objects stages
    dashboards.py                #   kizen dashboards
    layouts.py                   #   kizen layouts
    records.py                   #   kizen records — reads
    records_write.py             #   kizen records — mutations + --field/spec parsing
    filters.py                   #   kizen filters (the operator reference)
    team.py                      #   kizen team
    permissions.py               #   kizen roles + kizen permissions
    automations.py               #   kizen automations — reads, start, diagnostics
    steps.py                     #   kizen automations steps
    messages.py                  #   kizen messages
    runs.py                      #   kizen automations runs
    fields.py                    #   kizen fields (+ options)
    categories.py                #   kizen categories
    filter_groups.py             #   kizen filter-groups
    quick_filters.py             #   kizen quick-filters
    columns.py                   #   kizen columns
    activities.py                #   kizen activities — types + shared object resolution
    activities_fields.py         #   kizen activities fields (+ options)
    activities_instances.py      #   kizen activities logged / scheduled
    forms.py                     #   kizen forms + kizen surveys (one factory builds both)
    forms_fields.py              #   …and its `fields` / `fields options` sub-apps
    automations_write.py         #   kizen automations — create/update/lifecycle
    folders.py                   #   kizen automations folders
    apply.py                     #   kizen apply
    smart_connectors.py          #   kizen smart-connectors — app, shared helpers, build/configure
    smart_connectors_seeds.py    #   kizen smart-connectors seeds
    smart_connectors_run.py      #   webhook samples, activate, start-flow
    smart_connectors_reads.py    #   connectors, executions, scripts, events
    smart_connectors_dev.py      #   the local dev loop: pull, run, add-input, push
    upgrade.py                   #   kizen upgrade
    init.py                      #   kizen init
    code.py                      #   kizen code test
  config.py                      # Resolves the active env's credentials (chokepoint).
  docs.py                        # Resolves the packaged docs tree (chokepoint) + env stubs.
  profiles.py                    # Central credentials.toml store + .kizen/profile pin.
  upgrade.py                     # Install-shape detection and update planning.
  utils.py                       # Tiny helpers (slugify).

  vendor/                        # Vendored Kizen product source. See its PROVENANCE.md.
    connector_runtime/
      process_new_input_file.py  #     input normalization (CSV/Excel/zip → CSV), parametrized
      script_runner.py           #     verbatim upstream SQL execution engine; do not edit

  docs/                          # Ships in the wheel. Served by `kizen docs show`.
    operating.md                 #   how to operate an env (the manual)
    commands.md                  #   the command map
    reference.md                 #   thin router + cross-surface conventions
    filters.md                   #   filter DSL, wire format, per-type ops (cross-cutting)
    code-steps.md                #   code_step Python: namespace, input typing, test loop
    specs/                       #   one doc per surface, + README index

docs/                            # Repo-only. Never ships. Maintainer procedures.
  ARCHITECTURE.md
  RELEASING.md

.kizen/profile                    # non-secret pin: profile name + business_id
~/.config/kizen/credentials.toml  # central secret store (0600), never committed
```

Two directories are named `docs`: `src/kizen_builder/docs/` ships and is
user-facing; `docs/` at the repo root is for maintainers. A new user-facing doc
belongs in the first one and needs a matching `package-data` glob in
`pyproject.toml`, which `tests/test_docs_packaging.py` enforces.

An *environment* folder is much smaller: a `.kizen/profile` pin plus the
`CLAUDE.md` / `AGENTS.md` stubs that `kizen init` writes.

## Extending the CLI

**A new command on an existing surface.** Document the entity in its existing
`docs/specs/<surface>.md` — spec shape above the divider, wire behavior below.
Give the command a Typer `epilog` pointing at `kizen docs show <surface>` (grep
`epilog=` under `cli/` for the pattern). `tests/test_docs_specs_links.py` fails
if an epilog names a topic that doesn't resolve.

**A new surface** — a kind of Kizen entity nothing covers yet. It touches every
layer; [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) § "Where a new surface's
code goes" has the layer-by-layer list. On the docs side, add
`docs/specs/<surface>.md` and list it in `specs/README.md` and in
`reference.md`'s router table; `kizen docs list` picks it up from disk.

**The import order in `cli/__init__.py` controls `--help`.** Each `cli/` module
registers its commands and sub-apps at import time, and Typer renders `--help`
in registration order — so that list sets the order groups appear in
`kizen --help` and the order commands appear under each group. It's curated
rather than alphabetical, which is why the block is fenced with `# isort: off`.
`scripts/dump_cli_tree.py` captures the whole tree plus every command's
`--help`; diff it after any change to the wiring, and refresh the baseline in
the same commit when the change is intentional:

```bash
uv run python scripts/dump_cli_tree.py | diff scripts/cli-tree-baseline.txt -
```

**A new cross-cutting topic** — one that spans surfaces, like `filters` or
`code-steps` — goes at the root of `src/kizen_builder/docs/` rather than under
`specs/`, and must be added to `GUIDE_TOPICS` in `docs.py`.

**A new automation trigger or step type**, in order:

1. Create `Action*Config(BaseModel, extra="allow")` in
   `models/spec/automations_actions_*.py`.
2. Add it as an optional field on `AutomationStepDef`
   (`action_*: Action*Config | None = None`, in `models/spec/automations.py`).
3. Add an entry to `_STEP_TYPE_TO_CONFIG_FIELD` in `models/spec/automations.py`.
4. Write a `_step_<type>(block, auto, ctx) -> dict` builder in
   `tools/planners/automations.py` and register it in `_STEP_BUILDERS` — or in
   `_TRIGGER_BUILDERS` for a trigger. These registries are the authoritative
   gate for what's wired.
5. Update the wired list in `src/kizen_builder/docs/specs/automation.md`.
6. If the type has an enum-typed field whose valid values you've confirmed
   (live, in a fixture, or via the drift snapshot once it captures enum
   values), add them to `KNOWN_ENUM_CHOICES` / `KNOWN_ENUM_CHOICES_TRIGGERS`
   in `tools/planners/automations.py` rather than only writing them into
   `automation.md` prose — that's what turns a future rejection of the same
   value into a message naming the alternatives instead of a guessing game.

Wiring a new type also widens the drift suite's scope, which will fail until
someone covers it.

## Schema-drift and round-trip checks

Every tier above is offline, which keeps them fast and also limits what they can
prove. This CLI's job is producing correct request bodies for *undocumented*
Kizen endpoints, and the payload tests compare against fixtures — a contract
this repo doesn't control. Kizen can change an endpoint while the whole suite
stays green, and the first person to find out is a solutions engineer mid-build
in a customer environment.

`tests/drift/` covers that. Run it yourself; it stays out of CI, which has no
environment to reach, and the `drift` marker is deselected by `addopts`, so a
plain `pytest` never reaches it.

```bash
KIZEN_DRIFT_PROFILE=<profile> uv run pytest -m drift
```

Run it before cutting a release, alongside `-m wheel`. With no
`KIZEN_DRIFT_PROFILE` set, every drift test skips with setup instructions, so
it's harmless to leave in a normal invocation.

### Configuring a target

The profile resolves through the CLI's normal machinery
(`kizen_builder.config`), so any name from `kizen envs list` works. Point it at
a **disposable production business**:

* The round-trip half **writes**. It creates several custom objects, a pipeline
  object, a dozen-plus fields, records (including upserts and bulk field
  changes), filter groups, an activity type, a form, a survey, a permission
  group, and eight automations — one of them *active*, carrying nothing but the
  auto-prepended manual trigger, since a `start_automation` step's target has to
  be — then deletes all of it. Never aim it at a customer environment.
* Target production rather than staging. Matching the CLI to a staging-only
  behavior and then shipping a payload production rejects is the failure this
  suite exists to catch.

The target comes from your shell, so it stays out of the repo.

### What it covers

| Half | File | Catches |
|---|---|---|
| Schema diff | `test_schema_drift.py` | Endpoints and request fields **appearing or disappearing**, going newly required, or changing type. |
| Live round-trip | `test_roundtrip_drift.py` | **Behavior** — payloads that still parse but no longer work, and read-shape changes the planners depend on. Objects, fields, permissions. |
| …automations | `test_roundtrip_automations.py` | The same, for **every** wired step and trigger type rather than one representative payload. Two meta-tests reconcile the covered set against `_STEP_BUILDERS`/`_TRIGGER_BUILDERS`. |
| …records | `test_roundtrip_records.py` | Create/update/upsert (both branches)/bulk-field-value/delete against a real object, including the bare-scalar `bulk-change-field-value` wire quirk and a delete-then-refetch 404. |
| …filtering | `test_roundtrip_filtering.py` | The DSL resolved to a real search query against real records — that the *right records* come back, not just the shape — plus a filter-group round trip proving a read-back `config` is wire-shaped passthrough. |
| Fixture fidelity | `test_fixture_fidelity.py` | Whether the committed `tests/fixtures/*.json` files still match live shape, as a structural key-shape diff: fixture keys must exist live, extra live keys are fine. |

Fixtures shared by more than one round-trip module (`drift_object`,
`drift_related_object`, `drift_automation`) live in `conftest.py`, since a
fixture defined in a test module is invisible to every other module.

You need both halves. `GET /api/docs/schema` returns a real OpenAPI 3.0
document (~557 paths, ~1,300 component schemas), but it doesn't match the live
API: it declares `PermissionGroupRequest` as having exactly one field
while the live endpoint takes a ~35 KB body, documents the automation step
envelope in the read dialect rather than the write one, names one step block
`action_llm_call` where live uses `action_call_llm`, and leaves `pipeline`
optional on an endpoint that rejects a pipeline object without it. Those
divergences are recorded with evidence and a `confirmed live` date in
`KNOWN_SCHEMA_OMISSIONS` and `KNOWN_UNDOCUMENTED_BLOCKS` in
`tests/drift/contracts.py`, and asserted in **both** directions: a divergence
that goes away fails the same as a new one, so the list stays accurate.

Scope is narrow on purpose — the mutation surfaces where a silent wire change
becomes a customer-environment incident (automations, objects, fields, records,
filtering, permissions). The automation step and trigger sub-schemas derive from
the CLI's own `_STEP_BUILDERS` / `_TRIGGER_BUILDERS` registries, so wiring a new
step type widens coverage automatically.

### When it fails

The diff prints as prose: it names the contract, then the field, then what
moved. Decide which side is wrong. If the change is legitimate:

```bash
KIZEN_DRIFT_PROFILE=<profile> KIZEN_DRIFT_UPDATE_SNAPSHOT=1 uv run pytest -m drift
```

Then **read `git diff tests/drift/schema_snapshot.json`** before committing — a
committed snapshot is only worth something if a human looked at the change.

### Cleanup

Everything the round-trip creates is named `zz-drift-check <what> <UTC stamp>`,
so anything carrying that prefix in the target environment is debris from an
aborted run and safe to delete by hand.

Deletion is registered with a session-scoped ledger (`Scratch` in
`tests/drift/conftest.py`) the moment each POST returns, before any assertion
runs, and swept in reverse creation order at session teardown regardless of
outcome — an assertion failure, an exception inside a fixture, and `pytest -x`
all still tear down. A deletion that itself fails is reported by name. The one
case the ledger can't cover is the process being killed outright, which is what
the name prefix is for.

## Releasing

See [docs/RELEASING.md](docs/RELEASING.md).

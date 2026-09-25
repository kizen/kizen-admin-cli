# Surface: smart connectors — API, authoring path & local dev loop

Smart connectors are Kizen's data-ingestion / ETL layer. A connector owns one or
more **SQL scripts** (ClickHouse SQL you author) that transform input data —
uploaded file, webhook, schedule, bulk-action selection, activity, or a
polled/direct API — against seeded Kizen data and write records back.

Two docs, split by what you're doing:

- **this one** — the API, the create→execute authoring path, and the local
  `pull → run → push` loop.
- **`kizen docs show smart-connector-flow`** — the spec file you hand to
  `configure-flow` (execution variables + load steps + field mappings).

## Endpoints (verified live)

```
GET   /api/smart-connectors                                  # list (SmartConnectorSlimmed)
GET   /api/smart-connectors/{connector}                      # full detail — UUID or api_name
GET   /api/smart-connectors/metadata                         # connector-type / matching-rule catalog
GET   .../{connector}/executions                             # run history
GET   .../{connector}/executions/{eid}/sql-script            # the SQL used in one run
GET   .../{connector}/sql-scripts  ·  .../sql-scripts/{id}   # draft / live scripts
PATCH .../sql-scripts/{id}                                   # edit the draft (user_script)
POST  .../sql-scripts/{id}/publish                           # promote draft → live
GET   /api/smart-connectors/{smart_connector_id}/events-history
```

- List filters: `search`, `active`, `connector_type`, `custom_object`,
  `status`, `ordering`.
- Detail includes `last_draft_script` and `live_script` as **full objects**
  (id + `user_script`), not the bare ids the OpenAPI schema implies.
- **There is no single-execution GET.** Per-run detail is only the sql-script
  endpoint above, plus whatever the `executions` *list* carries — including
  `error_details`.
- `events-history` **keys off `smart_connector_id` (UUID) only**, not the
  api_name that every other path accepts. The CLI rejects an api_name here with
  a clear message rather than letting it 404.

## `config_metadata` is the `__config.json` payload

A SQL script's `config_metadata` comes back **as a dict** (the OpenAPI types it
`string` on read; live returns an object). It holds `input_tables`,
`seed_tables`, and a `triggered` block. That dict, plus the connector's
`sql_parameters` / `integration_secrets`, is exactly what the local dev
package's `__config.json` is built from.

## The local dev loop

```bash
kizen smart-connectors pull <connector> [--dir] [--live] [--force]
kizen smart-connectors run  [--dir] [--dry-run]
kizen smart-connectors push [--dir] [--publish] [--dry-run] [--yes]
kizen smart-connectors add-input <file> [--dir]      # swap in local sample data
```

`push` previews a unified SQL diff and confirms before writing; only
`--publish` promotes to live.

### Why `pull` assembles the directory client-side

The server's own dev-package endpoints don't return a package:
`GET .../sql-scripts/{id}/connector-local-dev-package` **500s**, and
`GET .../sql-scripts/{id}/dev-package` only kicks off an async server-side
generation job (`{progress_status_id, sql_script_id, status}`). Neither hands
back anything usable inline. So `pull` builds the working directory from data
that *is* reliable:

| file | source |
|---|---|
| `connector.sql` | the sql-script's `user_script` |
| `__config.json` | `config_metadata` + `sql_parameters` / `integration_secret_filenames`, in dev-package shape |
| `data/<input>.csv` | the uploaded input file via `GET /api/files/{file_id}/download`, then normalized (Excel→CSV, header dedupe) |
| `data/current_execution.json` | the `triggered` block mapped to the runner's meta columns |
| `.kizen-connector.json` | marker recording connector id / api_name / script id, so `run` and `push` work from `--dir` alone |

### The local runtime is Kizen's own, verbatim

`run` executes against Kizen's production dev-package runtime, vendored
byte-for-byte rather than reimplemented: the `input`/`output`/`connector`/
`kizen`/`meta` database model, column type mapping, named collections for
integration secrets, partial-output-on-error, and resource limits all match
production. That fidelity is the whole point — a connector's SQL has to behave
the same locally and live, and a reimplementation would only *probably* agree.

`run` reproduces the dev package's entrypoint against an arbitrary working
directory: chdir → build DBs → run your SQL → dump `output.*` tables to
`data/output/*.csv`.

### Installing the `connectors` extra

Only `run` and `add-input` need it — everything else in the group is plain REST
and stays dependency-free, with the runtime imported lazily. A CLI without the
extra works normally right up to the point you ask it to execute SQL.

The extra is three public PyPI packages: **`chdb`** (embedded ClickHouse — the
big one, a native wheel around 100 MB), **`python-calamine`** (Excel reader),
and **`charset-normalizer`** (encoding detection). Nothing in it comes from
Kizen and nothing about it is private; it's carved out purely so a CLI used
mostly for schema work doesn't drag in a database engine.

**Don't guess the install command — ask for it.** Run the command without the
extra and the error prints the exact line for *your* install:

```bash
kizen smart-connectors run --dir <workdir>
```

This matters because the obvious instruction is wrong for most install shapes.
`uv sync --extra connectors` works only if you run the CLI out of a checkout's
own `.venv`; from a `uv tool` or `pipx` install it succeeds, installs into a
virtualenv your `kizen` never reads, and leaves the identical error in place.
The resolved hint targets the interpreter actually running the CLI.

| install shape | command |
|---|---|
| Checkout, running from its `.venv` | `uv sync --extra connectors` |
| Anything with `pip` in it | `<that env's python> -m pip install 'chdb>=4.1' 'python-calamine>=0.6' 'charset-normalizer>=3.3'` |
| A pip-less venv (`uv tool`, `uv venv`) | `uv pip install --python <that env's python> 'chdb>=4.1' …` |

In the last two rows the interpreter path is the part that's easy to get wrong,
and it's the part the error message fills in for you. **Quote the specifiers** —
`pip install chdb>=4.1` in a shell is a redirection and writes a file called
`=4.1`.

## Connector-type coverage

The runtime handles **all** connector types (it special-cases `webhooks` and
`schedule` input tables, and named-collection secrets). Inspection and `push`
are type-agnostic. What differs by type is where `pull` gets the **sample
input**:

- **`spreadsheet`** — uploaded file, downloaded automatically (verified
  end-to-end).
- **`bulkaction`** — a generated sample file when one is attached; otherwise
  `add-input`.
- **`webhook` / `schedule` / `activity` / `polling_third_party_api` /
  `direct_api_connection`** — no local sample is auto-fetched. `pull` writes the
  correct `connector.sql` + `__config.json` and **warns** to supply a
  representative input via `add-input` (and, for API types, to drop a JSON
  secret file per integration secret into `data/`).

Spreadsheet, schedule, activity, and webhook are all live-verified end-to-end.

## The full create → execute path

```bash
smart-connectors create <name> --object <api_name> --type <type>
smart-connectors set-input <file> --connector <c>     # upload + get-file-template
smart-connectors pull/run/push                        # iterate on the SQL
smart-connectors generate-sample <c>                  # sql-scripts/{id}/start
smart-connectors push --publish                       # promote the draft
smart-connectors suggest-variables <c> --spec         # starting point for the spec
smart-connectors configure-flow <c> --spec-file <f>   # execution_variables + flow.loads
smart-connectors activate <c>                         # status: operational
smart-connectors start-flow <c> [--live]              # start-connector-flow
```

The raw calls behind each step, because knowing them is what makes a failure
legible:

1. **`POST /api/smart-connectors`** — despite the schema marking only `name`
   required, the server 400s without `custom_object` and `connector_type` too.

2. **Sample file upload.** Every connector type needs a `source_file_id`; only
   the required file *shape* varies (see below). The upload is the generic Kizen
   S3 presigned flow — `kizen docs show files` has it — then
   `PATCH .../source_file_id` with the new File's id.

3. **`POST .../get-file-template`** (body `{"source_file_id": "<uuid>"}` —
   **the id must be passed explicitly even if unchanged**, or it silently
   returns `{}`) returns an auto-generated `user_script` + `config_metadata`
   reflecting the file's columns, plus any `kizen_data_seeds` already
   configured. `PATCH .../sql-scripts/{id}` writes that, or your edit, onto the
   draft. Two side effects (confirmed live 2026-07-30):
   - **It creates a *new* draft script**, so the draft id you held before the
     call is superseded. Re-read `last_draft_script` before PATCHing or your
     edit lands on a script nothing looks at. (`PATCH .../sql-scripts/{id}`
     itself updates in place; it's the template call that forks.)
   - **The new draft comes back at `sql_version: 1.3.x`** regardless of what the
     connector was on — a silent downgrade, and fatal for a webhook connector,
     which needs 4.1.x. `sql_version` is patchable, but patching it **clears the
     script's `state`**, so restore the version *before* generating the output
     sample, not after. `set-input` does both.

4. **`POST .../sql-scripts/{id}/start`** generates the server-side output
   sample. Required before `publish` will accept the script (`publish` 400s with
   "Output sample file is not generated yet"). It also populates the connector's
   `headers` — the recognized output columns, keyed by scope — which every
   execution variable's `scope` is validated against, so nothing in steps 6–7
   can be configured until this succeeds. Generation is async: poll the script's
   `state`. Send `{"source_file_id": "<the connector's file>"}` as the body, or
   the script stays on whatever file it last ran against; see [Replacing the
   reference file](#replacing-the-reference-file).

5. **`POST .../sql-scripts/{id}/publish`** promotes the draft live.

6. **`POST .../generate-execution-variables`** suggests variables from the
   reference file's columns with inferred `data_type` / `input_format` /
   `output_format` (e.g. `"Yes"`/`"No"` values infer `boolean` +
   `input_format: yes_no`); `PATCH` them onto `execution_variables` to save.

   **`data_source` is validated against the connector's `headers`** — the
   columns of the *generated output sample*, i.e. what the SQL selects, keyed by
   scope. So the SQL is free to invent output columns that appear nowhere in the
   uploaded file; what it can't do is invent them **without regenerating the
   sample**, because `headers` is only refreshed by `sql-scripts/{id}/start`.
   Symptom of skipping that: "Scope X is not found in headers", or a
   `data_source` rejected for a column you can plainly see in your SQL. The fix
   is `generate-sample`, not a re-upload.

7. **Configure `flow.loads`** — one entry per object the connector writes to.
   Shape, matching rules, field mappings and their quirks:
   `kizen docs show smart-connector-flow`.

8. **`PATCH {"status": "operational"}`** — a connector created via the API
   defaults to `status: "setup"`. A **dry-run** execution runs fine regardless,
   but a **live** `start-connector-flow` **silently sits in `queued` forever**,
   with no error, until the connector is flipped to `operational`.

9. **`POST .../start-connector-flow`** (`{"is_dry_run": true|false}`) actually
   runs it. `is_dry_run: true` validates without writing. The response echoes
   the queued request and carries the id as **`execution_id`**, not `id`.

## Reading from other Kizen objects (`kizen_data_seeds`)

CLI: `kizen smart-connectors seeds list|add|remove`. The wire details:

- **`kizen_data_seeds[].group_id` is a saved filter group (segment) id** on the
  seeded object — confirmed against
  `GET /api/custom-objects/{object}/filter-groups`, and *not* a field category
  id. A category id 400s with the misleading "object does not exist".
- `fields_ids` (write-only — it doesn't come back on a read) picks which fields
  come along; the server always includes `kizen_id`.
- Only the field types in `metadata.kizen_data_seeds_allowed_field_types` can be
  seeded.

Seeded data is exposed to the SQL as a `kizen.<object_table_name>` view, added
to `config_metadata.seed_tables` **the next time `get-file-template` runs** —
PATCHing `kizen_data_seeds` does **not** retroactively populate an
already-generated script's `seed_tables`. A saved seed is inert until
regeneration, which is why `seeds add` regenerates by default (`seeds list`
shows `in_script` so you can see which state each seed is in). Regeneration
keeps the existing `user_script` and takes only the fresh `config_metadata`, so
adding a seed doesn't discard SQL you've been iterating on.

`pull` **exports each seeded object's rows** to `data/<seed name>` — the seed
table's `name` verbatim, since the runtime appends no extension, so
`orders.csv` and `webhooks` both mean what they say. Rows come from the same
saved filter group the live run uses, following the seed table's
`columns_mapping` exactly. `--seed-limit` caps rows per object (default 1000,
`0` for all).

That export is a local approximation, not the server's own: rich field values
flatten to one string per column — a dropdown or relationship collapses to its
label (falling back to its id), a multi-select joins on commas, a boolean
becomes `Yes`/`No`. Good enough to exercise the joins locally, which is the
point. If a seed can't be exported, `pull` warns and carries on; a missing seed
CSV only breaks `run`.

## ⚠️ Field-level partial-success warnings aren't visible from the CLI

A row-level write failure that isn't fatal to the whole run — e.g. a `date`
execution variable with no `output_format`, where Kizen defaults it to
`%m/%d/%Y` and a native ISO-only date field then rejects it — surfaces the run
as `status: Partial Success`. The per-row/per-field detail behind that is **not**
in `error_details` or anywhere else the executions list returns.

The only place it's visible is an `.xlsx` report downloaded by hand from the web
UI; there is no API endpoint for it. `configure-flow` warns at plan time when a
`date`/`datetime` variable has no `output_format`, since that's the one case
catchable before the executor. Treat that as a partial mitigation — catching the
general case needs Kizen to expose it over the API first.

## Replacing the reference file

`set-input` on a connector that already has a file replaces it. Confirmed live
2026-09-25, through publish and a dry run on the new file:

- **`start` needs `source_file_id` in its body.** The script's
  `config_metadata.triggered.fileupload_file_id` is the file the executor
  reads, and `POST .../sql-scripts/{id}/start` only sets it from the body. A
  start with no body keeps the old value, and a draft forked by `publish`
  inherits it. `PATCH .../sql-scripts/{id}` takes `source_file_id` too, returns
  200, and ignores it. That was the whole "can't swap a file" bug: the CLI
  started samples with no body. `generate-sample` now sends the connector's
  file.
- **What `set-input` does on a replace:** uploads and attaches the file, then
  regenerates the template and writes only its `config_metadata` onto the
  draft. The draft's `user_script` and `sql_version` are kept, the same way
  `seeds add` refreshes config (`--template-sql` takes the generated SQL
  instead). It then runs the output sample itself.
- **Input tables are renamed from the file name** (`zz_ref_b.csv` →
  `input.zz_ref_b_csv`), so kept SQL can read a table that no longer exists.
  `set-input` prints each renamed table. If the SQL still reads the old one,
  the sample ends in state `failed` and `set-input` exits 1.
- **Attaching the file rewrites the draft's `input_tables` straight away**
  (`PATCH` of the connector's `source_file_id`, before any template call), so
  the old table names are only readable before the attach.

A replace leaves this stale until you act:

- **The draft's sample.** A PATCH never resets `state`, so the draft stays at
  `success` against the old file. `set-input` runs the sample for this reason.
- **The live script** keeps running the old file until the next
  `push --publish`.
- **`headers`.** On a replace, `start` left them on the old columns and
  `publish` refreshed them.
- **Execution variables** whose `data_source` column is gone survive the
  publish. Re-check them with `suggest-variables`.

## Non-spreadsheet types: `get-file-template` works — upload a shaped CSV first

`connector_type: webhook | schedule | activity` (and presumably
`polling_third_party_api` / `direct_api_connection`) create fine — `schedule`
needs `cadence`; `activity` needs `activity_object`, which is an **activity
type** id from `kizen activities list`, not a custom-object id.

`get-file-template` no-ops to `{}` only when called with **no**
`source_file_id`, or one pointing at the wrong file shape. Exactly like
spreadsheet, these types need a file attached first — the required *shape*
differs per type and is validated server-side (`get-file-template` 400s with a
clear message, e.g. "Only CSV or ZIP files are accepted for webhook
connectors"):

- **`webhook`** — a CSV with columns `timestamp`, `employee_id` (a real
  team-member UUID; an empty string fails), `querystring`, `body` (a JSON
  string). `get-file-template` then auto-generates a real two-table script:
  `input.webhooks_raw` (all-string) plus a typed `input.webhooks` view with
  `body` as ClickHouse `JSON`.
- **`schedule`** — a CSV with one column, `schedule_trigger_time`.
- **`activity`** — a CSV with any columns; **the file's own columns are mostly
  ignored.** The server introspects the connector's `activity_object` and its
  real Kizen field schema directly, producing `input.logged_activities` (the
  standard logged-activity columns plus one `associated_<object>` tuple per
  object the activity type is associated with) and `input.activity_data` (native
  fields for that activity type). This is richer template generation, not a stub.

### `start` behaves differently by type

- **`schedule` and `activity`: confirmed working** — `start` reaches
  `state: success`, or a real SQL `state: failed` you can iterate on, across
  `sql_version` `1.3.x` and `4.1.x`.
- **`webhook`: confirmed working end-to-end, with two real requirements** — both
  of which the CLI now handles, so this is background rather than a checklist.
  `start` reliably 500'd until both were true:
  1. **`sql_version` is `4.1.x`.** Lower versions (`1.3.x`, `3.1.x`, `3.4.x`,
     `3.6.x`) 500 with an otherwise-identical script. Note the connector 400s
     below `3.1.x` with "SQL version must be at least 3.1.x", so `3.1.x` is only
     the *declared* floor, not one that works. `create --type webhook` pins
     4.1.x and rejects a lower explicit `--sql-version`; `set-input` restores
     the version after template generation downgrades it.
  2. **The SQL has only one `create table output.<object>` statement.** The
     generator emits a second (`create table output.webhooks …
     toJSONString(body)…`); `webhooks` isn't a Kizen object, so that table is a
     debug echo and including it crashes `start`. `set-input` drops generated
     output tables with no matching object and says which it dropped. Keep
     **both** `config_metadata.input_tables` entries (`webhooks_raw` and the
     typed `webhooks` view) even though the surviving SQL references only one —
     removing the typed entry also 500'd in testing.

### How a webhook connector actually runs

Not `start-connector-flow` — `start-flow` refuses a webhook connector and says
so. The real receiver is:

```
POST /api/smart-connectors/{connector}/webhook
```

Directly callable, accepts the body/query string as-is (JSON, form, multipart,
XML, querystring all listed as accepted), and returns 201 immediately while the
run happens asynchronously.

**Executions batch on the connector's `cadence` window** — the generated input
filename embeds the batch's start/end unix timestamps
(`..._webhooks_<start>_<end>.csv`) — rather than firing per request. Expect a
delay of up to the cadence interval before an execution appears;
`send-webhook` says as much after a send.

The reference file's shape is undiscoverable from the API, so
`kizen smart-connectors webhook-sample <path> --body <json> --employee <who>`
writes it. The generator types the whole `body` JSON column from that single
payload, so the sample should carry every field you intend to read.

## Command surface

Read: `list` / `get` / `metadata` / `executions` / `execution-sql` / `scripts` /
`events`, plus `suggest-variables` (a POST that saves nothing).

Authoring: `create` → `set-input` → `generate-sample` → `configure-flow` →
`activate` → `start-flow`. Plus `seeds list|add|remove`, and for webhook
connectors `webhook-sample` and `send-webhook`.

Every write previews and confirms first (`--dry-run` to stop after the preview,
`--yes` to skip the prompt). Two exceptions run without a prompt because they
write no records: `generate-sample`, and a `start-flow` without `--live`.

## See also

- `kizen docs show smart-connector-flow` — the `configure-flow` spec.
- `kizen docs show files` — the S3 upload flow behind `set-input`.
- `kizen docs show objects` — the custom object a connector loads into.

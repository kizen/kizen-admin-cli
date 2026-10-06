# Surface: automation runtime — starting, watching, and controlling runs

Everything about an automation *execution* (a run), as opposed to the
automation's own definition. Runs are runtime state, not schema, so these
commands sit outside the plan→preview gate; they ask y/N first instead.

- The automation **definition** → `kizen docs show automation`
- Editing **one step** of a definition → `kizen docs show automation-step`

## Starting a run

```bash
kizen automations start <api_name> --record <uuid>
kizen automations start <api_name> --record <uuid> --var org_match=true
```

Asks for y/N confirmation (`--yes` skips it): it runs live and can't be
undone. See `kizen docs show operating`, rule 3.

```
POST /api/automation2/automations/<automation-identifier>/start
{
  "record_id": "<uuid>",       # custom-object records
  "client_id":  "<uuid>",      # OR this, for contact (client_client) automations
  "variable_overrides": [ {"variable_name": "org_match", "value": "true"} ]
}
```

- `record_id` and `client_id` are **mutually exclusive** — contacts use
  `client_id`, custom-object records use `record_id`. The CLI routes one
  `--record` to the right key for you.
- `--record` is optional: global (record-less) automations start without it.
- **`variable_overrides` is a list, not a dict** (`StartAutomationRequest` →
  `VariableOverrideRequest`), keyed by the variable's `name` (not id), and
  `value` is always a **string** (nullable) that the server coerces by the
  variable's `data_type`. Verified against the public schema at
  `/api/docs/public/schema` plus a live start.
- Response is `{"execution": {ReadAutomationExecution}}` — or
  `{"execution": null}` if a UUID was passed as the identifier instead of the
  api_name.

### `--wait` / `--show-logs` — start and follow a run in one command

```bash
kizen automations start <api_name> --record <uuid> --wait
kizen automations start <api_name> --record <uuid> --wait --show-logs
```

Composes this section's `start` with "Watching a run" below's
`wait_for_execution()` and step history — no second poll loop, no second
`detailed_log` renderer. `--wait` blocks until the run reaches a terminal
status, printing each new step's status line the moment it appears (plus a
throttled heartbeat during a real gap between steps); `--show-logs` also
prints each step's `detailed_log` once that step finishes and implies
`--wait` — a `code_step`'s log is released on completion, not while it's
still running (see "Watching a run" below). `--timeout` /
`--poll-interval` are the same flags, same defaults, as `runs view --wait`
below, and the exit-code mapping (`completed` → 0, `failed`/`cancelled` → 1,
timeout or `paused*` → 3) is the same logic, reused rather than duplicated.
`--json --wait` never interleaves streaming text with the JSON blob; with
`--show-logs` its payload also carries the full step history under `steps`.

## Watching a run

```
GET /api/automation2/automation-execution?automation_id=<uuid>&record_id=<uuid>&size=25
GET /api/automation2/automation-execution/<execution-id>
GET /api/automation2/automation-execution/<execution-id>/history
```

`size` maxes at 100. CLI: `kizen automations runs list <api_name>`, and
`runs view <exec_uuid>` (`--no-steps` for the summary half only, `--wait` to
block until the run finishes). `runs logs <exec_uuid>` prints each step's
`detailed_log` — see below.

- Detail response has `status`, `record`, `automation`, `debug_mode`, `created`
  (≈ started) and `updated` (≈ finished). There is **no** `started_at` /
  `finished_at`. On a halted execution it also carries `paused_on_step`
  (`{id, type, branching_step, label}`) — `runs view` surfaces this whenever
  the GET carries it.
- **No trailing slash** — `…/<execution-id>/` 404s with an HTML page; the
  router only registers the slashless path.
- In `/history`, each row is one step or trigger firing. Type and human
  description live in a nested `step` / `trigger` object (`{type,
  description}`), **not** at the row top level. Timing is `execution_time_ms`
  (null for async steps like `code_step` / `call_llm`) plus `created` /
  `updated`.
- `detailed_log` on a history row is where a `code_step`'s output lands —
  **only** `outputs.log("…")` populates it; plain `print()` does not.
  `runs logs <exec_uuid>` renders it. **Confirmed live 2026-08-13**, its shape
  varies by step type, and none of them is a plain `{stdout, traceback}`:
  a code step carries `{logs, inputs, values, duration, http_requests}` (a
  full outbound HTTP trace); an action-step failure carries `{reasons}`; a
  debug advance carries `{debug_action}`. `runs logs` renders the shapes it
  can name and falls back to pretty-printed JSON for the rest. **Owner-
  confirmed 2026-08-13**: a `code_step`'s log is released only once the step
  finishes — it does not fill in incrementally while the step runs — so
  anything that watches for it (`start --wait --show-logs` included) is
  printing a completed log, not tailing a running one.

### Execution status — drive terminal detection off the schema, not off what's been seen

`ReadEntityAutomationExecutionStatusEnum`, confirmed live 2026-08-13:

```
active, paused, completed, cancelled, paused_by_automation, paused_by_failure, failed
```

`completed`, `failed`, `cancelled`, and the three `paused*` values are
terminal; `active` and anything the enum doesn't list are not.
`paused_by_failure` is a **distinct value from `paused`** — confirmed live on
an execution halted with a `paused_on_step` naming the failing step; it means
halted and needs a human, not "still running."

Per-step rows use a different, 12-value enum (`AutomationHistoryStatusEnum`),
including `debug_waiting`, `waiting_for_branches`, `pending_retry`,
`pending_throttled`.

**Queue latency between steps in the same chain is not the same as step
execution time.** On a real 9-step chain, individual step execution measured
sub-500ms every time, but the queue latency *between* steps on that same
chain ranged from ~60s to over 10 minutes. A wait that times out quickly, or
that treats an unfamiliar status as done or stuck, is not evidence the run
failed — `runs view --wait` defaults `--timeout` to 900s for exactly this
reason (`--timeout 0` waits indefinitely).

## Controlling a run

Every verb except `pause` asks for y/N confirmation (`--yes` skips it). CLI:
`kizen automations runs
pause|resume|cancel|skip-and-resume|debug-rerun|debug-restart|debug-step|debug-sendit <exec>`.

```
POST /api/automation2/automation-execution/<id>/pause
POST /api/automation2/automation-execution/<id>/play             # resume
POST /api/automation2/automation-execution/<id>/cancel           # irreversible
POST /api/automation2/automation-execution/<id>/skip-and-resume
POST /api/automation2/automation-execution/<id>/debug-sendit     # run a debug execution to completion
POST /api/automation2/automation-execution/<id>/debug-rerun      # re-run one step, nothing after it scheduled
POST /api/automation2/automation-execution/<id>/debug-restart    # restart from a step, subsequent steps ARE scheduled
POST /api/automation2/automation-execution/<id>/debug-step       # skip or execute one step of a debug execution
```

**Confirmed live 2026-07-22** for pause/play/cancel against a real delayed
execution — status flipped `active` → `paused` → `active` → `cancelled` on the
following GET each time. `debug-step` **confirmed live 2026-08-13**: it
started an execution with `debug_mode: "active"`, which parks at each step in
status `debug_waiting` until advanced.

### The oddly-heavy shared request body

pause / play / cancel / debug-sendit all take a
`LightReadAutomationExecutionRequest`: `id`, `automation_id`, `client_id`,
`record_id`, `status`, `trigger_history_id`, `debug_mode` — **all marked
required** by the schema, despite the action being implied entirely by the
endpoint rather than by anything in the body.

In practice, echo the execution's own current `GET`, current `status` included.
Sending the *pre-transition* status worked fine in every direction tested, so
the field is presumably read-only in effect despite being schema-required. The
CLI builds this body from a live execution read — don't hand-construct it.

Per-verb bodies:

- **`skip-and-resume`** — `InlineFormRequest`: `{skip_step_id,
  continue_with_branch?}` (`"yes"`/`"no"`). Resumes an execution paused on a
  step failure by skipping that step.
- **`debug-rerun` / `debug-restart`** — `DebugRerunRequest`: `{step_id}`.
- **`debug-step`** — `DebugStepRequest`: `{action: "execute"|"skip"|"debug",
  history_id, continue_with_branch?}`.

**Correction, confirmed live 2026-08-13**: only `debug-sendit` returns an
empty/`null` body. `debug-step` returns a full `LightAutomationHistory`, and
the schema `$ref`s the same shape for `debug-rerun`/`debug-restart`. The CLI
still re-`GET`s the execution afterward to report the before/after status —
`debug-step`'s response is the *step's* new history row, not the execution.

## Diagnostics

Read-only and paginated:

```
GET /api/automation2/automations/<identifier>/modification-history
  ?date_from=<iso>&date_to=<iso>&event_type=<type>&search=<text>
GET /api/automation2/automations/<identifier>/histories/failures?page=<n>&page_size=<n>
```

CLI: `kizen automations modification-history <api>` / `kizen automations
failures <api>`. `modification-history` accepts many more filters than the CLI
exposes (`custom_object_ids`, `has_comment`, `role_ids`, `team_member`,
`include_related`, `ordering`).

## See also

- `kizen docs show code-steps` — `kizen code test` unit-tests a `code_step`
  script without a run at all; prefer it over starting automations to debug
  script logic.

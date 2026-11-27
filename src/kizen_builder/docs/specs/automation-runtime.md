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

- One `--record` works for any object: the CLI sends a Contact as
  `client_id` and any other record as `record_id`.
- `--record` is optional: global (record-less) automations start without it.
- `--var name=value` sets a variable by its `name`. The value goes as text,
  and Kizen converts it to the variable's `data_type`. Verified against the
  public schema plus a live start.

### `--wait` / `--show-logs` — start and follow a run in one command

```bash
kizen automations start <api_name> --record <uuid> --wait
kizen automations start <api_name> --record <uuid> --wait --show-logs
```

`--wait` blocks until the run reaches a terminal status, printing each new
step's status line the moment it appears (plus a throttled heartbeat during a
real gap between steps); `--show-logs` also prints each step's `detailed_log`
once that step finishes and implies `--wait` — a `code_step`'s log is released
on completion, not while it's still running (see "Watching a run" below).
`--timeout` / `--poll-interval` are the same flags, same defaults, as `runs
view --wait` below, and so are the exit codes (`completed` → 0,
`failed`/`cancelled` → 1, timeout or `paused*` → 3).
`--json --wait` never interleaves streaming text with the JSON blob; with
`--show-logs` its payload also carries the full step history under `steps`.

## Watching a run

CLI: `kizen automations runs list <api_name>`, and
`runs view <exec_uuid>` (`--no-steps` for the summary half only, `--wait` to
block until the run finishes). `runs logs <exec_uuid>` prints each step's
`detailed_log` — see below.

- A run has `status`, `record`, `automation`, `debug_mode`, `created`
  (≈ started) and `updated` (≈ finished); there is no `started_at` or
  `finished_at`. A halted run also names the step it paused on, and `runs
  view` shows it.
- `runs view` reads every step row, however long the run (a 211-row run came
  back as three pages, confirmed live 2026-10-16).
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

- `pause`, `resume` and `cancel` (irreversible) do what they say.
- `skip-and-resume` resumes a run paused on a step failure by skipping that
  step (`--branch yes|no` past a condition).
- `debug-sendit` runs a debug execution to completion; `debug-rerun` re-runs
  one step with nothing after it scheduled; `debug-restart` restarts from a
  step and schedules the steps after it; `debug-step` skips or executes one
  step of a debug execution.

**Confirmed live 2026-07-22** for pause/resume/cancel against a real delayed
execution — status flipped `active` → `paused` → `active` → `cancelled` on the
following GET each time. `debug-step` **confirmed live 2026-08-13**: it
started an execution with `debug_mode: "active"`, which parks at each step in
status `debug_waiting` until advanced.

The debug verbs re-read the execution afterwards and report its status
before and after.

## Diagnostics

Read-only: `kizen automations modification-history <api>` / `kizen automations
failures <api>`. Both are paginated.

## See also

- `kizen docs show code-steps` — `kizen code test` unit-tests a `code_step`
  script without a run at all; prefer it over starting automations to debug
  script logic.

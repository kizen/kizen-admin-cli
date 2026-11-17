# Agent-usability evals

How many steps, wrong turns and rejected commands does it take an agent that
has never seen `kizen` to build a given automation with it? These evals answer
that with numbers you can compare before and after a change to the CLI or its
shipped docs.

Each run gives one task to one fresh agent session. The session gets a folder
shaped like the one `kizen init` leaves, a `kizen` on its PATH that logs every
call, and a prompt. Nothing else: no skills, no MCP servers, no notes. The
prompts spell out exactly what to build, so what varies is how hard the CLI
made it, not how well the model reasoned.

## What a run measures

From the logged CLI calls, whatever agent made them:

| Metric | Meaning |
|---|---|
| `cli_calls`, `cli_failed` | Every `kizen` invocation, and those that exited non-zero. |
| `invalid_commands` | Calls Click rejected: no such command or option, a missing argument, a bad value. |
| `spec_errors`, `plan_errors` | Calls that failed on `spec error:` or `plan error:`. |
| `api_errors` | Calls that failed on an HTTP 4xx or 5xx from Kizen. |
| `internal_errors`, `other_errors` | `error: internal:`, and every other failure (an unknown name, no automation, …). |
| `help_calls`, `docs_calls` | `--help` calls, and `kizen docs …` calls. |
| `docs_topics`, `docs_bytes` | Which topics were opened, and how much text that put in front of the agent. |
| `read_calls`, `dry_runs`, `applies`, `failed_applies` | The rest, by what they were for. |
| `calls_before_first_apply` | CLI calls made before the first successful apply. |

From the agent's transcript, when the harness launched Claude Code:
`tool_calls` (and by tool), `turns`, `input_tokens`, `output_tokens`,
`cost_usd`, `agent_seconds`, plus two counts of going around the CLI:
`raw_api_calls` (curl, requests) and `source_peeks` (reading the installed
package).

A run also **passes or fails**. After the session the harness reads back each
automation the task asked for and checks its shape: the triggers and step types
present, how the steps hang off each other (which branch, inside which loop,
rejoined by a merge, where a go-to lands), and a few scalar settings. Pass/fail
is a gate, not the score: compare the effort numbers across runs that passed.
The checks don't verify every field value, and nothing is executed.

## Before the first run

You need a **disposable Kizen business** with a profile (`kizen init --profile
<name>`), the same kind the drift tier uses. Sessions create real automations
in it and the harness deletes them afterwards.

```bash
uv sync --extra dev
uv run python -m evals seed --profile <name> --dry-run   # what it would create
uv run python -m evals seed --profile <name>             # create the eval world
uv run python -m evals doctor --profile <name>           # confirm, and list capabilities
```

`seed` builds the eval world from [`world/world.json`](world/world.json):
`eval_accounts`, `eval_tickets` and the `eval_deals` pipeline, five `eval_`
fields on Contacts, two activity types, a form, a survey and an email template.
It only adds what is missing, so it is safe to run again. Nothing removes the
world; delete the objects by hand to start over.

Two tasks need something a business may lack: a model that transcribes audio
(`t15`) and the `readonly` MCP server with the "Agentic Workflow Code" service
account (`t19`). `doctor` reports both, and `run` skips a task whose
requirement is missing.

## Running

```bash
uv run python -m evals list                                # the tasks
uv run python -m evals run --profile <name>                # every task, one session each
uv run python -m evals run t06 t12 tier1 --profile <name>  # by id prefix or tier
uv run python -m evals run --profile <name> --trials 3 --jobs 4 --model sonnet
```

`run --help` has the rest. Results go to `evals/results/<timestamp>/` (git
ignores it): `report.md` and `summary.json` at the top, and one directory per
run holding the prompt, the CLI call log (`kizen.jsonl`), the transcript, the
raw automations read back, and `result.json`.

Sessions vary from one to the next, so use `--trials` of 3 or more before
reading anything into a difference; the report shows per-task medians.

### Comparing a change

Run the same tasks, model and trials before and after, then diff them:

```bash
uv run python -m evals run --profile <name> --trials 3 --out evals/results/before
# make the change, or point at another install with --kizen /path/to/bin/kizen
uv run python -m evals run --profile <name> --trials 3 --out evals/results/after
uv run python -m evals compare evals/results/before evals/results/after
```

`--kizen` sets the CLI the sessions use. The harness uses the same one for its
own setup, read-back and cleanup.

### Another agent, or by hand

The default agent is a headless Claude Code session (`claude -p`) limited to
the shell and file tools, with user settings, MCP servers and skills switched
off. `--agent-cmd '<command>'` replaces it: the command runs in the session's
folder with the logging `kizen` on its PATH and gets the prompt on stdin. The
CLI metrics work with any agent. The transcript metrics need Claude Code's
`stream-json` output on stdout.

To drive a session yourself, in any tool:

```bash
uv run python -m evals prepare t12 --profile <name>   # prints the folder, prompt and PATH
# …run the session…
uv run python -m evals score <run directory> --cleanup
```

## Tasks

One TOML file per task in [`tasks/`](tasks/), with a reference solution of the
same name in [`solutions/`](solutions/). `python -m evals coverage` shows which
task covers each trigger, step type and feature.

| Tier | What it adds |
|---|---|
| 1 | One trigger, one or two steps in a line. |
| 2 | Branching, variables, waits, a go-to, a second automation, editing an existing automation. |
| 3 | Loops, code steps, AI steps, parallel lanes, related records, multi-command builds. |

Every task prefixes what it creates with a per-run token, written `{p}` in the
files, so runs can share a business and run at once.

A task file:

```toml
id = "t06_call_outcome_branch"        # must match the filename
tier = 2
surface = "pipeline"                  # contacts | pipeline | custom_object | global
title = "Branch on what a logged call recorded"
requires = []                         # capabilities from `doctor`
files = []                            # files from assets/ copied into the session's folder
prompt = """…{p}…"""

[covers]                              # must equal what the solution holds
triggers = ["activity_logged"]
steps = ["condition", "schedule_activity", "change_field_value"]
features = ["branching"]

[[setup]]                             # optional: created before the session
spec = "nurture.json"                 # a spec in setup/
activate = false

[[expect]]                            # one per automation the session must leave
api_name = "{p}_call_outcome"
type = "record_based"
target_object = "eval_deals"
active = false
triggers = ["activity_logged"]        # each must be present
steps = { condition = 1 }             # at least this many of each type
checks = [ { kind = "branch", of = "condition", yes = ["schedule_activity"], no = ["change_field_value"] } ]
```

The check kinds are in [`graph.py`](graph.py): `branch`, `loop_body`,
`merged`, `lanes`, `goto`, `order`, `block`, `trigger_block`, `absent` and
`kept_ids`. `block` and `trigger_block` compare config values loosely, because
a live read expands a reference the spec gave by name. Keep them to enum and
scalar keys that read back as written. A field reference doesn't: the spec's
`field_ref` reads back as a `field` object.

### Adding a task

1. Write `tasks/<id>.toml` and `solutions/<id>.json` (a list of specs, one per
   `[[expect]]`). Say what to build in the product's terms, completely enough
   that nothing is left to judgment, and don't name CLI commands or spec keys.
2. Name only things in `world/world.json`. If the task needs something new,
   add it there and re-run `seed`.
3. `uv run pytest tests/test_evals.py`. It plans every solution with the real
   planner against a fake of the eval world, runs each solution through its
   own task's checks, and holds `[covers]` to what the solution contains.
4. Run the task live once, and read the run's `final/*.raw.json` against your
   checks: a check that a correct build fails is worse than no check.

`tests/test_evals.py` also fails when a trigger or step type is wired in
`_TRIGGER_BUILDERS` / `_STEP_BUILDERS` and no task covers it. Cover it, or add
it to `UNCOVERED` in [`tasks.py`](tasks.py) with the reason.

## Layout

```
evals/
  __main__.py     the `python -m evals` commands
  runner.py       prepare → launch → score → cleanup for one run
  shim.py         the logging `kizen` each session runs
  metrics.py      CLI-log and transcript metrics
  graph.py        one graph shape for specs and live reads, and the checks
  report.py       summaries and comparisons
  seed.py         build and check the eval world
  tasks.py        task, solution and world loading
  preamble.md     the text every session gets ahead of its task
  tasks/  solutions/  setup/  assets/  world/
```

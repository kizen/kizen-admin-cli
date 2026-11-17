# Operating a Kizen environment with the `kizen` CLI

How to work in an environment folder: the approval gate, how mutations are
planned and applied, where to find things, and the conventions that keep a
session from acting on stale assumptions.

## The approval gate

**1. Reads never need approval.** Run `kizen objects get/list`,
`kizen automations get/list`, `kizen records get/list`, `kizen team search`,
and all other read-only CLI commands freely without asking.

**2. `--dry-run` never needs approval.** Mutation verbs (`create`/`update`)
run with `--dry-run` only render the plan.

**3. Applying needs approval.** Run the mutation verb with `--dry-run` → show
the rendered plan to the user → wait for approval → re-run without
`--dry-run` (add `--yes` since approval already happened in chat). Never apply
without explicit approval, even for additive changes that look safe.
Everything that executes asks first: `automations start`, `roundtrip
--execute` and the `automations runs` verbs y/N-confirm (`--yes` skips). `runs
pause` is the exception, so a run can be stopped fast. `code test` asks too:
it runs in the same Lambda as a real step and can write. Get approval before
running any of them.

## Under app-builder

App-builder is the npm package `@kizenapps/cli` (`appbuilder admin`). Every
`kizen <args>` in these docs is `npx @kizenapps/cli admin <args>` there. The
wrapper reads its own options first, so `admin --help` shows the wrapper's
help; `admin -- --help` shows `kizen --help`, and `admin docs show operating`
reaches `kizen` directly. There is no profile: the variables the wrapper sets
are in `kizen docs show environments`.

## Mutation flow (plan-then-apply)

Every mutation goes through this loop:

1. The desired state is a spec file (`--spec-file`, or stdin) or flags; each
   command's `--help` says which.
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

Plans are **ephemeral** — there's no on-disk plan store. Re-running the
verb with `--yes` re-plans against live state, so an approved change applied
later is still validated against reality at apply time. To keep a plan,
save a verb's `--dry-run --json` output; `kizen apply --plan-file <path>`
(or stdin) sends its payloads without re-validating them, and refuses a
plan built for another business. For automation updates, the plan bakes in
`last_revision` — if someone edits the automation between plan and apply,
the PUT fails loudly instead of clobbering their change.

Building an automation spec: `kizen docs show automation`.

## Where to find things

These sources, in this order — don't guess, and don't grep `src/` for any of them:

| You need… | Go to |
|-----------|-------|
| **Command syntax** — what flags a command takes, what it does | `kizen <group> <cmd> --help`. Authoritative and always current (generated from the code). Start here for any command. |
| **Everything about one kind of entity** — the spec shape a `--spec-file` wants, its wire formats, endpoints and quirks | `kizen docs show <surface>`. Each command's `--help` names its topic. |
| **One automation step or trigger type** | `kizen automations step-types show <type>` |
| **Filters** — the DSL, the wire format, per-type operators | `kizen docs show filters`. One shape shared by `records list --filter`, saved views, condition steps, `search_records`, and dashlets. |
| **Writing a `code_step` script** | `kizen docs show code-steps`. Write → `kizen code test` → wire into an automation. |
| **A first build, start to finish** | `kizen docs show examples` |
| **Profiles, credentials, app-builder variables** | `kizen docs show environments` |
| **Which topic do I want?** | `kizen docs show reference`, a router; `kizen docs list` names every topic. |
| **What commands exist** | `kizen docs show commands`. A map, not a contract: `--help` is the authority. |

Rule of thumb: `--help` tells you *how to call* a command; `kizen docs show
<surface>` tells you everything about the thing you're building. **Don't grep
for a quirk; open the surface's doc.**

## Conventions

- **Pull live before reasoning.** Before planning any change, call the
  relevant read commands to fetch the current state of the env. Don't assume
  from prior turns or from any local notes — Kizen state can change between
  sessions, and notes record intent, not reality.
- **Kizen is the source of truth, not your notes.** If a prior session's
  notes say field X was created but `kizen objects get` doesn't show it,
  Kizen wins — refresh and note the drift in your reply.
- **Never invent api_names.** When the user asks about an object or
  automation, run `kizen objects list` (or `kizen automations list`) and pick
  from what's actually there. `kizen objects list` includes built-in objects
  (Contacts) alongside custom ones. Contacts are also reachable via
  `kizen records list client_client` (or `kizen records get client_client <uuid>`).
  For contact field schemas and option UUIDs, use `kizen objects get client_client`.
- **One env per working directory.** The folder is pinned to one profile;
  `kizen envs list` shows what it resolves to (`kizen docs show
  environments`).
- **Use the CLI for exploration; Python only for production scripts.** When
  investigating Kizen state — looking up fields, reading records, tracing
  automation steps — always use `kizen` CLI commands. Do not write ad-hoc
  Python scripts to probe the API. Raw Python is appropriate only for
  production scripts that will run as Kizen code steps (use the `kizen.api`
  wrapper available inside the sandbox — see `kizen docs show code-steps`) or
  standalone Lambda functions (call the API directly). Endpoint patterns are
  in each surface's topic — `kizen docs show records` for record CRUD,
  including the `client_client` identifier contacts use.
- **Keep the CLI current.** Run `kizen upgrade --check` at the start of a
  session. It reports whether a newer version exists, caches the answer for a
  day, and always exits 0 — offline or with no channel configured it just
  says so quietly, so it's safe to run unconditionally. `kizen upgrade` then
  applies it, using whatever is right for how this CLI was installed. Docs
  ship with the package, so upgrading updates the instructions too.
- **Confirm rendered output in the browser.** The CLI has no renderer, so
  open anything a human would look at after writing it: `kizen docs show
  verifying`.
- **Show, don't paraphrase.** When relaying read results, prefer the actual
  command output (or a tight summary of it) over free-text descriptions. The
  user wants the ground truth.

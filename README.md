# Kizen Admin CLI

`kizen` is a command-line tool for designing and iterating on a Kizen
environment. It talks to the Kizen REST API directly, so you can read the live
schema, inspect and edit automations, browse records, build dashboards, and
apply changes — all from the terminal.

It is built to be driven by an AI agent as much as by a person. The
documentation ships inside the package and is served by the CLI, so an agent
working in an environment folder can read the operating rules and every
spec-file shape without leaving the terminal, and what it reads always matches
the version that's installed.

Every mutating command follows the same **plan → preview → confirm → apply**
loop: it pulls live state, validates your change against it, renders the plan,
and only writes after you confirm. `--dry-run` stops at the preview, so you can
see exactly what a command would do before anything touches the environment.

## Install

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and
`git` (uv shells out to `git` to fetch the repo; you don't run it yourself). If
you don't have `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
```

(see the link above for Windows). Then:

```bash
uv tool install https://github.com/kizen/kizen-admin-cli.git
```

That puts `kizen` on your `PATH` as a standalone tool — no local clone to keep
around, since there's nothing to run it *from*: you'll use it from environment
folders elsewhere on disk. `kizen upgrade` knows this shape and re-resolves
against that same URL. Confirm the install worked with `kizen --help`; if your
shell says `command not found`, open a new terminal (a fresh `uv` install
patches your shell's startup file, which only takes effect in a new shell) or
run `uv tool update-shell`.

Without `uv`, `pip install --user
git+https://github.com/kizen/kizen-admin-cli.git` into whichever user site owns
your `PATH` works the same way — this needs Python 3.12 or newer, which `uv`
would otherwise fetch for you automatically.

If you're going to *develop* the CLI rather than use it, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) — that setup clones the repo and runs
`uv sync`.

## Set up an environment folder

One folder per Kizen environment. Make it, `init` it, and you're working:

```bash
mkdir ~/"Builder - Acme" && cd ~/"Builder - Acme"
kizen init
```

`init` prompts for a profile name (defaulting to a slug of the folder name),
then API key, business ID, and user ID; the base URL defaults to the Kizen
cloud. It validates the credentials with a live call, stores them centrally in
`~/.config/kizen/credentials.toml` (mode 0600, never in the folder and never
committed), and writes two small non-secret files:

- `.kizen/profile` — the pin: which profile this folder targets, plus the
  `business_id` it expects.
- `CLAUDE.md` and `AGENTS.md` — a short stub telling an agent to read
  `kizen docs show operating` before it acts.

For scripted setup, pass `--api-key` / `--business-id` / `--user-id` (or set the
matching `KIZEN_*` variables) and it only prompts for what's missing.

## Then talk to it

The intended workflow is to open Claude Code — or any agent that reads
`CLAUDE.md` / `AGENTS.md` — **in the environment folder** and describe what you
want. The stub sends it to `kizen docs show operating`, which is the operating
model, the approval gate, and the rules for acting on live state. From there it
discovers commands through `--help` and spec shapes through `kizen docs list`.

Working by hand is the same tool, one verb at a time:

```bash
kizen objects list
kizen fields create invoice \
    --api-name po_number --name "PO Number" --type text \
    --category "Details" --dry-run     # review the plan…
kizen fields create invoice \
    --api-name po_number --name "PO Number" --type text \
    --category "Details" --yes         # …then apply it
```

Read verbs are always safe to run. Every create/update verb takes `--dry-run`,
`--yes`, and `--json`; a plan saved with `--dry-run --json` can be applied later
with `kizen apply --plan-file plan.json`.

`kizen --help` lists the command groups; each group's `--help` lists its verbs
and flags. That's the source of truth for syntax.

## One environment per working directory

Environment selection is *positional* — the working directory's `.kizen/profile`
pin decides which profile a command targets, so there's no global "current env"
to switch or drift onto. The pin also records the expected `business_id`, and
every command verifies the resolved profile matches it, so a command run from a
pinned directory can't act against the wrong environment.

Profile-name resolution order:

```
--profile / -p  >  $KIZEN_PROFILE  >  .kizen/profile pin  >  $KIZEN_ENV (legacy)
```

To work against a second environment, use a **different folder** pinned to its
own profile — one folder, one environment.
`kizen envs list` shows what the current directory resolves to. A one-off
`--profile <name>` overrides the pin, but a pinned directory still refuses any
profile whose `business_id` doesn't match.

## Documentation

**The docs ship with the CLI and are served by it**, so what you read always
matches the version you have installed — there's no sync step and nothing to
copy into an environment folder:

```bash
kizen docs show operating    # the operating model and the approval gate
kizen docs show commands     # the command map
kizen docs show reference    # API quirks, wire formats, process guides
kizen docs show automation   # a spec-file shape — one topic per shape
kizen docs list              # every topic
kizen docs path              # where they live on disk
```

Layered so each fact lives in exactly one place:

| You need | Read |
|---|---|
| Command syntax and flags | `kizen <group> <cmd> --help` — generated from the code |
| The JSON/CSV a `--spec-file` expects | `kizen docs show <shape>`; each command's `--help` names its topic |
| Why the API behaves the way it does | `kizen docs show reference` |
| How to operate an environment safely | `kizen docs show operating` |

## Staying current

```bash
kizen upgrade --check   # is there a newer version? cached for a day, exits 0 either way
kizen upgrade           # apply it
```

`upgrade` works out how the CLI was installed — editable checkout, `uv tool`,
`pipx`, or a direct VCS install — and runs the right commands for that shape.
For a checkout that means `git pull --ff-only` **and** a dependency reinstall,
so a new upstream dependency can't surface later as a bare `ImportError`.
`--dry-run` shows the commands without running them. Since the docs ship inside
the package, upgrading updates them too.

`kizen init --refresh-stubs` re-writes the agent stubs in an existing folder
without touching credentials, for when the stub template changes.

What changed in each release is in [CHANGELOG.md](CHANGELOG.md).

## Notes and limits

- **Automation and trigger coverage.** The plan-builder dispatches on type and
  raises a clear error listing what's supported if you hit one that isn't
  wired. The authoritative list is in `kizen docs show automation`.
- **`code_step.secrets`** reference env-specific secret bindings. The
  environment must have a secret configured under each name the step expects,
  or it fails at runtime.
- **Step UUIDs rotate on every automation PUT**, because the API replaces the
  step set rather than merging it. Anything outside the automation that depends
  on a stable step UUID will break across updates.
- **The `connectors` extra** is needed by exactly two verbs —
  `smart-connectors run` and `smart-connectors add-input`, the local loop for
  iterating on connector SQL. It's three public PyPI packages, one of which
  (`chdb`, embedded ClickHouse) is a native wheel around 100 MB, so it's carved
  out rather than made a core dependency. Add it at install time with
  `uv tool install --editable ".[connectors]"`, or later — run the command
  without it and the error prints the exact install line for your environment.
  See `kizen docs show reference`, "Installing the `connectors` extra".

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers dev setup, the test tiers, the source
tree, and how to add a command or a surface.

## License

MIT — see [LICENSE](LICENSE).

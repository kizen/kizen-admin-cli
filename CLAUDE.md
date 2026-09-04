# Kizen Admin CLI — working in this repo

This repo is the source of the `kizen` CLI. Work here is building and
maintaining the tool.

**Designing a Kizen solution happens somewhere else** — in an environment folder
pinned to a profile. Those instructions ship inside the package and are served
by the CLI:

```bash
kizen docs show operating     # the approval gate, live-state rules, conventions
kizen docs show commands      # the command map
kizen docs list               # every topic
```

## Starting a session

```bash
kizen upgrade --dry-run   # show what updating this checkout would run
kizen upgrade             # git pull --ff-only + dependency reinstall, in one step
uv run pytest             # confirm green before changing anything
```

`kizen upgrade` pulls *and* reinstalls once it detects an editable checkout. Both
matter: a pull that adds a dependency without a reinstall surfaces later as a
bare `ImportError` with nothing pointing at the cause. Use git directly for
anything `--ff-only` won't do, like a rebase or a specific branch.

## Where to look

Consult these in order. Grepping `src/` for any of them wastes time.

| You need… | Go to |
|---|---|
| **Command syntax** — what flags a command takes | `kizen <group> <cmd> --help`, generated from the code |
| **One kind of entity** — spec shape, wire format, endpoints, quirks | `src/kizen_builder/docs/specs/<surface>.md` |
| **Filters** — DSL, wire format, per-type operators | `src/kizen_builder/docs/filters.md` |
| **The Python inside a `code_step`** | `src/kizen_builder/docs/code-steps.md` |
| **Which topic covers this?** | `src/kizen_builder/docs/reference.md` |
| **How the tool is meant to be operated** | `src/kizen_builder/docs/operating.md` |
| **Where the code lives, how to extend it, how to test it** | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| **Why the code is shaped the way it is** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |

Everything under `src/kizen_builder/docs/` ships in the wheel; `kizen docs show`
reads those exact files, so edit them where they live. `docs/` at the repo root
holds maintainer notes and stays out of the wheel.

## Rules

- **One fact, one home.** When two docs would state the same quirk, the surface
  doc owns it and the other links to it. Keep `reference.md` a router.
- **Let `--help` be the source of truth for flags** — in the README, the shipped
  docs, and here. Link to it instead of restating them in prose.
- **Record findings, not narrative, in shipped docs**: what the API does, plus
  `confirmed live <date>`.
- **Use neutral placeholders for customer and environment names** in docs, code,
  and tests. The date makes a finding checkable; the name only identifies the
  customer.
- **Check the registry, not the prose.** `_TRIGGER_BUILDERS` / `_STEP_BUILDERS`
  in `tools/planners/automations.py` decide which automation triggers and steps
  are wired. Answer "is X supported?" from there.
- **Planners read live state; they never write it.** There is no
  POST/PUT/PATCH/DELETE under `tools/planners/`. The approval model depends on
  it, which is what makes `--dry-run` safe to run against anything.
- **Anything a user would notice goes in `CHANGELOG.md` under `[Unreleased]`, in
  the same change that makes it.** Write it for someone deciding whether to
  upgrade — what will be different afterwards.

Step-by-step procedures for adding a command, a surface, or a step type are in
[CONTRIBUTING.md § Extending the CLI](CONTRIBUTING.md#extending-the-cli).

## Before pushing

```bash
uv run pytest                                          # offline suite — always
KIZEN_DRIFT_PROFILE=<profile> uv run pytest -m drift   # after touching a mutation payload
```

The drift tier hits a real environment and creates and deletes entities there,
so point it at a disposable one. CONTRIBUTING.md covers what each tier proves.

## Releasing

[`docs/RELEASING.md`](docs/RELEASING.md) has the procedure. Tag on `main` only:
`kizen upgrade --check` reads tags from the remote, and a tag cut on a feature
branch can end up on a commit that doesn't survive the merge.

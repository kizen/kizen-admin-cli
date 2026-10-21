# Releasing

Versions are SemVer, static in `pyproject.toml`, tagged `vX.Y.Z`. Bumping the
number is a deliberate act rather than something derived from the git history.
Users install from the default branch (see the README) and contributors from an
editable checkout, so for both an scm-derived version would churn to
`0.2.1.dev4+g1a2b3c` on every commit and make "what version are you on?"
unanswerable in a chat.

While the version is `0.x`, a minor bump may carry a breaking change. Say so in
the changelog under **Changed** or **Removed**.

## Before you start

Run the live drift checks against a disposable environment:

```bash
KIZEN_DRIFT_PROFILE=<profile> uv run pytest -m drift
uv run pytest -m wheel
```

The offline suite can't tell you whether Kizen has changed a wire contract since
the last release; that's what the drift tier is for. Setup and the cleanup
guarantees are in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## The recipe

1. **Move the `[Unreleased]` entries** in `CHANGELOG.md` under a new
   `## [X.Y.Z] — <date>` heading, and add the link definition at the bottom.
   Entries should already be there, written in the change that made them.
2. **Bump `version` in `pyproject.toml`**, then **re-run `uv lock`**. The
   lockfile records the project's own version and CI syncs with `--locked`, so
   skipping this fails CI with a resolution error that says nothing about the
   real cause. `tests/test_release_metadata.py` catches all three files
   disagreeing before you get that far.
3. **Commit and merge to `main`.**
4. **Tag on `main` and push the tag:**

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

## What the tag does

Pushing a `v*` tag runs `.github/workflows/release.yml`, which:

- **refuses a tag that disagrees with `pyproject.toml`** — the first thing it
  checks, before spending time on a build. A mismatch would ship a wheel whose
  `kizen --version` contradicts the release it came from, and
  `kizen upgrade --check` compares exactly those two numbers;
- runs the suite, `uv build`, `twine check`, and the `-m wheel` packaging tests;
- extracts the matching `## [X.Y.Z]` section from `CHANGELOG.md` as the release
  body, and creates the GitHub Release with the sdist and wheel attached.

Those attached artifacts are the release. Installation is from a clone — see
[README.md](../README.md).

## The tag goes on `main`

`kizen upgrade --check` reads tags from the **remote**, so a local tag is
invisible to everyone and an unpushed one does nothing. A tag cut on a feature
branch can also end up pointing at a commit that doesn't survive a rebase or
squash-merge.

Until the first tag exists, `--check` falls back to counting commits behind the
remote's default branch. Tagging is what replaces that.

`kizen --version` reads *installed* metadata (`importlib.metadata`), a snapshot
taken at install time, so a version bump only shows up after a re-sync or
reinstall — including in this checkout.

## Why `uv.lock` is committed

This is an application rather than a library: nothing depends on
`kizen-builder`, so the usual "don't over-constrain downstream consumers"
argument doesn't apply, and the lockfile doesn't affect the built wheel's
metadata. Without it, every clone resolves dependencies independently and a
user's breakage can't be reproduced here.

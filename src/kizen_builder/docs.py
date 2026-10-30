"""Resolution of the packaged documentation tree.

The docs ship *inside* the package (``src/kizen_builder/docs/``) rather than at
the repo root, and are served by ``kizen docs show`` rather than copied into
each environment folder. That is what lets the CLI be installed as a wheel:
there is no checkout to resolve paths against, and the docs an agent reads
always match the installed version.

This module is the single chokepoint for finding them. It deliberately raises
``DocsUnavailable`` rather than degrading quietly — a missing docs tree means
the package was built without its ``package-data``, which is a packaging bug
that must be loud.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

DOCS_DIRNAME = "docs"
#: Historical directory name. What lives in here is now one document per Kizen
#: *surface* (spec shape + wire formats + quirks for one kind of entity), not
#: only spec-file shapes — `kizen docs list` labels them "surface". The
#: directory keeps its old name so topic resolution and the package-data globs
#: don't churn; nothing outside this module depends on the name.
SPECS_DIRNAME = "specs"

#: Cross-cutting topics that aren't about one entity. Order matters: it's the
#: order `kizen docs list` presents them in, which is also roughly the order a
#: newcomer should read them.
GUIDE_TOPICS = (
    "operating",
    "commands",
    "reference",
    "code-steps",
    "filters",
    "examples",
)


class DocsUnavailable(RuntimeError):
    """The packaged docs tree is missing or unreadable."""


def _package_root() -> Path:
    # `files()` states the intent — this is package data, and it ships — and
    # keeps working if the package layout is ever restructured. For an unpacked
    # wheel or an editable install it resolves to a real directory, which
    # matters: callers read these paths directly.
    return Path(str(importlib.resources.files("kizen_builder")))


def docs_root() -> Path:
    """The packaged docs directory.

    Raises ``DocsUnavailable`` if it isn't there — which happens when the
    package was built without the ``[tool.setuptools.package-data]`` globs that
    ship it.
    """
    root = _package_root() / DOCS_DIRNAME
    if not root.is_dir():
        raise DocsUnavailable(
            f"kizen-builder has no docs tree at {root} — it was installed "
            "without its package data. Reinstall from a wheel built with the "
            "`docs/*.md` package-data globs (see pyproject.toml)."
        )
    return root


def specs_dir() -> Path:
    """The directory of spec-file shape templates."""
    return docs_root() / SPECS_DIRNAME


def list_topics() -> list[str]:
    """Every topic ``docs show`` accepts: the guides, then the spec shapes.

    Spec topics are discovered from disk, so a newly added template is
    servable without touching this module. ``README`` is excluded — it's the
    index, surfaced by ``docs list`` itself.
    """
    root = docs_root()
    guides = [t for t in GUIDE_TOPICS if (root / f"{t}.md").is_file()]
    specs = sorted(
        p.stem for p in specs_dir().glob("*.md") if p.stem.lower() != "readme"
    )
    return guides + specs


def topic_path(topic: str) -> Path:
    """Resolve a topic name to a file, or raise ``LookupError``.

    Accepts a bare name (``automation``) or one with the extension
    (``automation.md``); ``specs`` and ``list`` are handled by the CLI.
    """
    name = topic.strip().removesuffix(".md").lower()
    root = docs_root()

    candidate = root / f"{name}.md"
    if candidate.is_file():
        return candidate

    candidate = specs_dir() / f"{name}.md"
    if candidate.is_file():
        return candidate

    available = ", ".join(list_topics())
    raise LookupError(f"no docs topic '{topic}'. Available: {available}")


def specs_index() -> Path:
    """The spec index (``specs/README.md``)."""
    return specs_dir() / "README.md"


# ---------------------------------------------------------------------------
# Env-folder scaffolding
# ---------------------------------------------------------------------------

#: Agent-instruction filenames written into an environment folder. Claude Code
#: auto-loads ``CLAUDE.md`` from the directory you open and will not read
#: site-packages, so *something* has to be local; ``AGENTS.md`` is the
#: cross-harness equivalent. Both get the same content.
STUB_FILENAMES = ("CLAUDE.md", "AGENTS.md")

#: Deliberately a pointer, not a copy. Everything substantive stays in the
#: package where it versions with the install, so this text carries nothing
#: that can go stale and never needs re-syncing.
STUB_TEMPLATE = """\
# Kizen environment — {profile}

This folder is pinned to a Kizen environment. The operating instructions ship
with the CLI rather than living here, so they always match the installed
version.

**Before doing anything:**

```bash
kizen upgrade --check        # is a newer version out? (quiet, safe, cached)
kizen docs show operating    # the operating model, and the approval gate
kizen envs list              # confirm which environment this folder targets
```

- **Never run a mutation verb without `--dry-run` first**, and never apply
  without explicit approval in the conversation — even for additive changes.
- Command syntax: `kizen --help`. The command map: `kizen docs show commands`.
- Spec-file shapes: `kizen docs list`.
- API quirks and wire formats: the surface's own topic
  (`kizen docs show reference` routes to it).
"""

#: Paths that older versions symlinked into env folders, pointing at a repo
#: checkout. They dangle once the docs move into the package, so `init` clears
#: them rather than leaving a folder full of broken links.
_LEGACY_LINKS = (
    Path("CLAUDE.md"),
    Path(".kizen") / "reference.md",
    Path(".kizen") / "specs",
)


def clear_legacy_links(directory: Path) -> list[Path]:
    """Remove symlinks left by the pre-packaged-docs layout.

    Only *symlinks* at these three paths are touched — the old ``link_docs``
    was the only thing that ever created them, so a symlink here is always
    ours, while anything a user wrote themselves is a real file and is left
    alone.

    Note this removes links that still resolve, not just dangling ones: after
    the split, a surviving ``CLAUDE.md`` link points at the *contributor*
    instructions for developing the CLI, which is actively wrong for an
    environment folder. Returns what was removed.
    """
    removed: list[Path] = []
    for relpath in _LEGACY_LINKS:
        target = directory / relpath
        if target.is_symlink():
            target.unlink()
            removed.append(target)
    return removed


def write_stubs(directory: Path, profile: str, force: bool = False) -> list[Path]:
    """Write the agent-instruction stubs into an environment folder.

    Existing files are left alone unless ``force`` — someone may have added
    project-specific notes, and clobbering those would be worse than a slightly
    stale pointer. Returns the paths actually written.
    """
    body = STUB_TEMPLATE.format(profile=profile)
    written: list[Path] = []
    for name in STUB_FILENAMES:
        path = directory / name
        if path.exists() and not force:
            continue
        path.write_text(body)
        written.append(path)
    return written

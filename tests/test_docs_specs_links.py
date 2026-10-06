"""Guards the docs-routing contract so it can't silently rot.

The CLI routes agents to documentation via Typer ``epilog`` strings that name a
``kizen docs show <topic>`` command, and the spec docs cross-link each other.
These tests assert those pointers actually resolve, so adding a spec-consuming
command (or renaming a spec doc) without updating the other side fails the suite
instead of shipping a dead pointer.

Everything here resolves through ``kizen_builder.docs`` — the *packaged* docs
tree — rather than against the repo checkout, so the contract is guarded for an
installed wheel too, not just a source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

import kizen_builder
import kizen_builder.cli as cli
from kizen_builder import docs
from tests.conftest import iter_commands

SPECS_DIR = docs.specs_dir()

# "kizen docs show <topic>" as it appears inside a command epilog.
_EPILOG_TOPIC_REF = re.compile(r"kizen docs show ([a-z][a-z0-9-]*)")
# A bare sibling "<name>.md" reference inside a spec doc.
_SIBLING_MD_REF = re.compile(r"([a-z][a-z0-9_-]*\.md)")


def test_every_epilog_docs_pointer_resolves():
    root = typer.main.get_command(cli.app)
    topics = set(docs.list_topics())
    seen = 0
    missing = []
    for path, command in iter_commands(root, []):
        epilog = getattr(command, "epilog", None)
        if not epilog:
            continue
        for topic in _EPILOG_TOPIC_REF.findall(epilog):
            seen += 1
            if topic not in topics:
                missing.append(f"`kizen {' '.join(path)}` -> docs show {topic}")
    assert not missing, "epilog points at a missing docs topic:\n" + "\n".join(missing)
    # Sanity floor: if this drops, epilogs were lost or the regex broke.
    assert seen >= 15, f"expected many docs-routing epilogs, found {seen}"


def test_docstring_docs_pointers_resolve():
    """Pointers in module/source prose resolve too, not just epilogs."""
    topics = set(docs.list_topics())
    # `src/`, so the failure message reads `kizen_builder/...`. Anchored on the
    # package rather than a module inside it, which keeps the depth stable when
    # a module becomes a package.
    src = Path(kizen_builder.__file__).resolve().parents[1]
    missing = []
    for py in sorted(src.rglob("*.py")):
        for topic in set(_EPILOG_TOPIC_REF.findall(py.read_text())):
            if topic not in topics:
                missing.append(f"{py.relative_to(src)} -> docs show {topic}")
    assert not missing, "source points at a missing docs topic:\n" + "\n".join(missing)


def test_docs_cross_references_resolve():
    """A `kizen docs show <topic>` inside a doc must name a real topic.

    The docs route to each other far more than they used to, now that each
    surface owns its own wire reference — this is the pointer class most likely
    to rot when a topic is split, renamed, or absorbed.
    """
    topics = set(docs.list_topics()) | {
        "list",
        "specs",
    }  # `docs list`/`docs specs` are commands
    dangling = []
    seen = 0
    for doc in sorted(docs.docs_root().rglob("*.md")):
        for topic in sorted(set(_EPILOG_TOPIC_REF.findall(doc.read_text()))):
            seen += 1
            if topic not in topics:
                dangling.append(f"{doc.name} -> docs show {topic}")
    assert not dangling, "a doc points at a missing topic:\n" + "\n".join(dangling)
    assert seen >= 30, f"expected many inter-doc pointers, found {seen}"


def test_spec_docs_have_no_dangling_sibling_links():
    # The guide topics live one level up in the docs tree, not in specs/, so a
    # `../<guide>.md` link out of a spec file is fine. Derived from
    # GUIDE_TOPICS rather than hardcoded: adding a guide shouldn't need a test
    # edit to be linkable from a spec.
    external = {f"{topic}.md" for topic in docs.GUIDE_TOPICS}
    dangling = []
    for doc in sorted(SPECS_DIR.glob("*.md")):
        for name in set(_SIBLING_MD_REF.findall(doc.read_text())):
            if name in external:
                continue
            if not (SPECS_DIR / name).exists():
                dangling.append(f"{doc.name} -> {name}")
    assert not dangling, "spec doc links to a missing sibling:\n" + "\n".join(dangling)

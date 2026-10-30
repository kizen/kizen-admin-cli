"""Shipped doc examples must never quietly diverge from their fixtures.

Every `*.md` under the packaged docs tree, `specs/` included, is scanned for
```` ```json ```` blocks whose line above is a marker comment. Two forms:

- `<!-- fixture: examples/<set>/<file>.json -->` — the block is byte-for-byte
  (modulo a trailing newline) the file under `tests/fixtures/`.
- `<!-- fixture: examples/<set>/<file>.json#/steps/3 -->` — the block parses
  to JSON equal to the value at that RFC 6901 pointer. Key order and
  whitespace are free.

Every fixture in a set directory under `tests/fixtures/examples/` must be
referenced by some marker, and one shown only in fragments must show every
element of its top-level `steps`, `triggers` and `fields` arrays. The drift
tests (`tests/drift/test_worked_examples.py`) apply those same files against a
real environment, so this check is what makes "the doc shows what the drift
test applies" a guarantee rather than a convention. Unmarked blocks are not
checked.

Runs in the default, every-commit suite — no live environment, no marker.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kizen_builder import docs

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"

_MARKER_RE = re.compile(r"^\s*<!--\s*fixture:\s*(?P<target>\S+?)\s*-->\s*$")
_MARKER_START_RE = re.compile(r"<!--\s*fixture:")
_MARKER_PATH_RE = re.compile(r"^examples/[^/]+/[^/]+\.json$")
_COVERED_ARRAYS = ("steps", "triggers", "fields")
_MISSING = object()


@dataclass(frozen=True)
class MarkedBlock:
    where: str  # "<doc path relative to the docs root>:<marker line>"
    fixture: str  # relative to tests/fixtures/
    pointer: str | None  # None for a whole-file marker
    body: str
    body_line: int  # doc line of the block's first body line


def collect(docs_root: Path) -> tuple[list[MarkedBlock], list[str]]:
    """Every marked block under ``docs_root``, and every malformed marker."""
    blocks: list[MarkedBlock] = []
    problems: list[str] = []
    for doc in sorted(docs_root.rglob("*.md")):
        lines = doc.read_text().split("\n")
        rel = doc.relative_to(docs_root).as_posix()
        for i, line in enumerate(lines):
            if not _MARKER_START_RE.search(line):
                continue
            where = f"{rel}:{i + 1}"
            m = _MARKER_RE.match(line)
            if not m:
                problems.append(f"{where}: a fixture marker must be on its own line.")
                continue
            fixture, has_pointer, pointer = m.group("target").partition("#")
            if not _MARKER_PATH_RE.match(fixture):
                problems.append(
                    f"{where}: marker path '{fixture}' must have the form "
                    "'examples/<set>/<file>.json', relative to tests/fixtures/."
                )
                continue
            if i + 1 >= len(lines) or lines[i + 1].strip() != "```json":
                problems.append(
                    f"{where}: the line after a fixture marker must open a "
                    "```json fenced block (not jsonc, no blank line between)."
                )
                continue
            end = next(
                (j for j in range(i + 2, len(lines)) if lines[j].strip() == "```"), None
            )
            if end is None:
                problems.append(
                    f"{where}: the ```json block under this marker is never closed."
                )
                continue
            blocks.append(
                MarkedBlock(
                    where=where,
                    fixture=fixture,
                    pointer=pointer if has_pointer else None,
                    body="\n".join(lines[i + 2 : end]),
                    body_line=i + 3,
                )
            )
    return blocks, problems


def resolve_pointer(doc: Any, pointer: str) -> tuple[Any, str | None]:
    """``(value, None)`` at an RFC 6901 pointer, or ``(None, why it failed)``."""
    if pointer == "":
        return doc, None
    if not pointer.startswith("/"):
        return None, "a JSON pointer must be empty or start with '/'"
    value, at = doc, ""
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if token not in value:
                return None, f"no key '{token}' at '{at or '/'}'"
            value = value[token]
        elif isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token) or int(token) >= len(value):
                return None, f"no index '{token}' at '{at or '/'}' ({len(value)} items)"
            value = value[int(token)]
        else:
            return None, f"'{at}' is a {type(value).__name__}, not an object or array"
        at = f"{at}/{raw}"
    return value, None


def first_difference(doc: Any, fixture: Any, at: str = "") -> str | None:
    """Where two JSON values first differ, as ``"<pointer>: <how>"``, or None."""
    here = at or "/"
    if isinstance(doc, dict) and isinstance(fixture, dict):
        for key in [*doc, *(k for k in fixture if k not in doc)]:
            path = f"{at}/{key.replace('~', '~0').replace('/', '~1')}"
            d, f = doc.get(key, _MISSING), fixture.get(key, _MISSING)
            if d is _MISSING:
                return f"{path}: missing from the doc"
            if f is _MISSING:
                return f"{path}: in the doc but not in the fixture"
            if diff := first_difference(d, f, path):
                return diff
        return None
    if isinstance(doc, list) and isinstance(fixture, list):
        for i, (d, f) in enumerate(zip(doc, fixture, strict=False)):
            if diff := first_difference(d, f, f"{at}/{i}"):
                return diff
        if len(doc) != len(fixture):
            return f"{here}: the doc has {len(doc)} items, the fixture {len(fixture)}"
        return None
    # bool is an int in Python; JSON keeps them apart.
    if isinstance(doc, bool) != isinstance(fixture, bool) or doc != fixture:
        return (
            f"{here}: the doc has {json.dumps(doc)}, the fixture {json.dumps(fixture)}"
        )
    return None


def block_problems(block: MarkedBlock, fixtures_root: Path) -> list[str]:
    path = fixtures_root / block.fixture
    if not path.is_file():
        return [
            f"{block.where}: marker names '{block.fixture}', which doesn't exist "
            "under tests/fixtures/. Fix the path or add the fixture."
        ]
    try:
        shown = json.loads(block.body)
    except json.JSONDecodeError as e:
        return [
            f"{block.where}: the block isn't valid JSON ({e.msg} at doc line "
            f"{block.body_line + e.lineno - 1}). A marked block must be strict "
            "JSON: no comments, no '…', no trailing commas."
        ]
    on_disk = path.read_text()
    if block.pointer is None:
        doc_lines = block.body.strip("\n").split("\n")
        file_lines = on_disk.strip("\n").split("\n")
        if doc_lines == file_lines:
            return []
        n = next(
            (
                i
                for i, (a, b) in enumerate(zip(doc_lines, file_lines, strict=False))
                if a != b
            ),
            min(len(doc_lines), len(file_lines)),
        )
        return [
            f"{block.where}: the block is no longer byte-identical to "
            f"'{block.fixture}'; they first differ at doc line "
            f"{block.body_line + n} (fixture line {n + 1}). "
            "Edit whichever one is stale, not both independently."
        ]
    expected, why = resolve_pointer(json.loads(on_disk), block.pointer)
    if why:
        return [
            f"{block.where}: pointer '{block.pointer}' doesn't resolve in "
            f"'{block.fixture}': {why}. Fix the pointer (~1 is '/', ~0 is '~')."
        ]
    if diff := first_difference(shown, expected, block.pointer):
        return [
            f"{block.where}: fragment '{block.fixture}#{block.pointer}' differs from the fixture "
            f"at {diff}. Edit whichever one is stale, not both independently."
        ]
    return []


def orphan_problems(blocks: list[MarkedBlock], fixtures_root: Path) -> list[str]:
    """Set fixtures that no marker, whole or fragment, references."""
    referenced = {b.fixture for b in blocks}
    on_disk = sorted(
        p.relative_to(fixtures_root).as_posix()
        for p in (fixtures_root / "examples").glob("*/*.json")
    )
    return [
        f"tests/fixtures/{rel}: no shipped doc references this fixture. Show it "
        f"under '<!-- fixture: {rel} -->' (whole) or '<!-- fixture: "
        f"{rel}#/<pointer> -->' (fragments), or delete it."
        for rel in on_disk
        if rel not in referenced
    ]


def coverage_problems(blocks: list[MarkedBlock], fixtures_root: Path) -> list[str]:
    """Fragment-only fixtures with a ``steps``/``triggers``/``fields`` element no doc shows."""
    pointers: dict[str, set[str | None]] = {}
    for b in blocks:
        pointers.setdefault(b.fixture, set()).add(b.pointer)
    problems = []
    for rel, shown in sorted(pointers.items()):
        path = fixtures_root / rel
        if None in shown or "" in shown or not path.is_file():
            continue
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            continue
        uncovered = [
            (f"{key}[{i}]", f"/{key}/{i}")
            for key in _COVERED_ARRAYS
            if isinstance(data.get(key), list) and f"/{key}" not in shown
            for i in range(len(data[key]))
            if f"/{key}/{i}" not in shown
        ]
        if uncovered:
            problems.append(
                f"'{rel}' is shown only in fragments, and no marker shows "
                f"{', '.join(name for name, _ in uncovered)}. Above a ```json block "
                f"of each, in the doc that teaches it, add '<!-- fixture: "
                f"{rel}#{uncovered[0][1]} -->' (and so on), or show the whole file."
            )
    return problems


@functools.cache
def _shipped() -> tuple[list[MarkedBlock], list[str]]:
    return collect(docs.docs_root())


def test_the_docs_actually_have_marked_fixture_blocks():
    """A guard against the marker regex silently matching nothing after an edit."""
    blocks, _ = _shipped()
    assert len(blocks) >= 3, (
        "expected at least the object, fields, and automation blocks to carry "
        f"a '<!-- fixture: ... -->' marker; found {len(blocks)}"
    )


def test_every_marker_is_well_formed():
    _, problems = _shipped()
    assert not problems, "\n".join(problems)


def test_marked_blocks_match_their_fixture():
    """The whole point: doc prose and the committed fixture never diverge."""
    blocks, _ = _shipped()
    problems = [p for b in blocks for p in block_problems(b, FIXTURES_ROOT)]
    assert not problems, "\n".join(problems)


def test_every_example_fixture_is_referenced():
    """Catches the opposite drift: a fixture nobody points at from the docs."""
    blocks, _ = _shipped()
    problems = orphan_problems(blocks, FIXTURES_ROOT)
    assert not problems, "\n".join(problems)


def test_fragment_only_fixtures_show_every_step_trigger_and_field():
    blocks, _ = _shipped()
    problems = coverage_problems(blocks, FIXTURES_ROOT)
    assert not problems, "\n".join(problems)


_FIXTURE = {
    "name": "Demo",
    "meta": {"a/b": {"c~d": 1}},
    "triggers": [{"type": "manual"}],
    "steps": [{"type": "x", "on": True}, {"type": "y"}, {"type": "z"}],
}


def _tree(tmp_path: Path, doc: str) -> tuple[list[MarkedBlock], list[str], Path]:
    docs_root, fixtures_root = tmp_path / "docs", tmp_path / "fixtures"
    (docs_root / "specs").mkdir(parents=True)
    (docs_root / "specs" / "demo.md").write_text(doc)
    (fixtures_root / "examples" / "demo").mkdir(parents=True)
    (fixtures_root / "examples" / "demo" / "auto.json").write_text(
        json.dumps(_FIXTURE, indent=2)
    )
    blocks, problems = collect(docs_root)
    return blocks, problems, fixtures_root


def _fragment(pointer: str, body: str) -> str:
    """Prose, a blank line, then a marked block: the marker is on the 3rd line."""
    return f"Some prose.\n\n<!-- fixture: examples/demo/auto.json#{pointer} -->\n```json\n{body}\n```\n"


def test_a_matching_fragment_passes_whatever_its_key_order(tmp_path):
    blocks, problems, fixtures = _tree(
        tmp_path, _fragment("/steps/0", '{"on": true,\n "type": "x"}')
    )
    assert problems == []
    assert [b.where for b in blocks] == ["specs/demo.md:3"]
    assert block_problems(blocks[0], fixtures) == []


def test_a_differing_fragment_names_doc_line_fixture_pointer_and_key_path(tmp_path):
    blocks, _, fixtures = _tree(
        tmp_path, _fragment("/steps/0", '{"type": "x", "on": 1}')
    )
    [problem] = block_problems(blocks[0], fixtures)
    assert problem.startswith(
        "specs/demo.md:3: fragment 'examples/demo/auto.json#/steps/0'"
    )
    assert "at /steps/0/on: the doc has 1, the fixture true" in problem


def test_escaped_pointers_resolve_and_a_bad_one_is_named(tmp_path):
    doc = _fragment("/meta/a~1b/c~0d", "1") + _fragment("/meta/a~1c", "1")
    blocks, _, fixtures = _tree(tmp_path, doc)
    assert block_problems(blocks[0], fixtures) == []
    [problem] = block_problems(blocks[1], fixtures)
    assert problem.startswith(
        "specs/demo.md:9: pointer '/meta/a~1c' doesn't resolve in 'examples/demo/auto.json'"
    )
    assert "no key 'a/c' at '/meta'" in problem


def test_a_marked_block_that_is_not_json_fails_with_the_parse_error(tmp_path):
    blocks, _, fixtures = _tree(
        tmp_path, _fragment("/steps/1", '{"type": "y", // note\n}')
    )
    [problem] = block_problems(blocks[0], fixtures)
    assert problem.startswith(
        "specs/demo.md:3: the block isn't valid JSON (Expecting property name"
    )
    assert "at doc line 5" in problem


def test_an_uncovered_step_is_named(tmp_path):
    doc = _fragment("/triggers", '[{"type": "manual"}]') + _fragment(
        "/steps/0", '{"type": "x", "on": true}'
    )
    blocks, _, fixtures = _tree(tmp_path, doc)
    [problem] = coverage_problems(blocks, fixtures)
    assert "no marker shows steps[1], steps[2]." in problem
    assert "<!-- fixture: examples/demo/auto.json#/steps/1 -->" in problem


def test_a_whole_file_marker_needs_no_fragments_and_unreferenced_fixtures_are_orphans(
    tmp_path,
):
    whole = json.dumps(_FIXTURE, indent=2)
    blocks, _, fixtures = _tree(
        tmp_path, f"<!-- fixture: examples/demo/auto.json -->\n```json\n{whole}\n```\n"
    )
    (fixtures / "examples" / "other").mkdir()
    (fixtures / "examples" / "other" / "spare.json").write_text("{}")
    assert block_problems(blocks[0], fixtures) == []
    assert coverage_problems(blocks, fixtures) == []
    [orphan] = orphan_problems(blocks, fixtures)
    assert orphan.startswith(
        "tests/fixtures/examples/other/spare.json: no shipped doc references"
    )


def test_a_marker_not_followed_by_a_json_fence_is_reported(tmp_path):
    _, problems, _ = _tree(
        tmp_path, "<!-- fixture: examples/demo/auto.json -->\n```jsonc\n{}\n```\n"
    )
    assert problems == [
        "specs/demo.md:1: the line after a fixture marker must open a ```json "
        "fenced block (not jsonc, no blank line between)."
    ]


def test_a_marker_sharing_its_line_with_text_is_reported(tmp_path):
    _, problems, _ = _tree(
        tmp_path, "Text <!-- fixture: examples/demo/auto.json -->\n```json\n{}\n```\n"
    )
    assert problems == ["specs/demo.md:1: a fixture marker must be on its own line."]

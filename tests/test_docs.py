"""Env-folder scaffolding: instruction stubs and legacy-link cleanup."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder import docs

runner = CliRunner()


def test_write_stubs_creates_both_harness_files(tmp_path):
    written = docs.write_stubs(tmp_path, "acme-sandbox")

    assert {p.name for p in written} == set(docs.STUB_FILENAMES)
    for name in docs.STUB_FILENAMES:
        body = (tmp_path / name).read_text()
        assert "acme-sandbox" in body
        # The stub's whole job is routing to the packaged docs.
        assert "kizen docs show operating" in body
        assert "--dry-run" in body


def test_write_stubs_does_not_clobber_existing_notes(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# hand-written project notes\n")

    written = docs.write_stubs(tmp_path, "acme")

    assert [p.name for p in written] == ["AGENTS.md"]
    assert (tmp_path / "CLAUDE.md").read_text() == "# hand-written project notes\n"


def test_write_stubs_force_overwrites(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("stale\n")

    written = docs.write_stubs(tmp_path, "acme", force=True)

    assert {p.name for p in written} == set(docs.STUB_FILENAMES)
    assert "stale" not in (tmp_path / "CLAUDE.md").read_text()


def test_write_stubs_is_idempotent(tmp_path):
    assert docs.write_stubs(tmp_path, "acme")
    assert docs.write_stubs(tmp_path, "acme") == []


def test_clear_legacy_links_removes_dangling_symlinks(tmp_path):
    """Folders set up by the old layout point at a checkout that has moved."""
    (tmp_path / ".kizen").mkdir()
    dangling = tmp_path / "CLAUDE.md"
    dangling.symlink_to(tmp_path / "gone" / "CLAUDE.md")

    removed = docs.clear_legacy_links(tmp_path)

    assert [p.name for p in removed] == ["CLAUDE.md"]
    assert not dangling.is_symlink()


def test_clear_legacy_links_removes_resolving_symlinks_too(tmp_path):
    """A surviving CLAUDE.md link now points at the *contributor* docs.

    That's worse than a broken link — it would tell an agent in an environment
    folder that it's in the CLI source repo.
    """
    target = tmp_path / "elsewhere.md"
    target.write_text("# instructions for developing the CLI")
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(target)

    removed = docs.clear_legacy_links(tmp_path)

    assert [p.name for p in removed] == ["CLAUDE.md"]
    assert not link.exists()
    assert target.exists(), "only the link is removed, never its target"


def test_init_migration_replaces_a_legacy_link_with_the_stub(tmp_path):
    """End to end: an old-layout folder comes out with real stub files."""
    (tmp_path / ".kizen").mkdir()
    (tmp_path / "CLAUDE.md").symlink_to(tmp_path / "gone" / "CLAUDE.md")
    (tmp_path / ".kizen" / "specs").symlink_to(tmp_path / "gone" / "specs")

    docs.clear_legacy_links(tmp_path)
    docs.write_stubs(tmp_path, "acme")

    claude = tmp_path / "CLAUDE.md"
    assert claude.is_file() and not claude.is_symlink()
    assert "kizen docs show operating" in claude.read_text()
    assert not (tmp_path / ".kizen" / "specs").is_symlink()


def test_clear_legacy_links_leaves_real_files_alone(tmp_path):
    real = tmp_path / "CLAUDE.md"
    real.write_text("a real file, not a link")

    assert docs.clear_legacy_links(tmp_path) == []
    assert real.read_text() == "a real file, not a link"


def test_topic_path_accepts_bare_name_and_extension():
    assert docs.topic_path("automation") == docs.topic_path("automation.md")
    assert docs.topic_path("REFERENCE").name == "reference.md"


def test_list_topics_puts_guides_first_and_hides_the_index():
    topics = docs.list_topics()
    assert topics[: len(docs.GUIDE_TOPICS)] == list(docs.GUIDE_TOPICS)
    assert "readme" not in [t.lower() for t in topics]


def test_docs_unavailable_message_is_actionable(monkeypatch, tmp_path):
    """A wheel built without package-data must fail loudly, not silently."""
    monkeypatch.setattr(docs, "_package_root", lambda: tmp_path)

    try:
        docs.docs_root()
    except docs.DocsUnavailable as exc:
        assert "package data" in str(exc)
        assert "pyproject.toml" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected DocsUnavailable")


@pytest.mark.parametrize("flags", [[], ["--raw"]])
def test_docs_show_prints_the_file_verbatim_when_not_a_terminal(flags):
    """CliRunner's stdout isn't a TTY, like a pipe or an agent's capture."""
    result = runner.invoke(cli.app, ["docs", "show", "automation", *flags])

    assert result.exit_code == 0, result.output
    assert result.output == docs.topic_path("automation").read_text()


def test_docs_show_rendered_forces_rich_when_not_a_terminal():
    heading = docs.topic_path("operating").read_text().splitlines()[0]
    assert heading.startswith("# Operating a Kizen environment")

    result = runner.invoke(cli.app, ["docs", "show", "operating", "--rendered"])

    assert result.exit_code == 0, result.output
    assert "Operating a Kizen environment" in result.output
    assert heading not in result.output

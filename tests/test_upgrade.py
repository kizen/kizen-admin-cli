"""Install detection, upgrade planning, and the update check.

Nothing here touches the network: every test that exercises the check patches
``upgrade._git``, which is the single seam every git call goes through.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kizen_builder import upgrade
from kizen_builder.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------


def test_file_url_unquotes_spaces():
    # Install directories routinely contain spaces; a percent-escaped path
    # silently fails every is_dir() check downstream.
    path = upgrade._file_url_to_path("file:///Users/me/Kizen%20Admin%20CLI")
    assert path == Path("/Users/me/Kizen Admin CLI")


def test_non_file_url_is_not_a_path():
    assert upgrade._file_url_to_path("https://example.test/x") is None


def test_editable_install_detected_as_checkout(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "_direct_url",
        lambda: {"url": "file:///src/kb", "dir_info": {"editable": True}},
    )

    install = upgrade.detect_install()

    assert install.method == upgrade.CHECKOUT
    assert install.path == Path("/src/kb")


def test_editable_wins_over_a_tool_manager_prefix(monkeypatch):
    """A checkout must be updated with git no matter who owns the venv."""
    monkeypatch.setattr(
        upgrade,
        "_direct_url",
        lambda: {"url": "file:///src/kb", "dir_info": {"editable": True}},
    )
    monkeypatch.setattr(upgrade, "_managed_by", lambda: upgrade.PIPX)

    assert upgrade.detect_install().method == upgrade.CHECKOUT


def test_tool_manager_detected(monkeypatch):
    monkeypatch.setattr(upgrade, "_direct_url", lambda: None)
    monkeypatch.setattr(upgrade, "_managed_by", lambda: upgrade.UV_TOOL)

    install = upgrade.detect_install()

    assert install.method == upgrade.UV_TOOL
    # No PEP 610 record at all (a plain index install of the tool), so there's
    # no git URL to check tags against.
    assert install.repo_url is None


def test_tool_manager_from_a_git_source_keeps_the_url(monkeypatch):
    """`uv tool install <git-url>` still writes a direct_url.json; a plain
    index-installed tool manager shouldn't be confused with one that has a
    remote worth checking tags on."""
    monkeypatch.setattr(
        upgrade,
        "_direct_url",
        lambda: {
            "url": "https://example.test/kb.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
    )
    monkeypatch.setattr(upgrade, "_managed_by", lambda: upgrade.UV_TOOL)

    install = upgrade.detect_install()

    assert install.method == upgrade.UV_TOOL
    assert install.repo_url == "https://example.test/kb.git"


def test_vcs_install_preserves_the_requested_spec(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "_direct_url",
        lambda: {
            "url": "https://example.test/kb.git",
            "vcs_info": {"vcs": "git", "requested_revision": "main"},
        },
    )
    monkeypatch.setattr(upgrade, "_managed_by", lambda: None)

    install = upgrade.detect_install()

    assert install.method == upgrade.VCS
    assert install.url == "git+https://example.test/kb.git@main"
    # The bare URL, not the `git+…@rev` spec, so it can be handed straight to
    # `git ls-remote`.
    assert install.repo_url == "https://example.test/kb.git"


def test_falls_back_to_an_enclosing_checkout(monkeypatch, tmp_path):
    """PEP 610 misses setup.py-develop installs and stale egg-info shadowing."""
    monkeypatch.setattr(upgrade, "_direct_url", lambda: None)
    monkeypatch.setattr(upgrade, "_managed_by", lambda: None)
    monkeypatch.setattr(upgrade, "_source_checkout", lambda: tmp_path)

    install = upgrade.detect_install()

    assert install.method == upgrade.CHECKOUT
    assert install.path == tmp_path


def test_index_install_when_nothing_else_matches(monkeypatch):
    monkeypatch.setattr(upgrade, "_direct_url", lambda: None)
    monkeypatch.setattr(upgrade, "_managed_by", lambda: None)
    monkeypatch.setattr(upgrade, "_source_checkout", lambda: None)

    assert upgrade.detect_install().method == upgrade.INDEX


def test_source_checkout_ignores_an_unrelated_enclosing_repo(monkeypatch, tmp_path):
    """Only a pyproject that names *this* project counts."""
    root = tmp_path / "someone-elses-repo"
    (root / "src" / "kizen_builder").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
    monkeypatch.setattr(
        upgrade, "__file__", str(root / "src" / "kizen_builder" / "upgrade.py")
    )

    assert upgrade._source_checkout() is None


def test_source_checkout_finds_this_project(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "kizen_builder").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nname = "{upgrade.DIST_NAME}"\n')
    monkeypatch.setattr(
        upgrade, "__file__", str(root / "src" / "kizen_builder" / "upgrade.py")
    )

    assert upgrade._source_checkout() == root


# ---------------------------------------------------------------------------
# Upgrade planning
# ---------------------------------------------------------------------------


def _git_checkout(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_checkout_upgrade_always_resyncs_dependencies(tmp_path, monkeypatch):
    """Pulling without syncing is the bug this command exists to prevent."""
    # Which reinstaller gets planned depends on the environment; that it plans
    # one at all is the contract here, so give it something to find.
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    steps = upgrade.upgrade_steps(install)

    assert len(steps) == 2
    assert steps[0].argv[:3] == ["git", "pull", "--ff-only"]
    assert all(step.cwd == tmp_path for step in steps)
    # Either `uv sync` or the pip equivalent, depending on what's on PATH.
    assert steps[1].argv[:2] == ["uv", "sync"] or "install" in steps[1].argv


def test_uv_sync_when_running_from_the_projects_own_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(upgrade.sys, "prefix", str(tmp_path / ".venv"))
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    assert upgrade.upgrade_steps(install)[1].argv == ["uv", "sync"]


def test_pip_when_installed_into_some_other_environment(tmp_path, monkeypatch):
    """`uv sync` would update the checkout's .venv, not the env running us.

    Syncing the wrong environment installs new dependencies somewhere the
    running `kizen` never looks — the ImportError survives a successful-looking
    upgrade — so having `uv` on PATH is not on its own enough to use it.
    """
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(upgrade.sys, "prefix", "/usr/local/some-other-python")
    monkeypatch.setattr(upgrade, "_has_pip", lambda: True)
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    argv = upgrade.upgrade_steps(install)[1].argv
    assert argv[1:4] == ["-m", "pip", "install"]
    assert argv[-1] == str(tmp_path)


def test_checkout_upgrade_falls_back_to_pip_without_uv(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: None)
    monkeypatch.setattr(upgrade.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(upgrade, "_has_pip", lambda: True)
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    argv = upgrade.upgrade_steps(install)[1].argv
    assert argv[1:4] == ["-m", "pip", "install"]
    assert argv[-1] == str(tmp_path)


def test_uv_pip_when_the_environment_has_no_pip(tmp_path, monkeypatch):
    """The `uv tool install --editable` shape: editable, but pip-less.

    uv builds tool venvs without pip, so planning `python -m pip install -e`
    there produces a command that fails with "No module named pip" — after
    `git pull` has already run, which is the worst place to stop.
    """
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(upgrade.sys, "prefix", "/home/u/.local/share/uv/tools/kizen")
    monkeypatch.setattr(upgrade, "_has_pip", lambda: False)
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    argv = upgrade.upgrade_steps(install)[1].argv
    assert argv[:3] == ["uv", "pip", "install"]
    # --python pins the target to the interpreter that's running, not to
    # whatever venv uv would otherwise discover from the cwd.
    assert argv[3:5] == ["--python", upgrade.sys.executable]
    assert argv[-1] == str(tmp_path)


def test_checkout_with_neither_pip_nor_uv_is_actionable(tmp_path, monkeypatch):
    """Better to say so than to plan a pull with no reinstall behind it."""
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: None)
    monkeypatch.setattr(upgrade, "_has_pip", lambda: False)
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )

    with pytest.raises(upgrade.UpgradeUnsupported) as exc:
        upgrade.upgrade_steps(install)
    assert exc.value.advice


def test_missing_checkout_directory_is_actionable(tmp_path):
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=tmp_path / "gone"
    )

    with pytest.raises(upgrade.UpgradeUnsupported) as exc:
        upgrade.upgrade_steps(install)
    assert exc.value.advice


def test_editable_source_that_is_not_a_git_checkout(tmp_path):
    install = upgrade.Install(method=upgrade.CHECKOUT, version="0.1.0", path=tmp_path)

    with pytest.raises(upgrade.UpgradeUnsupported, match="isn't a git checkout"):
        upgrade.upgrade_steps(install)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (upgrade.UV_TOOL, ["uv", "tool", "upgrade", upgrade.DIST_NAME]),
        (upgrade.PIPX, ["pipx", "upgrade", upgrade.DIST_NAME]),
    ],
)
def test_managed_installs_delegate_to_their_manager(method, expected):
    steps = upgrade.upgrade_steps(upgrade.Install(method=method, version="0.1.0"))

    assert [step.argv for step in steps] == [expected]


def test_vcs_install_reinstalls_the_same_spec():
    install = upgrade.Install(
        method=upgrade.VCS, version="0.1.0", url="git+https://example.test/kb@main"
    )

    argv = upgrade.upgrade_steps(install)[0].argv

    assert argv[-3:] == ["install", "--upgrade", "git+https://example.test/kb@main"]


def test_index_install_refuses_to_guess_a_channel():
    """There is no configured index; inventing one would be worse than saying so."""
    install = upgrade.Install(method=upgrade.INDEX, version="0.1.0")

    with pytest.raises(upgrade.UpgradeUnsupported) as exc:
        upgrade.upgrade_steps(install)
    assert "channel" in exc.value.advice


def test_step_display_quotes_paths_with_spaces():
    step = upgrade.Step(["pip", "install", "-e", "/a b/c"], why="x")

    assert '"/a b/c"' in step.display()


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("0.1.0", "0.2.0", True),
        ("0.2.0", "0.2.0", False),
        ("1.0.0", "0.9.9", False),
        ("0.9.0", "0.10.0", True),  # not a string comparison
    ],
)
def test_out_of_date_by_version(current, latest, expected):
    result = upgrade.CheckResult(current=current, latest=latest, source="tags")

    assert result.out_of_date is expected


def test_behind_takes_precedence_over_version():
    """Before releases are cut, commits-behind is the only real signal."""
    result = upgrade.CheckResult(current="0.1.0", behind=3, source="commits")

    assert result.out_of_date is True
    assert "3 commits behind" in result.summary()


def test_inconclusive_is_not_out_of_date():
    result = upgrade.CheckResult(current="0.1.0", reason="offline")

    assert result.conclusive is False
    assert result.out_of_date is False
    assert "offline" in result.summary()


def test_https_equivalent_of_an_ssh_remote():
    assert (
        upgrade._https_equivalent("git@github.test:acme/repo.git")
        == "https://github.test/acme/repo.git"
    )


def test_https_remote_has_no_alternate_form():
    assert upgrade._https_equivalent("https://github.test/acme/repo.git") is None


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------


class FakeGit:
    """Stands in for ``upgrade._git``, keyed by the first word of the command."""

    def __init__(self, responses: dict[str, str | None]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, timeout=upgrade.NETWORK_TIMEOUT):
        self.calls.append(list(args))
        if timeout <= 0:
            return None
        for key, value in self.responses.items():
            if args[: len(key.split())] == key.split():
                return value
        return None


def test_tags_are_preferred_over_commit_counting(monkeypatch, tmp_path):
    fake = FakeGit(
        {
            "remote get-url": "https://github.test/acme/repo.git\n",
            "ls-remote": "abc\trefs/tags/v0.1.0\ndef\trefs/tags/v0.10.2\n",
        }
    )
    monkeypatch.setattr(upgrade, "_git", fake)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.source == "tags"
    assert result.latest == "0.10.2"
    assert result.out_of_date is True
    assert not any(call[0] == "fetch" for call in fake.calls)


def test_prerelease_and_junk_tags_are_ignored(monkeypatch, tmp_path):
    fake = FakeGit(
        {
            "remote get-url": "https://github.test/acme/repo.git\n",
            "ls-remote": (
                "a\trefs/tags/v0.2.0\nb\trefs/tags/v0.3.0-rc1\nc\trefs/tags/nightly\n"
            ),
        }
    )
    monkeypatch.setattr(upgrade, "_git", fake)

    assert upgrade._check_checkout("0.1.0", tmp_path).latest == "0.2.0"


def test_falls_back_to_commits_when_there_are_no_tags(monkeypatch, tmp_path):
    """The repo has no releases yet, so this is today's only working path."""
    fake = FakeGit(
        {
            "remote get-url": "https://github.test/acme/repo.git\n",
            "ls-remote": "",
            "fetch": "",
            "rev-list": "4\n",
        }
    )
    monkeypatch.setattr(upgrade, "_git", fake)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.source == "commits"
    assert result.behind == 4
    # Fetched into FETCH_HEAD rather than updating a named remote, so a check
    # leaves nothing behind in the user's ref namespace.
    assert ["rev-list", "--count", "HEAD..FETCH_HEAD"] in fake.calls


def test_unreachable_ssh_remote_retries_over_https(monkeypatch, tmp_path):
    """The author's own checkout can't reach origin over SSH; HTTPS works."""
    calls: list[str] = []

    def fake_git(args, cwd, timeout=upgrade.NETWORK_TIMEOUT):
        if args[:3] == ["remote", "get-url", "origin"]:
            return "git@github.test:acme/repo.git\n"
        if args[0] == "ls-remote":
            calls.append(args[-1])
            if args[-1].startswith("git@"):
                return None  # Permission denied (publickey)
            return "a\trefs/tags/v0.5.0\n"
        return None

    monkeypatch.setattr(upgrade, "_git", fake_git)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.latest == "0.5.0"
    assert calls == [
        "git@github.test:acme/repo.git",
        "https://github.test/acme/repo.git",
    ]


def test_a_reachable_remote_with_no_tags_is_not_retried(monkeypatch, tmp_path):
    """Asking the same repository a second time gets the same empty answer."""
    fake = FakeGit(
        {
            "remote get-url": "git@github.test:acme/repo.git\n",
            "ls-remote": "",
            "fetch": "",
            "rev-list": "0\n",
        }
    )
    monkeypatch.setattr(upgrade, "_git", fake)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.behind == 0
    assert sum(1 for call in fake.calls if call[0] == "ls-remote") == 1


def test_offline_is_inconclusive_not_an_error(monkeypatch, tmp_path):
    fake = FakeGit({"remote get-url": "https://github.test/acme/repo.git\n"})
    monkeypatch.setattr(upgrade, "_git", fake)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.conclusive is False
    assert "couldn't reach" in result.reason


def test_no_origin_remote_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "_git", FakeGit({}))

    assert "no 'origin' remote" in upgrade._check_checkout("0.1.0", tmp_path).reason


def test_check_budget_stops_a_pile_of_slow_remotes(monkeypatch, tmp_path):
    """A whole check is bounded, however many URLs it ends up trying."""
    monkeypatch.setattr(upgrade, "CHECK_BUDGET", -1.0)
    fake = FakeGit(
        {
            "remote get-url": "git@github.test:acme/repo.git\n",
            "ls-remote": "a\trefs/tags/v9.9.9\n",
        }
    )
    monkeypatch.setattr(upgrade, "_git", fake)

    result = upgrade._check_checkout("0.1.0", tmp_path)

    assert result.conclusive is False
    assert not any(call[0] == "ls-remote" for call in fake.calls)


def test_non_checkout_installs_have_no_channel_to_query(monkeypatch):
    """`_latest_from_index` is the seam; until it's wired, the answer is honest."""
    install = upgrade.Install(method=upgrade.INDEX, version="0.1.0")

    result = upgrade._check_uncached(install)

    assert result.conclusive is False
    assert "no distribution channel" in result.reason


def test_non_checkout_git_install_checks_tags(monkeypatch):
    """A uv-tool/pipx/VCS install has no local checkout, but does have a URL —
    the same `git ls-remote --tags` path a checkout uses works from any
    directory, no local repo required."""
    fake = FakeGit({"ls-remote": "a\trefs/tags/v0.5.0\n"})
    monkeypatch.setattr(upgrade, "_git", fake)
    install = upgrade.Install(
        method=upgrade.UV_TOOL,
        version="0.1.0",
        repo_url="https://github.test/acme/repo.git",
    )

    result = upgrade._check_uncached(install)

    assert result.source == "tags"
    assert result.latest == "0.5.0"
    assert result.out_of_date is True


def test_non_checkout_git_install_with_no_tags_says_so(monkeypatch):
    """There's no local history to fall back to counting commits against, so
    until a release tag exists this stays honestly inconclusive."""
    fake = FakeGit({"ls-remote": ""})
    monkeypatch.setattr(upgrade, "_git", fake)
    install = upgrade.Install(
        method=upgrade.VCS,
        version="0.1.0",
        repo_url="https://github.test/acme/repo.git",
    )

    result = upgrade._check_uncached(install)

    assert result.conclusive is False
    assert "no release tags yet" in result.reason


def test_non_checkout_git_install_offline_is_inconclusive(monkeypatch):
    monkeypatch.setattr(upgrade, "_git", FakeGit({}))
    install = upgrade.Install(
        method=upgrade.UV_TOOL,
        version="0.1.0",
        repo_url="https://github.test/acme/repo.git",
    )

    result = upgrade._check_uncached(install)

    assert result.conclusive is False
    assert "couldn't reach" in result.reason


def test_index_seam_is_used_when_implemented(monkeypatch):
    monkeypatch.setattr(upgrade, "_latest_from_index", lambda: "9.0.0")

    result = upgrade._check_uncached(
        upgrade.Install(method=upgrade.INDEX, version="0.1.0")
    )

    assert result.source == "index"
    assert result.out_of_date is True


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_honors_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert upgrade.cache_home() == tmp_path / "kizen"


def test_check_is_served_from_cache_without_touching_git(monkeypatch, tmp_path):
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )
    monkeypatch.setattr(
        upgrade,
        "_git",
        FakeGit(
            {
                "remote get-url": "https://x.test/a.git\n",
                "ls-remote": "a\trefs/tags/v2.0.0\n",
            }
        ),
    )
    first = upgrade.check_latest(install)
    assert first.cached is False

    def explode(*args, **kwargs):
        raise AssertionError("cached result should not re-run git")

    monkeypatch.setattr(upgrade, "_git", explode)
    second = upgrade.check_latest(install)

    assert second.cached is True
    assert second.latest == "2.0.0"


def test_refresh_bypasses_the_cache(monkeypatch, tmp_path):
    install = upgrade.Install(
        method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
    )
    monkeypatch.setattr(
        upgrade,
        "_git",
        FakeGit(
            {
                "remote get-url": "https://x.test/a.git\n",
                "ls-remote": "a\trefs/tags/v2.0.0\n",
            }
        ),
    )
    upgrade.check_latest(install)

    monkeypatch.setattr(
        upgrade,
        "_git",
        FakeGit(
            {
                "remote get-url": "https://x.test/a.git\n",
                "ls-remote": "a\trefs/tags/v3.0.0\n",
            }
        ),
    )

    assert upgrade.check_latest(install, refresh=True).latest == "3.0.0"


def test_stale_cache_entries_are_ignored(monkeypatch):
    upgrade._write_cache(
        upgrade.CheckResult(current="0.1.0", latest="9.9.9", source="tags")
    )
    path = upgrade._cache_path()
    payload = json.loads(path.read_text())
    payload["checked_at"] = time.time() - upgrade.CHECK_TTL_SECONDS - 1
    path.write_text(json.dumps(payload))

    assert upgrade._read_cache("0.1.0") is None


def test_upgrading_invalidates_the_cache(monkeypatch):
    """Otherwise the nag would outlive the fix that silenced it."""
    upgrade._write_cache(
        upgrade.CheckResult(current="0.1.0", latest="0.2.0", source="tags")
    )

    assert upgrade._read_cache("0.2.0") is None
    assert upgrade._read_cache("0.1.0") is not None


def test_corrupt_cache_is_ignored_not_fatal():
    path = upgrade._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    assert upgrade._read_cache("0.1.0") is None


def test_unwritable_cache_is_not_an_error(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/proc/nonexistent-and-unwritable")

    upgrade._write_cache(upgrade.CheckResult(current="0.1.0", latest="0.2.0"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_check_always_exits_zero_even_when_everything_fails(monkeypatch, tmp_path):
    """A version check must never be the reason a session stalls or errors."""
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )
    monkeypatch.setattr(upgrade, "_git", lambda *a, **k: None)

    result = runner.invoke(app, ["upgrade", "--check"])

    assert result.exit_code == 0


def test_check_nags_visibly_when_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )
    monkeypatch.setattr(
        upgrade,
        "check_latest",
        lambda install, refresh=False: upgrade.CheckResult(
            current="0.1.0", behind=7, source="commits"
        ),
    )

    result = runner.invoke(app, ["upgrade", "--check"])

    assert result.exit_code == 0
    assert "update available" in result.output
    assert "7 commits behind" in result.output
    assert "kizen upgrade" in result.output


def test_dry_run_shows_the_plan_and_runs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )

    def explode(steps):
        raise AssertionError("--dry-run must not run anything")

    monkeypatch.setattr(upgrade, "run_steps", explode)

    result = runner.invoke(app, ["upgrade", "--dry-run"])

    assert result.exit_code == 0
    assert "git pull" in result.output


def test_unsupported_install_exits_with_advice(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(method=upgrade.INDEX, version="0.1.0"),
    )

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert "can't upgrade automatically" in result.output


def test_yes_skips_confirmation_and_runs(monkeypatch, tmp_path):
    ran: list[list[upgrade.Step]] = []
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )
    monkeypatch.setattr(
        upgrade, "run_steps", lambda steps: (ran.append(steps), (True, ""))[1]
    )

    result = runner.invoke(app, ["upgrade", "--yes"])

    assert result.exit_code == 0
    assert len(ran) == 1
    assert "upgraded" in result.output


def test_a_failing_step_surfaces_as_a_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )
    monkeypatch.setattr(
        upgrade, "run_steps", lambda steps: (False, "git pull --ff-only exited 1.")
    )

    result = runner.invoke(app, ["upgrade", "--yes"])

    assert result.exit_code == 1
    assert "upgrade failed" in result.output


def test_declining_the_confirmation_runs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.1.0", path=_git_checkout(tmp_path)
        ),
    )

    def explode(steps):
        raise AssertionError("declining must not run anything")

    monkeypatch.setattr(upgrade, "run_steps", explode)

    result = runner.invoke(app, ["upgrade"], input="n\n")

    assert result.exit_code == 1
    assert "aborted" in result.output


# ---------------------------------------------------------------------------
# Optional extras
# ---------------------------------------------------------------------------


def test_extra_requirements_read_from_installed_metadata():
    """Not a hardcoded list — it has to track pyproject without being edited."""
    reqs = upgrade.extra_requirements("connectors")

    assert any(r.startswith("chdb") for r in reqs)
    # The `dev` extra's requirements must not leak into `connectors`.
    assert not any(r.startswith("pytest") for r in reqs)
    assert upgrade.extra_requirements("no-such-extra") == []


def test_extra_hint_is_uv_sync_from_the_projects_own_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(upgrade.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.CHECKOUT, version="0.2.0", path=_git_checkout(tmp_path)
        ),
    )

    assert upgrade.extra_install_hint("connectors") == "uv sync --extra connectors"


def test_extra_hint_targets_this_environment_when_uv_sync_would_miss_it(monkeypatch):
    """A `uv tool` install: `uv sync` would change a venv this CLI never reads.

    The failure mode being avoided is advice that appears to work — the sync
    succeeds, and the next `smart-connectors run` raises the same ImportError.
    """
    monkeypatch.setattr(upgrade, "_has_pip", lambda: False)
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.UV_TOOL, version="0.2.0", detail="uv tool manages this"
        ),
    )

    hint = upgrade.extra_install_hint("connectors")

    assert hint.startswith("uv pip install --python ")
    assert upgrade.sys.executable in hint
    assert "uv sync" not in hint


def test_extra_hint_quotes_version_specifiers(monkeypatch):
    """`pip install chdb>=4.1` pasted into a shell writes a file called `=4.1`."""
    # Force the branch that names requirements: from the project's own venv the
    # hint is `uv sync --extra connectors`, which has no specifiers to quote.
    monkeypatch.setattr(upgrade, "_has_pip", lambda: True)
    monkeypatch.setattr(
        upgrade,
        "detect_install",
        lambda: upgrade.Install(
            method=upgrade.UV_TOOL, version="0.2.0", detail="uv tool manages this"
        ),
    )

    hint = upgrade.extra_install_hint("connectors")

    assert "'chdb>=4.1'" in hint
    assert " chdb>=" not in hint


def test_missing_runtime_message_names_a_runnable_command():
    from kizen_builder.tools import smart_connectors as sct

    message = sct._missing_runtime_message()

    assert "connectors" in message
    assert upgrade.extra_install_hint("connectors") in message

"""Detect how this CLI was installed, and how to update it.

The tool reaches people in more than one shape — an editable git checkout (the
way everyone gets it today), a ``uv tool`` / ``pipx`` install, or eventually a
plain wheel off some index — and each one updates differently. Rather than
document four procedures and hope the reader picks the right one, this module
works out which shape is live and produces the exact commands for it.

Detection order, and why:

1. **PEP 610 ``direct_url.json``** is authoritative — the installer writes it,
   so an editable install always announces itself along with the directory it
   points at. That case wins outright: a checkout must be updated with git, no
   matter what else is true about it.
2. **``sys.prefix`` shape** identifies a tool manager (``pipx``, ``uv tool``),
   which owns the venv and should do the upgrading itself.
3. **``vcs_info``** preserves the exact spec a ``pip install git+…`` was made
   from, so the upgrade can re-request it verbatim.
4. **An enclosing git checkout of this project** catches what PEP 610 misses —
   legacy ``setup.py develop`` installs, a stale ``*.egg-info`` shadowing the
   real dist-info, or a checkout merely put on ``PYTHONPATH``.
5. Anything else came from an index, and there is no index configured yet —
   see ``_latest_from_index``.

Everything that touches the network lives behind :func:`check_latest`, is
capped by :data:`NETWORK_TIMEOUT`, is cached for a day, and fails silent. A
version check is a courtesy at session start; it must never be the reason a
session stalls or errors.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

DIST_NAME = "kizen-builder"

#: Seconds any single network git call may take before it's abandoned. Sized
#: for "cheap when it works, unnoticeable when it doesn't" — this runs at
#: session start, so a slow answer is worth less than a fast silence.
NETWORK_TIMEOUT = 5.0

#: Ceiling on a whole check, however many remotes it ends up trying. Bounds the
#: worst case (several unreachable URLs, each burning the full per-call
#: timeout) to something a person will still sit through.
CHECK_BUDGET = 12.0

#: Purely local git calls can't hang on the network, but shouldn't wedge a
#: session either if the repository is in a strange state.
LOCAL_TIMEOUT = 5.0

#: How long a check result stays fresh. A day is long enough that the network
#: call is rare, short enough that a push lands with a session or two.
CHECK_TTL_SECONDS = 24 * 60 * 60

_RELEASE_TAG = re.compile(r"^refs/tags/v?(\d+)\.(\d+)\.(\d+)$")
_VERSION_CORE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------

#: Install shapes, in the order the table in ``kizen upgrade --help`` lists them.
CHECKOUT = "checkout"
UV_TOOL = "uv-tool"
PIPX = "pipx"
VCS = "vcs"
INDEX = "index"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Install:
    """How the running ``kizen`` got onto this machine."""

    method: str
    version: str
    #: The checkout directory, for ``CHECKOUT``. ``None`` otherwise.
    path: Path | None = None
    #: The requested spec, for ``VCS`` (e.g. ``git+https://…@main``).
    url: str | None = None
    #: The bare git URL (no ``vcs+`` prefix, no pinned rev), when one is known
    #: for a non-``CHECKOUT`` install (``UV_TOOL``, ``PIPX``, ``VCS``). Lets
    #: :func:`check_latest` ask the remote about release tags even though
    #: there's no local checkout to walk. ``None`` when no VCS source is known
    #: (a plain index install, or a tool manager that isn't wired for one).
    repo_url: str | None = None
    #: One line naming the evidence, so ``--dry-run`` can show its work.
    detail: str = ""

    @property
    def is_checkout(self) -> bool:
        return self.method == CHECKOUT


def _installed_version() -> str:
    from kizen_builder import __version__

    return __version__


def _direct_url() -> dict | None:
    """The PEP 610 record for this install, if the installer wrote one."""
    try:
        dist = Distribution.from_name(DIST_NAME)
    except PackageNotFoundError:
        return None
    try:
        raw = dist.read_text("direct_url.json")
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _file_url_to_path(url: str) -> Path | None:
    """``file:///Users/me/Kizen%20Admin%20CLI`` -> ``/Users/me/Kizen Admin CLI``.

    The unquoting is load-bearing: install directories routinely contain
    spaces, and a percent-escaped path silently fails every ``is_dir`` check.
    """
    parts = urlsplit(url)
    if parts.scheme != "file":
        return None
    return Path(unquote(parts.path))


def _managed_by() -> str | None:
    """``pipx``/``uv tool`` own their venvs; spot them by where we're running.

    Both lay out ``…/<manager>/…/<package>/`` under a user data dir, so the
    marker is a path segment rather than anything the package can read about
    itself.
    """
    prefix = Path(sys.prefix).resolve()
    parts = [p.lower() for p in prefix.parts]
    if "pipx" in parts:
        return PIPX
    # uv installs tools under `<data>/uv/tools/<name>`; `uv/tools` together
    # avoids matching an unrelated directory that happens to be named "uv".
    for i in range(len(parts) - 1):
        if parts[i] == "uv" and parts[i + 1] == "tools":
            return UV_TOOL
    return None


def _source_checkout() -> Path | None:
    """A git checkout of *this project* that the running code lives inside.

    A fallback for the cases PEP 610 doesn't cover: a legacy ``setup.py
    develop`` install (which writes an egg-link, not ``direct_url.json``), a
    stale ``*.egg-info`` shadowing the real dist-info on ``sys.path``, or a
    checkout simply put on ``PYTHONPATH`` without being installed at all. All
    three otherwise look like an index install, which is the one answer that
    can't be acted on.

    The walk is bounded and verifies the ``pyproject.toml`` actually names this
    project, so an unrelated enclosing repository is never mistaken for ours.
    """
    here = Path(__file__).resolve().parent
    for candidate in list(here.parents)[:4]:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file() or not (candidate / ".git").exists():
            continue
        try:
            text = pyproject.read_text()
        except OSError:
            continue
        if re.search(rf'^name\s*=\s*"{re.escape(DIST_NAME)}"', text, re.MULTILINE):
            return candidate
    return None


def detect_install() -> Install:
    """Work out how this CLI was installed. Never raises."""
    version = _installed_version()
    record = _direct_url() or {}
    url = record.get("url") or ""
    dir_info = record.get("dir_info") or {}
    vcs_info = record.get("vcs_info") or {}

    if dir_info.get("editable"):
        path = _file_url_to_path(url)
        return Install(
            method=CHECKOUT,
            version=version,
            path=path,
            detail=f"editable install (PEP 610) from {path or url}",
        )

    managed = _managed_by()
    if managed:
        return Install(
            method=managed,
            version=version,
            repo_url=url if vcs_info else None,
            detail=f"{managed} manages the venv at {sys.prefix}",
        )

    if vcs_info:
        vcs = vcs_info.get("vcs", "git")
        rev = vcs_info.get("requested_revision")
        spec = f"{vcs}+{url}" + (f"@{rev}" if rev else "")
        return Install(
            method=VCS,
            version=version,
            url=spec,
            repo_url=url,
            detail=f"installed from {spec} (PEP 610)",
        )

    checkout = _source_checkout()
    if checkout is not None:
        return Install(
            method=CHECKOUT,
            version=version,
            path=checkout,
            detail=f"running from the git checkout at {checkout}",
        )

    if record:
        # A local wheel/sdist or a non-editable directory install. There's no
        # source to pull from and no index to query, so it's the same dead end
        # as INDEX but worth naming differently in the message.
        return Install(
            method=UNKNOWN,
            version=version,
            url=url or None,
            detail=f"installed directly from {url or 'a local artifact'}",
        )

    return Install(
        method=INDEX,
        version=version,
        detail=f"installed into {sys.prefix} from a package index",
    )


# ---------------------------------------------------------------------------
# Upgrade steps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One command to run, with the reason it's there."""

    argv: list[str]
    why: str
    cwd: Path | None = None

    def display(self) -> str:
        """The command as you'd type it. Deliberately excludes ``cwd``.

        Steps in a plan share a directory, so repeating it per row only makes
        every command wrap; callers print it once instead.
        """
        return " ".join(_quote(a) for a in self.argv)


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


class UpgradeUnsupported(RuntimeError):
    """This install shape can't be upgraded automatically.

    Carries ``advice``: what the user should do by hand instead.
    """

    def __init__(self, message: str, advice: str = "") -> None:
        super().__init__(message)
        self.advice = advice


def upgrade_steps(install: Install) -> list[Step]:
    """The commands that bring ``install`` up to date.

    Raises :class:`UpgradeUnsupported` when there's nothing safe to run — a
    printed instruction is a better outcome than a guessed index URL.
    """
    if install.is_checkout:
        return _checkout_steps(install)

    if install.method == UV_TOOL:
        return [
            Step(
                ["uv", "tool", "upgrade", DIST_NAME],
                why="uv owns this install and knows where it came from",
            )
        ]

    if install.method == PIPX:
        return [
            Step(
                ["pipx", "upgrade", DIST_NAME],
                why="pipx owns this install and knows where it came from",
            )
        ]

    if install.method == VCS and install.url:
        return [
            Step(
                [sys.executable, "-m", "pip", "install", "--upgrade", install.url],
                why="re-request the exact spec this was installed from",
            )
        ]

    raise UpgradeUnsupported(
        f"can't upgrade automatically — {install.detail}.",
        advice=(
            "No distribution channel is configured yet, so there is no index "
            "to upgrade from. Reinstall from the source you originally used, "
            "or clone the repo and `pip install -e .` for an install that "
            "`kizen upgrade` can keep current."
        ),
    )


def _checkout_steps(install: Install) -> list[Step]:
    path = install.path
    if path is None or not path.is_dir():
        raise UpgradeUnsupported(
            f"this is an editable install, but its source directory is gone "
            f"({path or 'unknown path'}).",
            advice="Re-clone the repo and re-run `pip install -e .` from it.",
        )
    if not (path / ".git").exists():
        raise UpgradeUnsupported(
            f"the editable source at {path} isn't a git checkout.",
            advice=(
                "Update the files however you obtained them, then re-run "
                "`uv sync` (or `pip install -e .`) in that directory."
            ),
        )

    steps = [
        Step(
            ["git", "pull", "--ff-only"],
            why="fast-forward the checkout; refuses rather than merging if it diverged",
            cwd=path,
        )
    ]
    # Pulling code without re-resolving dependencies is the failure mode this
    # exists to prevent: an upstream dependency addition currently surfaces as
    # a bare ImportError at the next command, with nothing pointing at the
    # cause. Syncing is not optional.
    #
    # Which syncer, though, is decided by where the *running* interpreter lives
    # — not merely by whether `uv` is on PATH. `uv sync` manages the checkout's
    # own `.venv`; if this CLI was pip-installed into some other environment,
    # syncing that .venv would install the new dependencies somewhere the
    # running `kizen` will never look, and the ImportError would survive an
    # apparently successful upgrade. `pip install -e` always targets the
    # environment it's invoked from, so it's the safe answer everywhere else.
    if _in_project_venv(path) and shutil.which("uv"):
        steps.append(
            Step(
                ["uv", "sync"], why="pick up any new or changed dependencies", cwd=path
            )
        )
    elif _has_pip():
        steps.append(
            Step(
                [sys.executable, "-m", "pip", "install", "-e", str(path)],
                why=f"pick up any new or changed dependencies, into {sys.prefix}",
                cwd=path,
            )
        )
    elif shutil.which("uv"):
        # `uv tool install --editable <checkout>` lands here: PEP 610 reports an
        # editable install, but the venv uv built for the tool has no pip in it,
        # so the branch above would plan a command that fails with "No module
        # named pip". `uv pip --python` installs into a named interpreter, which
        # is the same target `pip install -e` would have had.
        steps.append(
            Step(
                ["uv", "pip", "install", "--python", sys.executable, "-e", str(path)],
                why=f"pick up any new or changed dependencies, into {sys.prefix}",
                cwd=path,
            )
        )
    else:
        raise UpgradeUnsupported(
            f"the checkout at {path} can be pulled, but this environment "
            f"({sys.prefix}) has neither pip nor uv to reinstall it with.",
            advice=(
                "Run `git pull --ff-only` in that directory yourself, then "
                "reinstall with whatever tool built this environment. Without "
                "the reinstall, a new dependency shows up later as a bare "
                "ImportError."
            ),
        )
    return steps


def _in_project_venv(checkout: Path) -> bool:
    """Is the running interpreter inside the checkout's own virtualenv?"""
    try:
        return Path(sys.prefix).resolve().is_relative_to(checkout.resolve())
    except OSError:
        return False


def _has_pip() -> bool:
    """Can the running interpreter run ``-m pip``?

    Not a given: uv builds virtualenvs without pip, so every `uv venv` and
    `uv tool install` environment answers no.
    """
    try:
        return importlib.util.find_spec("pip") is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Optional extras
# ---------------------------------------------------------------------------

_EXTRA_MARKER = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


def extra_requirements(extra: str) -> list[str]:
    """What an optional extra pulls in, read from *installed* metadata.

    Deliberately not a hardcoded list: `Requires-Dist` in the dist-info is
    generated from `pyproject.toml`, so it can't drift from the real extra the
    way a copy in an error message would.
    """
    try:
        meta = metadata(DIST_NAME)
    except PackageNotFoundError:
        return []

    reqs = []
    for raw in meta.get_all("Requires-Dist") or []:
        spec, _, marker = raw.partition(";")
        found = _EXTRA_MARKER.search(marker)
        if found and found.group(1) == extra:
            reqs.append(spec.strip())
    return reqs


def extra_install_hint(extra: str) -> str:
    """The command that adds ``extra`` to *this* environment.

    Same trap as :func:`upgrade_steps`. The idiomatic instruction —
    ``uv sync --extra connectors`` — only works when you're running out of the
    checkout's own `.venv`. Anyone who installed with `uv tool install` or
    `pipx` would run it, watch it succeed, and hit the identical ImportError,
    because the dependency landed in an environment their `kizen` never looks
    at. So resolve the command against the install shape rather than printing
    the one from the README.

    Installs the extra's *requirements* rather than ``kizen-builder[extra]``:
    for import purposes it's equivalent, and it doesn't need an index to fetch
    this package from — which matters, since there isn't one yet.
    """
    install = detect_install()

    if (
        install.is_checkout
        and install.path is not None
        and _in_project_venv(install.path)
        and shutil.which("uv")
    ):
        return f"uv sync --extra {extra}"

    reqs = extra_requirements(extra)
    if not reqs:
        # No metadata to read (running from a source tree with no dist-info,
        # say). Name the extra and let the packaging tool resolve it.
        target = (
            _quote(f"{install.path}[{extra}]")
            if install.path
            else f"{DIST_NAME}[{extra}]"
        )
        editable = "-e " if install.is_checkout else ""
        return f"{sys.executable} -m pip install {editable}{target}"

    # Requirement specs must be single-quoted, not conditionally quoted like an
    # argv element: `pip install chdb>=4.1` pasted into a shell is a
    # redirection, and silently writes a file called `=4.1` instead of
    # installing anything.
    joined = " ".join(f"'{r}'" for r in reqs)
    if _has_pip():
        return f"{sys.executable} -m pip install {joined}"
    if shutil.which("uv"):
        return f"uv pip install --python {_quote(sys.executable)} {joined}"
    return f"(install {joined} into {sys.prefix})"


def run_steps(steps: list[Step]) -> tuple[bool, str]:
    """Run steps in order, stopping at the first failure.

    Returns ``(ok, message)``. Output is not captured — the user should see
    git's and uv's own progress, which is more informative than anything this
    could summarize. The environment is inherited untouched, unlike the update
    check: someone who just confirmed an upgrade is present to answer an SSH
    passphrase or a credential prompt, and suppressing it would only turn a
    solvable pause into an unexplained failure.
    """
    for step in steps:
        try:
            result = subprocess.run(
                step.argv,
                cwd=str(step.cwd) if step.cwd else None,
                check=False,
            )
        except FileNotFoundError:
            return False, f"{step.argv[0]} is not installed or not on PATH."
        except OSError as exc:
            return False, f"could not run {step.display()}: {exc}"
        if result.returncode != 0:
            return False, f"{step.display()} exited {result.returncode}."
    return True, ""


# ---------------------------------------------------------------------------
# Update check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """What a version check could determine. Inconclusive is a valid answer."""

    current: str
    #: The newest released version, when a tag comparison was possible.
    latest: str | None = None
    #: Commits the checkout is behind its upstream, when tags weren't usable.
    behind: int | None = None
    #: ``tags`` | ``commits`` | ``index`` | ``none``
    source: str = "none"
    #: Why the answer is inconclusive, when it is. Never shown by default.
    reason: str = ""
    #: True when the result came from cache rather than the network.
    cached: bool = False

    @property
    def conclusive(self) -> bool:
        return self.latest is not None or self.behind is not None

    @property
    def out_of_date(self) -> bool:
        if self.behind is not None:
            return self.behind > 0
        if self.latest is None:
            return False
        return _version_tuple(self.latest) > _version_tuple(self.current)

    def summary(self) -> str:
        """One line, suitable for a session-start nag."""
        if self.behind is not None and self.behind > 0:
            plural = "s" if self.behind != 1 else ""
            return f"{self.behind} commit{plural} behind upstream"
        if self.latest is not None and self.out_of_date:
            return f"{self.latest} is available (you have {self.current})"
        if self.conclusive:
            return f"up to date ({self.current})"
        return f"can't tell — {self.reason or 'no update source available'}"


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = _VERSION_CORE.match(text.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _noninteractive_env() -> dict[str, str]:
    """Env that guarantees git can never block on a credential prompt.

    Without this a check against an SSH remote can sit forever on a passphrase
    prompt — and a hang at session start is far worse than an unanswered
    question about versions.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh")
    env["GIT_SSH_COMMAND"] += " -oBatchMode=yes"
    return env


def _git(args: list[str], cwd: Path, timeout: float = NETWORK_TIMEOUT) -> str | None:
    """Run a git command, returning stdout or ``None`` on any failure.

    A non-positive ``timeout`` means the caller's budget is already spent, which
    counts as a failure rather than an unbounded call.
    """
    if timeout <= 0:
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=_noninteractive_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


_SSH_REMOTE = re.compile(r"^(?:ssh://)?(?:git@)([^:/]+)[:/](.+?)(?:\.git)?/?$")


def _https_equivalent(url: str) -> str | None:
    """``git@host:owner/repo.git`` -> ``https://host/owner/repo.git``."""
    match = _SSH_REMOTE.match(url.strip())
    if not match:
        return None
    return f"https://{match.group(1)}/{match.group(2)}.git"


def _remote_candidates(cwd: Path) -> list[str]:
    """URLs worth asking, best first.

    An SSH remote needs a loaded key; the HTTPS form of the same repository
    needs only a credential helper. Since a check is read-only, trying both is
    free and turns "can't tell" into a real answer for anyone whose git is set
    up to push over one and not the other.
    """
    out = _git(["remote", "get-url", "origin"], cwd, timeout=LOCAL_TIMEOUT)
    if not out or not out.strip():
        return []
    url = out.strip()
    candidates = [url]
    https = _https_equivalent(url)
    if https and https != url:
        candidates.append(https)
    return candidates


def _latest_tag(cwd: Path, url: str, timeout: float) -> tuple[bool, str | None]:
    """Highest ``vX.Y.Z`` tag on ``url``, without fetching objects.

    Returns ``(reached, tag)``. The flag matters: "reached it, and it has no
    release tags" is a real answer that must not trigger a retry against the
    next URL, and it's indistinguishable from failure by the tag alone.
    """
    out = _git(["ls-remote", "--tags", "--refs", url], cwd, timeout=timeout)
    if out is None:
        return (False, None)
    best: tuple[int, int, int] | None = None
    for line in out.splitlines():
        _, _, ref = line.partition("\t")
        match = _RELEASE_TAG.match(ref.strip())
        if not match:
            continue
        candidate = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or candidate > best:
            best = candidate
    return (True, ".".join(str(n) for n in best) if best else None)


def _commits_behind(cwd: Path, url: str, timeout: float) -> int | None:
    """How many commits the remote's default branch has that HEAD doesn't.

    Fetches into ``FETCH_HEAD`` rather than updating a named remote, so a check
    leaves no trace in the user's ref namespace. Comparing against the default
    branch (rather than the current branch's tracking ref) answers the question
    that matters — "is there work upstream I don't have" — even when the
    checkout is sitting on a feature branch the remote has never seen.
    """
    if _git(["fetch", "--quiet", url, "HEAD"], cwd, timeout=timeout) is None:
        return None
    out = _git(["rev-list", "--count", "HEAD..FETCH_HEAD"], cwd, timeout=LOCAL_TIMEOUT)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _latest_from_index() -> str | None:
    """The newest published version, once there is somewhere to publish to.

    This is the seam. The distribution channel is deliberately undecided — the
    repo is private and shipped content needs review first — so every
    non-checkout install currently reports "can't tell" rather than guessing at
    an index. Wiring a registry later means implementing this one function; no
    caller changes.
    """
    return None


def cache_home() -> Path:
    """The kizen cache directory, honoring ``$XDG_CACHE_HOME``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "kizen"


def _cache_path() -> Path:
    return cache_home() / "upgrade-check.json"


def _read_cache(current: str) -> CheckResult | None:
    """A fresh cached result for *this* version, or ``None``.

    Keyed on the installed version so that upgrading invalidates the cache
    immediately — otherwise the nag would survive the fix that silenced it.
    """
    try:
        raw = json.loads(_cache_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("installed") != current:
        return None
    checked_at = raw.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return None
    if time.time() - checked_at > CHECK_TTL_SECONDS:
        return None
    payload = raw.get("result")
    if not isinstance(payload, dict):
        return None
    try:
        return CheckResult(**payload, cached=True)
    except TypeError:
        return None


def _write_cache(result: CheckResult) -> None:
    """Persist a result, best-effort. An unwritable cache is not an error."""
    payload = {
        "installed": result.current,
        "checked_at": time.time(),
        "result": {
            "current": result.current,
            "latest": result.latest,
            "behind": result.behind,
            "source": result.source,
            "reason": result.reason,
        },
    }
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError:
        pass


def check_latest(install: Install, *, refresh: bool = False) -> CheckResult:
    """Find out whether a newer version exists. Never raises, never hangs.

    Answers from cache unless ``refresh``. Every failure path — offline, no
    remote, auth prompt, no tags, no channel — collapses to an inconclusive
    result with a reason attached, because a version check that can break a
    session is worse than no version check.
    """
    current = install.version
    if not refresh:
        cached = _read_cache(current)
        if cached is not None:
            return cached

    result = _check_uncached(install)
    _write_cache(result)
    return result


def _check_checkout(current: str, cwd: Path) -> CheckResult:
    """Compare a checkout against its remote, tags first, commits as a fallback.

    Tags are the better answer once releases are cut — they're what
    ``current`` is actually comparable to. Until then there are none, and
    "N commits behind" is the only signal that exists, so both paths stay.
    """
    candidates = _remote_candidates(cwd)
    if not candidates:
        return CheckResult(
            current=current, reason="this checkout has no 'origin' remote"
        )

    deadline = time.monotonic() + CHECK_BUDGET

    def remaining() -> float:
        return min(NETWORK_TIMEOUT, deadline - time.monotonic())

    reached_any = False
    for url in candidates:
        budget = remaining()
        if budget <= 0:
            break
        reached, tag = _latest_tag(cwd, url, budget)
        if not reached:
            continue
        reached_any = True
        if tag:
            return CheckResult(current=current, latest=tag, source="tags")
        # Reached a remote that simply has no releases yet; asking a second URL
        # for the same repository would only get the same empty answer.
        behind = _commits_behind(cwd, url, remaining())
        if behind is not None:
            return CheckResult(current=current, behind=behind, source="commits")
        break

    reason = (
        "the remote has no releases and its history couldn't be compared"
        if reached_any
        else "couldn't reach the git remote"
    )
    return CheckResult(current=current, reason=reason)


def _check_remote_tags(current: str, url: str) -> CheckResult:
    """Compare against release tags on a known git URL.

    For ``UV_TOOL``/``PIPX``/``VCS`` installs there's no local checkout to walk,
    so unlike :func:`_check_checkout` this can't fall back to a commit count —
    there's no local history to diff it against, only the URL the tool was
    installed from. Once a ``vX.Y.Z`` tag exists, that's enough; until then this
    is the same "can't tell" as no distribution channel at all, just with a more
    specific reason.
    """
    reached, tag = _latest_tag(Path.cwd(), url, NETWORK_TIMEOUT)
    if not reached:
        return CheckResult(current=current, reason="couldn't reach the git remote")
    if tag:
        return CheckResult(current=current, latest=tag, source="tags")
    return CheckResult(current=current, reason="the remote has no release tags yet")


def _check_uncached(install: Install) -> CheckResult:
    current = install.version

    if install.is_checkout and install.path and (install.path / ".git").exists():
        return _check_checkout(current, install.path)

    if install.repo_url:
        return _check_remote_tags(current, install.repo_url)

    latest = _latest_from_index()
    if latest:
        return CheckResult(current=current, latest=latest, source="index")
    return CheckResult(
        current=current,
        reason="no distribution channel is configured for this install",
    )

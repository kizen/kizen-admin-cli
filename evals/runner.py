"""Prepare a run, hand it to an agent, score what the agent left behind.

One run is one task, once: a fresh working folder shaped like the one `kizen
init` leaves, a logging `kizen` on its PATH, and a prompt. Everything a run
produces lands in its own directory:

    <out>/<task>/<trial>/
        run.json          task, prefix, how it was launched
        prompt.md         exactly what the agent was given
        work/             the agent's working directory
        bin/kizen         the logging shim
        kizen.jsonl       one line per CLI call the session made
        transcript.jsonl  the agent's transcript, when the harness launched it
        setup/, final/    raw reads of the task's automations, before and after
        result.json       pass/fail, problems and metrics
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import graph, metrics, shim, tasks
from evals.tasks import Task

#: The default agent: a headless Claude Code session with no user settings, no
#: MCP servers, no skills, and only the file and shell tools.
_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
CLAUDE_CMD = [
    "claude", "-p", "--output-format", "stream-json", "--verbose",
    "--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands",
    "--no-session-persistence", "--tools", _TOOLS, "--allowedTools", _TOOLS,
]  # fmt: skip


@dataclass
class Config:
    out: Path
    profile: str | None = None
    kizen: list[str] = field(default_factory=shim.default_real_kizen)
    agent_cmd: list[str] = field(default_factory=lambda: list(CLAUDE_CMD))
    model: str | None = None
    timeout: int = 1800
    keep: bool = False


class HarnessError(RuntimeError):
    """The harness itself couldn't do its part (setup, a read, a cleanup)."""


def new_prefix() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "ev" + "".join(secrets.choice(alphabet) for _ in range(6))


def kizen(
    cfg: Config, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI (unlogged) against the eval profile."""
    env = dict(os.environ)
    if cfg.profile:
        env["KIZEN_PROFILE"] = cfg.profile
    return subprocess.run(
        [*cfg.kizen, *args], input=stdin, capture_output=True, text=True, env=env
    )


def _must(proc: subprocess.CompletedProcess[str], what: str) -> str:
    if proc.returncode != 0:
        raise HarnessError(
            f"{what} failed: {(proc.stderr or proc.stdout).strip()[-600:]}"
        )
    return proc.stdout


def read_raw(cfg: Config, api_name: str) -> dict[str, Any] | None:
    """The automation as the API returns it, or None when there is none."""
    proc = kizen(cfg, "automations", "get", api_name, "--raw")
    if proc.returncode != 0:
        if "no automation" in proc.stderr:
            return None
        raise HarnessError(f"reading {api_name} failed: {proc.stderr.strip()[-600:]}")
    return json.loads(proc.stdout)


def prepare(task: Task, cfg: Config, trial: int = 1) -> Path:
    """Build the run directory and apply the task's setup. Returns the run dir."""
    run_dir = (cfg.out / task.id / f"{trial:02d}").resolve()
    if run_dir.exists():
        raise HarnessError(f"{run_dir} already exists; pick another --out")
    work = run_dir / "work"
    work.mkdir(parents=True)
    prefix = new_prefix()

    from kizen_builder import docs

    docs.write_stubs(work, cfg.profile or "eval")
    for name in task.files:
        shutil.copy(tasks.ASSETS_DIR / name, work / name)
    shim.write_shim(run_dir / "bin", run_dir / "kizen.jsonl", cfg.kizen, cfg.profile)

    (run_dir / "setup").mkdir()
    for item in task.setup:
        spec = tasks.load_setup(item["spec"], prefix)
        _must(
            kizen(cfg, "automations", "create", "--yes", stdin=json.dumps(spec)),
            f"setup: creating {spec['api_name']}",
        )
        if item.get("activate"):
            _must(
                kizen(cfg, "automations", "activate", spec["api_name"], "--yes"),
                f"setup: activating {spec['api_name']}",
            )
        raw = read_raw(cfg, spec["api_name"])
        (run_dir / "setup" / f"{spec['api_name']}.raw.json").write_text(json.dumps(raw))

    (run_dir / "prompt.md").write_text(tasks.render_prompt(task, prefix))
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "task": task.id,
                "trial": trial,
                "prefix": prefix,
                "profile": cfg.profile,
                "kizen": cfg.kizen,
                "kizen_version": kizen(cfg, "--version").stdout.strip(),
                "model": cfg.model,
                "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=2,
        )
    )
    return run_dir


def session_env(run_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{run_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def launch(run_dir: Path, cfg: Config) -> None:
    """Run the agent on the prompt, in the run's working folder."""
    cmd = list(cfg.agent_cmd)
    if cfg.model:
        cmd += ["--model", cfg.model]
    started = time.time()
    with (
        (run_dir / "transcript.raw").open("wb") as out,
        (run_dir / "agent.stderr").open("wb") as err,
    ):
        try:
            proc = subprocess.run(
                cmd,
                input=(run_dir / "prompt.md").read_bytes(),
                stdout=out,
                stderr=err,
                cwd=run_dir / "work",
                env=session_env(run_dir),
                timeout=cfg.timeout,
            )
            status: Any = proc.returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
        except FileNotFoundError as e:
            raise HarnessError(f"agent command not found: {cmd[0]}") from e
    # Token-by-token stream events are most of the file and none of the signal.
    raw = run_dir / "transcript.raw"
    with (run_dir / "transcript.jsonl").open("w") as slim:
        for line in raw.read_text(errors="replace").splitlines():
            if '"type":"stream_event"' not in line.replace(" ", ""):
                slim.write(line + "\n")
    raw.unlink()
    meta = json.loads((run_dir / "run.json").read_text())
    meta.update(
        agent_cmd=cmd, agent_exit=status, wall_seconds=round(time.time() - started, 1)
    )
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))


def score(
    run_dir: Path, cfg: Config, tasks_by_id: dict[str, Task] | None = None
) -> dict[str, Any]:
    """Read back what the session built, check it, and write result.json."""
    meta = json.loads((run_dir / "run.json").read_text())
    by_id = tasks_by_id or {t.id: t for t in tasks.load_tasks()}
    task = by_id[meta["task"]]
    prefix = meta["prefix"]
    (run_dir / "final").mkdir(exist_ok=True)
    problems: list[str] = []
    for expect in task.expect:
        expect = tasks.fill(expect, prefix)
        name = expect["api_name"]
        raw = read_raw(cfg, name)
        if raw is None:
            problems.append(f"{name}: not found")
            continue
        (run_dir / "final" / f"{name}.raw.json").write_text(json.dumps(raw))
        before_file = run_dir / "setup" / f"{name}.raw.json"
        before = (
            graph.from_raw(json.loads(before_file.read_text()))
            if before_file.is_file()
            else None
        )
        problems += [
            f"{name}: {p}"
            for p in graph.run_checks(graph.from_raw(raw), expect, before)
        ]
    result = {
        **{
            k: meta.get(k)
            for k in ("task", "trial", "prefix", "model", "kizen_version")
        },
        "tier": task.tier,
        "surface": task.surface,
        "passed": not problems,
        "problems": problems,
        "agent_exit": meta.get("agent_exit"),
        "wall_seconds": meta.get("wall_seconds"),
        **metrics.run_metrics(run_dir),
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    return result


def cleanup(run_dir: Path, cfg: Config) -> list[str]:
    """Delete every automation carrying the run's prefix. Returns their api_names."""
    prefix = json.loads((run_dir / "run.json").read_text())["prefix"]
    listing = json.loads(
        _must(kizen(cfg, "automations", "list", "--json"), "listing automations")
    )
    rows = listing if isinstance(listing, list) else listing.get("automations", [])
    mine = [a for a in rows if str(a.get("api_name", "")).startswith(f"{prefix}_")]
    # Active ones first deactivated: another automation may still start them.
    for a in mine:
        if a.get("active"):
            kizen(cfg, "automations", "deactivate", a["api_name"], "--yes")
    deleted = []
    for a in mine:
        if kizen(cfg, "automations", "delete", a["api_name"], "--yes").returncode == 0:
            deleted.append(a["api_name"])
    return deleted


def run_one(task: Task, cfg: Config, trial: int = 1) -> dict[str, Any]:
    """prepare → launch → score → cleanup, never raising for a run's own failure."""
    run_dir: Path | None = None
    try:
        run_dir = prepare(task, cfg, trial)
        launch(run_dir, cfg)
        return score(run_dir, cfg, {task.id: task})
    except HarnessError as e:
        result = {"task": task.id, "trial": trial, "tier": task.tier, "passed": False,
                  "harness_error": str(e), "problems": [f"harness: {e}"]}  # fmt: skip
        if run_dir is not None and run_dir.is_dir():
            (run_dir / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        if run_dir is not None and (run_dir / "run.json").is_file() and not cfg.keep:
            try:
                cleanup(run_dir, cfg)
            except HarnessError as e:
                print(f"cleanup of {run_dir} failed: {e}")

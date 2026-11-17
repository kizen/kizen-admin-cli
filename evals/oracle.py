"""Apply each task's reference solution and score it, with no agent involved.

This is the check to run before spending anything on sessions. A solution that
doesn't apply means the task, the solution or the eval world is wrong. One
that applies and then fails its checks or its rubric means the grading is
wrong: a correct build would be marked down.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evals import runner, seed, tasks
from evals.runner import Config, HarnessError, _must, kizen
from evals.tasks import Task

_MESSAGE_TOKEN = re.compile(r"<message_\d+>")
TEMPLATE = "Eval Notice"


def _apply(cfg: Config, spec: dict[str, Any], exists: bool) -> None:
    verb = "update" if exists else "create"
    _must(
        kizen(cfg, "automations", verb, "--yes", stdin=json.dumps(spec)),
        f"automations {verb} {spec['api_name']}",
    )


def _new_message(cfg: Config, api_name: str) -> str:
    out = _must(
        kizen(
            cfg,
            "messages",
            "create",
            api_name,
            "--template",
            TEMPLATE,
            "--yes",
            "--json",
        ),
        f"messages create {api_name}",
    )
    return json.loads(out)["results"][0]["server_uuid"]


def apply_solution(task: Task, cfg: Config, prefix: str) -> None:
    """Build the task's reference solution in the business, through the CLI."""
    for spec in tasks.load_solution(task, prefix):
        spec = seed._fill_tokens(spec, cfg)
        text = json.dumps(spec)
        if "<transcription_model>" in text:
            model = seed.transcription_model(cfg)
            if model is None:
                raise HarnessError("this business has no transcription model")
            text = text.replace("<transcription_model>", model)
        spec = json.loads(text)
        activate = bool(spec.pop("active", False))
        name = spec["api_name"]
        exists = runner.read_raw(cfg, name) is not None
        tokens = sorted(set(_MESSAGE_TOKEN.findall(text)))
        if tokens:
            # An email step points at a message, and a message needs its
            # automation to exist: create it empty, make the messages, then
            # add the steps.
            if not exists:
                _apply(cfg, {**spec, "steps": []}, exists=False)
                exists = True
            for token in tokens:
                text = text.replace(token, _new_message(cfg, name))
            spec = json.loads(text)
            spec.pop("active", None)
        _apply(cfg, spec, exists)
        if activate:
            _must(
                kizen(cfg, "automations", "activate", name, "--yes"),
                f"automations activate {name}",
            )


def run_oracle(task: Task, cfg: Config) -> dict[str, Any]:
    """prepare → apply the solution → score → cleanup, for one task."""
    run_dir: Path | None = None
    try:
        run_dir = runner.prepare(task, cfg)
        meta = json.loads((run_dir / "run.json").read_text())
        apply_solution(task, cfg, meta["prefix"])
        meta["agent_exit"] = "oracle"
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
        return runner.score(run_dir, cfg, {task.id: task}, compare_ids=False)
    except HarnessError as e:
        return {"task": task.id, "tier": task.tier, "passed": False, "correct": False,
                "harness_error": str(e), "problems": [f"harness: {e}"]}  # fmt: skip
    finally:
        if run_dir is not None and (run_dir / "run.json").is_file() and not cfg.keep:
            try:
                runner.cleanup(run_dir, cfg)
            except HarnessError as e:
                print(f"cleanup of {run_dir} failed: {e}")

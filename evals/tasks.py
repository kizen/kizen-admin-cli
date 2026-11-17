"""Load the task definitions, their reference solutions, and the eval world."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
TASKS_DIR = ROOT / "tasks"
SOLUTIONS_DIR = ROOT / "solutions"
SETUP_DIR = ROOT / "setup"
ASSETS_DIR = ROOT / "assets"
WORLD_FILE = ROOT / "world" / "world.json"
PREAMBLE_FILE = ROOT / "preamble.md"

#: The token a task, setup spec or solution writes where the run's prefix goes.
PREFIX_TOKEN = "{p}"

#: Wired types no task covers, each with the reason. `test_evals.py` fails
#: when a wired type is in neither a task nor this table, so wiring a new
#: type forces a decision here.
UNCOVERED: dict[str, str] = {
    "plugin_code_step": "needs an installed plugin, which the eval world doesn't have",
    "send_related_contact_text": "points at a text message resource the CLI can't create",
}


@dataclass(frozen=True)
class Task:
    id: str
    tier: int
    surface: str
    title: str
    prompt: str
    triggers: tuple[str, ...]
    steps: tuple[str, ...]
    features: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    setup: tuple[dict[str, Any], ...] = ()
    expect: tuple[dict[str, Any], ...] = field(default=())


def fill(value: Any, prefix: str) -> Any:
    """`value` with every `{p}` replaced by `prefix`, at any depth."""
    if isinstance(value, str):
        return value.replace(PREFIX_TOKEN, prefix)
    if isinstance(value, dict):
        return {k: fill(v, prefix) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [fill(v, prefix) for v in value]
    return value


def load_tasks() -> list[Task]:
    tasks = []
    for path in sorted(TASKS_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text())
        if data["id"] != path.stem:
            raise ValueError(
                f"{path.name}: id {data['id']!r} doesn't match the filename"
            )
        covers = data.get("covers", {})
        tasks.append(
            Task(
                id=data["id"],
                tier=data["tier"],
                surface=data["surface"],
                title=data["title"],
                prompt=data["prompt"].strip(),
                triggers=tuple(covers.get("triggers", ())),
                steps=tuple(covers.get("steps", ())),
                features=tuple(covers.get("features", ())),
                requires=tuple(data.get("requires", ())),
                files=tuple(data.get("files", ())),
                setup=tuple(data.get("setup", ())),
                expect=tuple(data["expect"]),
            )
        )
    return tasks


def select(tasks: list[Task], patterns: list[str]) -> list[Task]:
    """Tasks whose id starts with a pattern, or whose tier is `tier<N>`."""
    if not patterns:
        return tasks
    out = []
    for t in tasks:
        if any(t.id.startswith(p) or p == f"tier{t.tier}" for p in patterns):
            out.append(t)
    return out


def load_setup(name: str, prefix: str) -> dict[str, Any]:
    return fill(json.loads((SETUP_DIR / name).read_text()), prefix)


def load_solution(task: Task, prefix: str) -> list[dict[str, Any]]:
    """The reference specs for a task, one per `[[expect]]`, in the same order."""
    data = json.loads((SOLUTIONS_DIR / f"{task.id}.json").read_text())
    return fill(data, prefix)


def load_world() -> dict[str, Any]:
    return json.loads(WORLD_FILE.read_text())


def preamble(prefix: str) -> str:
    return fill(PREAMBLE_FILE.read_text(), prefix).strip()


def render_prompt(task: Task, prefix: str) -> str:
    return f"{preamble(prefix)}\n\n## Task\n\n{fill(task.prompt, prefix)}\n"

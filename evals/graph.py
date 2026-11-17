"""One shape for an automation, whether it came from a spec or a live read.

A spec (`automations create --spec-file`) and a live read (`automations get
--raw`) describe the same graph in two dialects. `from_spec` and `from_raw`
turn either into a `Graph`, and `run_checks` asserts a task's expectations
against it. The checks stay at the level both dialects share: which triggers
and steps exist, how they hang off each other, and scalar config values.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    id: str
    type: str
    parent: str | None
    branch: str  # "yes", "no" or ""
    block: dict[str, Any]
    goto: str | None = None  # id of the step a go_to_automation_step jumps to


@dataclass
class Graph:
    api_name: str
    type: str
    target_object: str | None
    active: bool
    triggers: list[tuple[str, dict[str, Any]]]
    steps: list[Step]
    _children: dict[str | None, list[Step]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for s in self.steps:
            self._children.setdefault(s.parent, []).append(s)

    def children(self, step: Step, branch: str | None = None) -> list[Step]:
        kids = self._children.get(step.id, [])
        return kids if branch is None else [k for k in kids if k.branch == branch]

    def subtree(self, roots: list[Step]) -> list[Step]:
        out, todo = [], list(roots)
        while todo:
            s = todo.pop()
            out.append(s)
            todo.extend(self._children.get(s.id, []))
        return out

    def of_type(self, step_type: str) -> list[Step]:
        return [s for s in self.steps if s.type == step_type]

    def by_id(self, step_id: str | None) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)


def _block(item: dict[str, Any], prefixes: tuple[str, ...]) -> dict[str, Any]:
    """The item's config block: its one non-empty `step_*`/`action_*`/`trigger_*` dict."""
    for key, value in item.items():
        if key.startswith(prefixes) and isinstance(value, dict) and value:
            return value
    return {}


_STEP_BLOCKS = ("step_", "action_")


def from_spec(spec: dict[str, Any]) -> Graph:
    steps = []
    for s in spec.get("steps") or []:
        block = _block(s, _STEP_BLOCKS)
        steps.append(
            Step(
                id=s["key"],
                type=s["step_type"],
                parent=s.get("parent_key"),
                branch=s.get("parent_branch") or "",
                block=block,
                goto=block.get("step_key") if s["step_type"] == _GOTO else None,
            )
        )
    return Graph(
        api_name=spec["api_name"],
        type=spec.get("type") or "record_based",
        target_object=spec.get("target_object"),
        active=bool(spec.get("active")),
        triggers=[
            (t["trigger_type"], _block(t, ("trigger_",)))
            for t in spec.get("triggers") or []
        ],
        steps=steps,
    )


def from_raw(raw: dict[str, Any]) -> Graph:
    steps = []
    for s in raw.get("steps") or []:
        block = _block(s, _STEP_BLOCKS)
        target = block.get("step") if s["step_type"] == _GOTO else None
        steps.append(
            Step(
                id=s["id"],
                type=s["step_type"],
                parent=s.get("parent_step_id"),
                branch=s.get("parent_condition") or "",
                block=block,
                goto=target.get("id") if isinstance(target, dict) else target,
            )
        )
    obj = raw.get("custom_object") or {}
    return Graph(
        api_name=raw["api_name"],
        type=raw.get("type") or "record_based",
        target_object=obj.get("name") if isinstance(obj, dict) else obj,
        active=bool(raw.get("active")),
        triggers=[
            (t["trigger_type"], _block(t, ("trigger_",)))
            for t in raw.get("triggers") or []
        ],
        steps=steps,
    )


_GOTO = "go_to_automation_step"
_CLOSERS = {"merge_branches", "next_or_finish"}
_REF_KEYS = ("name", "api_name", "id", "value")


def loosely_matches(expected: Any, actual: Any) -> bool:
    """Whether `actual` carries `expected`, across the spec and read dialects.

    A dict must hold every expected key. A list must hold a match for every
    expected item. A scalar matches an equal scalar, any item of a list, or a
    read's expanded reference (`{id, name, …}`) that names it. `"*"` matches
    anything non-empty.
    """
    if expected == "*":
        return actual not in (None, "", [], {})
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and loosely_matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(loosely_matches(e, a) for a in actual) for e in expected
        )
    if isinstance(actual, dict):
        return any(actual.get(k) == expected for k in _REF_KEYS)
    if isinstance(actual, list):
        return any(loosely_matches(expected, a) for a in actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    return expected == actual


def _types(steps: list[Step]) -> Counter[str]:
    return Counter(s.type for s in steps)


def _has_all(wanted: list[str], steps: list[Step]) -> bool:
    have = _types(steps)
    return all(have[t] >= n for t, n in Counter(wanted).items())


def _check(g: Graph, c: dict[str, Any], before: Graph | None) -> str | None:
    """Why check `c` fails on `g`, or None."""
    kind = c["kind"]
    if kind == "branch":
        # Some `of` step whose yes/no branches hold the listed step types.
        for s in g.of_type(c.get("of", "condition")):
            if all(
                _has_all(c.get(b, []), g.subtree(g.children(s, b)))
                for b in ("yes", "no")
            ):
                return None
        return f"no {c.get('of', 'condition')} with yes={c.get('yes', [])} no={c.get('no', [])}"
    if kind == "loop_body":
        for s in g.of_type("loop"):
            body = [k for k in g.children(s) if k.type != "next_or_finish"]
            if _has_all(c["has"], g.subtree(body)):
                return None
        return f"no loop whose body holds {c['has']}"
    if kind == "merged":
        for s in g.of_type(c["of"]):
            if any(k.type == "merge_branches" for k in g.children(s)):
                return None
        return f"no {c['of']} whose branches rejoin at a merge_branches step"
    if kind == "lanes":
        # A parallel `branch` step whose lanes together hold the listed types.
        for s in g.of_type("branch"):
            lanes = [k for k in g.children(s) if k.type not in _CLOSERS]
            if len(lanes) >= c.get("min", 2) and _has_all(
                c.get("has", []), g.subtree(lanes)
            ):
                return None
        return f"no parallel branch with {c.get('min', 2)}+ lanes holding {c.get('has', [])}"
    if kind == "goto":
        for s in g.of_type(_GOTO):
            target = g.by_id(s.goto)
            if target is not None and target.type == c["target"]:
                return None
        return f"no go_to_automation_step that jumps to a {c['target']} step"
    if kind == "order":
        for s in g.of_type(c["first"]):
            if any(d.type == c["then"] for d in g.subtree(g.children(s))):
                return None
        return f"no {c['then']} step downstream of a {c['first']} step"
    if kind == "block":
        if any(loosely_matches(c["match"], s.block) for s in g.of_type(c["step"])):
            return None
        return f"no {c['step']} step whose config holds {c['match']}"
    if kind == "trigger_block":
        if any(
            t == c["trigger"] and loosely_matches(c["match"], b) for t, b in g.triggers
        ):
            return None
        return f"no {c['trigger']} trigger whose config holds {c['match']}"
    if kind == "absent":
        if g.of_type(c["step"]):
            return f"a {c['step']} step is still there"
        return None
    if kind == "kept_ids":
        # Every step the automation had before the session is still there
        # under the same id. Skipped when there is no "before" (a spec).
        if before is None:
            return None
        lost = {s.id for s in before.steps if s.type not in c.get("except", [])}
        lost -= {s.id for s in g.steps}
        return f"{len(lost)} pre-existing step(s) lost their id" if lost else None
    raise ValueError(f"unknown check kind {kind!r}")


def run_checks(
    g: Graph, expect: dict[str, Any], before: Graph | None = None
) -> list[str]:
    """Every way `g` falls short of one `[[expect]]` table. Empty means it passes."""
    problems: list[str] = []
    if expect.get("type") and g.type != expect["type"]:
        problems.append(f"type is {g.type}, expected {expect['type']}")
    if "target_object" in expect and g.target_object != expect["target_object"]:
        problems.append(
            f"target_object is {g.target_object}, expected {expect['target_object']}"
        )
    if "active" in expect and g.active is not expect["active"]:
        problems.append(f"active is {g.active}, expected {expect['active']}")
    have_triggers = {t for t, _ in g.triggers}
    for t in expect.get("triggers", []):
        if t not in have_triggers:
            problems.append(f"missing trigger {t}")
    have = _types(g.steps)
    for step_type, n in (expect.get("steps") or {}).items():
        if have[step_type] < n:
            problems.append(f"{step_type}: {have[step_type]} step(s), expected {n}+")
    for c in expect.get("checks", []):
        problem = _check(g, c, before)
        if problem:
            problems.append(problem)
    return problems

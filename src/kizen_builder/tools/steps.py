"""Step-level graph surgery on translated automation payloads.

The Kizen API has no per-step endpoint: every change to a step means
GET → translate (:func:`kizen_builder.translate.live_to_payload`) → mutate
the payload in memory → validate → PUT the whole automation. This module is
the "mutate in memory" part — pure functions over the wire payload, no I/O.

Steps in a translated payload carry synthesized keys (``s07_condition``)
that are stable handles within one GET→PUT cycle. All functions here take
and return those keys. Order values may go non-integer mid-surgery; callers
finish with :func:`renumber_steps` (the API requires sequential 0..N).
"""

from __future__ import annotations

from typing import Any

from kizen_builder.tools.plans import PlanError

_BRANCHING_TYPES = {"condition", "goal"}
# Closers stay on the loop or branch group they close: moved under a new
# step, the server rejects them.
_CLOSER_TYPES = {"next_or_finish", "merge_branches"}


def find_step(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the step with the given synthesized key, or raise PlanError."""
    for s in payload["steps"]:
        if s["key"] == key:
            return s
    known = ", ".join(s["key"] for s in payload["steps"]) or "(none)"
    raise PlanError(f"no step with key '{key}'. Steps: {known}")


def children_of(
    payload: dict[str, Any], key: str | None, branch: str | None = None
) -> list[dict[str, Any]]:
    """Steps whose parent_key is ``key`` (None = roots), optionally one branch."""
    kids = [s for s in payload["steps"] if s.get("parent_key") == key]
    if branch is not None:
        kids = [s for s in kids if (s.get("parent_yes_no") or "") == branch]
    return sorted(kids, key=lambda s: s.get("order") or 0)


def subtree_keys(payload: dict[str, Any], root_key: str) -> list[str]:
    """``root_key`` plus every descendant key, breadth-first."""
    out = [root_key]
    frontier = {root_key}
    while frontier:
        frontier = {
            s["key"]
            for s in payload["steps"]
            if s.get("parent_key") in frontier and s["key"] not in out
        }
        out.extend(sorted(frontier))
    return out


def renumber_steps(payload: dict[str, Any]) -> None:
    """Reassign sequential 0..N orders, preserving the current relative order."""
    for i, s in enumerate(sorted(payload["steps"], key=lambda x: x.get("order") or 0)):
        s["order"] = i


def synthesize_key(payload: dict[str, Any], step_type: str) -> str:
    """A fresh key in the translator's ``sNN_<type>`` style, unique in payload."""
    taken = {s["key"] for s in payload["steps"]}
    n = len(payload["steps"])
    while f"s{n:02d}_{step_type}" in taken:
        n += 1
    return f"s{n:02d}_{step_type}"


def _gotos_pointing_at(payload: dict[str, Any], keys: set[str]) -> list[dict[str, Any]]:
    out = []
    for s in payload["steps"]:
        g = s.get("action_go_to_automation_step")
        if g and g.get("step_key") in keys:
            out.append(s)
    return out


def remove_step(
    payload: dict[str, Any], key: str, cascade: bool = False
) -> dict[str, Any]:
    """Splice a step out of the graph (or, with cascade, its whole subtree).

    Without cascade: children adopt the removed step's parent (inheriting
    its branch position), and go_to steps targeting it are retargeted to the
    same parent. Refuses to splice a condition/goal that still has children
    — their yes/no positions have no meaning under a different parent.

    Returns a report: removed keys, (child, new_parent) re-parentings, and
    retargeted go_to keys.
    """
    dead = find_step(payload, key)
    kids = children_of(payload, key)

    if dead["type"] == "next_or_finish":
        raise PlanError(
            f"'{key}' closes loop '{dead.get('parent_key')}' and can't be "
            "removed on its own. Remove the loop instead."
        )
    if cascade:
        doomed = set(subtree_keys(payload, key))
    else:
        if dead["type"] in _BRANCHING_TYPES and kids:
            raise PlanError(
                f"'{key}' is a {dead['type']} with children "
                f"({', '.join(k['key'] for k in kids)}); removing it would "
                "orphan its yes/no branches. Remove or re-parent them first, "
                "or pass cascade=True to delete the whole subtree."
            )
        if dead["type"] == "loop" and kids:
            raise PlanError(
                f"'{key}' is a loop with steps under it "
                f"({', '.join(k['key'] for k in kids)}); removing it alone would "
                "leave its next_or_finish without a loop. Remove its body first, "
                "or pass cascade=True to delete the loop with its body and "
                "everything after it."
            )
        doomed = {key}

    new_parent = dead.get("parent_key")
    gotos = _gotos_pointing_at(payload, doomed)
    live_gotos = [g for g in gotos if g["key"] not in doomed]
    if live_gotos:
        parent_step = find_step(payload, new_parent) if new_parent else None
        # A go_to may not target an initialize_variable step (server 400),
        # and obviously can't target nothing.
        if parent_step is None or parent_step["type"] == "initialize_variable":
            reason = (
                "there is no parent to retarget to"
                if parent_step is None
                else f"its parent '{new_parent}' is an initialize_variable "
                "step, which a go_to may not target (server rule)"
            )
            raise PlanError(
                "go_to step(s) "
                f"{', '.join(g['key'] for g in live_gotos)} target the removed "
                f"step(s), and {reason}. Edit or remove those go_to steps "
                "first."
            )

    report: dict[str, Any] = {
        "removed": sorted(doomed),
        "reparented": [],
        "retargeted_gotos": [],
    }
    if not cascade:
        for child in kids:
            child["parent_key"] = new_parent
            if dead.get("parent_yes_no") and not child.get("parent_yes_no"):
                child["parent_yes_no"] = dead["parent_yes_no"]
                child["parent_condition"] = dead["parent_condition"]
            report["reparented"].append((child["key"], new_parent))
    for g in live_gotos:
        g["action_go_to_automation_step"]["step_key"] = new_parent
        report["retargeted_gotos"].append(g["key"])

    payload["steps"] = [s for s in payload["steps"] if s["key"] not in doomed]
    renumber_steps(payload)
    return report


def insert_step(
    payload: dict[str, Any],
    new_step: dict[str, Any],
    parent_key: str | None = None,
    branch: str = "",
    adopt_children: bool = True,
) -> dict[str, Any]:
    """Link a fully-built wire step into the graph.

    ``new_step`` must already carry ``key``, ``type``, and its config block
    (envelope linkage/order fields are overwritten here). With
    ``adopt_children`` (the default) the parent's existing children in the
    target branch move under the new step — i.e. insertion into the chain;
    without it the new step is a leaf. A ``next_or_finish`` or
    ``merge_branches`` child is never adopted, so under a loop the new step
    heads the body. ``parent_key=None`` inserts a new root above the current
    one.
    """
    if any(s["key"] == new_step["key"] for s in payload["steps"]):
        raise PlanError(f"step key '{new_step['key']}' already exists")
    parent = find_step(payload, parent_key) if parent_key else None
    if branch:
        if branch not in ("yes", "no"):
            raise PlanError(f"branch must be 'yes' or 'no', got '{branch}'")
        if parent is None or parent["type"] not in _BRANCHING_TYPES:
            raise PlanError("branch placement requires a condition or goal parent step")

    report: dict[str, Any] = {"key": new_step["key"], "adopted": []}
    if adopt_children:
        for child in children_of(payload, parent_key, branch if parent else None):
            if child["type"] in _CLOSER_TYPES:
                continue
            child["parent_key"] = new_step["key"]
            child["parent_yes_no"] = ""
            child["parent_condition"] = ""
            report["adopted"].append(child["key"])

    new_step["parent_key"] = parent_key
    new_step["parent_yes_no"] = branch
    new_step["parent_condition"] = branch
    new_step["order"] = (parent["order"] + 0.5) if parent else -0.5
    payload["steps"].append(new_step)
    renumber_steps(payload)
    return report


def edit_step(
    payload: dict[str, Any], key: str, patch: dict[str, Any]
) -> dict[str, Any]:
    """Shallow-merge ``patch`` over one step's wire dict.

    Top-level keys replace wholesale — including the ``action_*`` /
    ``step_*`` config block, so a config edit must send the complete block
    (start from the current wire step and modify). ``key``, ``type``, and
    ``id`` are immutable: other steps reference the key, a type change
    invalidates the config block (remove + add instead), and changing ``id``
    would reassign this step's execution history to whatever step the patch's
    id was copied from.

    Returns {field: (before, after)} for the changed keys.
    """
    step = find_step(payload, key)
    for frozen in ("key", "type", "id"):
        if frozen in patch and patch[frozen] != step.get(frozen):
            raise PlanError(
                f"'{frozen}' cannot be changed on an existing step "
                "(remove the step and add a new one instead)"
            )
    changes: dict[str, Any] = {}
    for k, v in patch.items():
        if step.get(k) != v:
            changes[k] = (step.get(k), v)
            step[k] = v
    if "parent_yes_no" in patch and "parent_condition" not in patch:
        step["parent_condition"] = step["parent_yes_no"]
    return changes

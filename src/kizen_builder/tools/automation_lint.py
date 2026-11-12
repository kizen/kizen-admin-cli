"""Check an automation spec with no live environment.

`lint_spec` reports everything the CLI can know from the spec file alone: the
shape of each config block, the step graph, the variables steps name, and the
keys a chosen option is normally written with. What needs the environment
(does this field exist, is this activity type real) stays with `--dry-run`.

Each finding names the step and the dotted path inside it, so a caller can
fix one key without re-reading the spec.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from pydantic import ValidationError

from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools import step_catalog
from kizen_builder.tools.planners.automations import (
    _fan_out_warnings,
    _unwrap_variable_name,
    triggers_with_manual,
)
from kizen_builder.translate import validate_payload

Finding = dict[str, Any]

# Variables only these types hold: `schedule_activity.target_variable` takes
# the new activity's id (see `kizen docs show automation`).
_ID_VARIABLE_TYPES = frozenset({"string", "uuid"})


def lint_spec(spec: Any) -> dict[str, Any]:
    """Lint one `AutomationDef` spec. Returns ``{"ok", "errors", "warnings"}``;
    ``ok`` is false when there is at least one error."""
    errors: list[Finding] = []
    warnings: list[Finding] = []
    if not isinstance(spec, dict):
        errors.append(
            {
                "path": "",
                "message": "the spec must be a JSON object (an AutomationDef)",
                "hint": "kizen docs show automation",
            }
        )
        return _result(errors, warnings)

    steps = [s for s in spec.get("steps") or [] if isinstance(s, dict)]
    errors.extend(_unwired_types(spec))

    auto: AutomationDef | None = None
    try:
        auto = AutomationDef.model_validate(spec)
    except ValidationError as e:
        model_errors = [_from_pydantic(err, spec) for err in e.errors()]
    else:
        model_errors = []

    graph = validate_payload(_graph_skeleton(spec, steps, auto))
    for finding in model_errors:
        if finding.pop("_unknown_type", False):
            continue  # `_unwired_types` already said it, with the wired list
        # The model's own graph check stops at its first problem; the graph
        # pass below lists them all.
        whole_spec = finding.pop("_whole_spec", False)
        if whole_spec and graph and "target_object" not in finding["message"]:
            continue
        errors.append(finding)
    errors.extend({"path": "steps", "message": problem} for problem in graph)

    declared = _declared_variables(steps)
    for step in steps:
        errors.extend(_variable_errors(step, declared))
        warnings.extend(_selector_warnings(step))
    warnings.extend(_order_warnings(steps))
    if auto is not None:
        warnings.extend(
            {"path": "steps", "message": w} for w in _fan_out_warnings(auto)
        )
    return _result(errors, warnings)


def _result(errors: list[Finding], warnings: list[Finding]) -> dict[str, Any]:
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Block shape: the spec models
# ---------------------------------------------------------------------------

_QUOTED = re.compile(r"'([^']*)'")


def _type_hint(kind: str, type_name: Any) -> str | None:
    types = step_catalog.STEP_TYPES if kind == "step" else step_catalog.TRIGGER_TYPES
    if type_name not in types:
        return f"kizen automations {kind}-types list"
    return f"kizen automations {kind}-types show {type_name}"


def _from_pydantic(err: Any, spec: dict[str, Any]) -> Finding:
    loc = tuple(err["loc"])
    finding: Finding = {}
    inner = loc
    for section, kind, label in (("steps", "step", "key"), ("triggers", "trigger", "")):
        if len(loc) >= 2 and loc[0] == section and isinstance(loc[1], int):
            items = spec.get(section)
            item = items[loc[1]] if isinstance(items, list) else None
            item = item if isinstance(item, dict) else {}
            finding[kind] = item.get(label) if label else item.get("trigger_type")
            finding["index"] = loc[1]
            inner = loc[2:]
            hint = _type_hint(kind, item.get(f"{kind}_type"))
            if hint:
                finding["hint"] = hint
            break
    finding["path"] = ".".join(str(p) for p in inner)
    message = str(err["msg"]).removeprefix("Value error, ")
    finding["_whole_spec"] = not loc
    finding["_unknown_type"] = message.startswith(
        ("unknown step_type", "unknown trigger_type")
    )

    kind = err["type"]
    got = err.get("input")
    if kind in ("literal_error", "enum"):
        allowed = _QUOTED.findall(str((err.get("ctx") or {}).get("expected", "")))
        finding["got"] = got
        finding["allowed"] = allowed
        message = f"{got!r} is not one of: {', '.join(allowed)}"
        close = difflib.get_close_matches(str(got), allowed, n=1)
        if close:
            finding["suggestion"] = close[0]
    elif kind == "missing":
        message = "required key is missing"
    elif kind == "extra_forbidden":
        message = "unknown key"
    elif isinstance(got, (str, int, float, bool)):
        finding["got"] = got
    finding["message"] = message
    return finding


def _unwired_types(spec: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    for section, kind, wired in (
        ("steps", "step", step_catalog.STEP_TYPES),
        ("triggers", "trigger", step_catalog.TRIGGER_TYPES),
    ):
        items = spec.get(section)
        for i, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            type_name = item.get(f"{kind}_type")
            if type_name is None or type_name in wired:
                continue
            finding: Finding = {
                kind: item.get("key") if kind == "step" else type_name,
                "index": i,
                "path": f"{kind}_type",
                "message": f"{type_name!r} is not a {kind} type this CLI wires",
                "got": type_name,
                "allowed": list(wired),
                "hint": f"kizen automations {kind}-types list",
            }
            close = difflib.get_close_matches(str(type_name), wired, n=1)
            if close:
                finding["suggestion"] = close[0]
            out.append(finding)
    return out


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _graph_skeleton(
    spec: dict[str, Any], steps: list[dict[str, Any]], auto: AutomationDef | None
) -> dict[str, Any]:
    """The parts of the PUT payload `validate_payload` reads, built from the
    spec alone. Trigger orders are checked only for a spec the models accept,
    since the sent order depends on the parsed triggers."""
    wire_steps = []
    for s in steps:
        step_type = s.get("step_type")
        raw_loop = s.get("step_loop")
        loop: dict[str, Any] = raw_loop if isinstance(raw_loop, dict) else {}
        init = s.get("action_initialize_variable")
        goto = s.get("action_go_to_automation_step")
        goal = s.get("step_goal")
        wire_steps.append(
            {
                "key": s.get("key"),
                "id": s.get("id"),
                "parent_key": s.get("parent_key"),
                "parent_yes_no": s.get("parent_branch") or "",
                "type": step_type,
                "order": s.get("order") if isinstance(s.get("order"), int) else 0,
                "is_branch_group_initiator": bool(
                    s.get("is_branch_group_initiator") or step_type == "loop"
                ),
                "should_skip_execution": s.get("should_skip_execution"),
                "continue_with_branch": s.get("continue_with_branch"),
                "step_loop": {
                    "array_variable": _unwrap_variable_name(loop.get("array_variable")),
                    "item_variable": _unwrap_variable_name(loop.get("item_variable")),
                },
                "action_initialize_variable": init if isinstance(init, dict) else None,
                "action_go_to_automation_step": goto
                if isinstance(goto, dict)
                else None,
                "step_goal": goal if isinstance(goal, dict) else None,
            }
        )
    triggers = (
        [{"order": t.order or 0, "id": t.id} for t in triggers_with_manual(auto)]
        if auto is not None
        else []
    )
    return {"steps": wire_steps, "triggers": triggers}


def _order_warnings(steps: list[dict[str, Any]]) -> list[Finding]:
    raw = [s.get("order") for s in steps]
    orders = [o for o in raw if isinstance(o, int)]
    if len(orders) != len(raw):
        return []  # the model reports a missing or non-integer order
    if sorted(orders) == list(range(len(orders))):
        return []
    return [
        {
            "path": "steps",
            "message": (
                f"step orders should run 0 to {len(orders) - 1} with no gaps or "
                f"duplicates; got {sorted(orders)}"
            ),
        }
    ]


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


def _declared_variables(steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for s in steps:
        if s.get("step_type") != "initialize_variable":
            continue
        block = s.get("action_initialize_variable")
        var = block.get("variable") if isinstance(block, dict) else None
        if isinstance(var, dict) and isinstance(var.get("name"), str):
            declared.setdefault(var["name"], var)
    return declared


def _dig(node: Any, path: str) -> list[tuple[str, Any]]:
    """Values at a dotted path, with ``[]`` fanning out over a list. Returns
    ``(concrete path, value)`` pairs for the values that are present."""
    found: list[tuple[str, Any]] = [("", node)]
    for part in path.split("."):
        fan = part.endswith("[]")
        name = part.removesuffix("[]")
        nxt: list[tuple[str, Any]] = []
        for prefix, value in found:
            child = value.get(name) if isinstance(value, dict) else None
            here = f"{prefix}.{name}" if prefix else name
            if fan:
                if isinstance(child, list):
                    nxt.extend((f"{here}[{i}]", c) for i, c in enumerate(child))
            elif child is not None:
                nxt.append((here, child))
        found = nxt
    return found


# step_type -> (dotted path to a variable reference, requirement). The
# requirement is "" (any declared variable), "array", or "id".
_VARIABLE_REFS: dict[str, tuple[tuple[str, str], ...]] = {
    "schedule_activity": (
        ("target_variable", "id"),
        ("schedule.with_delay.delay_variable", ""),
        ("schedule.on_or_around_date.variable", ""),
        ("assigned_to.variable", ""),
    ),
    "search_records": (("destination_variable", "array"),),
    "update_variable": (("variable", ""), ("sources[].variable", "")),
    "initialize_variable": (("sources[].variable", ""),),
    "call_llm": (("destinations[].variable", ""),),
    "file_content_extraction": (("destinations[].variable", ""),),
    "audio_transcription": (("destinations[].variable", ""),),
    "code_step": (("inputs[].variable_name", ""), ("outputs[].variable_name", "")),
    "plugin_code_step": (
        ("inputs[].variable_name", ""),
        ("outputs[].variable_name", ""),
    ),
}


def _variable_errors(
    step: dict[str, Any], declared: dict[str, dict[str, Any]]
) -> list[Finding]:
    step_type = step.get("step_type")
    if not isinstance(step_type, str) or step_type not in _VARIABLE_REFS:
        return []
    refs = _VARIABLE_REFS[step_type]
    block_field = step_catalog._STEP.block_field(step_type)
    block = step.get(block_field) if block_field else None
    out: list[Finding] = []
    for path, requirement in refs:
        for where, value in _dig(block, path):
            name = _unwrap_variable_name(value)
            if not name:
                continue
            base: Finding = {
                "step": step.get("key"),
                "path": f"{block_field}.{where}",
                "got": name,
            }
            var = declared.get(name)
            if var is None:
                out.append(
                    {
                        **base,
                        "message": (
                            f"variable {name!r} is not declared by an "
                            "initialize_variable step"
                        ),
                        "allowed": sorted(declared),
                        "hint": "kizen automations step-types show initialize_variable",
                    }
                )
            elif requirement == "array" and not var.get("is_array"):
                out.append(
                    {**base, "message": f"variable {name!r} must be an array variable"}
                )
            elif requirement == "id" and (
                var.get("is_array") or var.get("data_type") not in _ID_VARIABLE_TYPES
            ):
                out.append(
                    {
                        **base,
                        "message": (
                            f"variable {name!r} must be a single string or uuid "
                            f"variable; it is declared as {var.get('data_type')!r}"
                        ),
                    }
                )
    return out


# ---------------------------------------------------------------------------
# Selector companions
# ---------------------------------------------------------------------------


def _selector_warnings(step: dict[str, Any]) -> list[Finding]:
    step_type = step.get("step_type")
    if not isinstance(step_type, str) or step_type not in step_catalog.STEP_TYPES:
        return []
    block_field = step_catalog._STEP.block_field(step_type)
    block = step.get(block_field) if block_field else None
    if not isinstance(block, dict):
        return []
    return [
        {
            "step": step.get("key"),
            "path": f"{block_field}.{f['path']}",
            "message": f["message"],
            "hint": f"kizen automations step-types show {step_type}",
        }
        for f in step_catalog.selector_findings(step_type, block)
    ]

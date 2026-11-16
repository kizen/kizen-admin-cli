"""Check an automation spec before planning it, with no live call.

`check_automation` runs first in `plan_create_automation` and
`plan_update_automation`. It reports everything the spec file alone can
show, all at once, as one :class:`SpecProblems`: the shape of each block,
step and trigger types the CLI doesn't wire, and keys nothing reads. What
needs the environment (does this field exist) stays with the planner.
"""

from __future__ import annotations

import difflib
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools import step_catalog
from kizen_builder.tools.findings import Finding, SpecProblems
from kizen_builder.tools.planners.automations import (
    _STEP_BUILDERS,
    NO_MOVE_STAGE_STEP,
    _block_field_for,
)


@dataclass
class CheckedSpec:
    """A spec that passed: the parsed automation and what to warn about."""

    auto: AutomationDef
    warnings: list[Finding] = field(default_factory=list)


def check_automation(spec: Any) -> CheckedSpec:
    """Parse ``spec`` into an `AutomationDef`, or raise :class:`SpecProblems`
    listing every problem found."""
    if not isinstance(spec, dict):
        raise SpecProblems(
            [
                Finding(
                    "the spec must be a JSON object (an AutomationDef)",
                    hint="kizen automations step-types skeleton",
                )
            ]
        )
    raw_steps = spec.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []

    errors: list[Finding] = []
    errors.extend(_unwired_types(spec))
    auto: AutomationDef | None = None
    try:
        auto = AutomationDef.model_validate(spec)
    except ValidationError as e:
        errors.extend(
            f
            for f in (_from_pydantic(err, spec) for err in e.errors())
            if f is not None
        )
    for i, step in enumerate(steps):
        errors.extend(_unread_block_keys(i, step))

    errors = _unique(errors)
    if errors or auto is None:
        raise SpecProblems(errors)
    return CheckedSpec(auto)


def _unique(findings: list[Finding]) -> list[Finding]:
    """Each problem once: a union field can report the same error per
    member."""
    seen: set[tuple[Any, ...]] = set()
    out = []
    for f in findings:
        key = (f.step, f.index, f.path, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _type_hint(type_name: Any) -> str:
    known = (*step_catalog.STEP_TYPES, *step_catalog.TRIGGER_TYPES)
    if type_name in known:
        return f"kizen automations step-types show {type_name}"
    return "kizen automations step-types list"


def _where(spec: dict[str, Any], loc: tuple[Any, ...]) -> tuple[dict[str, Any], str]:
    """The step (or trigger) a pydantic ``loc`` falls in, as Finding fields,
    and the rest of the path."""
    for section in ("steps", "triggers"):
        if len(loc) >= 2 and loc[0] == section and isinstance(loc[1], int):
            items = spec.get(section)
            item = items[loc[1]] if isinstance(items, list) else None
            item = item if isinstance(item, dict) else {}
            rest = ".".join(str(p) for p in loc[2:])
            if section == "triggers":
                path = f"triggers[{loc[1]}]" + (f".{rest}" if rest else "")
                return {"hint": _type_hint(item.get("trigger_type"))}, path
            return {
                "step": item.get("key"),
                "index": loc[1],
                "hint": _type_hint(item.get("step_type")),
            }, rest
    return {}, ".".join(str(p) for p in loc)


_QUOTED = re.compile(r"'([^']*)'")

# Keys agents write that difflib can't place.
_MEANT = {"object": "target_object", "trigger_type": "type"}
_VARIABLES_HINT = (
    "unknown key; declare each variable with an initialize_variable step at "
    "the front of `steps`"
)


def _from_pydantic(err: Any, spec: dict[str, Any]) -> Finding | None:
    loc = tuple(err["loc"])
    message = str(err["msg"]).removeprefix("Value error, ")
    if message.startswith(("unknown step_type", "unknown trigger_type")):
        return None  # `_unwired_types` says it, with the wired list
    where, path = _where(spec, loc)
    kind = err["type"]
    got = err.get("input")
    extra: dict[str, Any] = {}
    if kind in ("literal_error", "enum"):
        allowed = _QUOTED.findall(str((err.get("ctx") or {}).get("expected", "")))
        message = f"{got!r} is not one of: {', '.join(allowed)}"
        extra = {"got": got, "allowed": allowed}
        close = difflib.get_close_matches(str(got), allowed, n=1)
        if close:
            extra["suggestion"] = close[0]
    elif kind == "missing":
        message = "required key is missing"
    elif kind == "extra_forbidden":
        key = str(loc[-1])
        allowed = _keys_at(loc[:-1])
        extra = {"got": key, "allowed": allowed or None}
        if loc == ("variables",):
            message = _VARIABLES_HINT
            where["hint"] = "kizen automations step-types show initialize_variable"
        else:
            message = "unknown key"
            meant = _MEANT.get(key)
            close = (
                [meant]
                if meant in allowed
                else difflib.get_close_matches(key, allowed, n=1)
            )
            if close:
                extra["suggestion"] = close[0]
    elif isinstance(got, (str, int, float, bool)):
        extra = {"got": got}
    return Finding(message, path=path, **where, **extra)


def _model_in(annotation: Any) -> type[BaseModel] | None:
    """The one model a field holds, through `X | None`, `list[X]` and the
    like; None when it holds none, or more than one."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = typing.get_origin(annotation)
    if origin is None:
        return None
    found = {
        m for arg in typing.get_args(annotation) if (m := _model_in(arg)) is not None
    }
    if origin in (typing.Union, types.UnionType, list, tuple) and len(found) == 1:
        return found.pop()
    return None


def _keys_at(loc: tuple[Any, ...]) -> list[str]:
    """The keys the model at ``loc`` takes, or [] when it can't be told."""
    model: type[BaseModel] | None = AutomationDef
    for part in loc:
        if isinstance(part, int):
            continue
        info = model.model_fields.get(str(part)) if model else None
        if info is None:
            return []
        model = _model_in(info.annotation)
    return sorted(model.model_fields) if model else []


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
            if not isinstance(type_name, str) or type_name in wired:
                continue
            names = [*wired, *step_catalog.ALIASES] if kind == "step" else wired
            meant = [
                step_catalog.ALIASES.get(m, (m,))[0]
                for m in difflib.get_close_matches(type_name, names, n=1)
            ]
            where: dict[str, Any] = (
                {"step": item.get("key"), "index": i, "path": "step_type"}
                if kind == "step"
                else {"path": f"triggers[{i}].trigger_type"}
            )
            message = f"{type_name!r} is not a {kind} type this CLI wires"
            if type_name == "update_pipeline_status":
                message, meant = NO_MOVE_STAGE_STEP, ["change_field_value"]
            out.append(
                Finding(
                    message,
                    **where,
                    got=type_name,
                    allowed=list(wired),
                    suggestion=meant[0] if meant else None,
                    hint="kizen automations step-types list",
                )
            )
    return out


def _unread_block_keys(index: int, step: Any) -> list[Finding]:
    """A block key the step's builder doesn't read would be dropped on
    write. The block models carry no aliases, so the keys given are the
    keys the planner checks."""
    if not isinstance(step, dict) or step.get("step_type") not in _STEP_BUILDERS:
        return []
    step_type = step["step_type"]
    honoured = _STEP_BUILDERS[step_type].honoured_keys  # type: ignore[attr-defined]
    block_field = _block_field_for(step_type)
    block = step.get(block_field)
    if honoured is None or not isinstance(block, dict):
        return []
    allowed = sorted(honoured)
    return [
        Finding(
            "unknown key; the planner doesn't read it, so it would be dropped",
            path=f"{block_field}.{key}",
            step=step.get("key"),
            index=index,
            got=key,
            allowed=allowed,
            suggestion=next(iter(difflib.get_close_matches(key, allowed, n=1)), None),
            hint=_type_hint(step_type),
        )
        for key in sorted(set(block) - honoured)
    ]

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
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ValidationError

from kizen_builder.models.spec import AutomationDef, AutomationStepDef
from kizen_builder.models.spec.automations_actions_data import ArrayAggregationMode
from kizen_builder.models.spec.automations_shared import UNION_TAGS
from kizen_builder.tools import step_catalog
from kizen_builder.tools.findings import Finding, SpecProblems
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    NO_MOVE_STAGE_STEP,
    _block_field_for,
    _fan_out_warnings,
    _unwrap_variable_name,
    triggers_with_manual,
)
from kizen_builder.translate import trigger_order_problem
from kizen_builder.utils import slugify


@dataclass
class CheckedSpec:
    """A spec that passed: the parsed automation, what to warn about, and a
    line per value it defaulted."""

    auto: AutomationDef
    warnings: list[Finding] = field(default_factory=list)
    defaults: list[str] = field(default_factory=list)


def check_automation(spec: Any) -> CheckedSpec:
    """Parse ``spec`` into an `AutomationDef`, or raise :class:`SpecProblems`
    listing every problem found. An `AutomationDef` passes as it is, with
    the warnings the parsed model alone can give."""
    if isinstance(spec, AutomationDef):
        return CheckedSpec(spec, _fan_out(spec))
    if not isinstance(spec, dict):
        raise SpecProblems(
            [
                Finding(
                    "the spec must be a JSON object (an AutomationDef)",
                    hint="kizen automations step-types skeleton",
                )
            ]
        )
    spec, defaults = with_defaults(spec)
    raw_steps = spec.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []

    errors: list[Finding] = []
    warnings: list[Finding] = []
    errors.extend(_unwired_types(spec))
    auto: AutomationDef | None = None
    try:
        # Graph problems come from the built payload, all at once.
        auto = AutomationDef.model_validate(spec, context={"graph": False})
    except ValidationError as e:
        errors.extend(
            f
            for f in (_from_pydantic(err, spec) for err in e.errors())
            if f is not None
        )
    top = {k: v for k, v in spec.items() if k != "steps"}
    errors.extend([*_placeholders(top, ""), *_elided(top, "")])
    declared = declared_variables(steps)
    for i, step in enumerate(steps):
        step_errors, step_warnings = check_step(
            step, declared, index=i, is_global=spec.get("type") == "global"
        )
        errors.extend(step_errors)
        warnings.extend(step_warnings)
    warnings.extend(_order_warnings(steps))
    if auto is not None:
        problem = trigger_order_problem([t.order for t in triggers_with_manual(auto)])
        if problem:
            errors.append(Finding(problem, path="triggers"))
        warnings.extend(_fan_out(auto))

    errors = _unique(errors)
    if errors or auto is None:
        raise SpecProblems(errors, warnings)
    return CheckedSpec(auto, warnings, defaults)


def with_defaults(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``spec`` with a left-out ``api_name`` taken from ``name`` and each
    left-out step or trigger ``order`` from its position. A value given
    always wins. Returns the spec and a line per default. `step-types`
    entries carry no `order` and rely on this."""
    spec = dict(spec)
    defaults: list[str] = []
    if "api_name" not in spec and isinstance(spec.get("name"), str):
        spec["api_name"] = slugify(spec["name"])[:100]
        defaults.append(f"api_name: '{spec['api_name']}' (from name)")
    for section, name_key in (("triggers", "trigger_type"), ("steps", "key")):
        items = spec.get(section)
        if not isinstance(items, list):
            continue
        if section == "triggers" and not any(
            isinstance(t, dict) and t.get("trigger_type") == "manual" for t in items
        ):
            continue  # `triggers_with_manual` numbers them after the manual
        spec[section] = [
            {**item, "order": i}
            if isinstance(item, dict) and "order" not in item
            else item
            for i, item in enumerate(items)
        ]
        positions = [
            f"'{item.get(name_key, f'#{i}')}' {i}"
            for i, item in enumerate(items)
            if isinstance(item, dict) and "order" not in item
        ]
        if positions:
            defaults.append(
                f"{section[:-1]} order from position: {', '.join(positions)}"
            )
    return spec, defaults


def check_step(
    step: Any,
    declared: dict[str, dict[str, Any]],
    *,
    index: int | None = None,
    is_global: bool = False,
) -> tuple[list[Finding], list[Finding]]:
    """The checks one step gets on its own: block keys nothing reads,
    placeholders left in, variables nothing declares, an array variable
    with no aggregation mode, an entity variable with no object, a field
    change in a global automation (a warning for a step that has an `id`),
    and selector options missing the keys they're written with. Returns
    ``(errors, warnings)``.
    ``declared`` maps each variable name to its declaration."""
    if not isinstance(step, dict):
        return [], []
    errors = [
        *_unread_block_keys(index, step),
        *_placeholders(step, "", _type_hint(step.get("step_type")), step, index),
        *_elided(step, "", step.get("key"), index),
        *_variable_errors(index, step, declared),
        *_array_mode_errors(index, step, declared),
        *_entity_subtype_errors(index, step),
    ]
    warnings = _selector_warnings(index, step)
    own_record = _own_record_errors(index, step, is_global)
    # A step with an id already exists, so it doesn't block an unrelated
    # update; a new one does.
    (warnings if step.get("id") else errors).extend(own_record)
    return errors, warnings


def block_rule_errors(step: dict[str, Any]) -> list[Finding]:
    """The cross-field rules on a step's config model (a static source's
    source_subtype, a date offset's time and day count, is_advanced for
    more than one LLM destination), for `steps add`/`edit`. Only those:
    an edit's block may be a `steps get` shape the rest of the model
    refuses, such as an update_variable's variable given by name."""
    cfg_key = _block_field_for(str(step.get("step_type")))
    info = AutomationStepDef.model_fields.get(cfg_key)
    model = _model_in(info.annotation) if info else None
    if model is None or not isinstance(step.get(cfg_key), dict):
        return []
    try:
        model.model_validate(step[cfg_key])
    except ValidationError as e:
        spec = {"steps": [step]}
        return [
            replace(f, index=None)
            for err in e.errors()
            if err["type"] == "value_error"
            and (
                f := _from_pydantic(
                    {**err, "loc": ("steps", 0, cfg_key, *err["loc"])}, spec
                )
            )
        ]
    return []


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
_MEANT = {
    "object": "target_object",
    "trigger_type": "type",
    "value": "specific_field_value",
}
_VARIABLES_HINT = (
    "unknown key; declare each variable with an initialize_variable step at "
    "the front of `steps`"
)


_BLOCK_FIELDS = frozenset(_block_field_for(t) for t in _STEP_BUILDERS)


def _suggest_key(key: str, allowed: list[str]) -> str | None:
    """The key in ``allowed`` that ``key`` most likely meant. Typos of a
    common wrong name (`objct`) resolve through `_MEANT`."""
    meant = [
        _MEANT.get(m, m)
        for m in difflib.get_close_matches(key, [*allowed, *_MEANT], n=1)
    ]
    return meant[0] if meant and meant[0] in allowed else None


def _from_pydantic(err: Any, spec: dict[str, Any]) -> Finding | None:
    loc = tuple(p for p in err["loc"] if p not in UNION_TAGS)
    message = str(err["msg"]).removeprefix("Value error, ")
    if message.startswith(("unknown step_type", "unknown trigger_type")):
        return None  # `_unwired_types` says it, with the wired list
    where, path = _where(spec, loc)
    kind = err["type"]
    got = err.get("input")
    extra: dict[str, Any] = {}
    if kind in ("literal_error", "enum"):
        allowed = _QUOTED.findall(str((err.get("ctx") or {}).get("expected", "")))
        shown = ", ".join(a or '""' for a in allowed)
        message = f"{got!r} is not one of: {shown}"
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
            extra["suggestion"] = _suggest_key(key, allowed)
            if extra["suggestion"] in _BLOCK_FIELDS and loc[0] == "steps":
                # Another type's block (`action_delay` → `action_code_step`);
                # the step's own block is what was meant.
                step_type = spec["steps"][loc[1]].get("step_type")
                if step_type in _STEP_BUILDERS:
                    extra["suggestion"] = _block_field_for(step_type)
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
            meant = step_catalog.suggest(type_name, wired)
            where: dict[str, Any] = (
                {"step": item.get("key"), "index": i, "path": "step_type"}
                if kind == "step"
                else {"path": f"triggers[{i}].trigger_type"}
            )
            message = f"{type_name!r} is not a {kind} type this CLI wires"
            if len(meant) > 1:
                could_be = " or ".join(repr(m) for m in meant)
                message += f"; it could be {could_be}"
                meant = []
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


def _unread_block_keys(index: int | None, step: dict[str, Any]) -> list[Finding]:
    """A block key the step's builder doesn't read would be dropped on
    write. The block models carry no aliases, so the keys given are the
    keys the planner checks."""
    if step.get("step_type") not in _STEP_BUILDERS:
        return []
    step_type = step["step_type"]
    honoured = _STEP_BUILDERS[step_type].honoured_keys  # type: ignore[attr-defined]
    block_field = _block_field_for(step_type)
    block = step.get(block_field)
    if honoured is None or not isinstance(block, dict):
        return []
    allowed = sorted(honoured)
    # The builder also reads keys of other steps' dialects (`value_type`);
    # suggest only the ones this step's block documents.
    own = [k for k in _keys_at(("steps", block_field)) if k in honoured] or allowed
    return [
        Finding(
            "unknown key; the planner doesn't read it, so it would be dropped",
            path=f"{block_field}.{key}",
            step=step.get("key"),
            index=index,
            got=key,
            allowed=allowed,
            suggestion=_suggest_key(key, own),
            hint=_type_hint(step_type),
        )
        for key in sorted(set(block) - honoured)
    ]


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------

# A value made only of `<...>` slots, as `step-types` prints them:
# `<activity api_name>`, `<object>.<date field>`. A closing tag or an email
# address isn't one, and keys that hold HTML are skipped. The root folder
# really is named `<business_root>`.
_PLACEHOLDER = re.compile(r"[\w.]*(?:<[^<>/@]+>[\w.]*)+")
_REAL_NAMES = frozenset({"<business_root>"})


# What `automations get --json` prints in place of a long value.
ELIDED = "<{n:,} chars elided; {full}>"
_ELIDED = re.compile(r"<[\d,]+ chars elided; ([^<>]+)>")


def _is_placeholder(value: str) -> bool:
    return (
        value not in _REAL_NAMES
        and bool(_PLACEHOLDER.fullmatch(value))
        and not _ELIDED.fullmatch(value)
    )


def _elided(
    node: Any, path: str, step: str | None = None, index: int | None = None
) -> list[Finding]:
    """A value copied from `automations get --json` that still holds its
    stand-in for a long string, which a write would send as the value."""
    if isinstance(node, dict):
        return [
            f
            for k, v in node.items()
            for f in _elided(v, f"{path}.{k}" if path else str(k), step, index)
        ]
    if isinstance(node, list):
        return [
            f
            for i, v in enumerate(node)
            for f in _elided(v, f"{path}[{i}]", step, index)
        ]
    match = _ELIDED.fullmatch(node) if isinstance(node, str) else None
    if match is None:
        return []
    return [
        Finding(
            "this is the stand-in `automations get --json` prints for a long "
            f"value; fetch the full value with `{match[1]}` (or `kizen "
            "automations show <api_name> --json`)",
            path=path,
            step=step,
            index=index,
        )
    ]


def _placeholders(
    node: Any,
    path: str,
    hint: str | None = None,
    step: dict[str, Any] | None = None,
    index: int | None = None,
) -> list[Finding]:
    if isinstance(node, dict):
        return [
            f
            for k, v in node.items()
            if "html" not in str(k)
            for f in _placeholders(
                v, f"{path}.{k}" if path else str(k), hint, step, index
            )
        ]
    if isinstance(node, list):
        return [
            f
            for i, v in enumerate(node)
            for f in _placeholders(v, f"{path}[{i}]", hint, step, index)
        ]
    if not isinstance(node, str) or not _is_placeholder(node):
        return []
    step_type = (step or {}).get("step_type")
    block_path = re.sub(r"\[\d+\]", "", path).partition(".")[2]
    lookup = (
        step_catalog.lookup_command(step_type, block_path)
        if isinstance(step_type, str)
        else None
    )
    return [
        Finding(
            "replace the placeholder with a real value",
            path=path,
            step=(step or {}).get("key") if step is not None else None,
            index=index,
            got=node,
            hint=lookup or hint,
        )
    ]


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

# Variables only these types hold: `schedule_activity.target_variable` takes
# the new activity's id, and a goal's Activity Logged trigger waits for the
# activity whose id is in `scheduled_activity_variable` (see `kizen
# automations step-types show schedule_activity` and `show goal`).
_ID_VARIABLE_TYPES = frozenset({"string", "uuid"})

# step_type -> (dotted path to a variable reference, requirement). The
# requirement is "" (any declared variable), "array", or "id".
_VARIABLE_REFS: dict[str, tuple[tuple[str, str], ...]] = {
    "schedule_activity": (
        ("target_variable", "id"),
        ("schedule.with_delay.delay_variable", ""),
        ("schedule.on_or_around_date.variable", ""),
        ("assigned_to.variable", ""),
    ),
    "goal": (("triggers[].trigger_activity_logged.scheduled_activity_variable", "id"),),
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


def declared_variables(steps: list[Any]) -> dict[str, dict[str, Any]]:
    """Each variable an `initialize_variable` step declares, by name. Reads
    spec steps (`step_type`) and wire steps (`type`) alike."""
    declared: dict[str, dict[str, Any]] = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        if (s.get("step_type") or s.get("type")) != "initialize_variable":
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


def _variable_errors(
    index: int | None, step: dict[str, Any], declared: dict[str, dict[str, Any]]
) -> list[Finding]:
    step_type = step.get("step_type")
    if not isinstance(step_type, str) or step_type not in _VARIABLE_REFS:
        return []
    block_field = _block_field_for(step_type)
    block = step.get(block_field)
    out: list[Finding] = []
    for path, requirement in _VARIABLE_REFS[step_type]:
        for where, value in _dig(block, path):
            name = _unwrap_variable_name(value)
            if not name or _is_placeholder(name):
                continue
            base: dict[str, Any] = {
                "step": step.get("key"),
                "index": index,
                "path": f"{block_field}.{where}",
                "got": name,
            }
            var = declared.get(name)
            if var is None:
                out.append(
                    Finding(
                        f"variable {name!r} is not declared by an "
                        "initialize_variable step",
                        **base,
                        allowed=sorted(declared),
                        suggestion=next(
                            iter(difflib.get_close_matches(name, declared, n=1)),
                            None,
                        ),
                        hint="kizen automations step-types show initialize_variable",
                    )
                )
            elif requirement == "array" and not var.get("is_array"):
                out.append(
                    Finding(f"variable {name!r} must be an array variable", **base)
                )
            elif requirement == "id" and (
                var.get("is_array") or var.get("data_type") not in _ID_VARIABLE_TYPES
            ):
                out.append(
                    Finding(
                        f"variable {name!r} must be a single string or uuid "
                        f"variable; it is declared as {var.get('data_type')!r}",
                        **base,
                    )
                )
    return out


def _array_mode_errors(
    index: int | None, step: dict[str, Any], declared: dict[str, dict[str, Any]]
) -> list[Finding]:
    """A variable step on an array variable with no
    ``array_aggregation_mode``: the server 400s it (confirmed live
    2026-11-18 for both steps)."""
    step_type = step.get("step_type")
    if step_type not in ("initialize_variable", "update_variable"):
        return []
    block_field = _block_field_for(step_type)
    block = step.get(block_field)
    if not isinstance(block, dict) or block.get("array_aggregation_mode") is not None:
        return []
    var = block.get("variable")
    name = _unwrap_variable_name(var)
    own = var if isinstance(var, dict) else {}
    # update_variable's own copy may leave is_array out; the declaration has it.
    if step_type == "update_variable" and name in declared:
        own = declared[name]
    if not own.get("is_array"):
        return []
    return [
        Finding(
            f"variable {name!r} is an array, so {step_type} needs "
            "array_aggregation_mode",
            step=step.get("key"),
            index=index,
            path=f"{block_field}.array_aggregation_mode",
            allowed=list(typing.get_args(ArrayAggregationMode)),
            hint=f"kizen automations step-types show {step_type}",
        )
    ]


def _own_record_errors(
    index: int | None, step: dict[str, Any], is_global: bool
) -> list[Finding]:
    """A change_field_value writes the automation's own record, which a
    global automation doesn't have: its runs never finish (seen live
    2026-11-17)."""
    if not is_global or step.get("step_type") != "change_field_value":
        return []
    return [
        Finding(
            "change_field_value writes the automation's own record, and a global "
            "automation has none. To change the records a search found, loop "
            "over them and use modify_related_entities with record_source: "
            "automation_variable and the loop's item variable",
            step=step.get("key"),
            index=index,
            path="step_type",
            got="change_field_value",
            hint="kizen automations step-types show modify_related_entities",
        )
    ]


def _entity_subtype_errors(index: int | None, step: dict[str, Any]) -> list[Finding]:
    """An entity variable declared with no ``data_subtype``: the server
    400s it (confirmed live 2026-11-16)."""
    if step.get("step_type") != "initialize_variable":
        return []
    block = step.get("action_initialize_variable")
    var = block.get("variable") if isinstance(block, dict) else None
    if not isinstance(var, dict) or var.get("data_type") != "entity":
        return []
    if var.get("data_subtype"):
        return []
    return [
        Finding(
            "an entity variable needs data_subtype: the api_name or UUID of "
            "the object its records belong to",
            path="action_initialize_variable.variable.data_subtype",
            step=step.get("key"),
            index=index,
            hint="kizen objects list",
        )
    ]


# ---------------------------------------------------------------------------
# Warnings: selector companions, step order, fan-out
# ---------------------------------------------------------------------------


def _selector_warnings(index: int | None, step: dict[str, Any]) -> list[Finding]:
    step_type = step.get("step_type")
    if not isinstance(step_type, str) or step_type not in step_catalog.STEP_TYPES:
        return []
    if step_type in _CONFIGLESS_STEP_TYPES:
        return []
    block_field = _block_field_for(step_type)
    block = step.get(block_field)
    if not isinstance(block, dict):
        return []
    return [
        Finding(
            f["message"],
            path=f"{block_field}.{f['path']}",
            step=step.get("key"),
            index=index,
            hint=_type_hint(step_type),
        )
        for f in step_catalog.selector_findings(step_type, block)
    ]


def _order_warnings(steps: list[Any]) -> list[Finding]:
    raw = [s.get("order") if isinstance(s, dict) else None for s in steps]
    orders = [o for o in raw if isinstance(o, int)]
    if len(orders) != len(raw) or sorted(orders) == list(range(len(orders))):
        return []  # the model reports a missing or non-integer order
    return [
        Finding(
            f"step orders should run 0 to {len(orders) - 1} with no gaps or "
            f"duplicates; got {sorted(orders)}",
            path="steps",
        )
    ]


def _fan_out(auto: AutomationDef) -> list[Finding]:
    return [Finding(w) for w in _fan_out_warnings(auto)]

"""Static scan of the automation step/trigger builders: which spec-block keys
each one reads, and which keys it writes into its wire block.

Keeps the `@honours(...)` declarations in `tools/planners/automations.py`
honest (`tests/test_automation_payloads.py`), and finds the keys a builder
forwards under their own name, for the drift tier to compare against the
published schema (`tests/drift/test_schema_drift.py`).
"""

from __future__ import annotations

import ast
import inspect

import kizen_builder.tools.planners.automations as automations_planner

# The block reaches these helpers through a derived variable rather than as
# a direct argument, so the scanner is told about them.
DERIVED_BLOCK_HELPERS = {"_step_change_field_value": ("_change_field_value_action",)}
# Declared without being read, each with its reason at the declaration.
DECLARED_NOT_READ = {"_step_go_to_automation_step": {"type"}}

_FUNCS = {
    n.name: n
    for n in ast.parse(inspect.getsource(automations_planner)).body
    if isinstance(n, ast.FunctionDef)
}


def _loop_literals(fn: ast.FunctionDef) -> dict[str, set[str]]:
    """Names bound by `for x in ("a", "b")` / `for r, w in (("a", "b"), ...)`."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)):
            continue
        targets = (
            [node.target] if isinstance(node.target, ast.Name) else node.target.elts
        )
        for i, t in enumerate(targets):
            if not isinstance(t, ast.Name):
                continue
            items = [
                e if isinstance(node.target, ast.Name) else e.elts[i]
                for e in node.iter.elts
            ]
            out[t.id] = {e.value for e in items if isinstance(e, ast.Constant)}
    return out


def _literals(node: ast.AST, loops: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return loops.get(node.id, set())
    return set()


def keys_read(func_name: str, param_index: int = 0) -> set[str]:
    """String keys `func_name` reads from its `param_index`th parameter via
    `.get(...)`, `[...]` or `in`, following module-level helpers the
    parameter is passed to directly."""
    fn = _FUNCS[func_name]
    param = fn.args.args[param_index].arg
    loops = _loop_literals(fn)

    def is_param(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == param

    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and is_param(node.func.value) and node.args:
                found |= _literals(node.args[0], loops)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FUNCS:
                for i, arg in enumerate(node.args):
                    if is_param(arg):
                        found |= keys_read(node.func.id, i)
        elif isinstance(node, ast.Subscript) and is_param(node.value):
            found |= _literals(node.slice, loops)
        elif (
            isinstance(node, ast.Compare)
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and is_param(node.comparators[0])
        ):
            found |= _literals(node.left, loops)
    return found


def keys_written(func_name: str) -> set[str]:
    """String keys `func_name` puts into any dict — literal keys and
    `x["key"] = ...` — following helpers its first parameter is passed to.
    Deliberately loose (nested dicts count too); callers intersect it with
    the keys read, which is what makes it precise enough."""
    fn = _FUNCS[func_name]
    param = fn.args.args[0].arg
    loops = _loop_literals(fn)
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    found |= _literals(k, loops)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            found |= _literals(node.slice, loops)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == param
        ):
            found |= keys_written(node.func.id)
    return found


def declarable(func_name: str) -> set[str]:
    """What a builder's `@honours(...)` declaration should list."""
    read = keys_read(func_name)
    for helper in DERIVED_BLOCK_HELPERS.get(func_name, ()):
        read |= keys_read(helper)
    return read | DECLARED_NOT_READ.get(func_name, set())


def forwarded_as_is(func_name: str) -> set[str]:
    """Declared keys the builder writes back under the same name — what
    reaches Kizen as the author spelled it, as opposed to an alias the
    builder translates (`relationship_fields` -> `relationship_field_ids`)."""
    return declarable(func_name) & keys_written(func_name)

"""What this repo believes the Kizen wire contract looks like — and the
machinery to compare that belief against a live ``GET /api/docs/schema``.

Two things live here:

* :data:`TRACKED` — the *scope*. A hand-curated list of the highest-risk
  mutation contracts (automations, objects, fields, permissions) plus, derived
  from the CLI's own ``_STEP_BUILDERS`` / ``_TRIGGER_BUILDERS`` registries, one
  entry per wired automation step and trigger type. Deriving the automation
  sub-schemas from the registries means wiring a new step type automatically
  widens the drift scope — and fails the snapshot check until a maintainer
  looks at the new sub-schema and refreshes it.

* :data:`KNOWN_SCHEMA_OMISSIONS` — the *honest part*. The published schema is
  not a trustworthy oracle: for several of these contracts it declares fewer
  request fields than the API actually accepts (``PermissionGroupRequest``
  declares exactly ``name`` while the live endpoint takes a ~35 KB body), and
  the automation step envelope is documented under entirely different key names
  than the ones the API accepts on write. Rather than pretend, every field the
  CLI sends that the schema does not declare is listed here with a reason. The
  round-trip half asserts that set still matches, so "the schema caught up" and
  "the CLI grew a field nobody vetted" both surface as failures.

Nothing here imports at module scope from anything that needs credentials, so
this module is safe to import during collection.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SNAPSHOT_PATH = Path(__file__).parent / "schema_snapshot.json"

#: Env var a maintainer sets to rewrite the snapshot instead of asserting on it.
UPDATE_ENV_VAR = "KIZEN_DRIFT_UPDATE_SNAPSHOT"


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    """One (method, path) the CLI mutates, or one component schema it fills in.

    ``path``/``method`` are the *schema's* spelling of the endpoint as of the
    last time the contract was edited — the OpenAPI path template uses
    different parameter names than the CLI's f-strings (``{id}`` vs
    ``{object_id}``), so these are recorded explicitly rather than scraped out
    of ``kizen_builder.api``. Matching against the live schema is on path
    *shape* (see :func:`match_endpoint`), so a placeholder rename upstream
    still resolves; the snapshot's ``schema_path`` is the live spelling.
    """

    surface: str
    label: str
    method: str | None = None
    path: str | None = None
    #: Component schema name, for entries that track a body shape directly
    #: rather than an endpoint (e.g. an automation step's action sub-block).
    schema_name: str | None = None
    note: str = ""

    @property
    def key(self) -> str:
        if self.path:
            return f"{self.method.upper()} {self.path}"
        return f"schema:{self.schema_name}"


#: Endpoint contracts. Scoped deliberately: these are the mutation surfaces
#: where a silent wire change turns into a user debugging a 400
#: in a customer environment. Read endpoints and the other ~500 paths in the
#: schema are out of scope on purpose — diffing them would be noisy forever.
ENDPOINT_CONTRACTS: tuple[Contract, ...] = (
    # --- automations -------------------------------------------------------
    Contract(
        "automations", "create automation", "post", "/api/automation2/automations"
    ),
    Contract(
        "automations",
        "replace automation",
        "put",
        "/api/automation2/automations/{automation_identifier}",
        note="the CLI updates via full PUT, not PATCH — revision is part of the body",
    ),
    Contract(
        "automations",
        "delete automation",
        "delete",
        "/api/automation2/automations/{automation_identifier}",
    ),
    # --- objects -----------------------------------------------------------
    Contract("objects", "create custom object", "post", "/api/custom-objects"),
    Contract(
        "objects",
        "update custom object",
        "patch",
        "/api/custom-objects/{object_identifier}",
    ),
    Contract(
        "objects",
        "delete custom object",
        "delete",
        "/api/custom-objects/{object_identifier}",
    ),
    Contract(
        "objects",
        "create field category",
        "post",
        "/api/custom-objects/{object_pk}/categories",
    ),
    Contract(
        "objects",
        "create pipeline stage",
        "post",
        "/api/pipelines/{object_pk}/stages",
    ),
    # --- fields ------------------------------------------------------------
    Contract(
        "fields",
        "create field",
        "post",
        "/api/custom-objects/{object_identifier}/fields",
    ),
    Contract(
        "fields",
        "update field",
        "patch",
        "/api/custom-objects/{object_identifier}/fields/{id}",
    ),
    Contract(
        "fields",
        "delete field",
        "delete",
        "/api/custom-objects/{object_identifier}/fields/{id}",
    ),
    Contract(
        "fields",
        "add field option",
        "post",
        "/api/custom-objects/{object_identifier}/fields/{field_identifier}/options",
    ),
    Contract(
        "fields",
        "replace field option",
        "post",
        "/api/custom-objects/{object_identifier}/fields/{field_identifier}/options/{id}/replace",
    ),
    # --- permissions -------------------------------------------------------
    Contract("permissions", "create permission group", "post", "/api/permission-group"),
    Contract(
        "permissions",
        "update permission group sections",
        "patch",
        "/api/permission-group/{id}",
    ),
    Contract(
        "permissions", "delete permission group", "delete", "/api/permission-group/{id}"
    ),
    Contract(
        "permissions",
        "set one object/field access level",
        "patch",
        "/api/permission-group/{permission_group_id}/object-update",
    ),
    Contract("permissions", "create role", "post", "/api/role"),
    Contract("permissions", "update role", "patch", "/api/role/{id}"),
    Contract("permissions", "delete role", "delete", "/api/role/{id}"),
    # --- records -------------------------------------------------------
    Contract(
        "records", "create record", "post", "/api/records/{object_identifier}/add"
    ),
    Contract(
        "records",
        "update record",
        "patch",
        "/api/records/{object_identifier}/{entity_id}",
    ),
    Contract(
        "records",
        "delete record",
        "delete",
        "/api/records/{object_identifier}/{entity_id}",
    ),
    Contract(
        "records",
        "upsert record",
        "post",
        "/api/records/{object_identifier}/upsert",
    ),
    Contract(
        "records",
        "bulk change field value",
        "post",
        "/api/custom-objects/{object_pk}/bulk-change-field-value",
        note=(
            "lives under /api/custom-objects, not /api/records — a detail "
            "action on the custom-object viewset, not the records viewset"
        ),
    ),
    Contract(
        "records",
        "archive record",
        "post",
        "/api/custom-objects/{object_pk}/bulk-archive-entity-record",
        note=(
            "lives under /api/custom-objects, same detail-action shape as "
            "bulk-change-field-value. The UI's Archive button and the only "
            "path `records archive` uses, with send_email_notification false "
            "(schema default true). DELETE /api/records/{o}/{id} reaches the "
            "same archived state, confirmed live 2026-09-28"
        ),
    ),
    Contract(
        "records",
        "unarchive record",
        "patch",
        "/api/records/{object_identifier}/{entity_id}/unarchive",
        note=(
            "reverses archive record above and, confirmed live 2026-08-13, "
            "DELETE /api/records/{o}/{id} too"
        ),
    ),
    # --- saved_views (filter groups / quick filters) ------------------
    Contract(
        "saved_views",
        "create filter group",
        "post",
        "/api/custom-objects/{object_pk}/filter-groups",
    ),
    Contract(
        "saved_views",
        "create quick filter",
        "post",
        "/api/custom-objects/{object_pk}/quick-filters",
        note="separate path from filter-groups, same request shape family",
    ),
)


#: Component schemas tracked directly (not reachable as a request body of a
#: tracked endpoint, or worth pinning in their own right).
BASE_SCHEMA_CONTRACTS: tuple[Contract, ...] = (
    Contract(
        "automations", "automation write body", schema_name="WriteAutomationRequest"
    ),
    Contract("automations", "trigger envelope", schema_name="WriteTriggerRequest"),
    Contract("automations", "step envelope", schema_name="WriteStepRequest"),
)


def automation_block_contracts() -> tuple[Contract, ...]:
    """One contract per automation step/trigger type the CLI actually wires.

    Resolves each wired type to the component schema behind
    ``WriteStepRequest.properties['action_<type>' | 'step_<type>']`` (and the
    trigger equivalent). The mapping from a wired type to its schema property
    name is the CLI's own — importing the registries here is what keeps the
    drift scope in step with ``_STEP_BUILDERS``.
    """
    from kizen_builder.tools.planners.automations import (
        _STEP_BUILDERS,
        _TRIGGER_BUILDERS,
        _prefix_for,
    )

    out: list[Contract] = []
    for step_type in sorted(_STEP_BUILDERS):
        out.append(
            Contract(
                "automations",
                f"step config: {step_type}",
                schema_name=f"WriteStepRequest.{_prefix_for(step_type)}_{step_type}",
            )
        )
    for trigger_type in sorted(_TRIGGER_BUILDERS):
        out.append(
            Contract(
                "automations",
                f"trigger config: {trigger_type}",
                schema_name=f"WriteTriggerRequest.trigger_{trigger_type}",
            )
        )
    return tuple(out)


def tracked() -> tuple[Contract, ...]:
    """Every contract in scope, endpoints first."""
    return ENDPOINT_CONTRACTS + BASE_SCHEMA_CONTRACTS + automation_block_contracts()


# ---------------------------------------------------------------------------
# Known divergences between the schema and real behavior
# ---------------------------------------------------------------------------

#: Tracked contracts the published schema is *expected* not to document, with
#: the evidence that the CLI's spelling is nonetheless the one the live API
#: takes. Asserted in both directions: something newly missing fails, and so
#: does something here turning up documented (delete the entry when it does).
KNOWN_UNDOCUMENTED_BLOCKS: dict[str, str] = {
    "schema:WriteStepRequest.action_call_llm": (
        "the schema declares the property as `action_llm_call`; both the write "
        "path and every captured read response use `action_call_llm` "
        "(confirmed live 2026-08-05)"
    ),
    "schema:WriteStepRequest.action_stop_execution": (
        "undeclared entirely; the API accepts `action_stop_execution: {}` and "
        "the round-trip half creates two such steps every run "
        "(confirmed live 2026-08-05)"
    ),
    "schema:WriteTriggerRequest.trigger_manual": (
        "undeclared; a manual trigger carries no config block and the planner "
        "auto-prepends one, which round-trips as trigger_type 'manual' "
        "(confirmed live 2026-08-05)"
    ),
}


#: **Top-level** request fields the CLI sends that ``GET /api/docs/schema``
#: does not declare, keyed by contract key. Each entry is a field the live API
#: demonstrably accepts — the round-trip half creates entities using exactly
#: these payloads — so a missing declaration is a gap in the published schema,
#: not a CLI bug. The round-trip half asserts these sets exactly: if the schema
#: starts declaring one, or the CLI starts sending something new, the check
#: fails and a maintainer decides which side moved.
#:
#: Two divergences deliberately do *not* live here — neither is an undeclared
#: top-level key, and each has a dedicated test that explains itself on failure:
#:
#: * ``POST /api/custom-objects`` declares ``pipeline`` but does not mark it
#:   required, while live rejects a pipeline object without it —
#:   ``test_pipeline_is_required_live_but_not_in_the_schema``.
#: * The automation step/trigger *envelope* is documented in the read dialect
#:   (``step_type`` required; no ``key`` / ``parent_key`` / ``parent_yes_no`` /
#:   ``parent_condition`` / ``prefix`` / ``goal_type``), and the schema's
#:   ``yes_step_ids`` / ``no_step_ids`` 500 on write —
#:   ``test_automation_write_dialect_is_accepted``.
KNOWN_SCHEMA_OMISSIONS: dict[str, dict[str, str]] = {
    "POST /api/automation2/automations": {
        "return_all_steps_errors": (
            "undeclared by WriteAutomationRequest; the planner sends it on every "
            "create and update so a failing automation reports every step's "
            "error rather than only the first (confirmed live 2026-08-05)"
        ),
    },
    "POST /api/custom-objects": {},
    "POST /api/permission-group": {
        "*_section": (
            "PermissionGroupRequest declares only `name`; the live endpoint "
            "accepts (and the read echoes back) every `*_section` dict plus "
            "`custom_objects` — a ~35 KB body the schema is entirely silent on "
            "(confirmed live 2026-08-05)"
        ),
        "custom_objects": (
            "same as *_section — undeclared but accepted and echoed back "
            "(confirmed live 2026-08-05)"
        ),
    },
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _type_of(node: Any, components: dict[str, Any] | None = None) -> str:
    """Compact, human-readable type descriptor for one schema node.

    Deliberately lossy: enough to catch a field changing type, format, enum,
    nullability or target ref (the ``config_metadata`` string-vs-object class
    of drift), without dragging descriptions and examples into the diff.

    ``components`` (the document's ``components.schemas``, when available)
    lets a ``$ref`` that points at a bare named enum — drf-spectacular's usual
    way of extracting a ``ChoiceField`` — carry its values, the same as an
    inline enum does below. A ref to anything else (the common case) is
    unaffected.
    """
    if not isinstance(node, dict):
        return "?"
    if "$ref" in node:
        ref_name = _ref_name(node["$ref"])
        target = (components or {}).get(ref_name)
        if _is_bare_enum(target):
            return ref_name + "{" + ",".join(str(e) for e in target["enum"]) + "}"
        return ref_name
    # `allOf: [{$ref}]` + siblings is drf-spectacular's way of decorating a ref
    if "allOf" in node and isinstance(node["allOf"], list):
        inner = [_type_of(x, components) for x in node["allOf"]]
        base = inner[0] if len(inner) == 1 else "allOf(" + ",".join(inner) + ")"
        return f"{base}?" if node.get("nullable") else base
    if "oneOf" in node:
        return "oneOf(" + ",".join(_type_of(x, components) for x in node["oneOf"]) + ")"
    parts: list[str] = [str(node.get("type", "any"))]
    if node.get("format"):
        parts.append(f"<{node['format']}>")
    if node.get("enum"):
        parts.append("{" + ",".join(str(e) for e in node["enum"]) + "}")
    if node.get("type") == "array":
        parts.append(f"[{_type_of(node.get('items') or {}, components)}]")
    out = "".join(parts)
    if node.get("nullable"):
        out += "?"
    if node.get("writeOnly"):
        out += " (writeOnly)"
    return out


def _is_bare_enum(node: Any) -> bool:
    """True when a component schema is nothing but a named enum.

    ``type`` in ``{"string", "integer"}``, an ``enum`` key, and nothing
    structurally meaningful beyond ``title``/``description``. A ref to a real
    object schema, a ``oneOf``, etc. is not a bare enum and is left alone.
    """
    if not isinstance(node, dict):
        return False
    if node.get("type") not in ("string", "integer"):
        return False
    if "enum" not in node:
        return False
    return not (set(node) - {"type", "enum", "title", "description"})


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _resolve(schema: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Resolve a component schema name, or a ``Parent.property`` path.

    ``WriteStepRequest.action_code_step`` resolves ``WriteStepRequest``, reads
    its ``action_code_step`` property, follows the ref/allOf, and returns the
    target object schema.
    """
    components = schema.get("components", {}).get("schemas", {})
    if "." not in name:
        return components.get(name)

    parent_name, prop = name.split(".", 1)
    parent = components.get(parent_name)
    if not parent:
        return None
    node = (parent.get("properties") or {}).get(prop)
    if node is None:
        return None
    return _follow(components, node)


def _follow(components: dict[str, Any], node: Any) -> dict[str, Any] | None:
    """Follow ``$ref`` / single-element ``allOf`` down to an object schema."""
    if not isinstance(node, dict):
        return None
    if "$ref" in node:
        return components.get(_ref_name(node["$ref"]))
    if isinstance(node.get("allOf"), list) and len(node["allOf"]) == 1:
        return _follow(components, node["allOf"][0])
    return node


def _shape(
    node: dict[str, Any] | None, components: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Snapshot-able shape of one object schema."""
    if node is None:
        return {"present": False}
    props = node.get("properties") or {}
    return {
        "present": True,
        "type": node.get("type", "object"),
        "required": sorted(node.get("required") or []),
        "properties": {k: _type_of(v, components) for k, v in sorted(props.items())},
    }


_PLACEHOLDER = re.compile(r"\{[^/{}]*\}")


def _path_shape(template: str) -> str:
    return _PLACEHOLDER.sub("{}", template)


def match_endpoint(
    paths: dict[str, Any], template: str, method: str
) -> tuple[str, dict[str, Any]] | None:
    """Find ``method`` on ``template`` in a live ``paths`` map.

    An exact template hit wins. Otherwise the match is the one live path with
    the same shape — every ``{...}`` placeholder treated alike, all other
    text compared exactly — that carries ``method``, so Kizen renaming a
    path parameter is not mistaken for the endpoint vanishing. Returns
    ``(live_path, operation)``, or None when nothing matches.

    Raises ValueError when more than one live path fits: picking one would
    silently track whichever the schema happened to list first.
    """
    op = (paths.get(template) or {}).get(method)
    if op is not None:
        return template, op

    shape = _path_shape(template)
    candidates = sorted(
        p
        for p, ops in paths.items()
        if _path_shape(p) == shape and ops.get(method) is not None
    )
    if len(candidates) > 1:
        raise ValueError(
            f"{method.upper()} {template} matches more than one live path by "
            f"shape: {', '.join(candidates)}. Spell the contract's path exactly "
            "as one of them."
        )
    if not candidates:
        return None
    return candidates[0], paths[candidates[0]][method]


def extract(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a live OpenAPI document to just the tracked contracts.

    The full document is ~1.5 MB / 557 paths / 1326 component schemas; this
    keeps only what the CLI writes to, so the committed snapshot is reviewable
    and the diff is about something.
    """
    paths = schema.get("paths") or {}
    components = schema.get("components", {}).get("schemas", {})
    out: dict[str, Any] = {}

    for c in tracked():
        if c.path:
            match = match_endpoint(paths, c.path, c.method)
            if match is None:
                out[c.key] = {"present": False, "surface": c.surface, "label": c.label}
                continue
            schema_path, op = match
            entry: dict[str, Any] = {
                "present": True,
                "surface": c.surface,
                "label": c.label,
                "schema_path": schema_path,
                "operation_id": op.get("operationId"),
                "query_params": sorted(
                    p["name"]
                    for p in op.get("parameters") or []
                    if p.get("in") == "query"
                ),
                "responses": sorted(op.get("responses") or {}),
            }
            body = op.get("requestBody")
            if body:
                node = (body.get("content") or {}).get("application/json", {}).get(
                    "schema"
                ) or {}
                entry["request_schema"] = (
                    _ref_name(node["$ref"]) if "$ref" in node else None
                )
                entry["request_required"] = bool(body.get("required"))
                entry["request_body"] = _shape(_follow(components, node), components)
            else:
                entry["request_schema"] = None
            out[c.key] = entry
        else:
            entry = {"surface": c.surface, "label": c.label}
            entry.update(_shape(_resolve(schema, c.schema_name), components))
            out[c.key] = entry

    return out


# ---------------------------------------------------------------------------
# Snapshot I/O + diff
# ---------------------------------------------------------------------------


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text())


def save_snapshot(extracted: dict[str, Any], meta: dict[str, Any]) -> None:
    SNAPSHOT_PATH.write_text(
        json.dumps({"_meta": meta, "contracts": extracted}, indent=2, sort_keys=True)
        + "\n"
    )


@dataclass
class Drift:
    """A legible account of how the live schema differs from the snapshot."""

    added_contracts: list[str] = field(default_factory=list)
    removed_contracts: list[str] = field(default_factory=list)
    #: contract key -> list of human-readable change lines
    changed: dict[str, list[str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.added_contracts or self.removed_contracts or self.changed)

    def report(self) -> str:
        lines: list[str] = []
        if self.removed_contracts:
            lines.append(
                "Contracts the snapshot knows but the extraction no longer produced:"
            )
            lines += [f"    - {k}" for k in self.removed_contracts]
        if self.added_contracts:
            lines.append("Contracts newly in scope (refresh the snapshot):")
            lines += [f"    + {k}" for k in self.added_contracts]
        if self.changed:
            lines.append(f"Changed ({len(self.changed)} contract(s)):")
        for key, changes in self.changed.items():
            lines.append(f"  {key}")
            lines += [f"      {c}" for c in changes]
        return "\n".join(lines)


def _diff_shape(prefix: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if old.get("present") != new.get("present"):
        return [
            f"{prefix}present: {old.get('present')} -> {new.get('present')}"
            "   <-- the body shape appeared/disappeared"
        ]
    if not new.get("present"):
        return out
    if old.get("type") != new.get("type"):
        out.append(f"{prefix}type: {old.get('type')} -> {new.get('type')}")

    old_req, new_req = set(old.get("required") or []), set(new.get("required") or [])
    for f in sorted(new_req - old_req):
        out.append(
            f"{prefix}required += {f}   <-- newly REQUIRED; payloads omitting it will 400"
        )
    for f in sorted(old_req - new_req):
        out.append(f"{prefix}required -= {f}   <-- no longer required")

    old_p = old.get("properties") or {}
    new_p = new.get("properties") or {}
    for f in sorted(set(new_p) - set(old_p)):
        out.append(f"{prefix}+ field {f}: {new_p[f]}")
    for f in sorted(set(old_p) - set(new_p)):
        out.append(f"{prefix}- field {f}: was {old_p[f]}   <-- GONE from the schema")
    for f in sorted(set(old_p) & set(new_p)):
        if old_p[f] != new_p[f]:
            out.append(f"{prefix}~ field {f}: {old_p[f]} -> {new_p[f]}")
    return out


def diff(snapshot_contracts: dict[str, Any], live: dict[str, Any]) -> Drift:
    """Compare a committed snapshot against a freshly extracted one."""
    d = Drift()
    d.added_contracts = sorted(set(live) - set(snapshot_contracts))
    d.removed_contracts = sorted(set(snapshot_contracts) - set(live))

    for key in sorted(set(snapshot_contracts) & set(live)):
        old, new = snapshot_contracts[key], live[key]
        changes: list[str] = []

        if old.get("present") != new.get("present"):
            changes.append(
                f"endpoint present: {old.get('present')} -> {new.get('present')}"
                "   <-- the endpoint appeared/disappeared"
            )
        else:
            for scalar in ("operation_id", "request_schema", "request_required"):
                if (scalar in old or scalar in new) and old.get(scalar) != new.get(
                    scalar
                ):
                    changes.append(
                        f"{scalar}: {old.get(scalar)!r} -> {new.get(scalar)!r}"
                    )
            for listy, arrow in (
                ("query_params", "query param"),
                ("responses", "documented response"),
            ):
                o, n = set(old.get(listy) or []), set(new.get(listy) or [])
                changes += [f"+ {arrow} {x}" for x in sorted(n - o)]
                changes += [f"- {arrow} {x}   <-- GONE" for x in sorted(o - n)]

            if "request_body" in old or "request_body" in new:
                changes += _diff_shape(
                    "request body: ",
                    old.get("request_body") or {"present": False},
                    new.get("request_body") or {"present": False},
                )
            else:
                # component-schema contract: the shape is the entry itself
                changes += _diff_shape("", old, new)

        if changes:
            d.changed[key] = changes
    return d

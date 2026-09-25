"""Authoring: ``configure-flow`` — save execution variables and load steps
from a spec. Object and field names resolve at plan time; variable references
are resolved round by round at apply time, since a load step's exposed
variable doesn't exist until that step is saved.

Everything that already exists live is sent back by id, so a re-run updates the
connector in place. A variable or load step sent without an id is recreated, and
recreating a variable deletes every rule that pointed at its old uuid.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec import (
    ExecutionVariableDef,
    LoadStepDef,
    SmartConnectorFlowDef,
)
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors.authoring._helpers import (
    _connector_ref,
    _field_lookup,
    _object_lookup,
    _resolved,
    _scopes,
    _sole_scope,
)

# Keys a load step round-trips. Live GETs return exactly these, so an
# already-saved step can be handed straight back on a later PATCH — which is how
# the multi-round apply below keeps the server ids (and therefore the exposed
# variable uuids an earlier round handed out) stable.
_LOAD_WIRE_KEYS = (
    "id",
    "custom_object",
    "scope",
    "type",
    "order",
    "matching_rules",
    "field_mapping_rules",
    "execution_variable",
    "automation_trigger_config",
    "newly_created_records_automations",
    "other_matches_records_automations",
)

_MATCH_ACTION_KEYS = (
    "no_match_action",
    "single_match_action",
    "multiple_match_action",
    "match_archive_action",
)


class PartialSaveError(Exception):
    """A write failed after earlier writes of the same save succeeded.

    ``report`` says which write failed and what state each load step is in now.
    """

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(f"configure-flow stopped at {report['failed_write']}")
        self.report = report


def _live_loads(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return (detail.get("flow") or {}).get("loads") or []


def _rule_counts(load: dict[str, Any]) -> dict[str, int]:
    return {
        "matching": len(load.get("matching_rules") or []),
        "mapping": len(load.get("field_mapping_rules") or []),
    }


def _pair_loads(
    spec: list[tuple[tuple[Any, Any], int]], live: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Pair spec load steps (``((custom_object, scope), order)``) with live ones.

    Returns spec index → the live load it updates. Loads sharing a key pair in
    ``order`` order on both sides; whatever is left over is a create (spec) or a
    delete (live).
    """
    free: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for load in sorted(live, key=lambda d: d.get("order") or 0):
        free.setdefault((load.get("custom_object"), load.get("scope")), []).append(load)
    pairs: dict[int, dict[str, Any]] = {}
    for index in sorted(range(len(spec)), key=lambda i: spec[i][1]):
        queue = free.get(spec[index][0])
        if queue:
            pairs[index] = queue.pop(0)
    return pairs


def _variable_ids(detail: dict[str, Any]) -> dict[str, str]:
    """name → uuid for every execution variable a connector exposes.

    Two places hold them: the connector's own ``execution_variables`` (the
    data-source ones) and each load step's ``execution_variable`` (the uuid of
    the record that step matched or created). A later load step referencing the
    latter is what populates a relationship field.
    """
    known: dict[str, str] = {}
    for var in detail.get("execution_variables") or []:
        if var.get("name") and var.get("id"):
            known[var["name"]] = var["id"]
    for load in (detail.get("flow") or {}).get("loads") or []:
        var = load.get("execution_variable") or {}
        if var.get("name") and var.get("id"):
            known[var["name"]] = var["id"]
    return known


def _resolve_field(fields: dict[str, str], token: str, object_label: str) -> str:
    if token in fields:
        return fields[token]
    if token in set(fields.values()):
        return token
    raise PlanError(
        f"field '{token}' not found on '{object_label}'. Available: {sorted(fields)}"
    )


def _validated_ref(
    ref: str,
    where: str,
    *,
    provided: set[str],
    exposed_by: dict[str, int],
    step: int,
) -> str:
    """Check one variable reference from load step ``step``, returning it unchanged.

    A reference is good if something provides the name, and — for a name a load
    step exposes — if that step runs *before* this one. A step can only point at
    a record an earlier step already wrote.
    """
    if ref not in provided:
        raise PlanError(
            f"{where} references variable '{ref}', which nothing provides. "
            f"Declared/live: {sorted(provided)}"
        )
    source_step = exposed_by.get(ref)
    if source_step is not None and source_step >= step:
        raise PlanError(
            f"{where} references '{ref}', exposed by a load step that runs later "
            f"— a step can only reference a record an earlier step wrote"
        )
    return ref


def _load_refs(load: dict[str, Any]) -> list[str]:
    """Every variable name a resolved load step reads."""
    refs = [r["variable_ref"] for r in load["matching_rules"]]
    for rule in load["field_mapping_rules"]:
        refs.extend(rule["variable_refs"])
    return refs


def _resolve_execution_variables(
    execution_variables: list[ExecutionVariableDef], scopes: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a spec's execution variables against the connector's output
    scopes, and build the PATCH-ready payload row for each.

    Returns ``(variables_payload, date_format_warnings)``.
    """
    variables_payload: list[dict[str, Any]] = []
    date_format_warnings: list[str] = []
    for order, var in enumerate(execution_variables, start=1):
        scope = var.scope or _sole_scope(scopes, f"execution variable '{var.name}'")
        if scope not in scopes:
            raise PlanError(
                f"execution variable '{var.name}' targets output table "
                f"'{scope}', which this connector doesn't produce. "
                f"Available: {sorted(scopes)}"
            )
        source = var.data_source or var.name
        if var.value is None and source not in scopes[scope]:
            raise PlanError(
                f"execution variable '{var.name}' reads column '{source}', "
                f"which isn't in output table '{scope}'. Available: "
                f"{scopes[scope]}. These are the columns of the last generated "
                f"output sample — if the SQL selects '{source}' now, the sample "
                f"is stale: re-run `smart-connectors generate-sample`."
            )
        row: dict[str, Any] = {
            "name": var.name,
            "data_type": var.data_type,
            "scope": scope,
            "type": "data_source",
            "display_order": var.display_order
            if var.display_order is not None
            else order,
            "is_array": var.is_array,
        }
        if var.value is None:
            row["data_source"] = source
        else:
            row["value"] = var.value
        for key in ("array_delimiter", "required", "input_format", "output_format"):
            value = getattr(var, key)
            if value is not None:
                row[key] = value
        if var.data_type in ("date", "datetime") and var.output_format is None:
            date_format_warnings.append(
                f"execution variable '{var.name}' is a {var.data_type} with "
                f"no output_format — Kizen defaults it to %m/%d/%Y, which a "
                f"native ISO-only date/datetime field then rejects per row. "
                f"That failure is a silent per-row 'Partial Success' — it "
                f"won't appear in `executions --json`, only in the .xlsx "
                f"report downloadable from the web UI. Set output_format "
                f"explicitly (e.g. '%Y-%m-%d') if the target field is a "
                f"native date/datetime type."
            )
        variables_payload.append(row)
    return variables_payload, date_format_warnings


def _resolve_load(
    load: LoadStepDef,
    index: int,
    *,
    client: KizenClient,
    scopes: dict[str, list[str]],
    provided: set[str],
    exposed_by: dict[str, int],
    by_api: dict[str, str],
    by_id: dict[str, str],
    field_cache: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Resolve one spec load step against live state: object/field names to
    UUIDs, variable references checked against ``provided``/``exposed_by``,
    and the object's own ``name`` field mapping required.

    ``field_cache`` is shared across calls for the whole flow so an object
    referenced by more than one load step only costs one fields lookup.
    """
    object_id = _resolved(load.custom_object, by_api, by_id, "custom object")
    if object_id not in field_cache:
        field_cache[object_id] = _field_lookup(client, object_id)
    fields = field_cache[object_id]
    label = load.custom_object
    scope = load.scope or _sole_scope(scopes, f"load step '{label}'")
    if scope not in scopes:
        raise PlanError(
            f"load step '{label}' reads output table '{scope}', which "
            f"this connector doesn't produce. Available: {sorted(scopes)}"
        )

    def _check_ref(ref: str, where: str, *, step: int = index) -> str:
        return _validated_ref(
            ref, where, provided=provided, exposed_by=exposed_by, step=step
        )

    matching: list[dict[str, Any]] = []
    for rule_order, rule in enumerate(load.matching_rules):
        row = {
            "order": rule.order if rule.order is not None else rule_order,
            "is_match_by_kizen_id": rule.is_match_by_kizen_id,
            "variable_ref": _check_ref(
                rule.variable, f"load step '{label}' matching rule {rule_order}"
            ),
            "field": (
                _resolve_field(fields, rule.field, label) if rule.field else None
            ),
            "field_label": rule.field,
        }
        for key in _MATCH_ACTION_KEYS:
            row[key] = getattr(rule, key)
        matching.append(row)

    mappings: list[dict[str, Any]] = []
    for map_order, map_rule in enumerate(load.field_mapping_rules):
        row = {
            "field": _resolve_field(fields, map_rule.field, label),
            "field_label": map_rule.field,
            "variable_refs": [
                _check_ref(ref, f"load step '{label}' mapping for '{map_rule.field}'")
                for ref in map_rule.variable_refs
            ],
            "can_create_field_options": map_rule.can_create_field_options,
            "display_order": (
                map_rule.display_order
                if map_rule.display_order is not None
                else map_order
            ),
        }
        if map_rule.conflict_resolution is not None:
            row["conflict_resolution"] = map_rule.conflict_resolution
        mappings.append(row)

    # Kizen requires the object's own name field on every load step.
    # Only enforced when the object actually has an api_name 'name'
    # field — contacts and other built-ins name themselves differently.
    if "name" in fields and not any(m["field"] == fields["name"] for m in mappings):
        raise PlanError(
            f"load step '{label}' has no mapping for the object's own "
            f"'name' field — Kizen requires one on every load step"
        )

    return {
        "object_label": label,
        "custom_object": object_id,
        "scope": scope,
        "type": load.type,
        "order": load.order if load.order is not None else index,
        "matching_rules": matching,
        "field_mapping_rules": mappings,
        "exposes_variable": load.exposes_variable,
        "automation_trigger_config": load.automation_trigger_config,
    }


def plan_configure_flow(
    spec: dict[str, Any], *, connector: str | None = None
) -> dict[str, Any]:
    """Validate a flow spec against live state; resolve names to UUIDs.

    Object and field names resolve here, at plan time. Variable *names* can't:
    the spec's own execution variables don't have UUIDs until they're saved, and
    a load step's exposed variable doesn't exist until that step is saved. So the
    plan carries variable references by name and :func:`apply_configure_flow`
    resolves them round by round.

    What this catches before any write: unknown objects/fields, a data_source
    that isn't a column of its output table, a variable reference nothing
    provides, a forward reference to a variable a *later* load step exposes, and
    a load step missing the mapping for its object's own ``name`` field (which
    Kizen requires on every step).
    """
    flow_def = SmartConnectorFlowDef.model_validate(spec)
    identifier = connector or flow_def.connector
    if not identifier:
        raise PlanError(
            "no connector given — pass one on the command line or set "
            "'connector' in the spec"
        )

    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, identifier)
        scopes = _scopes(detail)
        if not scopes:
            raise PlanError(
                f"'{detail.get('api_name')}' has no recognized output columns yet, "
                f"so nothing can be mapped. Run `smart-connectors generate-sample` "
                f"first — it populates them (and Kizen validates every variable's "
                f"scope against them)"
            )

        live_vars = {
            v["name"]: v
            for v in detail.get("execution_variables") or []
            if v.get("name") and v.get("id")
        }
        live_loads = _live_loads(detail)

        # --- execution variables ------------------------------------------
        variables_payload, date_format_warnings = _resolve_execution_variables(
            flow_def.execution_variables, scopes
        )
        # A row without an id is a new variable to the server, and the old one
        # goes — taking every rule that referenced its uuid with it.
        for row in variables_payload:
            if row["name"] in live_vars:
                row["id"] = live_vars[row["name"]]["id"]

        # The data-source set is replaced wholesale, so anything live but not
        # re-declared is dropped. The drop is the save's last write, once no
        # load step the spec keeps can still reference it.
        declared = {v.name for v in flow_def.execution_variables}
        dropped_rows = (
            [v for name, v in live_vars.items() if name not in declared]
            if variables_payload
            else []
        )

        # --- load steps ---------------------------------------------------
        by_api, by_id = _object_lookup(client)
        spec_keys = [
            (
                (
                    _resolved(load.custom_object, by_api, by_id, "custom object"),
                    load.scope
                    or _sole_scope(scopes, f"load step '{load.custom_object}'"),
                ),
                load.order if load.order is not None else index,
            )
            for index, load in enumerate(flow_def.loads)
        ]
        orders = [order for _, order in spec_keys]
        if len(set(orders)) < len(orders):
            clash = sorted({o for o in orders if orders.count(o) > 1})
            raise PlanError(
                f"more than one load step has order {clash} — Kizen requires "
                f"each step's order to be unique"
            )
        pairs = _pair_loads(spec_keys, live_loads)
        paired_ids = {live["id"] for live in pairs.values()}
        unpaired = [live for live in live_loads if live.get("id") not in paired_ids]

        def _live_label(live: dict[str, Any]) -> str:
            obj = live["custom_object"]
            return f"'{by_id.get(obj, obj)}' (order {live.get('order')})"

        exposed_holder = {
            var["name"]: live
            for live in live_loads
            if (var := live.get("execution_variable") or {}).get("name")
        }
        # The variables write runs first, while every live exposed name still
        # exists — and a shared name would resolve to the exposed uuid.
        if taken := sorted(declared & set(exposed_holder)):
            raise PlanError(
                f"execution variable '{taken[0]}' has the same name as the "
                f"variable live load step {_live_label(exposed_holder[taken[0]])} "
                f"exposes — rename it in the spec"
            )
        for index, load in enumerate(flow_def.loads):
            name = load.exposes_variable
            if not name:
                continue
            where = f"load step '{load.custom_object}' (order {spec_keys[index][1]})"
            if name in live_vars:
                raise PlanError(
                    f"{where} exposes '{name}', which is already the name of a live "
                    f"execution variable on this connector — rename it in the spec"
                )
            holder = exposed_holder.get(name)
            if holder is not None and holder is not pairs.get(index):
                fate = "updates" if holder.get("id") in paired_ids else "deletes"
                raise PlanError(
                    f"{where} exposes '{name}', but live load step "
                    f"{_live_label(holder)} already exposes that name (this spec "
                    f"{fate} that step). Kizen rejects a reused name even when its "
                    f"holder is removed in the same save — rename '{name}' in the spec"
                )

        # What a reference can resolve to once the save is done: the declared
        # variables (or the live set, when the spec leaves it alone), the names
        # paired live steps keep exposing, and what the spec's steps expose.
        provided: set[str] = declared if variables_payload else set(live_vars)
        exposed_by: dict[str, int] = {}
        for index, load in enumerate(flow_def.loads):
            live_var = (pairs.get(index) or {}).get("execution_variable") or {}
            kept = live_var.get("name") if live_var.get("id") else None
            if load.exposes_variable in (None, kept) and kept:
                exposed_by[kept] = index
        known_at_start = provided | set(exposed_by)
        for index, load in enumerate(flow_def.loads):
            if load.exposes_variable:
                exposed_by[load.exposes_variable] = index
        provided = provided | set(exposed_by)

        field_cache: dict[str, dict[str, str]] = {}

        resolved_loads: list[dict[str, Any]] = [
            _resolve_load(
                load,
                index,
                client=client,
                scopes=scopes,
                provided=provided,
                exposed_by=exposed_by,
                by_api=by_api,
                by_id=by_id,
                field_cache=field_cache,
            )
            for index, load in enumerate(flow_def.loads)
        ]

    for index, live in pairs.items():
        resolved = resolved_loads[index]
        resolved["id"] = live["id"]
        resolved["live_counts"] = _rule_counts(live)
        live_var = live.get("execution_variable") or {}
        if resolved["exposes_variable"] and live_var.get("id"):
            resolved["execution_variable_id"] = live_var["id"]

    rounds = _plan_rounds(resolved_loads, known_at_start)
    existing_flow = {
        k: v for k, v in (detail.get("flow") or {}).items() if k != "loads"
    }
    deleted_loads = [
        {
            "id": live["id"],
            "object_label": by_id.get(live["custom_object"], live["custom_object"]),
            "order": live.get("order"),
            "live_counts": _rule_counts(live),
        }
        for live in unpaired
    ]
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "scopes": {scope: len(cols) for scope, cols in scopes.items()},
        "execution_variables": variables_payload,
        "dropped_variables": [v["name"] for v in dropped_rows],
        "dropped_variable_rows": dropped_rows,
        "date_format_warnings": date_format_warnings,
        "loads": resolved_loads,
        "load_changes": {
            "update": [_step_label(load) for load in resolved_loads if "id" in load],
            "create": [
                _step_label(load) for load in resolved_loads if "id" not in load
            ],
            "delete": [_step_label(load) for load in deleted_loads],
        },
        "deleted_loads": deleted_loads,
        "existing_flow": existing_flow,
        "rounds": rounds,
        "deferred_loads": [
            resolved_loads[i]["object_label"] for r in rounds[1:] for i in r
        ],
    }


def _step_label(load: dict[str, Any]) -> str:
    return f"{load['object_label']} (order {load['order']})"


def _plan_rounds(loads: list[dict[str, Any]], known: set[str]) -> list[list[int]]:
    """Group load steps (by index) into the PATCH rounds the save needs.

    A step is ready once every name it references has a uuid: ``known`` holds
    the names that already do, and a step's own exposed name gets one when that
    step is saved. Plan-time validation guarantees every reference is provided
    by an earlier step, so each round makes progress.
    """
    known = set(known)
    remaining = list(range(len(loads)))
    rounds: list[list[int]] = []
    while remaining:
        ready = [i for i in remaining if all(r in known for r in _load_refs(loads[i]))]
        if not ready:
            raise PlanError(
                "no load step can be saved first: "
                + ", ".join(loads[i]["object_label"] for i in remaining)
                + " all reference a variable only a later step provides"
            )
        rounds.append(ready)
        known |= {
            loads[i]["exposes_variable"] for i in ready if loads[i]["exposes_variable"]
        }
        remaining = [i for i in remaining if i not in ready]
    return rounds


def _wire_load(load: dict[str, Any], known: dict[str, str]) -> dict[str, Any]:
    """Turn a resolved load step into the wire body, variable names → uuids.

    The asymmetry is Kizen's, not ours: a matching rule takes a single
    ``variable``, a field mapping takes a plural ``variables`` list.
    """
    body: dict[str, Any] = {
        **({"id": load["id"]} if "id" in load else {}),
        "custom_object": load["custom_object"],
        "scope": load["scope"],
        "type": load["type"],
        "order": load["order"],
        "matching_rules": [],
        "field_mapping_rules": [],
    }
    for rule in load["matching_rules"]:
        row: dict[str, Any] = {
            "order": rule["order"],
            "is_match_by_kizen_id": rule["is_match_by_kizen_id"],
            "variable": known[rule["variable_ref"]],
        }
        if rule.get("field"):
            row["field"] = rule["field"]
        for key in _MATCH_ACTION_KEYS:
            row[key] = rule[key]
        body["matching_rules"].append(row)
    for rule in load["field_mapping_rules"]:
        row = {
            "field": rule["field"],
            "variables": [known[ref] for ref in rule["variable_refs"]],
            "can_create_field_options": rule["can_create_field_options"],
            "display_order": rule["display_order"],
        }
        if "conflict_resolution" in rule:
            row["conflict_resolution"] = rule["conflict_resolution"]
        body["field_mapping_rules"].append(row)
    if load.get("exposes_variable"):
        # Setting this explicitly rather than hoping the server auto-creates it:
        # on a fresh connector it often comes back null, and then there's nothing
        # for the next load step to reference.
        # By id when the paired live step already exposes one: that keeps the
        # uuid other steps' rules reference, and a new name renames it in place.
        body["execution_variable"] = {
            **(
                {"id": load["execution_variable_id"]}
                if "execution_variable_id" in load
                else {}
            ),
            "name": load["exposes_variable"],
            "data_type": "uuid",
            "scope": load["scope"],
        }
    if load.get("automation_trigger_config"):
        body["automation_trigger_config"] = load["automation_trigger_config"]
    return body


def apply_configure_flow(plan: dict[str, Any]) -> dict[str, Any]:
    """Save the execution variables and load steps the plan resolved.

    The writes run so that none of them removes a rule the spec keeps:

    1. the variables, declared ones plus any the plan drops, by id where live;
    2. the load steps, in rounds, because a step that populates a relationship
       field references a variable that only exists once the *earlier* step has
       been saved and the server has assigned it a uuid. ``flow.loads`` is a set
       on the server, so every round carries every live step: the ready ones in
       their spec version, the rest as last read. Steps the spec no longer lists
       are left out of the final round only;
    3. if the plan drops variables, the declared set on its own.

    Kizen rejects a ``flow`` whose load steps repeat an ``order``, so every
    round gives each of the spec's steps its spec order, whichever form it goes
    in, and puts the steps being deleted after them.

    Each PATCH is all-or-nothing on the server, but the sequence isn't. A failure
    after the first write raises :class:`PartialSaveError` describing the state
    the connector was left in.
    """
    config = load_env_config()
    connector = plan["connector"]
    loads = plan["loads"]
    rounds = plan["rounds"]
    delete_ids = {load["id"] for load in plan["deleted_loads"]}
    load_ids = {i: load["id"] for i, load in enumerate(loads) if "id" in load}
    after_spec = max(load["order"] for load in loads) + 1
    done: list[str] = []
    step = ""
    with KizenClient(config) as client:
        try:
            if plan["execution_variables"]:
                step = "the execution-variables write"
                sc_api.update_smart_connector(
                    client,
                    connector,
                    {
                        "execution_variables": plan["execution_variables"]
                        + plan["dropped_variable_rows"]
                    },
                )
                done.append(step)
            step = "re-reading the connector"
            detail = sc_api.get_smart_connector(client, connector)
            known = _variable_ids(detail)

            for number, indices in enumerate(rounds, start=1):
                step = f"flow round {number} of {len(rounds)}"
                unresolved = sorted(
                    {
                        ref
                        for i in indices
                        for ref in _load_refs(loads[i])
                        if ref not in known
                    }
                )
                if unresolved:
                    raise PlanError(
                        f"stuck: no remaining load step can be saved because these "
                        f"variables don't exist: {unresolved}"
                    )
                replaced = {load_ids[i] for i in indices if i in load_ids}
                spec_order = {load_ids[i]: loads[i]["order"] for i in load_ids}
                final = number == len(rounds)
                live = _live_loads(detail)
                body = [_wire_load(loads[i], known) for i in indices]
                extra = after_spec
                for load in sorted(live, key=lambda d: d.get("order") or 0):
                    load_id = load.get("id")
                    if load_id in replaced or (final and load_id in delete_ids):
                        continue
                    row = {k: v for k, v in load.items() if k in _LOAD_WIRE_KEYS}
                    if load_id in spec_order:
                        row["order"] = spec_order[load_id]
                    else:
                        row["order"], extra = extra, extra + 1
                    body.append(row)
                body.sort(key=lambda d: d["order"])
                sc_api.update_smart_connector(
                    client,
                    connector,
                    {"flow": {**plan["existing_flow"], "loads": body}},
                )
                done.append(step)
                step = f"re-reading the connector after {step}"
                before = {load.get("id") for load in live}
                detail = sc_api.get_smart_connector(client, connector)
                known = _variable_ids(detail)
                created = [i for i in indices if i not in load_ids]
                new = [
                    load for load in _live_loads(detail) if load.get("id") not in before
                ]
                for pos, load in _pair_loads(
                    [
                        (
                            (loads[i]["custom_object"], loads[i]["scope"]),
                            loads[i]["order"],
                        )
                        for i in created
                    ],
                    new,
                ).items():
                    load_ids[created[pos]] = load["id"]

            if plan["dropped_variable_rows"]:
                step = (
                    "the final execution-variables write (dropping "
                    + ", ".join(plan["dropped_variables"])
                    + ")"
                )
                live_ids = {
                    v["name"]: v["id"]
                    for v in detail.get("execution_variables") or []
                    if v.get("name") and v.get("id")
                }
                sc_api.update_smart_connector(
                    client,
                    connector,
                    {
                        "execution_variables": [
                            {**row, "id": live_ids[row["name"]]}
                            if row["name"] in live_ids
                            else row
                            for row in plan["execution_variables"]
                        ]
                    },
                )
                done.append(step)
        except (KizenAPIError, PlanError) as e:
            if not done:
                raise
            raise PartialSaveError(
                _partial_report(plan, client, e, step, done, load_ids)
            ) from e

    return {
        "connector": plan["connector_api_name"],
        "variables_saved": len(plan["execution_variables"]),
        "loads_saved": len(_live_loads(detail)),
        "loads_deleted": len(delete_ids),
        "rounds": len(rounds),
        "exposed_variables": {
            load["exposes_variable"]: known.get(load["exposes_variable"])
            for load in loads
            if load.get("exposes_variable")
        },
    }


def _in_spec_state(
    live: dict[str, Any], load: dict[str, Any], known: dict[str, str]
) -> bool:
    """Whether a live load step already has everything the spec sends for it.

    Rules are compared as multisets, and only on the keys the CLI sends: the
    server fills in the rest and doesn't keep field mappings in order.
    """
    if any(ref not in known for ref in _load_refs(load)):
        return False
    wire = _wire_load(load, known)
    for key in ("matching_rules", "field_mapping_rules"):
        unmatched = list(live.get(key) or [])
        for rule in wire[key]:
            same = next(
                (r for r in unmatched if all(r.get(k) == v for k, v in rule.items())),
                None,
            )
            if same is None:
                return False
            unmatched.remove(same)
        if unmatched:
            return False
    exposed = wire.get("execution_variable")
    if (
        exposed
        and (live.get("execution_variable") or {}).get("name") != exposed["name"]
    ):
        return False
    trigger = wire.get("automation_trigger_config")
    return trigger is None or live.get("automation_trigger_config") == trigger


def _partial_report(
    plan: dict[str, Any],
    client: KizenClient,
    error: Exception,
    step: str,
    done: list[str],
    load_ids: dict[int, str],
) -> dict[str, Any]:
    """Re-read the connector after a failed write and describe each load step.

    Every state comes from the re-read, not from which writes returned: a write
    that timed out may still have landed. If the re-read fails, every state is
    ``unknown``.
    """
    reread_error = None
    try:
        now = sc_api.get_smart_connector(client, plan["connector"])
    except KizenAPIError as e:
        now, reread_error = None, str(e)
    live = {load.get("id"): load for load in _live_loads(now or {})}
    known = _variable_ids(now or {})

    # A create whose write landed without the client seeing the response has no
    # recorded id yet: it is whichever new live step pairs with it.
    ids = dict(load_ids)
    missing = [i for i in range(len(plan["loads"])) if i not in ids]
    seen = set(ids.values()) | {load["id"] for load in plan["deleted_loads"]}
    for pos, found in _pair_loads(
        [
            (
                (plan["loads"][i]["custom_object"], plan["loads"][i]["scope"]),
                plan["loads"][i]["order"],
            )
            for i in missing
        ],
        [load for load_id, load in live.items() if load_id not in seen],
    ).items():
        ids[missing[pos]] = found["id"]

    rows: list[dict[str, Any]] = []
    for index, load in enumerate(plan["loads"]):
        current = live.get(ids.get(index))
        if now is None:
            state = "unknown"
        elif current is None:
            state = "missing" if "id" in load else "not created yet"
        elif _in_spec_state(current, load, known):
            state = "updated" if "id" in load else "created"
        else:
            state = "previous config" if "id" in load else "created, differs from spec"
        rows.append(
            {
                "load": load["object_label"],
                "order": load["order"],
                "state": state,
                "before": load.get("live_counts"),
                "now": _rule_counts(current) if current else None,
                "spec": {
                    "matching": len(load["matching_rules"]),
                    "mapping": len(load["field_mapping_rules"]),
                },
            }
        )
    for load in plan["deleted_loads"]:
        current = live.get(load["id"])
        if now is None:
            state = "unknown"
        else:
            state = "not deleted yet" if current else "deleted"
        rows.append(
            {
                "load": load["object_label"],
                "order": load["order"],
                "state": state,
                "before": load["live_counts"],
                "now": _rule_counts(current) if current else None,
                "spec": None,
            }
        )
    status = (now or {}).get("status")
    return {
        "connector": plan["connector_api_name"],
        "failed_write": step,
        "error": str(error),
        "completed_writes": done,
        "status": status,
        "live": status == "operational",
        "reread_error": reread_error,
        "loads": rows,
    }

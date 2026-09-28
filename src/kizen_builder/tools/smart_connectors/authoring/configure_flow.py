"""Authoring: ``configure-flow`` — save execution variables and load steps
from a spec. Object and field names resolve at plan time; variable references
are resolved round by round at apply time, since a load step's exposed
variable doesn't exist until that step is saved.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
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
                f"{scopes[scope]}. These are the columns Kizen last recorded, from "
                f"the template or the last publish — if the draft selects "
                f"'{source}' now, publish it first: `smart-connectors push --publish`."
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
                f"won't appear in `executions list --json`, only in the run's "
                f"Excel report (`executions download <connector> <id>`). Set "
                f"output_format explicitly (e.g. '%Y-%m-%d') if the target "
                f"field is a native date/datetime type."
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
                f"so nothing can be mapped. `set-input` fills them from the "
                f"template and `push --publish` refreshes them (Kizen validates "
                f"every variable's scope against them)"
            )

        live_vars = _variable_ids(detail)

        # --- execution variables ------------------------------------------
        variables_payload, date_format_warnings = _resolve_execution_variables(
            flow_def.execution_variables, scopes
        )

        # A PATCH replaces the data-source variable set wholesale, so anything
        # live but not re-declared disappears (load steps' own exposed variables
        # live on the load step, not here, and are untouched).
        declared = {v.name for v in flow_def.execution_variables}
        dropped = (
            [
                v.get("name")
                for v in detail.get("execution_variables") or []
                if v.get("name") and v["name"] not in declared
            ]
            if variables_payload
            else []
        )

        # --- load steps ---------------------------------------------------
        provided: set[str] = set(live_vars) | declared
        exposed_by: dict[str, int] = {}
        for index, load in enumerate(flow_def.loads):
            if load.exposes_variable:
                exposed_by[load.exposes_variable] = index
                provided.add(load.exposes_variable)

        by_api, by_id = _object_lookup(client)
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

    existing_flow = {
        k: v for k, v in (detail.get("flow") or {}).items() if k != "loads"
    }
    deferred = [
        load["object_label"]
        for load in resolved_loads
        if any(ref in exposed_by for ref in _load_refs(load))
    ]
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "scopes": {scope: len(cols) for scope, cols in scopes.items()},
        "execution_variables": variables_payload,
        "dropped_variables": dropped,
        "date_format_warnings": date_format_warnings,
        "loads": resolved_loads,
        "existing_loads": len((detail.get("flow") or {}).get("loads") or []),
        "existing_flow": existing_flow,
        "deferred_loads": deferred,
    }


def _wire_load(load: dict[str, Any], known: dict[str, str]) -> dict[str, Any]:
    """Turn a resolved load step into the wire body, variable names → uuids.

    The asymmetry is Kizen's, not ours: a matching rule takes a single
    ``variable``, a field mapping takes a plural ``variables`` list.
    """
    body: dict[str, Any] = {
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
        body["execution_variable"] = {
            "name": load["exposes_variable"],
            "data_type": "uuid",
            "scope": load["scope"],
        }
    if load.get("automation_trigger_config"):
        body["automation_trigger_config"] = load["automation_trigger_config"]
    return body


def apply_configure_flow(plan: dict[str, Any]) -> dict[str, Any]:
    """Save the execution variables and load steps the plan resolved.

    Load steps go up in rounds rather than one PATCH, because a step that
    populates a relationship field references a variable that only exists once
    the *earlier* step has been saved and the server has assigned it a uuid.
    Each round re-sends the already-saved steps exactly as the server returned
    them, ids included, so nothing is recreated and the uuids handed out in
    round N stay valid in round N+1.
    """
    config = load_env_config()
    connector = plan["connector"]
    with KizenClient(config) as client:
        if plan["execution_variables"]:
            sc_api.update_smart_connector(
                client, connector, {"execution_variables": plan["execution_variables"]}
            )
        detail = sc_api.get_smart_connector(client, connector)
        known = _variable_ids(detail)

        saved: list[dict[str, Any]] = []
        remaining = list(plan["loads"])
        rounds = 0
        while remaining:
            ready = [
                load
                for load in remaining
                if all(ref in known for ref in _load_refs(load))
            ]
            if not ready:
                unresolved = sorted(
                    {
                        ref
                        for load in remaining
                        for ref in _load_refs(load)
                        if ref not in known
                    }
                )
                raise PlanError(
                    f"stuck: no remaining load step can be saved because these "
                    f"variables don't exist: {unresolved}"
                )
            body = saved + [_wire_load(load, known) for load in ready]
            body.sort(key=lambda d: d.get("order") or 0)
            sc_api.update_smart_connector(
                client, connector, {"flow": {**plan["existing_flow"], "loads": body}}
            )
            detail = sc_api.get_smart_connector(client, connector)
            saved = [
                {k: v for k, v in load.items() if k in _LOAD_WIRE_KEYS}
                for load in ((detail.get("flow") or {}).get("loads") or [])
            ]
            known = _variable_ids(detail)
            remaining = [load for load in remaining if load not in ready]
            rounds += 1

    return {
        "connector": plan["connector_api_name"],
        "variables_saved": len(plan["execution_variables"]),
        "loads_saved": len(saved),
        "rounds": rounds,
        "exposed_variables": {
            load["exposes_variable"]: known.get(load["exposes_variable"])
            for load in plan["loads"]
            if load.get("exposes_variable")
        },
    }

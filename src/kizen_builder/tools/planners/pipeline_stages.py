"""Plan creation/update/removal of pipeline stages, and record stage-moves.

Stages are a sub-resource of a pipeline object (``/api/pipelines/{id}/stages``)
carrying attributes — ``status``, ``percentage_chance_to_close``, ``order`` —
that have no equivalent on the object's mirrored ``stage`` field. See
`kizen docs show reference` ("Pipeline stages") for why they can't be managed through
``fields options``.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from kizen_builder.utils import match_option

_STAGE_STATUSES = {"open", "won", "lost", "disqualified"}


def _require_pipeline(identifier: str) -> dict[str, Any]:
    try:
        obj = get_object(identifier)
    except LookupError as e:
        raise PlanError(f"object '{identifier}' not found: {e}") from e
    if obj.get("object_type") != "pipeline":
        raise PlanError(
            f"'{identifier}' is not a pipeline object (object_type="
            f"{obj.get('object_type')!r}) — stages only exist on pipeline objects."
        )
    return obj


def _match_stage(obj: dict[str, Any], token: str) -> dict[str, Any]:
    stages = obj.get("stages") or []
    match = match_option(stages, token)
    if match is not None:
        return match
    available = [s.get("name") for s in stages]
    raise PlanError(
        f"stage '{token}' not found on '{obj.get('api_name')}'. Available: {available}"
    )


def plan_create_stage(
    pipeline: str,
    name: str,
    *,
    status: str = "open",
    percentage_chance_to_close: int | None = None,
    order: int | None = None,
) -> Plan:
    """Plan creation of one stage on an existing pipeline object."""
    env = load_env_config().name
    obj = _require_pipeline(pipeline)
    if status not in _STAGE_STATUSES:
        raise PlanError(
            f"status must be one of {sorted(_STAGE_STATUSES)}, got {status!r}"
        )

    stages = obj.get("stages") or []
    if name.lower() in {(s.get("name") or "").lower() for s in stages}:
        raise PlanError(
            f"stage '{name}' already exists on '{obj.get('api_name')}'. "
            "Use plan_update_stage instead."
        )

    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "order": order if order is not None else len(stages),
    }
    if percentage_chance_to_close is not None:
        payload["percentage_chance_to_close"] = percentage_chance_to_close

    op = PlanOperation(
        action="create",
        kind="stage",
        key=f"{obj.get('api_name')}.stage:{name}",
        preview={
            "env": env,
            "pipeline": obj.get("api_name"),
            "name": name,
            "status": status,
            "order": payload["order"],
        },
        payload=payload,
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Create stage '{name}' on {obj.get('api_name')}",
        operations=[op],
    )


def plan_update_stage(pipeline: str, stage: str, changes: dict[str, Any]) -> Plan:
    """Plan an update to one stage on an existing pipeline object."""
    env = load_env_config().name
    obj = _require_pipeline(pipeline)
    existing = _match_stage(obj, stage)

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}

    if "name" in changes and changes["name"] != existing.get("name"):
        payload["name"] = changes["name"]
        diff["name"] = (existing.get("name"), changes["name"])
    if "status" in changes and changes["status"] != existing.get("status"):
        if changes["status"] not in _STAGE_STATUSES:
            raise PlanError(
                f"status must be one of {sorted(_STAGE_STATUSES)}, got {changes['status']!r}"
            )
        payload["status"] = changes["status"]
        diff["status"] = (existing.get("status"), changes["status"])
    if "percentage_chance_to_close" in changes and changes[
        "percentage_chance_to_close"
    ] != existing.get("percentage_chance_to_close"):
        payload["percentage_chance_to_close"] = changes["percentage_chance_to_close"]
        diff["percentage_chance_to_close"] = (
            existing.get("percentage_chance_to_close"),
            changes["percentage_chance_to_close"],
        )
    if "order" in changes and changes["order"] != existing.get("order"):
        payload["order"] = changes["order"]
        diff["order"] = (existing.get("order"), changes["order"])

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="stage",
        key=f"{obj.get('api_name')}.stage:{existing.get('name')}",
        preview={
            "env": env,
            "pipeline": obj.get("api_name"),
            "stage": existing.get("name"),
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=obj["id"],
    )
    summary = (
        f"Update stage '{existing.get('name')}' on {obj.get('api_name')} ({len(diff)} change(s))"
        if diff
        else f"No changes to stage '{existing.get('name')}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_remove_stage(pipeline: str, stage: str, move_to: str) -> Plan:
    """Plan removal of one stage, migrating its records onto ``move_to``.

    ``move_to`` is required — Kizen's remove-stage endpoint always migrates
    records off the removed stage onto another one.
    """
    if not move_to:
        raise PlanError(
            "--move-to is required: removing a stage migrates its records to another stage."
        )
    env = load_env_config().name
    obj = _require_pipeline(pipeline)
    target = _match_stage(obj, stage)
    destination = _match_stage(obj, move_to)
    if destination["id"] == target["id"]:
        raise PlanError(
            "--move-to must be a different stage than the one being removed."
        )

    op = PlanOperation(
        action="delete",
        kind="stage",
        key=f"{obj.get('api_name')}.stage:{target.get('name')}",
        preview={
            "env": env,
            "pipeline": obj.get("api_name"),
            "stage": target.get("name"),
            "records_move_to": destination.get("name"),
        },
        payload={"new_stage_id": destination["id"]},
        existing_uuid=target["id"],
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=(
            f"Remove stage '{target.get('name')}' from {obj.get('api_name')} "
            f"(records → '{destination.get('name')}')"
        ),
        operations=[op],
    )


def plan_move_record(object_api_name: str, record_id: str, stage: str) -> Plan:
    """Plan moving one record to a different stage of its pipeline object."""
    env = load_env_config().name
    obj = _require_pipeline(object_api_name)
    target = _match_stage(obj, stage)

    op = PlanOperation(
        action="update",
        kind="record_move",
        key=f"{object_api_name}#{record_id}.move",
        preview={
            "env": env,
            "object": object_api_name,
            "record": record_id,
            "to_stage": target.get("name"),
        },
        payload={"stage_id": target["id"]},
        existing_uuid=record_id,
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Move record {record_id} on {object_api_name} to stage '{target.get('name')}'",
        operations=[op],
    )

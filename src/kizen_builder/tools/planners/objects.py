"""Plan creation/update for custom objects and field categories."""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import FieldCategory, ObjectDef, PipelineStageSpec
from kizen_builder.tools.objects import get_object, list_objects
from kizen_builder.tools.planners.records import archive_operations
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from kizen_builder.tools.records import active_record_ids
from kizen_builder.utils import derived_object_api_name

# Seconds `objects delete` waits for its bulk archive to clear search before
# the object DELETE. Search reflects an archive well under 2 s later
# (docs/specs/records.md), so this only bounds a stuck job.
ARCHIVE_SETTLE_TIMEOUT = 300


class ApiNameMismatch(PlanError):
    """An asserted object api_name that Kizen won't derive from the name."""


def plan_create_object(obj: dict[str, Any] | ObjectDef) -> Plan:
    """Plan the creation of one custom object (no categories or fields yet).

    Kizen derives the api_name from the display name and refuses a supplied
    one (403), so ``api_name`` is only an assertion: it must equal the
    derived value, and the plan is keyed on the derived value.
    """
    obj_def = obj if isinstance(obj, ObjectDef) else ObjectDef.model_validate(obj)
    env = load_env_config().name

    live = [o for o in list_objects() if not o.get("deleted")]
    # Before the mismatch check: a stored name can carry a collision suffix
    # (`employee_m7SZCzg3`) that no display name derives.
    existing = next((o for o in live if o["api_name"] == obj_def.api_name), None)
    if existing is not None:
        raise PlanError(
            f"object '{obj_def.api_name}' already exists (uuid {existing['id']}). "
            "Use `objects update` to change it."
        )

    derived = derived_object_api_name(obj_def.name)
    if obj_def.api_name is not None and obj_def.api_name != derived:
        msg = (
            "Kizen derives the object api_name from the display name and "
            f"refuses a supplied one: '{obj_def.name}' becomes '{derived}', "
            f"not '{obj_def.api_name}'."
        )
        # The suggestion's slug is the asked api_name, which isn't live
        # (checked above), so Kizen won't suffix it.
        suggestion = obj_def.api_name.replace("_", " ").title()
        if derived_object_api_name(suggestion) == obj_def.api_name:
            raise ApiNameMismatch(
                f"{msg} For '{obj_def.api_name}', use --name \"{suggestion}\"."
            )
        raise ApiNameMismatch(f"{msg} No display name produces '{obj_def.api_name}'.")

    same_name = next((o for o in live if o.get("display_name") == obj_def.name), None)
    if same_name is not None:
        raise PlanError(
            f"an object named '{obj_def.name}' already exists (api_name "
            f"'{same_name['api_name']}', uuid {same_name['id']}), and Kizen "
            "requires unique object names. Use `objects update` to change it."
        )

    preview: dict[str, Any] = {
        "env": env,
        "api_name (derived)": derived,
        "object_name": obj_def.name,
        "entity_name": obj_def.effective_entity_name,
    }
    if any(o["api_name"] == derived for o in live):
        preview["warning"] = (
            f"an object already holds api_name '{derived}', so Kizen will add "
            "a random suffix to this one"
        )

    op = PlanOperation(
        action="create",
        kind="object",
        key=derived,
        preview=preview,
        payload=_build_object_payload(obj_def),
    )
    return Plan.build(
        env=env,
        summary=f"Create object '{derived}'",
        operations=[op],
    )


def plan_create_category(
    object_api_name: str,
    category: dict[str, Any] | FieldCategory,
) -> Plan:
    """Plan the creation of one field category on an existing object."""
    cat_def = (
        category
        if isinstance(category, FieldCategory)
        else FieldCategory.model_validate(category)
    )
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    name = cat_def.name
    existing = next((c for c in obj["categories"] if c["name"] == name), None)
    if existing is not None:
        raise PlanError(
            f"category '{name}' already exists on object '{object_api_name}' "
            f"(uuid {existing['id']}). Use plan_update_category to modify it."
        )

    payload: dict[str, Any] = {"name": cat_def.name}

    op = PlanOperation(
        action="create",
        kind="category",
        key=f"{object_api_name}.{cat_def.api_name}",
        preview={"env": env, "object": object_api_name, "name": cat_def.name},
        payload=payload,
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Create category '{cat_def.name}' on {object_api_name}",
        operations=[op],
    )


def plan_delete_object(object_api_name: str) -> Plan:
    """Plan deleting one custom object, archiving its active records first.

    Kizen refuses to delete an object that still has active records (400,
    confirmed live 2026-09-28), so the plan archives them in
    ``ARCHIVE_CHUNK``-id requests with email off, then deletes the object
    once search reads 0.
    """
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e
    if not obj["is_custom"]:
        raise PlanError(
            f"'{object_api_name}' is a built-in Kizen object. objects delete "
            "only deletes custom objects, so nothing was planned and no "
            "records were touched"
        )

    ids = active_record_ids(object_api_name)
    archive_ops = archive_operations(env, object_api_name, obj["id"], ids)
    warning = (
        "Kizen refuses to delete an object with active records, so this plan "
        f"archives its {len(ids)} active record(s) first, with no email"
        if ids
        else "deletes the object; it has no active records"
    )

    op = PlanOperation(
        action="delete",
        kind="object",
        key=object_api_name,
        preview={"env": env, "api_name": object_api_name, "warning": warning},
        payload={"wait_for_archive_seconds": ARCHIVE_SETTLE_TIMEOUT} if ids else {},
        existing_uuid=obj["id"],
    )
    summary = f"Delete object '{object_api_name}'"
    if ids:
        summary = (
            f"Archive {len(ids)} record(s) from {object_api_name} in "
            f"{len(archive_ops)} request(s), then delete object '{object_api_name}'"
        )
    return Plan.build(env=env, summary=summary, operations=[*archive_ops, op])


def plan_delete_category(object_api_name: str, category_name: str) -> Plan:
    """Plan deletion of one field category on an existing object."""
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    cat = next((c for c in obj["categories"] if c["name"] == category_name), None)
    if cat is None:
        available = [c["name"] for c in obj["categories"]]
        raise PlanError(
            f"category '{category_name}' not found on '{object_api_name}'. "
            f"Available: {available}"
        )

    op = PlanOperation(
        action="delete",
        kind="category",
        key=f"{object_api_name}.{category_name}",
        preview={
            "env": env,
            "object": object_api_name,
            "category": category_name,
            "warning": "removes the category",
        },
        existing_uuid=cat["id"],
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete category '{category_name}' from {object_api_name}",
        operations=[op],
    )


def plan_update_object(object_api_name: str, changes: dict[str, Any]) -> Plan:
    """Plan an update to one custom object."""
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    raw = obj.get("raw") or {}
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}

    if "object_name" in changes and changes["object_name"] != raw.get("object_name"):
        payload["object_name"] = changes["object_name"]
        diff["object_name"] = (raw.get("object_name"), changes["object_name"])
    if "entity_name" in changes and changes["entity_name"] != raw.get("entity_name"):
        payload["entity_name"] = changes["entity_name"]
        diff["entity_name"] = (raw.get("entity_name"), changes["entity_name"])
    if "description" in changes and changes["description"] != (
        raw.get("description") or ""
    ):
        payload["description"] = changes["description"]
        diff["description"] = (raw.get("description"), changes["description"])
    if "default_on_activities" in changes and (
        changes["default_on_activities"] != raw.get("default_on_activities")
    ):
        payload["default_on_activities"] = changes["default_on_activities"]
        diff["default_on_activities"] = (
            raw.get("default_on_activities"),
            changes["default_on_activities"],
        )

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="object",
        key=object_api_name,
        preview={
            "env": env,
            "api_name": object_api_name,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=obj["id"],
    )
    summary = (
        f"Update object '{object_api_name}' ({len(diff)} change(s))"
        if diff
        else f"No changes to object '{object_api_name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_update_category(
    object_api_name: str,
    category_name: str,
    changes: dict[str, Any],
) -> Plan:
    """Plan an update to one field category on an existing object."""
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    cat = next((c for c in obj["categories"] if c["name"] == category_name), None)
    if cat is None:
        available = [c["name"] for c in obj["categories"]]
        raise PlanError(
            f"category '{category_name}' not found on '{object_api_name}'. "
            f"Available: {available}"
        )

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != cat["name"]:
        payload["name"] = changes["name"]
        diff["name"] = (cat["name"], changes["name"])

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="category",
        key=f"{object_api_name}.{category_name}",
        preview={
            "env": env,
            "object": object_api_name,
            "category": category_name,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=cat["id"],
        parent_object_uuid=obj["id"],
    )
    summary = (
        f"Update category '{category_name}' on {object_api_name}"
        if diff
        else f"No changes to category '{category_name}' on {object_api_name}"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


# ---------------------------------------------------------------------------
# Object payload builder
# ---------------------------------------------------------------------------


def _build_object_payload(obj: ObjectDef) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "object_name": obj.name,
        "entity_name": obj.effective_entity_name,
        "object_type": obj.object_type,
        "default_on_activities": obj.default_on_activities,
    }
    if obj.description:
        payload["description"] = obj.description
    if obj.object_type == "pipeline":
        payload["pipeline"] = {"stages": _build_pipeline_stages(obj)}
    return payload


def _build_pipeline_stages(obj: ObjectDef) -> list[dict[str, Any]]:
    """Live API requires a non-empty `pipeline.stages` list even though the
    OpenAPI spec doesn't mark `pipeline` required. Default a single
    placeholder stage when none are given — `objects stages create/update`
    layers on the real ones afterward."""
    stages = obj.pipeline.stages if obj.pipeline else []
    if not stages:
        stages = [PipelineStageSpec(name="Open", status="open", order=0)]
    return [
        {
            "name": s.name,
            "status": s.status,
            "order": s.order if s.order is not None else i,
            **(
                {"percentage_chance_to_close": s.percentage_chance_to_close}
                if s.percentage_chance_to_close is not None
                else {}
            ),
        }
        for i, s in enumerate(stages)
    ]

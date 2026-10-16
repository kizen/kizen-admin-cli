"""Plan creation/update for fields on a custom object."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import FieldDef
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation

_CARDINALITY_TO_WIRE_RELATION_TYPE = {
    "one_to_one": "one_to_one",
    "many_to_one": "primary",
    "one_to_many": "primary_for",
    "many_to_many": "additional",
}


def _resolve_relation_type(value: str) -> str:
    """Translate a clear cardinality name to Kizen's wire relation_type.

    Raw wire values (e.g. `additional_for`) pass through unchanged, so specs
    built from live API output keep working.
    """
    return _CARDINALITY_TO_WIRE_RELATION_TYPE.get(value, value)


def plan_create_field(
    object_api_name: str,
    field: dict[str, Any] | FieldDef,
    category: str | None = None,
) -> Plan:
    """Plan the creation of one field on an existing custom object."""
    field_def = field if isinstance(field, FieldDef) else FieldDef.model_validate(field)
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    op = _build_create_field_op(obj, object_api_name, field_def, category, env)

    return Plan.build(
        env=env,
        summary=(
            f"Create field '{field_def.api_name}' ({field_def.field_type}) "
            f"on {object_api_name}"
        ),
        operations=[op],
    )


def plan_create_fields(
    object_api_name: str,
    fields: Sequence[tuple[dict[str, Any] | FieldDef, str | None]],
) -> Plan:
    """Plan the creation of many fields on one object in a single plan.

    ``fields`` is a list of ``(field, category)`` pairs — one plan, one
    confirm, one apply for the whole batch (vs. one round-trip per field).
    The object is fetched once and every field is validated against that same
    live snapshot, including cross-batch duplicate-api_name detection.
    """
    if not fields:
        raise PlanError("no fields provided to create")

    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    seen: set[str] = set()
    operations: list[PlanOperation] = []
    for field, category in fields:
        field_def = (
            field if isinstance(field, FieldDef) else FieldDef.model_validate(field)
        )
        if field_def.api_name in seen:
            raise PlanError(
                f"duplicate field api_name '{field_def.api_name}' in the batch — "
                "each field must have a unique api_name."
            )
        seen.add(field_def.api_name)
        operations.append(
            _build_create_field_op(obj, object_api_name, field_def, category, env)
        )

    return Plan.build(
        env=env,
        summary=f"Create {len(operations)} field(s) on {object_api_name}",
        operations=operations,
    )


def _build_create_field_op(
    obj: dict[str, Any],
    object_api_name: str,
    field_def: FieldDef,
    category: str | None,
    env: str,
) -> PlanOperation:
    """Validate one field against live state and build its create operation."""
    existing_field = next(
        (
            f
            for f in obj["fields"]
            if f["api_name"] == field_def.api_name and not f["deleted"]
        ),
        None,
    )
    if existing_field is not None:
        raise PlanError(
            f"field '{field_def.api_name}' already exists on object "
            f"'{object_api_name}' (uuid {existing_field['id']}). "
            "Use plan_update_field if you want to change it."
        )

    if not category:
        raise PlanError(
            f"category is required for field '{field_def.api_name}' "
            "(display name of the target category, e.g. 'Condition Info')"
        )
    cat_match = next((c for c in obj["categories"] if c["name"] == category), None)
    if cat_match is None:
        available = [c["name"] for c in obj["categories"]]
        raise PlanError(
            f"category '{category}' not found on '{object_api_name}'. "
            f"Available: {available}"
        )

    resolved_relation: dict[str, Any] | None = None
    if field_def.relation is not None:
        try:
            rel_obj = get_object(field_def.relation.target_object)
        except LookupError as e:
            raise PlanError(
                f"relation target object '{field_def.relation.target_object}' not found: {e}"
            ) from e
        if field_def.relation.target_category:
            rel_cat = next(
                (
                    c
                    for c in rel_obj["categories"]
                    if c["name"] == field_def.relation.target_category
                ),
                None,
            )
            if rel_cat is None:
                available = [c["name"] for c in rel_obj["categories"]]
                raise PlanError(
                    f"target_category '{field_def.relation.target_category}' not found "
                    f"on '{field_def.relation.target_object}'. Available: {available}"
                )
            rel_cat_id = rel_cat["id"]
        else:
            rel_cats = rel_obj.get("categories", [])
            rel_cat_id = rel_cats[0]["id"] if rel_cats else None
        resolved_relation = {
            "related_object": rel_obj["id"],
            "related_category": rel_cat_id,
            "relation_type": _resolve_relation_type(field_def.relation.relation_type),
            # Server-side 500s if this is omitted, despite being documented as
            # nullable — default to this object's label when not given explicitly.
            "related_name": field_def.relation.related_name
            or obj.get("entity_name")
            or obj["display_name"],
        }

    payload = _build_field_payload(
        field_def, category_uuid=cat_match["id"], resolved_relation=resolved_relation
    )

    return PlanOperation(
        action="create",
        kind="field",
        key=f"{object_api_name}.{field_def.api_name}",
        preview={
            "env": env,
            "object": object_api_name,
            "category": category,
            "api_name": field_def.api_name,
            "display_name": field_def.name,
            "field_type": field_def.field_type,
            "required": field_def.required,
        },
        payload=payload,
        parent_object_uuid=obj["id"],
    )


def plan_update_field(
    object_api_name: str,
    field_api_name: str,
    changes: dict[str, Any],
    category: str | None = None,
) -> Plan:
    """Plan an update to one field on an existing custom object."""
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = _find_field(obj, field_api_name)

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}

    if "name" in changes and changes["name"] != existing.get("display_name"):
        payload["display_name"] = changes["name"]
        diff["display_name"] = (existing.get("display_name"), changes["name"])
    if "description" in changes and changes["description"] != (
        existing.get("description") or ""
    ):
        payload["description"] = changes["description"]
        diff["description"] = (existing.get("description"), changes["description"])
    if "required" in changes and changes["required"] != existing.get("is_required"):
        payload["is_required"] = changes["required"]
        diff["is_required"] = (existing.get("is_required"), changes["required"])
    if "read_only" in changes and changes["read_only"] != existing.get("is_read_only"):
        payload["is_read_only"] = changes["read_only"]
        diff["is_read_only"] = (existing.get("is_read_only"), changes["read_only"])
    if "hidden" in changes and changes["hidden"] != existing.get("is_hidden"):
        payload["is_hidden"] = changes["hidden"]
        diff["is_hidden"] = (existing.get("is_hidden"), changes["hidden"])
    if "options" in changes:
        payload["options"] = [
            {"name": o, "code": o} for o in (changes["options"] or [])
        ]
        diff["options"] = ("…", changes["options"])
    for k in (
        "rating",
        "money_options",
        "decimal_options",
        "phone_options",
        "status_options",
    ):
        if k in changes:
            wire_key = "phonenumber_options" if k == "phone_options" else k
            payload[wire_key] = changes[k]
            diff[wire_key] = ("…", changes[k])

    if category and category != _category_name(obj, existing.get("category_id")):
        cat_match = next((c for c in obj["categories"] if c["name"] == category), None)
        if cat_match is None:
            available = [c["name"] for c in obj["categories"]]
            raise PlanError(
                f"category '{category}' not found on '{object_api_name}'. "
                f"Available: {available}"
            )
        payload["category"] = cat_match["id"]
        diff["category"] = (
            _category_name(obj, existing.get("category_id")),
            category,
        )

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind="field",
        key=f"{object_api_name}.{field_api_name}",
        preview={
            "env": env,
            "object": object_api_name,
            "field": field_api_name,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=obj["id"],
    )
    summary = (
        f"Update field '{field_api_name}' on {object_api_name} ({len(diff)} change(s))"
        if diff
        else f"No changes to field '{field_api_name}' on {object_api_name}"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


_OPTION_FIELD_TYPES = {
    "dropdown",
    "radio",
    "status",
    "choices",
    "selector",
    "checkboxes",
    "dynamictags",
    "yesnomaybe",
}


def _find_field(obj: dict[str, Any], field_api_name: str) -> dict[str, Any]:
    existing = next(
        (
            f
            for f in obj["fields"]
            if f["api_name"] == field_api_name and not f.get("deleted")
        ),
        None,
    )
    if existing is None:
        available = [f["api_name"] for f in obj["fields"] if not f.get("deleted")]
        raise PlanError(
            f"field '{field_api_name}' not found on '{obj['api_name']}'. "
            f"Available: {available}"
        )
    return existing


def plan_delete_field(object_api_name: str, field_api_name: str) -> Plan:
    """Plan deletion of one custom field (and its data across all records)."""
    env = load_env_config().name

    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = _find_field(obj, field_api_name)

    op = PlanOperation(
        action="delete",
        kind="field",
        key=f"{object_api_name}.{field_api_name}",
        preview={
            "env": env,
            "object": object_api_name,
            "field": field_api_name,
            "field_type": existing.get("field_type"),
        },
        existing_uuid=existing["id"],
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete field '{field_api_name}' from {object_api_name}",
        operations=[op],
    )


def _reject_if_stage_backed(
    obj: dict[str, Any],
    object_api_name: str,
    field_api_name: str,
    existing: dict[str, Any],
) -> None:
    """Refuse to touch a field whose options are a read-only stage mirror.

    On pipeline objects, the field that surfaces stage names as options is a
    projection of the pipeline's stages entity — POST/DELETE against its
    options is silently discarded server-side (it never reaches the field's
    own option list or the stages entity). Detected by exact id-set match
    against the live stages, so this holds regardless of the field's api_name.
    """
    if obj.get("object_type") != "pipeline":
        return
    stage_ids = {s.get("id") for s in (obj.get("stages") or [])}
    option_ids = {o.get("id") for o in (existing.get("options") or [])}
    if stage_ids and option_ids and option_ids == stage_ids:
        raise PlanError(
            f"'{field_api_name}' on pipeline '{object_api_name}' mirrors the pipeline's "
            "stages (a read-only projection) — adding/removing options here is silently "
            "discarded server-side. Use `kizen objects stages create/update/remove` instead."
        )


def plan_add_field_options(
    object_api_name: str, field_api_name: str, options: list[str]
) -> Plan:
    """Plan adding one or more options to an existing select-type field.

    Options whose name already exists are emitted as ``skip`` ops so a re-run
    is idempotent rather than erroring.
    """
    if not options:
        raise PlanError("no options provided to add")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = _find_field(obj, field_api_name)
    _reject_if_stage_backed(obj, object_api_name, field_api_name, existing)
    if existing.get("field_type") not in _OPTION_FIELD_TYPES:
        raise PlanError(
            f"field '{field_api_name}' is type '{existing.get('field_type')}', "
            "which has no options to add. Option fields are: "
            f"{sorted(_OPTION_FIELD_TYPES)}"
        )

    have = {(o.get("name") or "").lower() for o in (existing.get("options") or [])}
    operations: list[PlanOperation] = []
    for name in options:
        already = name.lower() in have
        operations.append(
            PlanOperation(
                action="skip" if already else "create",
                kind="field_option",
                key=f"{object_api_name}.{field_api_name}.option:{name}",
                preview={
                    "env": env,
                    "field": f"{object_api_name}.{field_api_name}",
                    "option": name,
                    "note": "already exists" if already else "add",
                },
                payload={"field_id": existing["id"], "name": name},
                parent_object_uuid=obj["id"],
            )
        )

    n_add = sum(1 for op in operations if op.action == "create")
    return Plan.build(
        env=env,
        summary=(
            f"Add {n_add} option(s) to {object_api_name}.{field_api_name}"
            + (
                ""
                if n_add == len(options)
                else f" ({len(options) - n_add} already present)"
            )
        ),
        operations=operations,
    )


def plan_remove_field_option(
    object_api_name: str,
    field_api_name: str,
    option: str,
    remap_to: str | None = None,
) -> Plan:
    """Plan removal of one option from a select-type field.

    ``option`` / ``remap_to`` are matched against option name (then code, then
    id). With ``remap_to`` set, records currently using the removed option are
    reassigned to it before the option is dropped; without it, those records
    lose the value.
    """
    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    existing = _find_field(obj, field_api_name)
    _reject_if_stage_backed(obj, object_api_name, field_api_name, existing)
    opts = existing.get("options") or []

    def _match(token: str) -> dict[str, Any]:
        for o in opts:
            if token in (o.get("id"), o.get("name"), o.get("code")) or (
                (o.get("name") or "").lower() == token.lower()
            ):
                return o
        available = [o.get("name") for o in opts]
        raise PlanError(
            f"option '{token}' not found on {object_api_name}.{field_api_name}. "
            f"Available: {available}"
        )

    target = _match(option)
    payload: dict[str, Any] = {"field_id": existing["id"]}
    remap_note = "dropped (records lose this value)"
    if remap_to:
        replacement = _match(remap_to)
        payload["remap_to"] = replacement["id"]
        remap_note = f"records remapped to '{replacement.get('name')}'"

    op = PlanOperation(
        action="delete",
        kind="field_option",
        key=f"{object_api_name}.{field_api_name}.option:{target.get('name')}",
        preview={
            "env": env,
            "field": f"{object_api_name}.{field_api_name}",
            "option": target.get("name"),
            "on_delete": remap_note,
        },
        payload=payload,
        existing_uuid=target["id"],
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Remove option '{target.get('name')}' from {object_api_name}.{field_api_name}",
        operations=[op],
    )


def _category_name(obj: dict[str, Any], cat_id: str | None) -> str | None:
    if not cat_id:
        return None
    match = next((c for c in obj["categories"] if c["id"] == cat_id), None)
    return match["name"] if match else None


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def _build_field_payload(
    field: FieldDef,
    *,
    category_uuid: str,
    resolved_relation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wire_field_type = "longtext" if field.field_type == "wysiwyg" else field.field_type

    payload: dict[str, Any] = {
        "name": field.api_name,
        "display_name": field.name,
        "field_type": wire_field_type,
        "category": category_uuid,
        "is_required": field.required,
        "is_read_only": field.read_only,
        "is_hidden": field.hidden,
    }
    if field.field_type == "wysiwyg":
        payload["meta"] = {"is_markdown": True}
    if field.description:
        payload["description"] = field.description

    effective_options = field.options
    if effective_options is None and field.field_type == "yesnomaybe":
        effective_options = ["Yes", "No", "Maybe"]
    if effective_options is not None:
        if field.field_type == "yesnomaybe":
            payload["options"] = [
                {"name": o, "code": o.lower()} for o in effective_options
            ]
        else:
            payload["options"] = [{"name": o, "code": o} for o in effective_options]

    if field.status_options is not None:
        payload["options"] = [
            {"name": s.name, "code": s.code or s.name.lower()}
            for s in field.status_options
        ]

    if resolved_relation is not None:
        payload["relation"] = resolved_relation
    elif field.relation is not None:
        payload["relation"] = field.relation.model_dump(exclude_none=True)

    if field.money_options is not None:
        payload["money_options"] = field.money_options.model_dump(exclude_none=True)

    if field.rating is not None:
        payload["rating"] = field.rating.model_dump(exclude_none=True)

    if field.decimal_options is not None:
        payload["decimal_options"] = field.decimal_options.model_dump(exclude_none=True)

    if field.phone_options is not None:
        payload["phonenumber_options"] = field.phone_options.model_dump(
            exclude_none=True
        )

    return payload

"""Plan creation/update/deletion of records (data, not schema).

Records are the rows inside a custom object (or a built-in type like
``client_client``). Unlike schema mutations these touch business data, but
they run through the same plan → preview → confirm → apply loop so a bulk
create/update is previewed and logged like everything else.

Field values authored as ``{api_name: value}`` are resolved against the live
object schema: option labels become option UUIDs, relationship ids become
``{"id": uuid}``, booleans/numbers are coerced from their string forms. A
record may instead carry a raw ``"fields"`` list (the wire shape accepted by
the records API) as an escape hatch for values the resolver doesn't cover.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation

# Field types whose value is one option chosen from a fixed set.
_SINGLE_SELECT = {"dropdown", "radio", "status", "choices", "selector", "yesnomaybe"}
# Field types whose value is a list of options.
_MULTI_SELECT = {"checkboxes", "dynamictags"}
_NUMERIC = {"integer", "decimal", "money", "rating"}
_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f", ""}


def _field_index(obj: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map api_name → field descriptor for the object's live (undeleted) fields."""
    return {
        f["api_name"]: f
        for f in obj["fields"]
        if f.get("api_name") and not f.get("deleted")
    }


def _resolve_option(field: dict[str, Any], value: Any) -> Any:
    """Resolve one option value to a wire form.

    A dict is passed through untouched (the caller already gave a wire ref).
    A string is matched against the field's option ``name`` then ``code``
    (case-insensitively); a hit becomes ``{"id": <option_uuid>}``. A miss
    falls back to ``{"name": value}`` so the server can still try to match by
    label rather than the tool rejecting a value it simply doesn't recognise.
    """
    if isinstance(value, dict):
        return value
    label = str(value)
    for opt in field.get("options") or []:
        if (opt.get("name") or "").lower() == label.lower() or (
            opt.get("code") or ""
        ).lower() == label.lower():
            return {"id": opt["id"]}
    return {"name": label}


def _resolve_value(field: dict[str, Any], value: Any) -> Any:
    """Coerce an authored field value into the shape the records API expects."""
    if value is None:
        return None
    ft = field.get("field_type")

    if ft in _SINGLE_SELECT:
        return _resolve_option(field, value)

    if ft in _MULTI_SELECT:
        items = value if isinstance(value, list) else [value]
        return [_resolve_option(field, v) for v in items]

    if ft == "relationship":

        def _rel(v: Any) -> Any:
            return v if isinstance(v, dict) else {"id": str(v)}

        return [_rel(v) for v in value] if isinstance(value, list) else _rel(value)

    if ft == "checkbox":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise PlanError(f"field '{field['api_name']}' expects a boolean, got {value!r}")

    if ft in _NUMERIC and isinstance(value, str):
        try:
            return int(value) if ft in ("integer", "rating") else float(value)
        except ValueError as e:
            raise PlanError(
                f"field '{field['api_name']}' expects a number, got {value!r}"
            ) from e

    return value


def _resolve_fields(
    obj: dict[str, Any], mapping: dict[str, Any]
) -> list[dict[str, Any]]:
    """Turn an ``{api_name: value}`` mapping into wire ``fields`` entries.

    A ``"fields"`` key holding a list is treated as a pre-built wire payload
    and passed through as-is (the raw escape hatch). ``"id"`` is reserved for
    the target record and never sent as a field.
    """
    if isinstance(mapping.get("fields"), list):
        return mapping["fields"]

    index = _field_index(obj)
    wire: list[dict[str, Any]] = []
    for api_name, value in mapping.items():
        if api_name == "id":
            continue
        field = index.get(api_name)
        if field is None:
            available = sorted(index)
            raise PlanError(
                f"field '{api_name}' not found on '{obj['api_name']}'. "
                f"Available: {available}"
            )
        wire.append({"name": api_name, "value": _resolve_value(field, value)})
    return wire


def _record_label(mapping: dict[str, Any]) -> str:
    """A short human tag for a record spec (its name field, if present)."""
    for key in ("name", "Name"):
        if mapping.get(key):
            return str(mapping[key])
    return "(record)"


def plan_create_records(object_api_name: str, records: list[dict[str, Any]]) -> Plan:
    """Plan the creation of one or more records on ``object_api_name``."""
    if not records:
        raise PlanError("no records provided to create")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    operations: list[PlanOperation] = []
    for i, rec in enumerate(records):
        fields = _resolve_fields(obj, rec)
        if not fields:
            raise PlanError(f"record #{i + 1} has no field values to set")
        operations.append(
            PlanOperation(
                action="create",
                kind="record",
                key=f"{object_api_name}#new-{i + 1}",
                preview={
                    "env": env,
                    "object": object_api_name,
                    "name": _record_label(rec),
                    "fields": len(fields),
                },
                payload={"fields": fields},
                parent_object_uuid=object_api_name,
            )
        )

    return Plan.build(
        env=env,
        summary=f"Create {len(operations)} record(s) on {object_api_name}",
        operations=operations,
    )


def plan_update_records(object_api_name: str, records: list[dict[str, Any]]) -> Plan:
    """Plan updates to existing records; each record dict must carry ``id``."""
    if not records:
        raise PlanError("no records provided to update")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    operations: list[PlanOperation] = []
    for i, rec in enumerate(records):
        record_id = rec.get("id")
        if not record_id:
            raise PlanError(
                f"record #{i + 1} has no 'id' — updates target an existing "
                "record by UUID (add an 'id' column/key)."
            )
        fields = _resolve_fields(obj, rec)
        if not fields:
            raise PlanError(f"record '{record_id}' has no field values to change")
        operations.append(
            PlanOperation(
                action="update",
                kind="record",
                key=f"{object_api_name}#{record_id}",
                preview={
                    "env": env,
                    "object": object_api_name,
                    "id": record_id,
                    "fields": len(fields),
                },
                payload={"fields": fields},
                existing_uuid=record_id,
                parent_object_uuid=object_api_name,
            )
        )

    return Plan.build(
        env=env,
        summary=f"Update {len(operations)} record(s) on {object_api_name}",
        operations=operations,
    )


def plan_upsert_records(
    object_api_name: str,
    records: list[dict[str, Any]],
    oncreate_unarchive: str | None = None,
    onupdate_archived_conflict: str | None = None,
) -> Plan:
    """Plan create-or-update of one or more records by ``lookup_value``.

    Each record dict must carry a ``lookup_value`` (the name field for
    custom objects, email for contacts) — the identifier Kizen matches an
    existing record against. ``oncreate_unarchive`` /
    ``onupdate_archived_conflict`` apply to every record in this call.
    """
    if not records:
        raise PlanError("no records provided to upsert")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    operations: list[PlanOperation] = []
    for i, rec in enumerate(records):
        lookup_value = rec.get("lookup_value")
        if not lookup_value:
            raise PlanError(
                f"record #{i + 1} has no 'lookup_value' — upsert matches an "
                "existing record by this value (add a 'lookup_value' column/key)."
            )
        fields = _resolve_fields(
            obj, {k: v for k, v in rec.items() if k != "lookup_value"}
        )
        if not fields:
            raise PlanError(f"record #{i + 1} has no field values to set")
        payload: dict[str, Any] = {"lookup_value": lookup_value, "fields": fields}
        if oncreate_unarchive is not None:
            payload["oncreate_unarchive"] = oncreate_unarchive
        if onupdate_archived_conflict is not None:
            payload["onupdate_archived_conflict"] = onupdate_archived_conflict
        operations.append(
            PlanOperation(
                action="upsert",
                kind="record",
                key=f"{object_api_name}#upsert-{i + 1}",
                preview={
                    "env": env,
                    "object": object_api_name,
                    "lookup_value": lookup_value,
                    "fields": len(fields),
                },
                payload=payload,
                parent_object_uuid=object_api_name,
            )
        )

    return Plan.build(
        env=env,
        summary=f"Upsert {len(operations)} record(s) on {object_api_name}",
        operations=operations,
    )


_FIELD_RESOLUTIONS = {
    "overwrite",
    "add_only",
    "remove_only",
    "update_if_blank",
    "overwrite_except_null",
}


def _unwrap_bulk_field_value(value: Any) -> Any:
    """``bulk-change-field-value``'s ``field_value`` wants the bare wire scalar —
    for select/relationship fields that's the option/record UUID string
    directly, not the ``{"id": ...}`` dict :func:`_resolve_value` normally
    produces for a record's own ``fields`` list. Confirmed live 2026-07-20."""
    if isinstance(value, dict) and "id" in value:
        return value["id"]
    if isinstance(value, list):
        return [_unwrap_bulk_field_value(v) for v in value]
    return value


def plan_set_field(
    object_api_name: str,
    record_ids: list[str],
    field_api_name: str,
    value: Any,
    field_resolution: str = "overwrite",
) -> Plan:
    """Plan setting one field to one value across many records in one call.

    Wraps ``POST /api/custom-objects/{id}/bulk-change-field-value`` — the
    id-targeted form (no server-side bulk-by-filter without the separate
    ``bulk-action-summary``/entity_records_set_key framework, which isn't
    wired up here yet).
    """
    if not record_ids:
        raise PlanError("no record ids provided")
    if field_resolution not in _FIELD_RESOLUTIONS:
        raise PlanError(
            f"invalid field_resolution {field_resolution!r}. "
            f"Valid: {sorted(_FIELD_RESOLUTIONS)}"
        )

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    index = _field_index(obj)
    field = index.get(field_api_name)
    if field is None:
        raise PlanError(
            f"field '{field_api_name}' not found on '{object_api_name}'. "
            f"Available: {sorted(index)}"
        )

    resolved = _unwrap_bulk_field_value(_resolve_value(field, value))
    payload: dict[str, Any] = {
        "record_ids": record_ids,
        "field_id": field["id"],
        "field_value": resolved,
        "field_resolution": field_resolution,
    }
    op = PlanOperation(
        action="update",
        kind="record_bulk_field_value",
        key=f"{object_api_name}.{field_api_name}#{len(record_ids)}-records",
        preview={
            "env": env,
            "object": object_api_name,
            "field": field_api_name,
            "value": resolved,
            "resolution": field_resolution,
            "record_count": len(record_ids),
        },
        payload=payload,
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Set '{field_api_name}' on {len(record_ids)} record(s) of {object_api_name}",
        operations=[op],
    )


# Ids per bulk-archive request. Unprobed beyond 3 ids in one call
# (confirmed live 2026-09-28); 500 is a chosen ceiling, not a server limit.
ARCHIVE_CHUNK = 500


def plan_archive_records(object_api_name: str, record_ids: list[str]) -> Plan:
    """Plan archiving records by UUID, ``ARCHIVE_CHUNK`` ids per request.

    Wraps `POST /api/custom-objects/{id}/bulk-archive-entity-record` — the
    operation the UI's Archive button performs. `object_uuid` — not the
    api_name — is what that endpoint's path takes, so this resolves the
    object the same way `plan_set_field` does. Each request writes one
    bulk-action-progress row, so one op per chunk rather than per id.
    """
    ids = list(dict.fromkeys(record_ids))
    if not ids:
        raise PlanError("no record ids provided to archive")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    chunks = [ids[i : i + ARCHIVE_CHUNK] for i in range(0, len(ids), ARCHIVE_CHUNK)]
    operations = [
        PlanOperation(
            action="update",
            kind="record_archive",
            key=(
                f"{object_api_name}#{chunk[0]}"
                if len(chunk) == 1
                else f"{object_api_name}#{chunk[0]}..{chunk[-1]}"
            ),
            preview={
                "env": env,
                "object": object_api_name,
                "record_count": len(chunk),
                "warning": (
                    "archives the records: they drop out of search/list "
                    "results and 404 on a direct read, but their data is "
                    "retained and they can be restored with 'records unarchive'."
                ),
            },
            payload={"record_ids": chunk, "send_email_notification": False},
            parent_object_uuid=obj["id"],
        )
        for chunk in chunks
    ]

    return Plan.build(
        env=env,
        summary=(
            f"Archive {len(ids)} record(s) from {object_api_name} "
            f"in {len(chunks)} request(s)"
        ),
        operations=operations,
    )


def plan_unarchive_records(object_api_name: str, record_ids: list[str]) -> Plan:
    """Plan unarchiving one or more records by UUID.

    Wraps `PATCH /api/records/{object_identifier}/{entity_id}/unarchive`, the
    round-trip counterpart to `plan_archive_records` — confirmed live to also
    restore a record removed by `DELETE /api/records/{o}/{id}`. No request
    body.
    """
    if not record_ids:
        raise PlanError("no record ids provided to unarchive")

    env = load_env_config().name
    try:
        get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e

    operations = [
        PlanOperation(
            action="update",
            kind="record_unarchive",
            key=f"{object_api_name}#{rid}",
            preview={"env": env, "object": object_api_name, "id": rid},
            existing_uuid=rid,
            parent_object_uuid=object_api_name,
        )
        for rid in record_ids
    ]

    return Plan.build(
        env=env,
        summary=f"Unarchive {len(operations)} record(s) on {object_api_name}",
        operations=operations,
    )

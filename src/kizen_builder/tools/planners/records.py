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

from typing import Any, cast

from kizen_builder.config import load_env_config
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Action, Plan, PlanError, PlanOperation

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


# `records import` modes → the uploader's `create_update_mode`.
IMPORT_MODES = {
    "create": "create_only",
    "upsert": "create_or_update",
    "update": "update_only",
}
# The uploader's per-field `conflict_resolution` (`ConflictResolutionEe6Enum`,
# confirmed live 2026-09-28). `overwrite` clears a field when its cell is
# blank; `overwrite_except_null` leaves it alone, like `records update`.
IMPORT_RESOLUTIONS = (
    "overwrite",
    "only_update_blank",
    "only_add_options",
    "overwrite_except_null",
)


def _import_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def plan_import_records(
    object_api_name: str,
    records: list[dict[str, Any]],
    mode: str = "upsert",
    resolution: str = "overwrite_except_null",
    timeout: float = 900.0,
) -> Plan:
    """Plan one bulk load through ``POST /api/custom-objects/{id}/uploader``.

    Validates columns and option labels against the live schema, then builds
    a single ``record_import`` op carrying the CSV (header plus string rows)
    and the uploader body. The server resolves the values; the upload and the
    submit happen at apply time. ``lookup_value`` is accepted as ``name``, so
    a ``records upsert`` spec imports unchanged.
    """
    if not records:
        raise PlanError("no records provided to import")
    if mode not in IMPORT_MODES:
        raise PlanError(f"invalid mode {mode!r}. Valid: {sorted(IMPORT_MODES)}")
    if resolution not in IMPORT_RESOLUTIONS:
        raise PlanError(
            f"invalid resolution {resolution!r}. Valid: {list(IMPORT_RESOLUTIONS)}"
        )
    if timeout <= 0:
        raise PlanError(f"timeout must be > 0 seconds, got {timeout!r}")

    env = load_env_config().name
    try:
        obj = get_object(object_api_name)
    except LookupError as e:
        raise PlanError(f"object '{object_api_name}' not found: {e}") from e
    if obj["api_name"] == "client_client" or obj.get("object_type") == "pipeline":
        raise PlanError(
            f"'{object_api_name}' is contacts or a pipeline, which Kizen imports "
            "through a separate uploader. `records import` supports standard "
            "custom objects only; use `records upsert` instead."
        )

    index = _field_index(obj)
    rows: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        if isinstance(rec.get("fields"), list):
            raise PlanError(
                f"record #{i + 1} carries a raw 'fields' list. `records import` "
                "takes {api_name: value} rows only."
            )
        row = dict(rec)
        if "lookup_value" in row:
            if "name" in row and row["name"] != row["lookup_value"]:
                raise PlanError(
                    f"record #{i + 1} has both 'name' and 'lookup_value'; pass one."
                )
            row["name"] = row.pop("lookup_value")
        for key, value in row.items():
            if isinstance(value, (list, dict)):
                raise PlanError(
                    f"record #{i + 1} column '{key}' is a list or object; "
                    "`records import` takes one scalar per cell."
                )
        rows.append(row)

    header: list[str] = []
    for row in rows:
        header.extend(k for k in row if k not in header)

    if resolution == "overwrite":
        # A CSV has one header, so a JSON row that lacks a column still sends
        # a blank cell for it, and `overwrite` clears the field on a blank.
        for i, row in enumerate(rows):
            missing = [k for k in header if k not in row]
            if missing:
                raise PlanError(
                    f"record #{i + 1} lacks {missing}; under --resolution "
                    "overwrite that sends a blank cell and clears the field. "
                    "Give every row the same keys (null clears a field), or "
                    "use overwrite_except_null."
                )

    has_id = "id" in header
    has_name = "name" in header
    if has_id and mode != "update":
        raise PlanError(
            f"an 'id' column only works with --mode update (got --mode {mode}); "
            "create and upsert match on name."
        )
    for i, row in enumerate(rows):
        name = _import_cell(row.get("name")).strip()
        if mode != "update" and not name:
            raise PlanError(
                f"record #{i + 1} has no 'name' — {mode} matches and names "
                "records by it (add a 'name' or 'lookup_value' column)."
            )
        if mode == "update" and has_id and not _import_cell(row.get("id")).strip():
            raise PlanError(
                f"record #{i + 1} has no 'id' — this file matches on id, so "
                "every row needs one."
            )
        if mode == "update" and not has_id and not name:
            raise PlanError(
                f"record #{i + 1} has neither 'id' nor 'name' — update matches "
                "an existing record by one of them."
            )
        if mode == "update" and has_id and has_name and not name:
            raise PlanError(
                f"record #{i + 1} has a blank 'name' in a file that sets names; "
                "the uploader would clear it. Give every row a name, or drop "
                "the column."
            )

    field_mapper: dict[str, dict[str, Any]] = {}
    for col, key in enumerate(header):
        if key in ("id", "name"):
            continue
        field = index.get(key)
        if field is None:
            raise PlanError(
                f"field '{key}' not found on '{obj['api_name']}'. "
                f"Available: {sorted(index)}"
            )
        entry: dict[str, Any] = {"csv_column": col, "conflict_resolution": resolution}
        if field.get("field_type") == "relationship":
            entry["field_for_matching"] = "name"
            entry["create_if_not_found"] = False
        field_mapper[field["id"]] = entry

        options = field.get("options") or []
        if field.get("field_type") in _SINGLE_SELECT and options:
            for i, row in enumerate(rows):
                label = _import_cell(row.get(key)).strip()
                if not label:
                    continue
                match = next(
                    (
                        o
                        for o in options
                        if (o.get("name") or "").lower() == label.lower()
                        or (o.get("code") or "").lower() == label.lower()
                    ),
                    None,
                )
                if match is None:
                    raise PlanError(
                        f"record #{i + 1}: {label!r} is not an option of "
                        f"'{key}'. Valid: {[o.get('name') for o in options]}"
                    )
                row[key] = match.get("name") or label

    match_on = "id" if has_id else "name"
    preview: dict[str, Any] = {
        "env": env,
        "object": object_api_name,
        "mode": mode,
        "rows": len(rows),
        "columns": header,
        "match_on": match_on,
        "resolution": resolution,
    }
    if mode != "create":
        # Live 2026-09-29: create_only makes a new record instead.
        preview["warning"] = (
            f"a row whose {match_on} matches an archived record unarchives "
            "and updates it"
        )
    body: dict[str, Any] = {
        "field_mapper": field_mapper,
        "create_update_mode": IMPORT_MODES[mode],
        # The server default, sent so it is visible in the plan. The other
        # `UnarchiveModeEnum` values are `create_new` and `error`.
        "fields_for_matching": [{"key": match_on, "unarchive_mode": "unarchive"}],
    }
    if has_name:
        body["name_column"] = header.index("name")
    if has_id:
        body["kizen_id_column"] = header.index("id")

    op = PlanOperation(
        action=cast(Action, mode),
        kind="record_import",
        key=f"{object_api_name}#import-{len(rows)}-rows",
        preview=preview,
        payload={
            "file_name": f"{obj['api_name']}-import.csv",
            "header": header,
            "rows": [[_import_cell(row.get(k)) for k in header] for row in rows],
            "body": body,
            "timeout": timeout,
        },
        parent_object_uuid=obj["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Import {len(rows)} record(s) into {object_api_name} ({mode})",
        operations=[op],
    )

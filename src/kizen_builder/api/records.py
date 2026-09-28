"""Record-level reads and writes against the Kizen API.

Custom-object records (and built-in types like ``client_client``) are exposed
under the object's own endpoint path.  The path follows the DRF convention
used throughout the rest of the API.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def get_record(
    client: KizenClient,
    object_identifier: str,
    record_id: str,
    include_hidden_fields: bool = True,
) -> dict[str, Any]:
    """GET /api/records/{object_identifier}/{record_id}."""
    params = {}
    if include_hidden_fields:
        params["include_hidden_fields"] = "true"
    return client.get(f"/api/records/{object_identifier}/{record_id}", params=params)


def search_records(
    client: KizenClient,
    object_identifier: str,
    filters: list[dict[str, Any]] | None = None,
    search: str | None = None,
    field_names: list[str] | None = None,
    page_size: int = 100,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """POST /api/records/{object_identifier}/search, paginated.

    ``filters`` is a list of filter-group dicts in Kizen's query format::

        [{"and": True, "filters": [{"type": "fields_v2", ...}]}]

    Pass ``None`` (default) or an empty list to return all records.
    ``search`` is an optional text string passed as a ``?search=`` query param.
    ``field_names`` limits which field api_names come back per record
    (confirmed live 2026-08-13 — see docs/specs/records.md); omit it (default)
    to get every field, which is the server's own default. ``limit`` stops
    pagination once that many records have been fetched (the last page may
    overshoot; callers truncate). ``None`` fetches all.
    """
    body: dict[str, Any] = {"query": filters or [], "and": True}
    if field_names is not None:
        body["field_names"] = field_names
    extra_params: dict[str, Any] = {}
    if search:
        extra_params["search"] = search
    results: list[dict[str, Any]] = []
    page = 1
    while True:
        data = client.post(
            f"/api/records/{object_identifier}/search",
            json=body,
            params={"page": page, "page_size": page_size, **extra_params},
        )
        if isinstance(data, list):
            results.extend(data)
            break
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        if limit is not None and len(results) >= limit:
            break
        page += 1
    return results


def count_records(
    client: KizenClient,
    object_identifier: str,
    filters: list[dict[str, Any]] | None = None,
) -> int:
    """How many records match ``filters`` (same format as :func:`search_records`).

    One ``page_size=1`` search; the response's ``count`` is the full total.
    """
    data = client.post(
        f"/api/records/{object_identifier}/search",
        json={"query": filters or [], "and": True},
        params={"page": 1, "page_size": 1},
    )
    return int(data["count"])


def create_record(
    client: KizenClient,
    object_identifier: str,
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """POST /api/records/{object_identifier}/add.

    ``fields`` is the list of field-value dicts accepted by the Kizen records
    API.  Each item is either ``{"name": api_name, "value": v}`` or
    ``{"id": field_uuid, "value": v}``.  Option values can be passed as
    ``{"name": "Option Label"}`` or ``{"id": option_uuid}``.  Relationship
    values for single-record fields use ``{"id": record_uuid}``; for
    multi-value relationship fields pass a list: ``[{"id": uuid}, ...]``.
    """
    return client.post(
        f"/api/records/{object_identifier}/add",
        json={"fields": fields},
    )


def update_record(
    client: KizenClient,
    object_identifier: str,
    record_id: str,
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """PATCH /api/records/{object_identifier}/{record_id}.

    ``fields`` uses the same format as :func:`create_record`.  Only the keys
    you include are modified; everything else is left as-is.
    """
    return client.patch(
        f"/api/records/{object_identifier}/{record_id}",
        json={"fields": fields},
    )


def upsert_record(
    client: KizenClient,
    object_identifier: str,
    lookup_value: str,
    fields: list[dict[str, Any]],
    oncreate_unarchive: str | None = None,
    onupdate_archived_conflict: str | None = None,
) -> dict[str, Any]:
    """POST /api/records/{object_identifier}/upsert.

    Creates a record if none matches ``lookup_value`` (the name field for
    custom objects, email for contacts), otherwise updates the match in
    place. ``fields`` uses the same format as :func:`create_record`.

    ``oncreate_unarchive`` controls what happens when creating and an
    archived record already matches ``lookup_value``: ``"prompt"`` (server
    default), ``"unarchive"``, or ``"overwrite"``. ``onupdate_archived_conflict``
    is ``"overwrite"`` to let an update proceed despite an archived-record
    naming conflict; omit both to keep the server's default (conflict-raising)
    behavior.
    """
    body: dict[str, Any] = {"lookup_value": lookup_value, "fields": fields}
    if oncreate_unarchive is not None:
        body["oncreate_unarchive"] = oncreate_unarchive
    if onupdate_archived_conflict is not None:
        body["onupdate_archived_conflict"] = onupdate_archived_conflict
    return client.post(f"/api/records/{object_identifier}/upsert", json=body)


def delete_record(
    client: KizenClient,
    object_identifier: str,
    record_id: str,
) -> dict[str, Any]:
    """DELETE /api/records/{object_identifier}/{record_id}.

    Despite the name, this archives the record rather than erasing it: the
    record 404s on a direct `get_record` and drops out of `search_records`,
    but it is restorable via `unarchive_record` or
    `upsert_record(..., oncreate_unarchive="unarchive")` — confirmed live
    2026-08-13 by deleting a record and unarchiving it back by the same id.
    Returns an empty dict (the endpoint answers 204 No Content).
    """
    resp = client.delete(f"/api/records/{object_identifier}/{record_id}")
    return resp if isinstance(resp, dict) else {}


def archive_record(
    client: KizenClient,
    object_uuid: str,
    record_id: str,
) -> dict[str, Any]:
    """POST /api/custom-objects/{object_uuid}/bulk-archive-entity-record.

    Archives one record — the operation the UI's Archive button performs.
    Lives under /api/custom-objects, not /api/records (same detail-action
    shape as `bulk_change_field_value`): the path segment is the object's
    **UUID**, not its api_name. The request body is a real bulk op
    (`record_ids` is a list); this wraps it one record at a time to match
    `delete_record`'s per-record shape.

    The response is `{"number_archived": N, "async": true}` — archiving
    happens server-side asynchronously, so a 200 here does not by itself
    prove the record is out of search yet (confirmed live 2026-08-13:
    observed as already reflected in `search_records` well under 2s later,
    same order of lag `_poll_field_value` documents for field writes).

    `DELETE /api/records/{object_identifier}/{record_id}` (`delete_record`)
    reaches the identical externally-observable state — confirmed live by
    archiving a record here, then a separately deleted record, and
    unarchiving both back by id through the same `unarchive_record` call.
    This function uses the dedicated endpoint anyway rather than aliasing to
    `delete_record`, so `archive_record` keeps working even if `DELETE`'s
    behavior is ever tightened to match its name.
    """
    resp = client.post(
        f"/api/custom-objects/{object_uuid}/bulk-archive-entity-record",
        json={"record_ids": [record_id]},
    )
    return resp if isinstance(resp, dict) else {}


def unarchive_record(
    client: KizenClient,
    object_identifier: str,
    record_id: str,
) -> dict[str, Any]:
    """PATCH /api/records/{object_identifier}/{record_id}/unarchive.

    Reverses `archive_record` — and, confirmed live 2026-08-13, `delete_record`
    too; the wire treats both the same way. Takes no request body.
    """
    return client.patch(f"/api/records/{object_identifier}/{record_id}/unarchive")


def related_pipeline_records(
    client: KizenClient,
    record_id: str,
    object_ids: list[str] | None = None,
    field_ids: list[str] | None = None,
    page_size: int = 100,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """POST /api/records/{entity_id}/related-pipeline-records, paginated.

    ``record_id`` is any record's UUID directly — this endpoint takes no
    object identifier, since a record UUID is already unique across objects.
    ``object_ids``/``field_ids`` optionally narrow to specific target
    pipelines/relationship fields; omit both to return everything related.
    """
    body: dict[str, Any] = {}
    if object_ids:
        body["object_ids"] = object_ids
    if field_ids:
        body["field_ids"] = field_ids
    results: list[dict[str, Any]] = []
    page = 1
    while True:
        data = client.post(
            f"/api/records/{record_id}/related-pipeline-records",
            json=body,
            params={"page": page, "page_size": page_size},
        )
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        if limit is not None and len(results) >= limit:
            break
        page += 1
    return results


def field_values(
    client: KizenClient,
    record_id: str,
    field_id: str,
    page_size: int = 100,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """GET /api/records/{entity_id}/field-values/{field_id}, paginated.

    Pulls all values from a summarized relationship field on one record —
    the same data code steps hand-roll today via a second per-relationship
    API call. ``record_id`` takes no object identifier (see
    :func:`related_pipeline_records`).
    """
    results: list[dict[str, Any]] = []
    page = 1
    while True:
        data = client.get(
            f"/api/records/{record_id}/field-values/{field_id}",
            params={"page": page, "page_size": page_size},
        )
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        if limit is not None and len(results) >= limit:
            break
        page += 1
    return results


def field_value(record: dict[str, Any], field_name: str) -> Any:
    """Extract a field's value from a record returned by the search/get API.

    Records from the search and get endpoints store fields in a dict keyed by
    field UUID, each entry shaped as ``{id, name, display_name, field_type,
    value}``.  This helper finds the entry whose ``name`` matches
    ``field_name`` and returns its ``value``.

    Returns ``None`` if the field is not present in the record.
    """
    for fdata in record.get("fields", {}).values():
        if fdata.get("name") == field_name:
            return fdata.get("value")
    return None

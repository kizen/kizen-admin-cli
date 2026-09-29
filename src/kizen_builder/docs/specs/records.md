# Spec shape: bulk records (CSV / JSON)

**Consumed by:** `kizen records create|update|upsert|import|archive <object> --spec-file <f>`
(also reads stdin). Records are *data*, not schema, but a bulk load runs
through the same plan → preview → confirm → apply loop.

> Single records don't need a file — use `--field api_name=value` (repeatable).
> A spec file is for loading **many rows at once**. Contacts use the object
> identifier `client_client`.

---

## Quick example

**CSV** (header row = field api_names):

```csv
name,account_region,account_seats
Acme Corp,North,12
Globex Inc,West,8
```

```bash
kizen records create accounts --spec-file accounts.csv --dry-run
kizen records create accounts --spec-file accounts.csv --yes
```

**JSON** (list of objects — same keys):

```json
[
  { "name": "Acme Corp", "account_region": "North", "account_seats": 12 },
  { "name": "Globex Inc",   "account_region": "West", "account_seats": 8 }
]
```

---

## Which column each verb needs

| Verb | Row must include | Behavior |
|------|------------------|----------|
| `create` | field values | Always inserts. Re-running **duplicates** — use `upsert` for idempotent loads. |
| `update` | an `id` column | Targets an existing record by UUID. Blank cells are skipped (not cleared). |
| `upsert` | a `lookup_value` column | Matches the object's name field (email for contacts); updates in place or creates. |
| `import` | `name` (or `lookup_value`); `id` or `name` with `--mode update` | One server-side job through the CSV uploader. See [Bulk import](#bulk-import-records-import). |

`lookup_value` is a single string matched against the object's identifying
field — there's no "match on field X" option.

---

## Value resolution

Cell/JSON values are resolved against the live object schema:

- **dropdown / status / radio** — pass the option **label**; resolved to its UUID.
- **relationship** — pass the related record's name or UUID; becomes `{"id": <uuid>}`.
- **checkbox / number** — string coerced by field type.
- A value starting with `[` or `{` is parsed as **JSON** (multi-select lists, explicit refs).
- **Full wire control** (JSON spec only): a row may carry a raw
  `"fields": [{ "name"|"id": ..., "value": ... }, ...]` list, passed through untouched.

---

## Upsert conflict flags

- `--oncreate-unarchive prompt|unarchive|overwrite` — what to do when an
  archived record already matches on create.
- `--onupdate-conflict overwrite` — let an update proceed past an
  archived-record naming conflict.

Omit both to keep the server's default (conflict-raising) behavior.

## Gotchas

- **`create` is not idempotent** — reload = duplicates. Reach for `upsert`.
- **Blank cells on `update` are skipped**, not cleared — you can't null a field
  by leaving it empty.
- **Contacts** are `client_client` here, like everywhere else.
- **`PATCH /api/records/{object_identifier}/{entity_id}` silently ignores an
  `archived` key.** `{"fields": [...], "archived": true}` returns 200 and
  changes nothing — DRF drops undeclared body keys, and
  `PatchedEntityRecordUpdateRequest` declares only `fields` and
  `archived_conflict`. Confirmed live 2026-08-13: PATCHing a record with
  `{"fields": [], "archived": true}` 200s, and the record is still 200 on a
  direct `GET` and still present in search — nothing archived. The near-miss:
  `archived_conflict` is a real property on this same endpoint, but it
  governs what happens when an update collides with an already-archived
  record's name — it does not archive anything. Use `records archive` /
  `records unarchive` instead.
- **Deleting a record is archiving it.** The live schema describes
  `DELETE /api/records/{object_identifier}/{entity_id}` as "Archive entity
  record"; there is no hard-delete or purge path. A record removed either way
  404s on a direct `GET`, drops out of search, and comes back with the same id
  through `records unarchive` or `records upsert --oncreate-unarchive
  unarchive` (confirmed live 2026-09-28). `records delete` was removed for
  that reason; `records archive` is the one command.

---

## Bulk import (`records import`)

`records import` sends the whole spec as **one** job through
`POST /api/custom-objects/{object_uuid}/uploader`, the endpoint behind the UI's
CSV import. It then waits on the job's `bulk-action-progress` row. The
per-row verbs above make one request per row (about 2.6 rows/s). An import creates about
25 rows/s and updates about 100 rows/s, so 1,000 rows take about 40 s to create
or 10 s to update. Standard custom objects only: contacts and pipelines have
their own uploaders, which are not wired. Confirmed live 2026-09-28 and 2026-09-29.

- **Modes.** `--mode create|upsert|update` becomes `create_update_mode`
  `create_only|create_or_update|update_only`. The default is `upsert`.
- **Matching.** `create` and `upsert` match on `name`, and `lookup_value` is read
  as `name`, so a `records upsert` spec imports unchanged. `update` matches on an
  `id` column (Kizen record UUID) when there is one, and on `name` otherwise.
  An `id` column is rejected in the other modes.
- **Archived records.** In `upsert` and `update`, a row that matches an
  archived record by name (or by id) **unarchives it** and applies the row, and
  the plan preview warns about it. `create` makes a new record beside the
  archived one. The command sends `fields_for_matching:
  [{key, unarchive_mode: "unarchive"}]` explicitly; it is also the server
  default. Confirmed live 2026-09-29.
- **Blank cells.** `--resolution` sets every column's `conflict_resolution`.
  `overwrite_except_null` is the default and leaves a field alone when its cell is
  blank, like `records update`. `overwrite` clears the field. The other values
  are `only_update_blank` and `only_add_options`. A CSV has one header, so a
  JSON row that leaves out a key would still send a blank cell for it. Under
  `overwrite` the plan rejects such a row: give every row the same keys, with
  `null` to clear a field.
- **Values are resolved by the server, not by the CLI.** The plan still rejects
  unknown columns, dropdown/radio/status labels that are not options (the
  match is case-insensitive), list or object cells, and raw `fields` rows. The server then applies these:
  - Money accepts both `1250.50` and `$1,250.50`.
  - A checkbox accepts `true` and `false`.
  - A relationship cell is the related record's **name**, matched
    case-insensitively. A UUID does not match. No related record is ever created.
- **Partial success.** When a cell fails (an unmatched relationship, an invalid
  value), the record is **still written**, with that field left blank. The job
  counts the row in `success_count`, and `failed_count` stays 0. Only the job's
  `failure_report` names the row. That report is a CSV of `Row Number,Entity Name,Record ID,Kizen URL,
  Failure Status,Error Messages`, where row 1 is the header. `records import` reads
  it and lists each row by its spec record number. **Any row error fails the op**, so
  the command exits 1. `--json` carries `row_errors`, `status_id`, the job counts,
  and the record count before and after.
- **Waiting.** The uploader answers 200 with an echo of the request plus
  `status_id`, the progress row's id. The command polls
  `GET /api/bulk-action-progress/{status_id}` until the status is `completed`,
  `failed`, `cancelled` or `skipped`, or until `--timeout` runs out. Don't find the job by
  a time window. `started_at` is rewritten when processing begins, and a
  window lookup matched the previous job. The uploader POST is never retried.
- The uploader ignores `send_email_notification`, because the key is not in its schema.
- The uploaded CSV (source `record_import`) and the failure report stay in
  the file store afterwards. See `kizen docs show files`.

---

# Wire format & API behavior

All record types — custom objects **and** contacts — use one unified records
API. Contacts are the object identifier `client_client`; the `/api/client/`
endpoint family is **deprecated**, don't use it. Accounts and every other CRM
object are plain custom objects whose identifier is their api_name.

## Endpoints (for production scripts)

| Operation | Method | Path |
|---|---|---|
| Get one record | `GET` | `/api/records/{object_identifier}/{entity_id}` |
| Search / list | `POST` | `/api/records/{object_identifier}/search` |
| Create | `POST` | `/api/records/{object_identifier}/add` |
| Update (partial) | `PATCH` | `/api/records/{object_identifier}/{entity_id}` |
| Delete (archives; unused by the CLI) | `DELETE` | `/api/records/{object_identifier}/{entity_id}` |
| Upsert | `POST` | `/api/records/{object_identifier}/upsert` |
| Bulk import (CSV) | `POST` | `/api/custom-objects/{object_uuid}/uploader` |
| Bulk job progress | `GET` | `/api/bulk-action-progress/{id}` |
| Move between stages | `PATCH` | `/api/records/{object_identifier}/{entity_id}/move` |
| Archive | `POST` | `/api/custom-objects/{object_uuid}/bulk-archive-entity-record` |
| Unarchive | `PATCH` | `/api/records/{object_identifier}/{entity_id}/unarchive` |

Search and list paginate via `page` / `page_size` query params — keep requesting
until a short page comes back. Archive is the odd one out: it lives under
`/api/custom-objects` and its path segment is the object's **UUID**, not its
api_name — every other row above takes either.

## The `fields` write shape

```json
{"fields": [
  {"name": "field_api_name",   "value": "some text"},
  {"id":   "<field-uuid>",     "value": {"id": "<option-uuid>"}},
  {"name": "relationship_field", "value": {"id": "<related-record-uuid>"}}
]}
```

- Reference a field by `name` (api_name) **or** `id` (UUID).
- **Option values** are `{"id": <option_uuid>}` or `{"name": "Label"}`.
- **Relationship values** are `{"id": <record_uuid>}` — a **list** of those for
  a multi-value field.

The CLI resolves labels and ids from live schema for you; pass a raw `fields`
list in a JSON spec to bypass that entirely.

## Search body

```json
{
  "query": [ { "and": true, "filters": [
      {"type": "fields_v2", "field": "\"custom\"::<field-uuid>",
       "subtype": "custom", "condition": "=", "value": "some text"} ] } ],
  "and": true,
  "field_names": ["name", "email"]
}
```

`field_names` limits which fields come back. Pass `"query": []` to return all
records. The `query` structure is the shared filter wire format —
`kizen docs show filters`.

`confirmed live 2026-08-13`: omitting `field_names` entirely returns **every**
field on the object (17/17 field keys observed on a test object) — the
server's own default is "everything," not "id + name." Matching is on field
**api_name only**; a display label or a field UUID in `field_names` matches
nothing (an all-bad list returns `"fields": {}`). An unrecognized api_name is
**silently dropped, not rejected** — the request still returns `200` with
that name simply absent from `fields`, so client-side validation before
sending the request is the only way to catch a typo (`kizen records list
--fields` does this).

## Bulk change field value (`records set-field`)

`POST /api/custom-objects/{object_pk}/bulk-change-field-value` sets **one field
to one value across many records** in one call.

```bash
kizen records set-field <object> <uuid> [<uuid> …] --field X --value Y [--resolution …]
```

- **`field_value` takes the bare wire scalar, not an object** — despite the
  OpenAPI spec typing it `object`. Confirmed live 2026-07-20: a `longtext` field
  wants a bare string, and a `dropdown` field wants the **bare option UUID
  string**. Either wrapped as `{"value": …}` or `{"id": …}` 400s with
  `field_value: ['Not a valid string.']`.
- So `field_value` is the same value a record's own `fields` entry would take,
  with any `{"id": …}` wrapper unwrapped to the bare id.
- `field_id` is the field's **UUID**, not its api_name.
- `field_resolution` is one of `overwrite`, `add_only`, `remove_only`,
  `update_if_blank`, `overwrite_except_null` (`add_only`/`remove_only` apply to
  multi-select fields).
- **Untested live:** `checkboxes`/`dynamictags` (multi-select) and
  `relationship` fields. By the same pattern they should want a bare list of
  ids rather than a list of `{"id": …}` dicts — confirm before relying on it.
- Id-targeted only. The request also accepts `entity_records_set_key` for
  filter-targeted bulk ops, but that needs the separate `bulk-action-summary`
  framework, which isn't wired up. `bulk-action-progress` polling is wired, but
  only `records import` uses it.

## Archive / unarchive (`records archive` / `records unarchive`)

`POST /api/custom-objects/{object_uuid}/bulk-archive-entity-record` is the
operation the UI's Archive button performs. Same request family as
`bulk-change-field-value` (`entity_records_set_key`/`bytes_start_index`/
`bytes_end_index` for the filter-targeted bulk framework, unused here):

```json
{"record_ids": ["<record-uuid>", ...], "send_email_notification": false}
```

```bash
kizen records archive <object> <uuid>
kizen records archive <object> --spec-file ids.csv   # rows with an id; `records list --limit N --output csv` works (default limit 100)
kizen records unarchive <object> <uuid> [<uuid> …]
```

- **One request per 500 ids.** Every call writes exactly one
  `bulk-action-progress` row (`action: custom_object_archive`) however many
  ids it carries, so the CLI batches rather than posting per id. 500 is a
  chosen ceiling, not a server limit; the largest batch probed is 22 ids in
  one call. Confirmed live 2026-09-28 and 2026-09-29.
- **Email is off.** `send_email_notification` defaults to `true`, and each
  progress row carries the flag, so an unset flag emails once per request.
  The CLI always sends `false`. Confirmed live 2026-09-28.
- The response is `{"number_archived": N, "async": true}`. `N` counts the ids
  sent, not the records archived: archiving an already-archived id returns 1
  while its progress row records `success_count: 0`. The response carries no
  progress-row id; find the row through the `bulk-action-progress` list
  filters (`custom_object_id`, `action`, `started_after`). Confirmed live
  2026-09-28.
- Archiving is asynchronous server-side. The change was visible in
  `search_records` well under 2s later (confirmed live 2026-08-13), the same
  order of lag `records set-field` shows.
- The path segment is the object's **UUID**, not its api_name — unlike every
  other records endpoint on this page.
- `records unarchive` wraps `PATCH /api/records/{object_identifier}/{entity_id}/unarchive`
  — the ordinary object identifier convention, and takes no request body.
  There is no bulk unarchive endpoint, so it stays one request per id.
- `DELETE` reaches the same state as this endpoint, and each treats the
  other's result as already done: `DELETE` on an archived record 404s, and
  archiving a deleted one records `success_count: 0`. The one observed
  difference is that `DELETE` writes no progress row. Confirmed live
  2026-09-28.

## See also

- `kizen docs show field` — the schema these values are resolved against.
- `kizen docs show filters` — the filter DSL and wire format used by `--filter`
  and the search body.
- `kizen docs show objects` — pipeline stages, and why `records move` exists.
- `kizen records create|update|upsert|import --help` — current flags.

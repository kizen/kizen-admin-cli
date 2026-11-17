# Business variables — wire format & API behavior

**Consumed by:** `kizen business-variables list|set|delete`. Flag-driven; no
spec shape.

A business variable is a business-wide named value that merge fields read as
`{{ business_variable.<api_name> }}`. Webhook trigger headers use them (see
[`automation.md`](automation.md)), and so does any text that takes merge fields.

## Commands

- `list`: every variable with its `name`, `api_name`, value and `id`.
- `set <api_name> --value V [--name N]`: create the variable, or change its
  value and/or display name. Creating needs both `--name` and `--value`. An
  unchanged variable is a `skip`. The `api_name` is the key and can't be renamed.
- `delete <api_name>`: remove one variable. Merge fields that reference it stop
  resolving. A webhook trigger whose header uses it fails validation with
  `business variables '<api_name>' not found`.

---

## Wire format (confirmed live 2026-11-12)

Variables have no endpoint of their own. They are a nested list on the
business record:

```
GET /api/business/{business_id}
→ { ..., "business_variables": [
      {"id": "<uuid>", "name": "Review Link", "api_name": "review_link",
       "value_string": "https://example.com/review"} ] }
```

`value_string` is the only value field: every value is text. It may be empty
(`""` saves) but not null. The list has no stable order: a save can move any
variable to another position, so compare by `id`, not by index. The order is
not append, front or alphabetical, and the order sent in the PATCH is ignored.
Updating one variable's value can move others, and so can deleting one.
Back-to-back GETs with no write return the same order. Confirmed live
2026-11-17. The read has no `ORDER BY`, so rows come back in Postgres storage
order.

## Writes replace the whole list

```
PATCH /api/business/{business_id}
{"business_variables": [ ...every variable that should exist... ]}
```

Create, update-in-place, delete and the stale-plan refusal below were confirmed
live 2026-11-12. The duplicate and reserved-name 400s are from the backend
serializer (`BaseBusinessSerializer.update`) only.

- An entry whose `id` matches a live variable updates it in place; its `id`
  stays the same.
- An entry without an `id` (or with an unknown one) is created. `api_name` is
  derived from `name` when omitted; the CLI always sends it.
- **Any live variable whose `id` is missing from the list is deleted.** Leaving
  the key out entirely leaves the variables alone.
- Two entries with the same `name` or `api_name` are a 400. So is an
  `api_name` that's a reserved field name.
- The PATCH needs write access to the business settings.

So every `set` and `delete` plan carries the full list as it should be after
the change: every other variable unchanged, by id. `existing_uuid` on the op is
the business id. At apply, the CLI reads the list again and refuses if any
other variable changed after planning. A stale plan would otherwise undo that
change, or delete a variable created in the meantime. Run the command again
to re-plan.

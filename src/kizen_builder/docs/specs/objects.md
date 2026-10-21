# Surface: objects, categories & pipeline stages

Objects and categories are **flag-driven** — there is no spec file. Build an
object with fields in three steps:

```bash
kizen objects create --name … --object-type …   # then --help for the rest
kizen categories create <object> --name …
kizen fields create <object> --spec-file fields.json         # kizen docs show field
```

## `objects get` is the lookup you want

`kizen objects get <object>` returns categories, every field with its **UUID
inline**, and every choice/status/yesnomaybe field's `options` array as
`{id, name, code}`. **There is no separate call needed for option UUIDs** — this
is the answer to "how do I find the option UUID for X". **Confirmed live
2026-08-13: true in every output mode**, with the full UUID, not shortened.

It also resolves each relationship field's target to a readable api_name plus
cardinality, so you never hand-resolve a `related_object` UUID.

One thing it does **not** cover:

- Pipeline **stages** are a separate resource, below.

`kizen objects list` includes built-in objects (Contacts, identifier
`client_client`) alongside custom ones. `kizen objects get client_client`
returns their field schema and option UUIDs in the same format as any custom
object.

## Kizen derives the object api_name from the display name

An object's api_name can't be chosen. Kizen slugs `--name` (the plural
`object_name`, not `--entity-name`): it folds accents to ASCII, lowercases,
drops apostrophes, and collapses every other run of non-alphanumerics to one
`_`. `Service Tickets` becomes `service_tickets`, and `Señor's  Policy &
Claims` becomes `senors_policy_claims`. A supplied api_name (`name`
in the request body) is refused with a 403 "You do not have permission to
change name.", on create and on update alike, and renaming the display name
later doesn't move it. `confirmed live 2026-10-14`.

- `objects create --api-name` is an assertion, not a request. If a live
  object already has it, the plan says so; otherwise a value that differs
  from the derived one exits 2 and names the value `--name` would give.
- Two live objects can't share a display name (400 "Custom Object names must
  be unique."), and the plan refuses one up front. A deleted object's name is
  reusable.
- If a live object already holds the derived slug, Kizen adds a random
  8-character suffix (`control_things_AHpZedMd`). The plan warns,
  and the result line shows the api_name Kizen actually stored.

## Pipeline stages

A pipeline object's stages are a **separate entity**, not the object's own field
options:

```
GET   /api/pipelines/{object_pk}/stages                  # list, ?ordering=order
POST  /api/pipelines/{object_pk}/stages                  # create: {name, status, order} required
PATCH /api/pipelines/{object_pk}/stages/{id}             # partial update
POST  /api/pipelines/{object_pk}/stages/remove-stage     # delete + migrate: {id, new_stage_id}
PATCH /api/records/{object_identifier}/{entity_id}/move  # move one record: {stage_id}
```

CLI: `kizen objects stages list|create|update|remove <pipeline>`, and
`kizen records move` as the runtime counterpart.

- `status` is one of `open | won | lost | disqualified`.
- **There is no bare DELETE on a stage.** `remove-stage` always migrates the
  removed stage's records onto `new_stage_id`, which is why `objects stages
  remove` requires `--move-to`.
- `object_pk` here is the pipeline's **custom-object UUID** — the same one
  `objects get <pipeline>` returns. You never need the `/api/pipelines`
  collection endpoint; a pipeline resolves through `/api/custom-objects` and
  carries a `stages` list when `object_type == "pipeline"`.

### The mirrored `stage` field is a read-only projection

A pipeline object also has a choice-type field whose options exactly match the
live stage ids (whatever its api_name). It looks editable and is not:
`POST .../fields/{id}/options` against it **returns 200 with a UUID**, but the
option never appears in `/api/pipelines/{id}/stages` nor in the field's own
option list, and the returned UUID 404s on any follow-up.

`kizen fields options add/remove` detects a stage-backed field — by exact
option-id-set equality against the live stages, not by field api_name — and
refuses, rather than reporting the fake success the API hands back. Use
`objects stages` instead.

## Deleting an object

`DELETE /api/custom-objects/{id}` answers 400 "This custom object still has
active records. Archive all records first using the bulk-archive action" while
any record is active, and `{}` once none is (confirmed live 2026-09-28). The
object DELETE writes no bulk-action-progress row, so it sends no email.

`kizen objects delete` plans both steps: it lists the active record ids,
archives them in 500-id bulk-archive requests with
`send_email_notification: false` (see `kizen docs show records`), then waits for search to count 0 before it
sends the DELETE. The wait is needed: on 3 records, a count taken 0.5 s after
the archive returned still read 3, and the next, 2 s later, read 0 (confirmed
live 2026-10-16). A transient 5xx while counting is retried; if an archive
request fails, the DELETE is skipped. Built-in objects such as `client_client`
are refused at plan time, before any record is listed.

## See also

- `kizen docs show field` — field spec shape, type-specific blocks, reserved
  api_names, and relationship-field creation.
- `kizen docs show records` — the records CRUD surface, including
  `records move` between stages.
- `kizen docs show layout` — how fields get arranged on the record page.

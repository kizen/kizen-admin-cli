# Spec shape: `FormDef` / `FormFieldDef` — forms & surveys

Forms and surveys are the **same wire shape**; only the API base path differs
(`/api/forms` vs `/api/surveys`). Everything here applies to both `kizen forms …`
and `kizen surveys …`.

**Consumed by:**
- `kizen forms create --spec-file <f>` — a `FormDef` (may include `fields`).
- `kizen forms fields create <form> --spec-file <f>` — a `FormFieldDef` list.
- `kizen forms set-ui <form> --spec-file <f>` — the `form_ui` page-layout blob.
- `kizen forms update <form> --spec-file <f>` — a JSON dict of `FormDef` changes.

> ⚠️ **A form needs both field schema *and* a `form_ui` page layout to render
> and be submittable.** Creating fields alone gives valid schema but no visual
> page — set the layout with `set-ui` (or include `form_ui`). The `form_ui`
> shape is in the wire section below.

---

## Quick example (`forms create`)

```json
{
  "name": "Lead Capture",
  "related_object": "accounts",
  "template_type": "modern",
  "fields": [
    { "name": "Full Name", "field_type": "text",  "order": 0, "required": true },
    { "name": "Email",     "field_type": "email", "order": 1, "required": true }
  ]
}
```

---

## `FormDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | **Required.** Display name. |
| `api_name` | string | Optional (2–255). Kizen may rewrite it on create. |
| `related_object` | api_name | **Required by the API** — the object submissions attach records to. Resolved to `related_object_id` at plan time. |
| `related_object_id` | UUID | Raw escape hatch for `related_object`. |
| `template_type` | enum | `modern` (default), `open`, or `splash`. Required by the API. |
| `submission_action` | enum | `go_to_page` or `go_to_url` (+ `redirect_url`). |
| `form_ui` | object | Page layout. Without it the form won't render — see the `form_ui` section below. |
| `fields` | `FormFieldDef[]` | Optional — create fields in the same plan. |

## `FormFieldDef` fields

Structurally identical to `ActivityFieldDef` (see `activity.md`) **minus** the
`activity_custom_field` linked-field concept — forms are standalone
data-capture surfaces. Flat list ordered by `order`; `api_name` optional; same
type-specific blocks as `field.md`.

## Gotchas

- **`yesnomaybe` requires an `options` list on forms/surveys** (unlike custom-object
  and activity fields, where it needs none). The API 400s without it.
- **`related_object` is mandatory** — a form with no target object won't create.
- **Fields-without-`form_ui` renders nothing** — always pair schema with a layout.

---

# Wire format & API behavior

Forms (`/api/forms`) and surveys (`/api/surveys`) are **structurally identical**
data-capture objects — same 24 endpoints, same request/response shapes, only the
base path differs. Everything below applies to both.

## Object CRUD

```
GET/POST              {base_path}
GET/PUT/PATCH/DELETE  {base_path}/{id_or_api_name}       # path accepts either
POST                  {base_path}/{id}/duplicate
```

`/duplicate` ignores `name`; the CLI renames the copy with a PATCH. Confirmed
live 2026-10-06.

**Any update to a form deletes every field not placed on one of its `form_ui`
pages**, default and CLI-created alike, whatever the update sets: a PATCH of
`name`, `description` or `enabled`, a full PUT, and `set-ui` (a `form_ui`
PATCH) all do it. A form with no pages loses all of its fields. So
`duplicate --name` refuses unless the source places every field; duplicate
without it, place every field on the copy with `set-ui`, then rename it with
`update`. `update` itself does not check yet. Confirmed live 2026-10-16 on
forms and surveys.

The API supports a full PUT, but `update` only ever sends a PATCH with the flags
that changed — matching every other `update` verb in this CLI rather than mixing
update semantics.

### `related_object_id` and `template_type` are required on create

Verified against the live `FormObjectRequest`/`FormObject` schemas at
`/api/docs/schema`. Note **the public schema at `/api/docs/public/schema`
doesn't cover forms/surveys at all** — don't rely on it for this object.

- **`related_object_id`** (a UUID) — which custom object submissions attach
  records to. The `--related-object` flag resolves an api_name to it.
- **`template_type`** — `modern` / `open` / `splash`. The CLI defaults to
  `modern` because the API has no server-side default.

**Posting without `related_object_id` doesn't 400 — it 404s** with "No
CustomObject matches the given query", which reads like a bad URL rather than a
missing body field.

Fields that do **not** exist on the real schema, despite looking plausible by
analogy with activities: `is_editable`, `webhook_url`, `association_mode`.

Real optional fields: `submission_action` (`go_to_page` / `go_to_url` — **not**
the `redirect` / `trigger_webhook` values activities use), `redirect_url`,
`pass_variables_on_redirect`, `challenge_token_required`, `subscribers`,
`business_merge_fields`, `form_ui`.

Read responses report the submission count as **`number_submissions`** (not
`n_submissions`) and the target object as **`related_object`** (a plain string,
not expanded).

## Fields are a sub-resource, not embedded

```
GET/POST              {base_path}/{id}/fields
GET/PUT/PATCH/DELETE  .../fields/{fid}
POST                  .../fields/{fid}/options            # add
DELETE                .../fields/{fid}/options/{oid}      # delete
POST                  .../fields/{fid}/options/{oid}/replace
```

Field shape ≈ custom-object / activity fields, ordered by `order` in one flat
list with no categories. **Three divergences that bite:**

- **`wysiwyg` IS a valid `field_type` here**, per the live
  `FormFieldFieldTypeEnum`. Do **not** apply the `wysiwyg` → `longtext` +
  `meta.is_markdown` remap that custom objects and activities need.
- **`yesnomaybe` requires explicit lowercase option codes.** A `yesnomaybe`
  field with no `options` 400s ("options: This field is required for
  yesnomaybe") — where custom-object and activity fields need none — and the
  codes must be exactly `yes`/`no`/`maybe`: `{"name": "Yes", "code": "Yes"}`
  400s with "yesnomaybe option code only allows 'yes', 'no', and 'maybe'". This
  spec builds `{"name": o, "code": o}` from one string, so pass lowercase
  values; there's no way to get an uppercase display label without extending the
  model.
- **Field reads carry no `description`**, on forms or surveys, so a field
  update that passes `description` always plans as `update`, even when the
  value hasn't changed. Custom-object and activity field reads do carry it,
  and diff it. `confirmed live 2026-10-01`

The field type enum also includes `custom_object_field` (a linked-field type
mirroring an existing custom-object field, like activities'
`activity_custom_field`) — not wired here, since forms aren't inherently tied to
one custom object the way activities are.

## ⚠️ A bulk field-create batch with a failing op can lose already-created fields

Observed live: creating 21 fields in one `fields create --spec-file` batch where
the **last** op 400s on bad option data left the prior ~19
successfully-created fields — real server UUIDs returned — **no longer
existing** moments later. Confirmed by direct GET 404s on those ids and an empty
`fields list`.

A batch with no failing op does **not** lose fields (confirmed with a clean
3-field batch, checked immediately and after a delay). Root cause unidentified,
likely server-side transactional behavior specific to this endpoint. **Practical
mitigation: validate a large field spec carefully before applying it**, because
a failure deep in the batch can retroactively undo earlier successes.

## `form_ui` — the visual page layout

`kizen forms set-ui` / `kizen surveys set-ui`. Confirmed live 2026-07-21 against
a real, already-submitted form — proven-working ground truth, not just
proven-saved.

`form_ui` is `{"pages": [...], "business_merge_fields": [...]}`.

**Casing is mixed, and conflating it is the classic failure here.** Each page
wrapper is **snake_case** (`id`, `hidden`, `page_name`, `is_form_page`,
`is_hideable`, `is_deletable`) — but **`page_data` is a JSON-encoded string**,
not a nested object, and its *contents* are entirely **camelCase** craft.js
keys. (A record layout's `blockJson` is the same vocabulary but a real nested
object; a dashboard static-content `content` dict is snake_case. All three
differ.)

**A real form has ≥2 pages:** one `is_form_page: true` page
(`is_hideable: true`, `is_deletable: false`) and one `is_form_page: false`
"Thank You Page" (`is_hideable: false`, `is_deletable: false`) shown
post-submit. The CLI appends the latter automatically unless your spec already
includes a non-form page.

### Node graph

`Root` (children in `nodes`) → `Section` (children in `nodes`) → `Row`
(`props.columns` is a list of fractional widths; children referenced via
`linkedNodes` as `{"column-N": cellId}`, its own `nodes` stays `[]`) → `Cell`
(`isCanvas: true`, `props: {}`, children in `nodes`) → a leaf block: `Text`,
`CustomField`, `FormField`, `Button` (`props.action="submit"` +
`custom.isSubmitButton=true` for the submit button), `Divider`, `Image`.

### A field input is one of TWO node types

Getting this wrong produces a real crash — `TypeError: undefined is not an
object (evaluating 'f.customObjectField.id')` — when reopening the form in the
Kizen builder.

| the form field | node type | notes |
|---|---|---|
| has a linked `custom_object_field` | **`CustomField`** | always sets `labelText`; full `access` |
| `custom_object_field: null` (created straight on the form) | **`FormField`** | explicit `customObjectField: null` (not omitted), slimmer `access` (`{"edit": true, "view": true}` — no `remove`), and **no `labelText` at all** |

`CustomField`'s renderer unconditionally reads `field.customObjectField.id`,
which is why using it for an unlinked field crashes the builder; `FormField`'s
renderer never touches that path. The CLI picks the node type automatically from
whether the field has the backlink.

### `CustomField.props.field` mapping

The block embeds the form field's **own** data (id, name/api_name,
required/hidden, order, meta, properties, options — deep-camelCased from the form
field's own snake_case response) but **borrows `displayName`/`fieldType` from
the linked `customObjectField`**. That's the common case: every field surfaced
from a form's `related_object` carries this backlink *regardless of whether the
form field's own `field_type` already matches* — a plain `decimal` field still
nests a full `customObjectField`. The form field's own `display_name` becomes
`labelText`, the editable on-page label, distinct from the schema `displayName`.

**The nested `customObjectField` must be the FULL custom-object field record** —
`category`, `canonical_display_name`, `is_hidden`, `is_suppressed`,
`include_in_short_form`, `order`, `meta`, `description(_visibility)`,
`properties`, `access`, `ui_default_value`, `allow_on_forms`, … 20+ keys, i.e.
what `objects get` returns — **not** the skinny 6-key stub (`id`, `name`,
`display_name`, `field_type`, `is_default`, `custom_object`) that the form's own
field list returns under `custom_object_field`.

A form saved with the skinny stub **renders correctly on the public submit page
and even accepts real submissions, but the Kizen page-builder fails to reopen it
for editing.** The public renderer is more tolerant than the builder. The CLI
swaps each stub for the matching full record before writing, which is why you
should always go through `set-ui` rather than assembling `props.field` yourself.

`isNew` and `placeholder` are genuinely UI-only synthesized keys, present on
neither the form field's own GET nor the full custom-object field record.

### Not modeled

Multi-`Section` pages; file uploads for `Image` blocks (an image block needs a
file id from a file uploaded some other way — `kizen docs show files`); and the
`custom_object_field`-**type** form field, meaning the form's own native
"primary field" wrapper type, as distinct from a regular field that merely
carries a `custom_object_field` backlink.

## Not wired

- `.../fields/search` exists in the spec; `forms fields list` already covers
  listing, so it wasn't worth the extra surface.
- **Deferred:** submissions (`POST .../submit`, `PATCH .../submit/{id}`,
  `POST {id}/export`), subscribers (`GET/POST/DELETE .../subscribers`),
  `GET {id}/page-view`, `POST {id}/upload`.
- **Assumption carried over from activities, not verified live:** option
  removal-with-remap here is implemented like the activities one — `replace`
  only reassigns, so the CLI deletes the old option explicitly afterward. If
  forms actually follow the custom-object behavior (replace does both), that
  explicit delete becomes a harmless no-op-turned-404. Verify before leaning on
  `--remap-to` hard.

## See also

- `kizen docs show field` / `kizen docs show activity` — shared type blocks, and
  the `wysiwyg` remap that does **not** apply here.
- `kizen docs show layout` — the same craft.js vocabulary, nested-object instead
  of string-encoded.
- `kizen docs show files` — image blocks.

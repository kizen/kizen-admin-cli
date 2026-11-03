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
>
> Every save of a form (any update or `set-ui`) deletes the fields not placed on
> a page, so `update` and `set-ui` refuse until each field is placed or deleted.
> Confirmed live 2026-10-16.

---

## Quick example (`forms create`)

```json
{
  "name": "Contact Support",
  "related_object": "support_requests",
  "template_type": "modern",
  "fields": [
    { "name": "Your email", "api_name": "customer_email",
      "custom_object_field": "customer_email", "order": 1, "required": true },
    { "name": "Order number", "api_name": "order_number", "field_type": "text",
      "order": 2 }
  ]
}
```

Kizen adds a required `name` field linked to the record's name. Your email is
saved to the record's `customer_email`; Order number is form-only.

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

Structurally close to `ActivityFieldDef` (see `activity.md`), **plus**
`custom_object_field`, which ties the answer to the related record's field.
Flat list ordered by `order`; `api_name` optional; same type-specific blocks as
`field.md`. Unknown keys are rejected exactly as in `field.md`.

| Key | Notes |
|-----|-------|
| `custom_object_field` | api_name or UUID of a field on the form's `related_object`, resolved at plan time. An unknown name is a plan error. `field_type` comes from that field when omitted; a different one is refused. Takes no `options` or type block: Kizen copies them from the record's field. |
| `hidden` | Sent as `is_hidden`, the field's record-view hidden flag. It has no effect on the form: the field still shows. See "A field the submitter doesn't see". |

A field without `custom_object_field` is form-only (`custom_object_field: null`
on read): a submission doesn't write its answer anywhere on the record it
creates. Link every answer the record needs.

Give a linked field an `api_name`. Without one Kizen derives a name with a
random suffix (`channel_cafd4d`), and `set-ui` places fields by api_name.
`forms fields create --custom-object-field <api_name>` adds one linked field.
`fields update` can't change a link: delete the field and create it again.
Confirmed live 2026-11-02.

## Gotchas

- **`yesnomaybe` requires an `options` list on forms/surveys** (unlike custom-object
  and activity fields, where it needs none). The API 400s without it. A linked
  one takes none: the CLI sends them for you.
- **`related_object` is mandatory** — a form with no target object won't create.
- **Fields-without-`form_ui` renders nothing** — always pair schema with a layout.
- **Saving a form deletes every field that isn't on a page**, so place each
  field with `set-ui` before any `update`. See "Saving a form deletes unplaced
  fields" below.

## A field the submitter doesn't see

A form can't hide a field or fill one in. The form builder has no hide
setting, no default value and no visibility rules, and the API has no value
key. `hidden` (`is_hidden`) is the record-view hidden flag, which has nothing
to do with whether the field shows on a form. Confirmed live 2026-11-02: a
dropdown sent with `hidden: true` showed on the public form with no default,
and the submitter's pick landed on the record. So leave a value the submitter
mustn't choose off the form, and set it in an automation.

This form ties every answer to a `support_requests` record and leaves Channel
off. The object and its fields are in `kizen docs show automation`, "Find or
create a Contact by email".

<!-- fixture: examples/support_desk/contact_support.form.json -->
```json
{
  "name": "Contact Support",
  "api_name": "contact_support",
  "related_object": "support_requests",
  "fields": [
    {
      "name": "Your email",
      "api_name": "customer_email",
      "custom_object_field": "customer_email",
      "required": true
    },
    {
      "name": "Subject",
      "api_name": "subject",
      "custom_object_field": "subject",
      "required": true
    },
    {
      "name": "How can we help?",
      "api_name": "message",
      "custom_object_field": "message",
      "required": true
    }
  ]
}
```

Kizen adds a required `name` field, linked to the request's name, so the page
places it too:

```json
{ "pages": [ { "simple": true, "heading": "Contact support",
  "fields": ["name", "customer_email", "subject", "message"] } ] }
```

An automation on the object sets Channel: a `new_entity_created` trigger, a
condition that Channel is blank, then `change_field_value` with the option's
label. A submission leaves Channel blank only because the form doesn't offer
it. The blank check keeps the step off requests that arrived another way and
were created with a channel. Triggers and steps are in `kizen docs show
automation`.

```json
{ "name": "Default the channel", "type": "record_based",
  "target_object": "support_requests",
  "triggers": [ { "trigger_type": "new_entity_created", "order": 0,
                  "trigger_new_entity_created": { "action": "create_only" } } ],
  "steps": [
    { "key": "no_channel", "parent_key": null, "step_type": "condition", "order": 0,
      "step_condition": { "type": "custom_filter",
        "filter_config": { "all": [ { "field": "channel", "op": "is_blank" } ] } } },
    { "key": "web_form", "parent_key": "no_channel", "parent_branch": "yes",
      "step_type": "change_field_value", "order": 1,
      "action_change_field_value": { "field_ref": "support_requests.channel",
        "change_type": "specific_value", "specific_field_value": "Web Form" } } ] }
```

Confirmed live 2026-11-02: the form applied with every field linked and
placed as a `CustomField`, a submission wrote each linked answer to the new
record, and the automation saved, read back, and round-tripped with no drift.
The automation hasn't run against a submission yet.

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

Renaming the copy is an update, and an update deletes every field not placed
on a page (see "Saving a form deletes unplaced fields" below). So
`duplicate --name` refuses unless the source places every field; duplicate
without it, place every field on the copy with `set-ui`, then rename it with
`update`.

The API supports a full PUT, but `update` only ever sends a PATCH with the flags
that changed — matching every other `update` verb in this CLI rather than mixing
update semantics. Before planning it, `update` (and `set-ui`) checks that every
field is placed on a page of the `form_ui` the form will hold afterwards, and
refuses with the unplaced fields' names otherwise.

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
`n_submissions`) and the target object as **`related_object`**, expanded to
`{id, name, object_type, …}`. Confirmed live 2026-11-02.

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

**A field links to the related object's field through `custom_object_field`**
(the field's UUID on write; the 6-key stub described under `form_ui` on read).
A form or survey on a custom object starts with one field, `name`, linked to
the record's `name`, required, and of the native `custom_object_field` type.
For any other link:

- Kizen copies the record field's options and settings. A dropdown sent with
  other options got the record field's three, and a `money` field sent EUR got
  the record field's USD `money_options`. A `money` link sent without
  `money_options` 400s, so the CLI sends the default block. A `yesnomaybe`
  link sent without `options` 400s too, so the CLI sends the record field's
  `yes`/`no`/`maybe` codes, and Kizen stores the record field's own options.
- A `field_type` different from the record field's is saved as sent: `text` on
  a longtext link, and a `dropdown` with no options on an email link. The CLI
  refuses it.
- `field_type` is required on the wire even with a link.

Confirmed live 2026-11-02.

## ⚠️ Saving a form deletes unplaced fields

**Every write to the form object deletes each field whose id is not placed on
one of its `form_ui` pages**, default and CLI-created fields alike. A field is
placed when a page's `page_data` holds a `CustomField` or `FormField` node whose
`props.field.id` is the field's id. The deleted fields' ids 404 on
`GET …/fields/{id}` afterwards, so the loss can't be undone.

- A new form has `form_ui: {}` and unplaced default fields (`first_name`,
  `last_name`, `email` on Contacts; `name` on a custom object), so its first
  update empties it.
- `set-ui` with a partial layout deletes the fields it leaves out.
- A field added with `fields create` after the layout is unplaced, and the next
  update deletes it.
- No PATCH body avoids it: a PATCH of `name`, `description` or `enabled`, one
  echoing the current `form_ui` back, a bare `form_ui: {}`, `{"pages": []}`,
  and a full PUT all delete the unplaced fields.
- Field-level writes (`PATCH …/fields/{id}`) delete nothing.

So `update` and `set-ui` refuse, naming the fields, until each one is placed
with `set-ui` or removed on purpose with `fields delete`.

A failing `fields create` batch on its own loses nothing: 2 good fields followed
by one that 400s left all 5 fields, read back at once and after 5 s. An earlier
report of a failing batch losing the fields it had created came from a later
save of the form, not from the batch.

Confirmed live 2026-10-16 on forms and surveys.

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

### Pages and buttons

A form moves between pages only through `Button` blocks. Each button's
**Action** in the builder is one of:

- **Submit and Finish**: `props.action: "submit"`, on every form and survey
  read so far (2026-11-02).
- **Submit and Go To Page** and **Do Not Submit and Go To Page**: the
  builder's code maps these to `go-to-page-submit` and `go-to-page`, with the
  target page in `props.goToPage`. No saved form with either has been read.

`set-ui` has no target-page setting, so it writes only a Submit and Finish
button. Set up multi-page navigation in the form builder.

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

Multi-`Section` pages; go-to-page buttons (see "Pages and buttons"); file uploads for `Image` blocks (an image block needs a
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

# Spec shape: `FieldDef` — custom-object fields

**Consumed by:** `kizen fields create <object> --spec-file <f>` (bulk) and
`kizen fields update` (single). Single-field creation can also use flags
(`--api-name/--name/--type/--category/--option/...`) — reach for a spec file
when you're adding **more than one field at once** (one plan/confirm/apply for
the whole batch).

> Always `kizen fields create --help` for the current flag list. This file
> documents the **spec-file shape** that `--help` can't show.

---

## Quick example

Two fields into an existing `Details` category, one plan/apply:

```json
{
  "category": "Details",
  "fields": [
    { "name": "Region", "api_name": "account_region", "field_type": "dropdown",
      "options": ["North", "South", "East", "West"] },
    { "name": "Seats", "api_name": "account_seats", "field_type": "integer" }
  ]
}
```

```bash
kizen fields create accounts --spec-file fields.json --dry-run   # preview
kizen fields create accounts --spec-file fields.json --yes       # apply (after approval)
```

---

## Two accepted top-level shapes

`fields create` accepts **either** form:

```jsonc
// (a) a bare list — every field uses the --category flag / its own "category"
[ { "name": "...", "api_name": "...", "field_type": "..." }, ... ]

// (b) an object with a default category for the batch
{ "category": "<category_display_name>", "fields": [ { ... }, ... ] }
```

**Category precedence** (most specific wins): a field's own `"category"` key >
the spec-level `"category"` > the `--category` flag. The category must already
exist (`kizen categories create <object> ...` first) — fields don't create
categories. **Match a category by its display name** (e.g. `Details`), *not* its
api_name — `kizen objects get <object>` lists the category display names.

---

## `FieldDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string (1–200) | **Required.** Human display name shown in the UI. |
| `api_name` | string | **Required.** `^[A-Za-z0-9_]+$`, unique within the object. See reserved-names gotcha. |
| `field_type` | enum | **Required.** One of the 24 types below. |
| `category` | string | Optional per-field override (bulk spec only; **the category's display name**; stripped before validation). |
| `description` | string (≤500) | Optional. |
| `required` | bool | Default `false`. |
| `read_only` | bool | Default `false`. |
| `hidden` | bool | Default `false`. Hides the field by default. |
| `options` | string[] | **Required** for `dropdown`/`radio`/`checkboxes`/`choices`; forbidden otherwise. Option **labels**. |
| `status_options` | `StatusOption[]` | **Required** for `status`; forbidden otherwise. |
| `relation` | `RelationConfig` | **Required** for `relationship`; forbidden otherwise. |
| `money_options` | `MoneyConfig` | Only for `money` (defaults to USD if omitted). |
| `rating` | `RatingConfig` | Only for `rating` (defaults to a 1–5 scale if omitted). |
| `decimal_options` | `DecimalConfig` | Only for `decimal`. |
| `phone_options` | `PhoneConfig` | Only for `phonenumber`. |

Exactly one type-specific block may be set, and it must match `field_type` —
mismatches are rejected at plan time.

### `field_type` values (24)

```
checkbox      checkboxes    choices       date          datetime
decimal       dropdown      dynamictags   email         files
integer       longtext      money         phonenumber   radio
rating        relationship  selector      status        team_selector
text          timezone      wysiwyg       yesnomaybe
```

`wysiwyg` is accepted and auto-translated to `longtext` + `meta.is_markdown`
(the API has no real `wysiwyg` type — see "Type quirks on the wire" below).

---

## Type-specific blocks

**`relation` (`RelationConfig`)** — for `relationship`:

```json
{ "name": "Account", "api_name": "deal_account", "field_type": "relationship",
  "relation": {
    "target_object": "accounts",
    "relation_type": "many_to_one",
    "related_name": "Deals",
    "target_category": null
  } }
```
- `target_object` (**required**): api_name of the target object.
- `relation_type`: use the UI-language cardinalities — `one_to_one`,
  `many_to_one` (this object holds one ref; many can share it — the usual
  case), `one_to_many`, `many_to_many`. Raw wire values
  (`primary`/`additional`/…) are also accepted. Default `many_to_one`.
- `related_name`: label for the inverse field on the target (defaults to this
  object's name).
- `target_category`: where the inverse field lands on the target (optional).

**`status_options` (`StatusOption[]`)** — for `status` (a standalone status
field; pipeline *stage* status is managed via `objects stages`, not here):

```json
{ "name": "Stage", "api_name": "deal_stage", "field_type": "status",
  "status_options": [
    { "name": "New",  "status": "open",  "percentage_chance_to_close": 10 },
    { "name": "Won",  "status": "won",   "percentage_chance_to_close": 100 },
    { "name": "Lost", "status": "lost" }
  ] }
```
- `status` ∈ `open|won|lost|disqualified` (default `open`).
- `code` optional; `percentage_chance_to_close` 0–100 optional.

**`money_options` / `rating` / `decimal_options` / `phone_options`:**

```json
{ "field_type": "money",   "money_options":   { "currency": "USD" } }
{ "field_type": "rating",  "rating":          { "min_value": 1, "max_value": 5,
                                                "min_label": "Low", "max_label": "High" } }
{ "field_type": "decimal", "decimal_options": { "min_value": 0, "max_value": 999999 } }
{ "field_type": "phonenumber", "phone_options": { "enable_extension": false } }
```
Omit the block to accept the defaults shown. A bare `rating` with no options
auto-generates the `"1 - Low" … "5 - High"` scale.

---

## Gotchas

- **Reserved api_names 500 on create.** `name`, `owner`, `created`, `updated`,
  `id`, and contact-ish names (`first_name`, `last_name`, `email`,
  `mobile_phone`, `birthday`, …) are auto-created by Kizen and rejected.
  **Rule of thumb: prefix api_names with the object's api_name**
  (`account_first_name`, not `first_name`).
- **Kizen may pluralize/rewrite api_names.** After applying, `kizen objects get
  <object>` to confirm the server's stored name before referencing it.
- **`required`/`read_only`** are for system fields, not custom ones — the
  single-field CLI flags don't expose them; setting them in a spec is accepted
  but usually a no-op for custom fields.
- **Editing options** on an existing select field uses
  `kizen fields options add/remove`, not a field update — see
  `fields options --help`.
- **Deleting a field drops its data** across all records, irreversibly.

---

# Wire format & API behavior

## Type quirks on the wire

| Spec `field_type` | What actually goes over the wire |
|---|---|
| `wysiwyg` | `field_type: longtext` + `meta: {is_markdown: true}` — there is no real `wysiwyg` type on custom objects or activities. The planner does this for you. |
| `status` | Uses the **`options`** wire key, `[{name, code}]`, exactly like `dropdown`. **Not** `status_options` (that's this spec file's friendlier input shape). |
| `yesnomaybe` | Option codes must be lowercase `yes` / `no` / `maybe`. The planner injects them. |
| `longtext` | Standard. Add `meta: {is_markdown: true}` by hand for rich text. |
| `rating` | With no options given, Kizen **auto-generates** a 1–5 scale (`"1 - Low"`, `"2"`, `"3"`, `"4"`, `"5 - High"`, codes `"1"`–`"5"`). Confirmed live 2026-07-20. |

**`wysiwyg` behaves differently on forms and surveys** — there it *is* a real
`field_type` and must **not** be remapped. See `kizen docs show form`; the
remap is specific to custom-object and activity fields, not a universal Kizen
rule.

## Field option & deletion endpoints

Options are edited through dedicated endpoints, **not** a field PATCH
(`object_pk` = the object's UUID):

| Operation | Method | Path |
|---|---|---|
| Delete field | `DELETE` | `/api/custom-objects/{object_pk}/fields/{field_id}` |
| Add option | `POST` | `.../fields/{field_pk}/options` — `{"name": …}` |
| Delete option | `DELETE` | `.../fields/{field_pk}/options/{id}` |
| Replace (remap) option | `POST` | `.../fields/{field_pk}/options/{id}/replace` — `{"id": <target_option_uuid>}` |

**Deleting a field, or an in-use option, drops the stored data.** To retire an
in-use option without data loss, POST to `.../options/{id}/replace` with the
survivor — it remaps records off the old option **and removes it**. That's what
`kizen fields options remove --remap-to` does.

Note the asymmetry: the custom-object `replace` both reassigns and deletes,
while the *activity* one only reassigns and needs an explicit DELETE after —
see `kizen docs show activity`.

## Relationship field creation

**`related_name` inside the `relation` block is functionally required.** The
OpenAPI spec marks it nullable and omits it from `required`, but leaving it out
returns **HTTP 500 every time** — confirmed empirically: `related_object` /
`related_category` alone, with or without `relation_type`, always 500s, and
adding any non-null `related_name` fixes it.

`model`, `is_default`, `meta`, `cardinality`, and the rollup/suppression flags
are **not** required — they're server-computed or read-only despite the
confusing OpenAPI shape. Minimal working payload:

```json
{
  "display_name": "…",
  "field_type": "relationship",
  "category": "<parent-category-uuid>",
  "relation": {
    "related_object": "<target-object-uuid>",
    "related_category": "<target-category-uuid>",
    "related_name": "<display label for the inverse field on the target object>",
    "relation_type": "primary"
  }
}
```

`name` (api_name) is **not** sent — Kizen auto-generates it from `display_name`.
The planner defaults `related_name` to the parent object's
`entity_name`/`display_name`; override it with `relation.related_name` in a spec
or `--relation-related-name` on `kizen fields create`.

### `relation_type` cardinality mapping

Kizen's wire enum (`one_to_one`, `primary`, `additional`, `primary_for`,
`additional_for`) **doesn't match the one_to_one/one_to_many/many_to_one/
many_to_many language its own UI uses**, and creating a relationship field
always auto-creates the mirror field on the target object. Confirmed
empirically by creating on A → target B, then inspecting B's generated field:

| you pick on A | cardinality (A→B) | auto-mirror on B | cardinality (B→A) |
|---|---|---|---|
| `one_to_one` | one_to_one | `one_to_one` | one_to_one |
| `primary` | many_to_one | `primary_for` | one_to_many |
| `primary_for` | one_to_many | `primary` | many_to_one |
| `additional` | many_to_many | `additional_for` | many_to_many |
| `additional_for` | many_to_many | `additional` | many_to_many |

`primary`/`primary_for` are a mirror pair — pick whichever side is the "many"
versus the "one". `additional`/`additional_for` are a mirror pair too, but both
sides are many_to_many either way, so it doesn't matter which you start from.
**`cardinality` itself is server-computed and read-only — never send it.**

This is why the spec above accepts the clear names: `relation.relation_type` and
`--relation-cardinality` take `one_to_one` / `many_to_one` / `one_to_many` /
`many_to_many` and translate to the wire value (`many_to_many` → `additional`).
Raw wire values still pass through unchanged, for specs authored from live API
output.

## See also

- `kizen docs show objects` — objects, categories, and pipeline stages (a
  pipeline's stages are **not** field options).
- `kizen docs show records` — writing values into these fields.
- `kizen docs show activity` / `kizen docs show form` — the same type blocks with
  their own divergences.

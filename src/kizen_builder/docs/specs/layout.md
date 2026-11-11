# Spec shape: `LayoutDef` — record layouts

**Consumed by:** `kizen layouts update <object> --spec-file <f>` (also stdin).
This is a **PUT-replace** of a record layout — the whole `config` is swapped.

> Author by reading the live layout first
> (`kizen layouts get <object> -o json`), editing it, and PUT-ing it back.
> Block `id`s are **injected automatically** at apply time, so authored blocks
> may omit them.

---

## Quick example

```json
{
  "name": "Standard View",
  "config": [
    {
      "columns": [
        { "width": 12, "blocks": [
          { "type": "fields", "fields": [
            { "field": "account_region" },
            { "field": "account_seats" }
          ] }
        ] }
      ]
    }
  ]
}
```

```bash
kizen layouts get accounts -o json > layout.json   # start from live
# …edit layout.json…
kizen layouts update accounts --spec-file layout.json --dry-run
```

---

## `LayoutDef` fields

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | Layout to target. An object's auto-created layout is `"Standard View"` (the default). |
| `config` | object[] | **Required.** List of column-group dicts (rows → columns → blocks). |
| `tabs` | object | Optional, e.g. `{"automations": true}`. Preserved from the live layout when omitted. |

## Block types (inside `columns[].blocks[]`)

- `fields` — the common one; lists `{ "field": "<api_name>" }` entries.
- `custom_content` — static-content / HTML block (CLI-wired).
- Other block types are **passed through opaquely** — copy from a live layout.

Column `width` is on a 12-unit grid.

## Gotchas

- **Whole-layout replace.** Anything omitted from `config` is gone — always
  start from `layouts get`, don't hand-author from nothing.
- **Block `id`s are auto-injected** — don't invent them; every item still ends
  up with a UUID (see "every item needs a UUID" below).

---

# Wire format & API behavior

```
GET /api/custom-objects/{id}/layouts                    # list
PUT /api/custom-objects/{id}/layouts/{layout_id}        # update (full replace)
```

No trailing slashes. **Kizen auto-creates a "Standard View" layout on object
creation** — always PUT to update it, never POST, which fails with "names must
be unique".

**A PUT needs the layout's own `name` in the body even when only `config`
changed.** A PUT with just `{"config": [...]}` 400s with
`"name: This field is required."` (confirmed live 2026-07-21).

## Critical: every item needs a UUID

Every level of the config must carry an `id`. **If ids are missing, Kizen
*merges* rather than replaces** — orphaned blocks from the previous layout
persist and corrupt the view. Required:

- the top-level config group — `config[0]["id"]`
- each column — `columns[n]["id"]`
- each item/block — `items[n]["id"]`

The CLI injects these for you; this matters if you're building a PUT by hand.

## Block types

**Block `type` is not only `fields`.** Real layouts also carry
`team_and_activities`, `lead_sources`, `action_block`, `timeline`, and
`custom_content`. Only `fields` blocks are built from a friendly spec — **every
other type must be preserved and passed through opaquely** when editing.

### `fields` blocks — two patterns

**Explicit include** — exact control over one category:

```json
{ "id": "<uuid>", "type": "fields", "label": "Fields",
  "metadata": { "excluded": [], "included": ["<cat-uuid>"], "autoInclude": false,
                "chosenCategories": [{"label": "Category Name", "value": "<cat-uuid>"}] },
  "displayName": "", "internalName": "Category Name" }
```

**Auto-include** — catches fields later added to the category. The `excluded`
list must contain **every other category UUID** or other categories bleed
through:

```json
{ "id": "<uuid>", "type": "fields", "label": "Fields",
  "metadata": { "excluded": ["<every-other-cat-uuid>", "…"], "included": [],
                "autoInclude": true,
                "chosenCategories": [{"label": "Category Name", "value": "<this-cat-uuid>"}] },
  "displayName": "", "internalName": "Category Name" }
```

### Column widths

`"third-width"` and `"two-third-width"` are the two valid values.

### `custom_content` blocks (static HTML)

Solved 2026-07-21 from a layout authored through the real Kizen layout builder.
One block item:

```json
{"id": "<uuid>", "label": "Custom Content", "type": "custom_content",
 "metadata": {"blockJson": { /* craft.js tree */ }}}
```

No `internalName`/`displayName` on this item — those are `fields`-block keys.

**`metadata.blockJson` is a nested object, NOT a JSON-encoded string.** This is
easy to get backwards, because a form/survey's `page_data` *is* a string while
using the exact same camelCase craft.js vocabulary:

`Root` → `Section` → `Row` (`props.columns` fractional widths, children via
`linkedNodes`) → `Cell` (`isCanvas: true`, `props: {}`, children in `nodes`) →
a leaf block: `Text`, `HTMLBlock` (content in **`props.htmlContent`**, not
`custom.text`), `Button`, `Divider`, `Image`.

`CustomField` blocks don't apply here — a layout isn't tied to one related
object the way a form is.

**The one confirmed structural difference from a form page's tree: this `Root`
carries no `container*`-prefixed props at all** — just `backgroundColor`,
`color`, `fontFamily`, `fontSize`, `linkColor`, `lineHeight`, `height`,
`maxWidth`, `hasShadow`, `tabletBreak`, `mobileBreak`. That matches the
dashboard static-content dashlet's Root shape (camelCased instead of
snake_case), not a form page's Root, which does carry `container*` keys.

**Styling defaults** match what the Kizen layout builder writes when a user
adds each node (read from the Kizen web app source, 2026-11-11). They are the
dashboard static-content block's defaults (`kizen docs show dashboard`, "HTML
dashlet styling"), except the `Root` is 400 tall and has no `columns`. Text,
divider, image and HTML blocks get the same props as on a form (`kizen docs
show form`, "Styling defaults").

## See also

- `kizen docs show files` — referencing an already-uploaded image from an
  `Image` block, and why you can't browse files over the API.
- `kizen docs show form` — the same craft.js vocabulary with the string-vs-object
  and `Root`-props differences called out.
- `kizen docs show objects` — the categories a `fields` block includes.

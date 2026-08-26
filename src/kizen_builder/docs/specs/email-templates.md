# Surface: email templates & automation messages

Two distinct resources, easy to confuse:

| resource | path | what it is |
|---|---|---|
| **Email template** | `/api/messages/templates` | the reusable library template you author in the email builder |
| **Automation message** | `/api/messages/automations/...` | one automation step's own message instance, usually cloned *from* a template |

`kizen messages templates list` reads the library. `kizen messages create <automation>
--template <name|uuid>` creates the automation message a `notify_member_via_email`
step points at.

## Building a template from a spec file — `messages templates create`/`update --spec-file`

`craft_json` (the editable tree) and `content` (the compiled, Outlook-safe
HTML that is actually sent) are independent stored fields, and the server
compiles neither from the other on `POST` or `PATCH` — see "Two content
fields that must be kept in sync" below. So a spec never names either field
directly; both are built together, from one pass over one node tree, by
`kizen messages templates create --spec-file <f>` (and `update <tmpl>
--spec-file <f>`, an alternative to that command's raw
`--craft-json-file`/`--content-file` PATCH path). Preview what a spec would
produce, offline, with `messages templates craft-config --spec-file <f>
[--out-html <file>]` — no args lists the available block kinds and layouts.

### Spec shape

```json
{
  "name": "Newsletter",
  "subject": "This month's update",
  "sections": [
    {
      "background_color": "#FFFFFF",
      "rows": [
        {
          "layout": "2 Columns",
          "cells": [
            {"blocks": [{"kind": "text", "html": "<p>Left column</p>"}]},
            {"blocks": [{"kind": "image", "file": "/path/to/logo.png", "alt": "Logo"}]}
          ]
        }
      ]
    }
  ]
}
```

- `sections[].rows[].layout` is one of **4 v1 presets**, a closed enum — a
  typo'd name is a spec-validation error, not a bad fractions array to catch
  later. `cells` must have exactly the count the preset needs.

  | preset | `columns` | `Cell.__width` |
  |---|---|---|
  | `1 Column` | `[1]` | `1` |
  | `2 Columns` | `[0.5, 0.5]` | `0.5`, `0.5` |
  | `2 Columns (1/3 and 2/3)` | `[0.3333333333333333, 0.6666666666666666]` | same |
  | `2 Columns (2/3 and 1/3)` | `[0.6666666666666666, 0.3333333333333333]` | same |

  Confirmed live 2026-08-25, byte-exact — not rounded, not recomputed as
  `1/3`. Five more presets (`3`/`4`/`5`/`6 Columns`, `3 Columns (gutters)`)
  are confirmed live but out of scope for this spec format; naming one is a
  clear error, not a silent reshape.
- `cells[].blocks[].kind` is one of `text`, `image`, `button`, `divider` — a
  closed set, same reasoning. There is no `attachments` kind and no raw-HTML
  escape hatch (no `HTMLBlock` on this surface at all, confirmed live).
  - `text`: `{"kind": "text", "html": "<p>...</p>"}` — embedded verbatim in
    both outputs.
  - `image`: `{"kind": "image", "file": "<local path>", "alt": "", "link": "", "width": 150}`.
    `file` is a **local path**, not a `file_id` — there is no CLI surface to
    look one up afterward (`GET /api/files` is broken; see below), so the
    spec captures the upload at the point it happens. **PNG and JPEG only**
    (pixel dimensions are read from the file's own header bytes, no
    dependency); GIF/WebP/SVG are rejected outright. Uploaded with
    `is_public=true` (confirmed live 2026-08-25 via `GET /api/docs/schema`
    on `POST /api/s3/success`) so the emitted `src` is reachable by a real
    recipient, not just an authenticated session. Uploading happens for real
    when a `create`/`update --spec-file` is actually applied; under
    `--dry-run` the CLI resolves images offline instead (a placeholder
    `fileId`/`src`, real dimensions still read from the file's own header
    bytes) so a dry run never writes.
  - `button`: `{"kind": "button", "label": "...", "url": "...", "color": null}`.
  - `divider`: `{"kind": "divider", "color": null}`.
- `sender_type` and `from_name_type` are not spec keys. They are hard-coded
  to `"business"`/`"default"`, the only values ever observed live — see
  "Other top-level fields" below. There is no `--sender-type` flag.
  **`from_name_type` is required on `POST`**, confirmed live 2026-08-25: a
  create without it 400s (`{"from_name_type": ["This field is required."]}`)
  — the earlier PATCH-only probing hadn't surfaced this since PATCH only
  needs the fields actually being changed.

No flag and no spec key on `create` accepts a raw `craft_json` or `content`
value, on purpose — that's the exact "hand-author both fields and hope they
agree" foot-gun this whole surface exists to close.

## Automation messages: create them *from a template*

A `notify_member_via_email` step's config has no subject/body — it is a bare
`id` pointing at an automation message. Kizen's builder UI "select email"
picker only recognizes a message as selected when it was created **from a real
template** (`base_message_id` set). A message created from raw content alone is
accepted by the API and is technically wired into the step, but shows as
*unselected* in the UI (confirmed live).

So: `kizen messages create <automation> --template <name|uuid>`, then reference
the returned UUID as the step's `email_template_id`.

## Merge fields in message content

`{{ <namespace>.<field_api_name> }}`. Confirmed namespaces:

- **`entity_record`** — the triggering record. Includes pseudo-fields that
  aren't real object fields (`link_url`, `created`, `estimated_close_date`).
- **`team_member`** — the notified team member's own fields.
- **`business`** — tenant settings.

No API-queryable catalog exists. Note the namespace token varies by step type —
`call_llm` prompts and variable static sources use the literal
`custom_objects.<field>` instead, regardless of the target object's real
api_name. Full table: `kizen docs show automation`.

Same convention applies in dashboard static-content text blocks and email
template `Text` blocks.

## Email template wire format

Confirmed live 2026-07-21 from a real save captured out of the Kizen email
template builder — `PATCH /api/messages/templates/{id}`. Read, clone,
create, update and delete are all CLI-wired (`kizen messages templates`) —
see "Building a template from a spec file" above for `create`/`update
--spec-file`/`craft-config`.

### Two content fields that must be kept in sync

- **`craft_json`** — the editable craft.js node tree (same camelCase
  vocabulary as form pages and layout `custom_content` blocks).
- **`content`** — the **actual send-time HTML body**: a fully-compiled,
  Outlook-safe MJML 4.x render (`<!--[if mso]-->` conditional comments, VML
  fallbacks, `mj-column-per-N` responsive classes, `.mj-outlook-group-fix`,
  `.moz-text-html` rules, inlined presentational styles, per-section `<style>`
  blocks keyed by a `section-<nodeId>` class).

**The server stores both verbatim and compiles neither from the other.**
Confirmed live 2026-08-25, both directions:

| PATCH | result |
|---|---|
| modified `craft_json` alone | stored; `content` came back **byte-identical**, still carrying the old copy |
| modified `content` alone | stored; `craft_json` unchanged |
| `content: null` | `HTTP 400 — content: This field may not be null.` |
| `craft_json: null` | `HTTP 400 — craft_json: This field may not be null.` |

So there is no server-side render to lean on, and no way to clear a field and
let it regenerate. A `craft_json`-only update leaves the email that actually
gets sent out of sync with what the builder shows — silently, since the
builder only ever reads `craft_json`.

### The two fields are coupled by node id

Not merely parallel: the compiled HTML carries a `section-<nodeId>` class for
every `Section` **and** every `Row` node. Verified against two independent
captures — in both, the `section-` class set and the Section/Row id set match
exactly, with no orphans on either side (`Section`×4 + `Row`×5 = 9 classes).
`Cell`/`Text`/`Image` nodes get no class.

**A generator must therefore emit both fields from one pass over one tree with
one set of ids.** Building the tree and then compiling HTML in a second step
that mints its own ids produces a broken template.

`kizen messages templates get` reports both halves of this: `structure
coupled` (id agreement) and `text in sync` (every `Text` node's copy actually
appears in the HTML). The second check exists because text-only drift — the
exact thing a `craft_json`-only PATCH produces — leaves the structure intact
and passes the id check.

Other top-level fields: `name`, `type` (`"email"`), `base_message_id` (set when
cloned from another template), `subject`, `sender_type` (`"business"` seen
live), `sender_role_id` / `sender_field_id` / `sender_team_member_id`,
`external_account` / `external_account_id`, `from_name_type` (`"default"`),
`custom_from_name`.

### `craft_json` node shape

Same topology as a form page: `Root` → `Section` → `Row` → `Cell` → leaf
block. `Row.nodes` stays `[]` and its children hang off
`linkedNodes` as `{"column-N": <cellId>}`. Leaf blocks confirmed live
2026-08-25: `Text`, `Image`, `Button`, `Divider`, `Attachments`. There is
**no `HTMLBlock`** — confirmed directly against the real builder, so there is
no raw-HTML escape hatch on this surface.

Confirmed differences from a form page:

- **`Root.props` is a form page's key set minus `tabletBreak`** — still has
  `backgroundColor`/`color`/`fontFamily`/`fontSize`/`linkColor`/`lineHeight`/
  `width`/`maxWidth`/`alignment`/`mobileBreak` plus the full `container*` set.
  The omission is confirmed present in two independent captures.
- **`Cell.props` is `{"__width": <fraction>}`** (e.g. `0.5` in a two-column
  row), not the empty `{}` that forms and layouts use — a literal
  double-underscore-prefixed key, redundant with the parent `Row`'s `columns`
  array but apparently expected here. Both must be set and must agree.
- **`displayName` is cosmetic.** Most nodes carry their own type name, but
  blocks inserted by the current builder get short random labels (`M1e`,
  `N1e`, `jBi`, `_F`). Nothing appears to read it.

Block props confirmed live 2026-08-25:

- **`Text`** — copy lives in `custom.text` as an HTML string, not in props.
  `content` embeds that markup **verbatim**, so comparing the two means
  tag-stripping both sides.
- **`Image`** — `src` is host-absolute, so an image reference is
  **environment-bound**; moving a template between envs needs `src`
  rewritten. Two URL schemes are both confirmed live on a plain `Image`
  node, not just on `Attachments`: `https://<host>/api/files/{fileId}/download`
  (used by the "All Rows" capture and most templates) and
  `https://<host>/api/public/s3/{fileId}/download` (seen on one image in
  another template) — either is accepted; `messages templates create`/
  `craft-config` always emit the `/api/files/` form. `naturalWidth`/
  `naturalHeight` are the uploaded file's real pixel dimensions and are not
  derivable from a spec — they have to be read off the image (this surface's
  spec-file emitter parses them from the file's own PNG/JPEG header bytes).
  **The uploaded file must be `is_public: true`** or `src` 404s for anyone
  without an authenticated Kizen session — confirmed live 2026-08-25 (`POST
  /api/s3/success`'s own `is_public` field, `GET /api/docs/schema`; both URL
  schemes 200 unauthenticated once set, 404 on both when not). The
  spec-file emitter's upload path sets it; a raw `upload_file()` call
  elsewhere in this repo does not unless asked (see `api/files.py`).
- **`Button`** — `{url, label, action: "url", color, textColor, fontSize,
  fontFamily, alignment, borderSize, borderColor, borderRadius,
  padding{Top,Left,Right,Bottom}, textStyles: [], openLinkInNewTab}` plus the
  `container*` set. The emitter's compiled `content` markup for this node
  (`_render_button`) was checked byte-exact against a real Button in a
  Kizen-authored template, read-only, 2026-08-26.
- **`Divider`** — `{size, color, width, alignment, borderStyle}` plus the
  `container*` set. Same verification: `_render_divider`'s output matches a
  real captured Divider's compiled markup byte-exact.
- **`Attachments`** — `props.attachments` is a list of **full file records**
  (id, key, url, name, size_bytes, content_type, thumbnail_url, `is_public`,
  and an `employee` object naming the uploader), plus an
  `attachmentIconUrl`. Note the URL scheme differs from `Image`:
  `/api/public/s3/<id>/download?disposition=attachment`. The embedded `key`
  contains the business id, and the `employee` block carries a real user's
  name and email — treat a captured `Attachments` node as environment-bound
  and as carrying personal data.

### If you build on this

A **surgical** edit is low-risk and proven: replacing an exact existing
substring in both `craft_json`'s `custom.text` and the parallel `content` HTML
round-trips and renders correctly (confirmed live 2026-07-21 by appending a
merge-field span to an existing `Text` node).

Generating genuinely **new** structure — new Sections/Rows/Images/Buttons from
scratch, the way forms/layouts/dashboards builders do — additionally requires
hand-producing matching Outlook-safe compiled HTML for `content`, with the
node ids threaded through it. **Built** by `tools/email_craft.py`'s
`build_email_content()`, wired to `messages templates create`/`update
--spec-file`/`craft-config` — see "Building a template from a spec file"
above. It covers `Text`/`Image`/`Button`/`Divider` and the 4 v1 column
presets; `Attachments` and the other 5 presets are confirmed live but still
unbuilt (their `columns`/markup are pre-captured for a follow-on). This
stayed meaningfully higher-stakes than the other craft.js surfaces even once
built: a wrong `content` is what real recipients actually receive, not an
editor-only concern, and nothing offline can substitute for opening a real
test send in Outlook.

`kizen messages templates clone` is still the safe path for copying an
existing design: it copies both content fields together, so the copy is
internally consistent by construction. Build the design once in the builder
UI (or with `create --spec-file`), then clone and surgically edit it.

Everything needed to build on the generation slice — node shapes, the
coupling rule, the compile findings, the spec-file format — is in this
document. It is deliberately the only home for those facts.

## See also

- `kizen docs show automation` — the `notify_member_via_email` /
  `notify_member_via_text` steps that consume these, and the per-step-type
  merge-field namespace table.
- `kizen docs show layout` / `kizen docs show form` — the same craft.js
  vocabulary, with the casing and `Root`-props differences called out.

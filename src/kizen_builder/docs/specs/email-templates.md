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
      "max_width": "600",
      "container_width": "900",
      "padding": {"top": "10", "right": "10", "bottom": "10", "left": "10"},
      "rows": [
        {
          "layout": "2 Columns",
          "width": "100",
          "container_width": "580",
          "padding": {"top": "10", "right": "10", "bottom": "10", "left": "10"},
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
  - `image`: `{"kind": "image", "file": "<local path>", "alt": "", "link": "", "width": 150,
    "container_width": null, "max_width": null, "max_height": null}`.
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
    bytes) so a dry run never writes. `container_width`/`max_width`/
    `max_height` default to `null`, meaning the corresponding `Image.props`
    key is omitted entirely, matching this emitter's output before these
    fields existed.
  - `button`: `{"kind": "button", "label": "...", "url": "...", "color": null,
    "border_radius": "8", "padding_left": "20", "padding_right": "20",
    "alignment": "center"}`. `alignment` is a closed enum
    (`left`/`center`/`right`).
  - `divider`: `{"kind": "divider", "color": null, "size": "3"}` — `size` is
    the rule's thickness in px.
- `sections[].max_width`/`container_width`/`padding` and
  `sections[].rows[].width`/`container_width`/`padding` set `Section`/`Row`
  width and padding directly. `padding` is `{"top", "right", "bottom",
  "left"}`, four independent strings — not a CSS-style shorthand, since the
  wire format itself has four independent `containerPadding{Top,Right,
  Bottom,Left}` keys (a shorthand would be a lossy abstraction over that).
  **Confirmed live 2026-08-26 against the reference template
  (`0bc71ca1-72c7-4f8d-934e-322d9fd40975`): `Row` layout props are not
  uniform.** Most rows in that template share `containerWidth: '580'` and
  uniform `10` padding, but the same template also has rows with
  `containerWidth: '600'`, one with asymmetric padding
  (`containerPaddingLeft/Right: '40'`, top/bottom still `10`), one with all
  four paddings at `0`, and one with `Row.width: '75'` (not the usual
  `100`) — `Row.containerWidth` does **not** cleanly derive from
  `Section.max_width - 2*padding` across these observations. So
  `width`/`container_width`/`padding` are independent, spec-settable fields
  on both `SectionDef` and `RowDef`, never computed from one another — the
  same "redundant-but-must-agree" trust model this surface already uses for
  `Row.props.columns` vs. every `Cell.props.__width`.

  Defaults reproduce this emitter's pre-existing hardcoded output exactly
  (`Section.max_width: "900"`, `Row.width: "100"`, all padding uniform
  `"10"`, no `containerWidth` key on either node) — **not** the reference
  template's own common values (`Section.max_width: "600"`,
  `Row.containerWidth: "580"`), which a spec sets explicitly when targeting
  that layout.

  **These values also drive the compiled `content`'s row width — not just
  `craft_json`.** Each row's mso table width and its `.section-<rowId> {
  max-width:...px; }` rule use an explicit `Row.container_width` directly
  when the spec set one; otherwise they derive it as `Section.max_width -
  (containerPaddingLeft + containerPaddingRight)` from the row's parent
  `Section`. That result is then scaled by `Row.width` (percent, default
  `"100"`) — confirmed live against the reference template, where a
  `width: '75'` row on a `containerWidth: '580'` row compiles to `435px`
  (`580 * 0.75`), not `580px`. With every field at its default this
  reduces to exactly `900 - 10 - 10 = 880`, this emitter's original
  hardcoded value — so an all-defaults spec's compiled HTML is unchanged. A
  spec that sets `Section.max_width` (or a row's own `container_width`/
  `width`) without this derivation would otherwise produce a `craft_json`
  that renders at the new width in Kizen's builder while `content` — the
  HTML actually sent — stayed at the old width: `craft_summary()`'s
  `structure_coupled`/`text_in_sync` checks cannot see this class of
  divergence, since node ids and text both still match.

  **Padding follows the same rule.** Each `Section`/`Row`'s own
  `containerPadding{Top,Right,Bottom,Left}` — default uniform `"10"` or an
  explicit `padding` override — is rendered as an inline
  `padding:Tpx Rpx Bpx Lpx;` (top/right/bottom/left, matching
  `_render_button`'s existing padding order) on that node's own wrapper
  `<div class="section-<nodeId>">` in `content`. This applies to every
  section and row, not only ones that explicitly set the new `padding`
  field — before this fix, `content` carried **no** padding declaration
  for `Section`/`Row` at all, on any template, so text always rendered
  flush against the canvas edge regardless of what `craft_json` said.

  **`Section.container_width` reaches `content` (BCLI-025), via an outer
  mso-conditional background-table wrapper.** An explicit `container_width`
  gets `_render_section` to wrap the section's own div in `<!--[if mso |
  IE]><table ... width="<containerWidth>" style="width:<containerWidth>px;">
  ...<![endif]-->`, plus a `.section-<sectionId> { max-width:...px; }` rule
  — a *simplified* version of Kizen's own wrapper: this emitter has no
  background-image concept at all (only `background_color`), so it skips the
  VML `<v:rect>`/`<v:fill>` fallback Kizen's compiler always emits alongside
  it, even for a solid colour. Section's own `background_color` is applied
  via a CSS class rule, as before — this fix only adds the width wrapper.
  When `container_width` is unset (the common case for an unmodified spec),
  `content` is unchanged from before this fix — no wrapper at all, matching
  this surface's existing "`None` means no override" convention.
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

A merge field is **not** a bare `{{ <namespace>.<field_api_name> }}` token —
that renders as inert literal braces in a recipient's inbox. Kizen's builder
UI always wraps the token in a `<span class="kzn-merge-field">` marker
(confirmed live 2026-08-26), and that wrapper is the real authoring/wire
format everywhere merge fields appear — automation notify steps, `call_llm`/
`file_content_extraction` prompts, dashboard static-content text blocks, and
email template `Text` blocks alike:

```html
<span class="kzn-merge-field"
      data-merge-field-fallback-label="Stage"
      data-merge-field-relationship="object_with_workflow.stage"
      data-merge-field-objectname="object with workflow">{{ object_with_workflow.stage }}</span>
```

`data-merge-field-objectname` (the object's *display* name) is present only
when the namespace is a real custom object's api_name; reserved namespaces —
`entity_record` (the triggering record, including pseudo-fields that aren't
real object fields: `link_url`, `created`, `estimated_close_date`),
`team_member` (the notified team member's own fields), `business` (tenant
settings), `contact`, `automation_variable`, `automation_history`, and
`custom_objects` (the literal token `call_llm`/`initialize_variable` use for
the automation's own target_object) — never carry it. No API-queryable
catalog of namespaces exists; any custom object's api_name can be one. Full
table and the shared span-building rules: `kizen docs show automation`,
`src/kizen_builder/tools/merge_fields.py`.

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

**This emitter's own `content` fidelity against Kizen's own compiler
(BCLI-025).** All confirmed against the reference template's real compiled
`content`, not inferred from `craft_json`: `font-family` (see "Text" below),
`.moz-text-html .mj-column-per-N { ... }` rules (one per distinct column
class, scoped to `@media (min-width:<mobileBreak>px)`, matching Kizen's own
wrapping — Gecko clients key column-stacking behaviour off this selector),
the static MJML reset block (`#outlook a`/`body{margin:0}`/
`table,td{border-collapse}`/`img{...}`/`p{display:block;margin:13px 0}`),
`@media (max-width:<mobileBreak>px)`'s breakpoint reading
`Root.props.mobileBreak` instead of a hardcoded `480`, `<body>`'s
`background-color` (from `Root.props.backgroundColor`) plus a matching
outer wrapping `<div>`, and no float-formatted lengths (`880px`, never
`880.0px`) anywhere in a row's compiled width output, including its
per-column mso `<td>` widths. **Not** replicated: Kizen's
own 6-`<style>`-block structure (this emitter stays at a handful of
`<style>` tags — the block *count* is organizational, not a correctness
gap) or its full VML/background-image fallback markup on the Section
wrapper (see `Section.container_width` below — this emitter has no
background-image concept at all).

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
  tag-stripping both sides. The wrapper *around* that markup carries a
  `kizen-text-styles` class and inline typography sourced from
  `Root.props` (`fontFamily`, `fontSize`, `color` — rgba->hex converted;
  `line-height:1`/`text-align:left` are fixed literals with no controlling
  Root prop in the reference), confirmed against the reference template's
  compiled `content` (BCLI-025) — before that fix, `content` carried no
  `font-family` for text at all, so every email rendered in the client's
  serif fallback. The `.kizen-text-styles` `<style>` rules themselves
  (link/paragraph/list/code styling) are static except for `linkColor`.
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
  `position: "center"` (the only value ever hardcoded — not spec-settable,
  no observed alternative) compiles to `align="center"` on the block-level
  `<td>` wrapping the image (see below) — confirmed against the reference
  template; the `<img>` itself carries no centering style of its own.

  **Two sizing modes (BCLI-025), confirmed against the reference template's
  compiled `content`**, chosen by whether `ImageBlockDef.width` is set:

  | `width` | compiled `<img>` `width` attr | `.image-<nodeId>-auto` rule |
  |---|---|---|
  | set (fixed mode) | that value | not emitted |
  | omitted (auto mode) | parent Section's `containerWidth`, falling back to `Root.maxWidth` if unset (the fallback is *inferred*, not observed live) | emitted: `.image-<nodeId>-auto > table td { width:100% !important; max-width:<naturalWidth>px; }` |

  Both modes compile the `<img>` to the same attribute/style set
  (`height="auto"`, `width:100%` in the inline style alongside the explicit
  pixel `width` attribute, `outline:none`, `text-decoration:none`,
  `font-size:13px`) and wrap it in the same two-level table Kizen's own
  compiler uses — an outer `<td align="center" class="...">` carrying the
  image block's own `container*` padding, then a nested
  `<table><td style="width:...px;">` around the `<img>`. `content`'s
  previous `data-natural-width`/`data-natural-height` attributes are gone —
  grepped as unused anywhere in this repo, so purely decorative in this
  emitter's own prior output. `container_width`/`max_width`/`max_height`
  remain spec-settable (`ImageBlockDef`) but stay **`craft_json`-only** —
  confirmed absent from the reference's compiled `content` in both sizing
  modes, not merely "no consumer in `_render_image`".
- **`Button`** — `{url, label, action: "url", color, textColor, fontSize,
  fontFamily, alignment, borderSize, borderColor, borderRadius,
  padding{Top,Left,Right,Bottom}, textStyles: [], openLinkInNewTab}` plus the
  `container*` set. The emitter's compiled `content` markup for this node
  (`_render_button`) was checked byte-exact against a real Button in a
  Kizen-authored template, read-only, 2026-08-26. `borderRadius`/
  `padding{Left,Right}`/`alignment` are spec-settable
  (`ButtonBlockDef.border_radius`/`padding_left`/`padding_right`/
  `alignment`), defaulting to this emitter's pre-existing hardcoded values
  (`"8"`/`"20"`/`"20"`/`"center"`); the reference template's own newsletter
  button uses `"20"`/`"30"`/`"30"`/`"center"`, set explicitly, not inherited
  as the default. `alignment` compiles to an `align="..."` attribute on the
  button's own wrapping `<table>` (which also carries `line-height:100%;`
  in its `style` — both confirmed present on a real Kizen-compiled button by
  independent comparison in this item's review); without it every button
  renders left-aligned in its cell regardless of the spec.
- **`Divider`** — `{size, color, width, alignment, borderStyle}` plus the
  `container*` set. Same verification: `_render_divider`'s output matches a
  real captured Divider's compiled markup byte-exact. `size` (thickness in
  px) is spec-settable (`DividerBlockDef.size`), defaulting to this
  emitter's pre-existing hardcoded `"3"`; the reference template's divider
  uses `"1"`, set explicitly.
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

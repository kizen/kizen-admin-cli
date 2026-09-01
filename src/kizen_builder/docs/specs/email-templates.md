# Surface: email templates & automation messages

Two distinct resources, easy to confuse:

| resource | path | what it is |
|---|---|---|
| **Email template** | `/api/messages/templates` | the reusable library template you author in the email builder |
| **Automation message** | `/api/messages/automations/...` | one automation step's own message instance, usually cloned *from* a template |

`kizen messages templates list` reads the library. `kizen messages create <automation>
--template <name|uuid>` creates the automation message a `notify_member_via_email`
step points at.

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
template builder — `PATCH /api/messages/templates/{id}`. Read, clone, update
and delete are CLI-wired (`kizen messages templates`); generating a template
from a spec file is not.

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
- **`Image`** — `src` is host-absolute
  (`https://<host>/api/files/<fileId>/download`) alongside a `fileId`, so an
  image reference is **environment-bound**; moving a template between envs
  needs the `src` rewritten. `naturalWidth`/`naturalHeight` are the uploaded
  file's real pixel dimensions and are not derivable from a spec — they have
  to be read off the image.
- **`Button`** — `{url, label, action: "url", color, textColor, fontSize,
  fontFamily, alignment, borderSize, borderColor, borderRadius,
  padding{Top,Left,Right,Bottom}, textStyles: [], openLinkInNewTab}` plus the
  `container*` set.
- **`Divider`** — `{size, color, width, alignment, borderStyle}` plus the
  `container*` set.
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
node ids threaded through it. Not attempted, and meaningfully higher-stakes
than the other craft.js surfaces: a wrong `content` is what real recipients
receive, not an editor-only concern. Since the server will not compile it for
you (see the table above), that emitter has to live somewhere.

`kizen messages templates clone` is the safe path in the meantime: it copies
both content fields together, so the copy is internally consistent by
construction. Build the design once in the builder UI, then clone and
surgically edit it.

Everything needed to build the generation slice — node shapes, the coupling
rule, the compile findings — is in this document. It is deliberately the only
home for those facts.

## See also

- `kizen docs show automation` — the `notify_member_via_email` /
  `notify_member_via_text` steps that consume these, and the per-step-type
  merge-field namespace table.
- `kizen docs show layout` / `kizen docs show form` — the same craft.js
  vocabulary, with the casing and `Root`-props differences called out.

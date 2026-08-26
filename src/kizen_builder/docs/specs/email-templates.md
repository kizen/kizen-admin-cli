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
template builder — `PATCH /api/messages/templates/{id}`. **Not CLI-wired**;
this is ground truth for a future slice.

### Two content fields that must be kept in sync

- **`craft_json`** — the editable craft.js node tree (same camelCase
  vocabulary as form pages and layout `custom_content` blocks).
- **`content`** — the **actual send-time HTML body**: a fully-compiled,
  Outlook-safe MJML-style render (`<!--[if mso]-->` conditional comments, VML
  fallbacks, `mj-column-per-N` responsive classes, inlined presentational
  styles, per-section `<style>` blocks keyed by a `section-<nodeId>` class).

**The API does not derive one from the other — both are independent stored
fields sent in the same PATCH.** This is the one structurally distinct risk of
this surface versus forms/layouts/dashboards, where only the editable tree
matters and the renderer produces the visible output: a `craft_json`-only
update leaves the email that actually gets sent out of sync with what the
builder shows.

Other top-level fields: `name`, `type` (`"email"`), `base_message_id` (set when
cloned from another template), `subject`, `sender_type` (`"business"` seen
live), `sender_role_id` / `sender_field_id` / `sender_team_member_id`,
`external_account` / `external_account_id`, `from_name_type` (`"default"`),
`custom_from_name`.

### `craft_json` node shape

Nearly identical to a form page's tree: `Root` → `Section` → `Row`
(`props.columns` fractional widths) → `Cell` → leaf block (`Text`, `Image`).
No `Button`/`Divider` example captured yet, and **no `HTMLBlock` at all** —
confirmed directly against the real builder, not merely unmodeled here.

Two confirmed differences from a form page:

- **`Root.props` is a form page's key set minus `tabletBreak`** — still has
  `backgroundColor`/`color`/`fontFamily`/`fontSize`/`linkColor`/`lineHeight`/
  `width`/`maxWidth`/`alignment`/`mobileBreak` plus the full `container*` set.
  Omitting `tabletBreak` is very likely harmless rather than required; not
  independently tested.
- **`Cell.props` is `{"__width": <fraction>}`** (e.g. `0.5` in a two-column
  row), not the empty `{}` that forms and layouts use — a literal
  double-underscore-prefixed key, redundant with the parent `Row`'s `columns`
  array but apparently expected here.

### If you build on this

A **surgical** edit is low-risk and proven: replacing an exact existing
substring in both `craft_json`'s `custom.text` and the parallel `content` HTML
round-trips and renders correctly (confirmed live 2026-07-21 by appending a
merge-field span to an existing `Text` node).

Generating genuinely **new** structure — new Sections/Rows/Images/Buttons from
scratch, the way forms/layouts/dashboards builders do — additionally requires
hand-producing matching Outlook-safe compiled HTML for `content`. Not
attempted, and meaningfully higher-stakes than the other craft.js surfaces: a
wrong `content` is what real recipients receive, not an editor-only concern.

## See also

- `kizen docs show automation` — the `notify_member_via_email` /
  `notify_member_via_text` steps that consume these, and the per-step-type
  merge-field namespace table.
- `kizen docs show layout` / `kizen docs show form` — the same craft.js
  vocabulary, with the casing and `Root`-props differences called out.

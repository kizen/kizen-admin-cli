# Changelog

Notable changes to `kizen-builder`, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are written by hand in the change that makes them, not generated from
commit messages — the audience is someone deciding whether to upgrade and what
will be different afterwards, which is a different document from `git log`.
Anything a user would notice belongs here; internal refactors don't.

While the version is `0.x`, a minor bump may carry a breaking change. Those are
called out explicitly under **Changed** or **Removed**.

## [Unreleased]

### Added

- **`kizen messages templates get/clone/update/delete` — the email-template
  surface can now be read and written, not just listed.** `list` only ever
  returned summary fields, so there was no way to see a template's body at
  all; `get` shows it (`--raw` dumps the full payload, the starting point for
  building a new one).

  The thing to know about this surface: a template stores the editable
  `craft_json` tree **and** the compiled `content` HTML that actually gets
  sent, and the server compiles neither from the other — confirmed live by
  PATCHing a modified `craft_json` alone and reading `content` back
  byte-identical. Writing one without the other leaves the builder showing one
  email while recipients receive another, silently. So `get` reports two drift
  checks — `structure coupled` (every `Section`/`Row` node has its matching
  `section-<nodeId>` class in the HTML) and `text in sync` (every `Text`
  node's copy actually appears there) — and `clone` always copies both fields
  together, which makes it the safe way to branch a design built in the
  builder UI.

  Generating a template from a spec file is still not wired; see `kizen docs
  show email-templates`.

- **`kizen messages templates create --spec-file <f>` builds a complete email
  template — `craft_json` and the compiled, Outlook-safe `content` HTML —
  from one declarative spec.** `update <tmpl> --spec-file <f>` rewrites an
  existing template the same way, as an alternative to its existing
  field-level `--craft-json-file`/`--content-file` PATCH path. Both fields
  come from one pass over one node tree, so a spec can never describe one
  without the other — no flag and no spec key accepts a raw `craft_json` or
  `content` value.

  A spec's rows pick one of 4 column layouts by name (`1 Column`, `2
  Columns`, `2 Columns (1/3 and 2/3)`, `2 Columns (2/3 and 1/3)`) and cells
  hold `text`/`image`/`button`/`divider` blocks — both closed sets, so an
  unsupported layout or block kind is a clear error, never a silent partial
  template. An `image` block names a local PNG/JPEG file; it's uploaded
  (`source="public_image"`, publicly readable so recipients can load it) and
  its real pixel dimensions are read from the file's own header bytes — no
  new dependency. `--dry-run` resolves images offline instead of uploading,
  so it never writes. `messages templates craft-config` previews the
  `{craft_json, content}` pair offline, with `--out-html` to drop the
  compiled body somewhere a browser (or Outlook) can open it.

  A real test send opened in Outlook is still the only way to confirm actual
  rendering — nothing offline can substitute for that.

- **Email template specs can now set the layout knobs a designed newsletter
  needs — `Section`/`Row` width and padding, `Divider` thickness, `Button`
  corner radius/padding/alignment — instead of every template landing at
  this emitter's fixed defaults.** `sections[].max_width`/`container_width`/
  `padding` and `sections[].rows[].width`/`container_width`/`padding` set
  `Section`/`Row` props directly; `padding` is four independent
  `{top, right, bottom, left}` strings, matching the wire format's four
  independent `containerPadding*` keys rather than a lossy CSS-style
  shorthand. `button` blocks gain `border_radius`/`padding_left`/
  `padding_right`/`alignment`; `divider` blocks gain `size`. Every new field
  defaults to this emitter's exact pre-existing hardcoded value, so a spec
  that sets none of them produces the same output as before this change.
  The compiled `content` HTML's row widths now track these same values too
  (previously frozen at a hardcoded 880px regardless of what the spec set —
  a real `craft_json`/`content` divergence, the exact failure this whole
  surface exists to prevent), `content` now carries `Section`/`Row`
  padding at all (previously absent entirely, on every template — text
  always rendered flush against the canvas edge regardless of what
  `craft_json` said), `content`'s `Button` markup now carries `align`
  (previously every button rendered left-aligned regardless of the spec's
  `alignment`), and a centered `Image` (`position: "center"`, the only
  value this surface sets) now actually renders centered in `content`
  instead of flush left.

### Fixed

- **Compiled email `content` no longer diverges from what Kizen's own
  builder produces for the same layout.** Every recipient's email now
  carries a real `font-family` for body text (`Root.props.fontFamily`,
  via the same `kizen-text-styles` wrapper class/`<style>` rules Kizen's
  own compiler uses) — previously `content` carried no `font-family` at
  all, so any template without hand-inlined font styles rendered in the
  client's serif fallback on every send. Also fixed: the missing
  `.moz-text-html` rule (Gecko-based clients like Thunderbird key
  column-stacking behaviour off it), the missing MJML reset block, a
  hardcoded `480px` mobile breakpoint that now reads
  `Root.props.mobileBreak`, a dropped `<body>` background colour, and
  `Section.container_width` now reaching `content` via an outer
  background-table wrapper (previously `craft_json`-only, deferred from
  the layout-knobs change above). `Image` blocks gain a genuine
  full-bleed auto-sizing mode — omitting `width` in an `image` spec block
  now sizes the image to its parent Section's `containerWidth` (capped at
  its own natural width by a per-image CSS rule) instead of silently
  defaulting to a fixed 150px — and their compiled markup now matches
  Kizen's own attribute/style set exactly (`data-natural-width`/
  `data-natural-height`, confirmed unused anywhere in this repo, are
  gone). Finally, a float-formatting artifact that printed
  `880.0px`-style widths in the compiled CSS (or any other row whose
  computed width happened to land on a whole number) now prints `880px`.
  Every fix was checked against Kizen's real compiled `content` for the
  same layout, not inferred from `craft_json` alone — see `kizen docs
  show email-templates`.

- **Automation email/text merge-field markup now matches what Kizen's builder
  UI actually writes**, fixing three divergences in the `notify_member_via_email`/
  `_via_text`, `call_llm`, and `file_content_extraction` steps' derived HTML:
  - A `{{ ns.field.field }}`-shaped multi-segment relationship-hop token (a
    real one, `custom_objects.primary_document_record.id`, is captured in
    this repo's own fixtures) previously failed to match the merge-field
    regex at all and rendered as literal, unconverted `{{ ... }}` braces in
    the recipient's message. The token grammar now accepts one or more
    dot-separated segments.
  - A real custom-object namespace (e.g. a related record's own object
    api_name) now gets `data-merge-field-objectname` holding that object's
    display name, matching every custom-object merge field Kizen's UI has
    ever been observed to write. Previously this attribute was never emitted
    at all.
  - Fallback labels for `team_member`/`business` are now Kizen's real
    stored field display names. The email builder's Business and Team Member
    merge-field pickers were captured in full — all 17 entries, matching the
    picker counts exactly — so these are pinned, verified values rather than
    guesses (e.g. `business.postal_code` -> "Business Zip/Postal Code" and
    `business.reply_to_email` -> "Business Notification Email", neither
    reachable from the api_name by any transform). A token outside those
    picker lists now at least keeps its namespace prefix;
    `automation_variable.<name>` now keeps the variable name literal, since
    Kizen never title-cases those. (`automation_history` labels turned out
    to vary by containing automation rather than being fixed per field, so
    they still fall back to a title-cased guess rather than a pinned
    value.) The rules are consolidated in a new `tools/merge_fields.py`,
    shared with a future email-template emitter instead of being
    re-derived.

- **`kizen upgrade --check` can now find a release tag from a `uv tool
  install`/`pipx`/direct-VCS install, not just an editable checkout.**
  Previously any non-checkout install shape skipped straight to the
  unimplemented package-index seam and always reported "no distribution
  channel is configured for this install" — true or not, and regardless of
  whether a release existed upstream. `Install` now carries the bare git URL
  from `direct_url.json` (`repo_url`) when one is known for these shapes, and
  the check runs the same `git ls-remote --tags` comparison a checkout uses.
  There's still no local history to fall back to counting commits against for
  these installs, so before a `vX.Y.Z` tag exists the answer stays
  inconclusive — just honestly ("the remote has no release tags yet") instead
  of implying no channel is configured at all.

- **`kizen objects list` now includes built-in objects like Contacts
  (`client_client`), not just custom ones.** The server excludes built-ins by
  default; `list_objects()` called it without `custom_only=false` and then
  filtered any built-in back out client-side even if it had come back. Both
  filters are gone — one paginated call now returns everything, matching the
  `custom_only=false` pattern already used by `schema.py` and the
  smart-connector authoring helpers. This also fixes a real break, not just a
  missing display row: `kizen activities list --object client_client` and
  associating an activity type with Contacts via `activities update --object
  client_client` previously failed with `object 'client_client' not found`,
  since both resolve through the same `list_objects()` call.

### Added

- **`kizen records archive` / `kizen records unarchive`.** Archiving a record —
  the operation the UI's Archive button performs — is now something the CLI
  can do, through the same plan → preview → confirm → apply gate as every
  other record mutation. Previously the only way to archive from a script was
  `PATCH .../{id}` with `{"archived": true}`, which returns 200 and does
  nothing — see the `archived` Gotchas entry in `kizen docs show records`.
  `kizen records delete` also archives rather than erasing (its help text now
  says so); `archive`/`unarchive` name that operation directly.

- **`kizen automations runs view --wait` blocks until a run finishes**, instead
  of leaving you to hand-roll polling (a real timing bug: chains where the gap
  between steps ran 60s to 10+ minutes were previously misread as "stalled" by
  a short-timeout wait). Defaults to 900s (`--timeout 0` waits indefinitely);
  a timeout or a `paused*` status is reported as "not done yet" and exits 3,
  never as a failure — `completed` exits 0, `failed`/`cancelled` exit 1. A
  halted execution's `paused_on_step` (which step it stopped on, and whether
  it branches) is now shown whenever the API sends it.
- **`kizen automations runs logs <exec>`** prints each step's `detailed_log` —
  a `code_step`'s stdout/traceback and other per-step diagnostic detail that
  previously only surfaced via `runs view --json` → `steps[].detailed_log`.
- **`kizen automations start --wait --show-logs`** triggers an automation and
  follows it to completion in one command: it blocks until the run finishes
  (reusing `runs view --wait`'s wait and exit-code logic) and prints each new
  step's status as it appears, instead of a silent block until the very end.
  `--show-logs` also prints a step's `detailed_log` once that step finishes —
  a `code_step`'s log is released on completion, so this is a completed log
  rather than a running one being tailed. Replaces the old
  `start` + hand-rolled polling + a separate `runs logs` call with one command
  and one exit code. Builds directly on `runs view --wait` / `runs logs`
  above — no second poll loop, no second log renderer.
- **`kizen records list <object> --fields a,b,c`** fetches `id`, `name`, and
  those field api_names in the same search call already used today, and
  shows them all as table columns — previously the table only ever showed
  `id` and `name`, no matter what the object carried. `--output json`/
  `--output csv` show the same `id` + `name` + requested set. An
  unrecognized api_name (a typo, a display label, or a field
  UUID) is rejected up front, listing the object's real field api_names,
  instead of silently returning a result missing that field.

- **`kizen docs show examples`: a complete, worked, end-to-end example.**
  Every other topic covers one surface; this one walks a single object, an
  activity type logged against it, an automation with a branching graph, and
  a generated dashboard, wired together in the order you'd actually build
  them — object → fields → activity → automation → dashboard, with every
  cross-entity UUID reference named and every step confirmed against a real
  environment. Backed by committed fixtures under
  `tests/fixtures/examples/service_ticket/`, checked two ways: an offline
  test that fails if the doc and the fixtures ever diverge, and an opt-in
  drift test that applies the same fixtures live.
- **The package declares its license.** `kizen-builder` is MIT-licensed, and the
  built wheel and sdist now carry `License-Expression: MIT` along with a copy of
  `LICENSE`.
- **Some enum rejections on automation writes now name the CLI's known valid
  values.** A 400 shaped like `"<value>" is not a valid choice` for
  `create_related_entity.new_entity_owner_type`,
  `notify_member_via_text.team_member.type`, or `on_or_around_date.date_offset`
  now carries whatever this repo has already confirmed about that field,
  instead of leaving you to guess and retry against a live environment. See
  `tools/planners/automations.py::KNOWN_ENUM_CHOICES` for what's known and
  where it came from.
- **`kizen init` asks which Kizen environment you're on instead of asking for a
  URL.** Pick `go`, `fmo`, `staging`, or `integration` and the right API host
  is resolved for you; a mistyped or misaddressed host (e.g. the SPA host
  instead of the API host) is no longer reachable through the normal setup
  path. Free-text URL entry is still available (choose `url`) for
  self-hosted or one-off setups. `--base-url` now also accepts these short
  names (`--base-url staging`) in addition to a full URL, for scripted setup.

### Changed

- **`kizen docs show operating` now states the CLI-plus-browser workflow as a
  rule, not left implicit.** A new numbered rule says to build and re-apply
  through the CLI and confirm rendered output — dashboards, dashlets, email
  bodies, condition labels — in the browser, treating both as one workflow.
  A new "Verifying rendered output" section lists the concrete categories the
  CLI cannot render, calls out the automation builder UI's condition-label
  display bug as product-side rather than a CLI defect, and names the record
  Timeline as the best single artifact for confirming an automation's
  provenance.
- **`kizen init` no longer silently defaults `--base-url` to `go`.** A
  non-interactive invocation that used to omit `--base-url` and succeed
  against `https://app.go.kizen.com` by default now exits 2 unless
  `--base-url` is passed or an environment choice arrives on stdin. Scripted
  callers relying on the implicit default need to add `--base-url <name>`.

- **The docs are now one topic per Kizen surface.** `reference.md` was a
  2,164-line file covering every entity at once, so working on forms meant
  loading or grepping the whole thing to reach its 200 relevant lines. Each
  entity's wire formats, endpoints and quirks now live in that entity's own
  topic, below the spec template it already had: `kizen docs show form` covers
  the `FormDef` shape **and** `form_ui`, the required-on-create fields, and the
  builder-crash node-type rule, in one place. `kizen docs list` labels these
  "surface" rather than "spec shape".

  New topics carved out of the old file: **`objects`** (objects, categories,
  pipeline stages), **`automation-runtime`** (starting/watching/controlling
  runs), **`smart-connectors`** (the API and local dev loop, with the flow spec
  staying in `smart-connector-flow`), **`email-templates`**, **`files`**, and
  the cross-cutting **`filters`** — one DSL that six surfaces share, previously
  described in three places at once.

  `kizen docs show reference` is now a router table plus the conventions that
  hold across every surface. Roughly 200 lines of duplication went with the
  move, including a trailing "quirks worth remembering" digest whose 17 bullets
  restated facts stated in full elsewhere — two of them pointing the reader at
  the file they were already reading. The step- and trigger-type tables in it
  had already drifted two entries behind `automation.md`, which they duplicated.

### Fixed

- **`automations update` no longer deactivates a live automation just
  because the spec doesn't mention `active`.** `AutomationDef.active` is now
  tri-state (`bool | None`, default `None`): an update spec that omits
  `active` preserves whatever the live automation already is, resolved from
  the live state the planner already fetches — no extra API call. A create
  spec that omits `active` still defaults to `False`, unchanged. An explicit
  `true`/`false` in an update spec still wins either direction, but the
  `--dry-run` preview now shows it as a transition (`True → False
  (DEACTIVATES a live automation)`) instead of a bare value, so it's legible
  before you approve it. `automations activate`/`deactivate` and
  `set_active()` are unaffected — they were always the explicit path.

- **Editing an automation no longer orphans its execution history.** Every
  automation-writing path (`automations steps add/edit/remove`, `roundtrip`,
  and `plan-update-automation`/`apply`) previously rebuilt every step and
  trigger from scratch on each PUT without ever sending back its real
  server `id` — which the live API uses to track identity across writes,
  including a goal step's own nested wait-until triggers. The result: a step
  that hadn't changed at all would get a fresh id on every edit, and Kizen's
  execution-history view would show its prior runs as "Deleted." `kizen
  automations steps add/edit/remove`, `roundtrip`, and `show` now always echo
  back the `id` they read from GET for every step/trigger — including goal
  steps' nested triggers — matching what a normal save from the Kizen web UI
  already does. `AutomationStepDef`/`AutomationTriggerDef` also gained an
  optional `id` field so a hand-authored `plan-update-automation` spec —
  seeded from a live read — can opt into the same identity preservation.
  Because `steps get` output now carries `id`, two misuse guards were added
  so copying it around can't silently corrupt a different step's history:
  `steps edit` rejects a patch that tries to change `id` (matching how
  `key`/`type` are already frozen), `steps add` drops any `id` on a new-step
  spec (a new step never inherits one), and `validate_payload` flags
  duplicate step/trigger ids anywhere in a payload as a last-resort check on
  every write path.

- **Running a command against a profile name that was never configured now
  fails with a clear error instead of an unhandled `AttributeError`.**
  `load_env_config()` resolved the profile name but silently returned `None`
  when it wasn't in `~/.config/kizen/credentials.toml`; every caller assumed a
  real `EnvConfig` and crashed the moment it touched `.base_url` or
  `.auth_headers()`. It now raises `ConfigError` naming the missing profile and
  the `kizen init --profile <name>` command to fix it.

- **`smart-connectors seeds add`/`seeds remove` no longer drop another
  seeded object's field restriction.** Both commands rebuild the full seed
  list from a fresh read, but `fields_ids` is write-only and never comes back
  on a GET — so the "preserve the seeds I'm not touching" pass was silently
  wiring every other seed back with no field restriction at all, undoing any
  `--field` list a previous `seeds add` had set on it. A connector seeding
  2+ objects, each restricted to specific fields, would lose all but the one
  most recently touched. Fixed by reconstructing the restriction from the
  script's generated seed table (`config_metadata.seed_tables[].columns_mapping`)
  — the same source `seeds list` already uses to show it, since it's the one
  place the restriction survives a read.

- **`kizen objects get`'s table and CSV output now include field option
  UUIDs** (previously JSON only). A choice/status/yesnomaybe field's options
  render as `name (id)` — the full UUID, since it's pasted into a spec, not
  just read.

### Added

- **New docs topic: `kizen docs show code-steps`.** Writing the Python inside a
  `code_step` now has its own page instead of being a section three-quarters of
  the way down `reference.md`: the namespace (`inputs.` / `outputs.` /
  `outputs.log` / `secrets[…]` / `kizen.api`), the `kizen code test` loop and its
  type-code table, and how to wire the finished step into an automation. New
  material there: `secrets[…]` is documented for the first time, and inputs are
  now documented as arriving **typed** — an `entity` input is a single
  `uuid.UUID` (not a list), and a date field declared `string` arrives as a
  `datetime.date`. `reference.md` keeps a pointer at the old location.

- **`automations list` shows and filters by folder.** Each row now carries
  `folder_name`/`folder_id` (a new `folder` table column, present in `--json`/
  `--output csv` too), and a new `--folder <name-or-uuid>` option filters the
  listing to one folder — previously the only way to find an automation's
  folder was `automations get <api> --raw`, one call per automation.
- **`automations create/update --dry-run` validates trigger order.** Triggers
  left without an explicit `order` in the spec all default to `0`; a spec with
  two or more such triggers used to render a clean-looking plan and only fail
  on the live apply, with `HTTP 400: triggers: Trigger orders must be
  sequential from 0 to N-1`. The dry-run now raises the same rule statically,
  before anything is sent.

- **Smart connectors can be built from scratch, not just edited.** Previously
  `smart-connectors` covered reading a connector and iterating on its SQL; a
  connector still had to be created and wired up in the UI. The whole path is
  now wired, in the order you use it:
  - `create <name> --object <api_name> [--type ...]` — with the per-type
    requirements enforced up front (`--cadence` for `schedule`, `--activity-object`
    for `activity`, and the object/type the API demands but its schema doesn't
    mark required).
  - `set-input <file> --connector <c>` — uploads the reference file, attaches it,
    and generates the SQL template and config from its real columns, replacing a
    four-call manual sequence. It **refuses to replace** a connector's existing
    reference file: swapping one is broken server-side (the executor keeps
    reading the old file's bytes), so the CLI explains that and points at
    building a fresh connector instead.
  - `generate-sample <c>` — the server-side output sample that `push --publish`
    silently requires, polled to completion.
  - `suggest-variables <c> [--spec]` — Kizen's inferred execution variables
    (data types, date and yes/no formats), emitted as a spec block to start from.
  - `configure-flow [<c>] --spec-file <f>` — execution variables and load steps
    from a spec that names objects, fields, and variables instead of UUIDs. It
    resolves them against live state, catches what Kizen would only reject at
    write time (a column the reference file never had, a missing `name`-field
    mapping, a variable nothing provides), and saves load steps in rounds when
    one step's records feed another's relationship field. Shape:
    `kizen docs show smart-connector-flow`.
  - `activate <c>` — the `status: operational` flip. Its own command because a
    live run of a connector that isn't operational sits queued forever with no
    error.
  - `start-flow <c> [--live]` — queue a run, dry by default, refusing to start
    one that can't work (no published script, no load steps, not operational)
    without `--force`.
- **Smart connectors can read from other Kizen objects.** `smart-connectors
  seeds list|add|remove` configures data seeds, which expose another object's
  records to the SQL as a `kizen.<object>` view — so a connector can join
  incoming data against what's already in Kizen. `--group` takes a saved filter
  group (segment) by name, which is what the API actually wants; passing a field
  category id, the intuitive mistake, gets you a misleading "object does not
  exist" from Kizen and a straight answer from the CLI. Adding a seed refreshes
  the script's config so the view actually exists — a saved seed is otherwise
  inert — while keeping the SQL you've been iterating on, and `seeds list` shows
  which state each seed is in.
- **`pull` exports seeded data, so `run` exercises the same joins locally.**
  Each seeded object's rows are written to `data/` from the same saved filter
  group the live run reads, following the seed table's own column list.
  Previously `pull` just warned that you'd have to hand-author those CSVs.
  `--seed-limit` caps rows per object (default 1000, `0` for all).
- **Webhook connectors are buildable end to end.** They needed two undocumented
  things that produced a bare 500 from sample generation, and both are now
  handled: `create --type webhook` pins `sql_version` to 4.1.x (every lower
  version fails, including the declared 3.1.x floor), and `set-input` drops the
  generated `create table output.webhooks` statement — a debug echo for an object
  that doesn't exist, which crashes generation if left in — while keeping both
  input tables. Two new commands cover the rest: `webhook-sample` writes the
  reference file whose required shape isn't discoverable from the API (columns
  `timestamp`, `employee_id`, `querystring`, `body`, with a real team member
  resolved by email or name), and `send-webhook` fires the real inbound receiver,
  which is how a webhook connector runs — `start-flow` doesn't apply to them and
  now says so instead of queueing something that will never execute.

### Changed

- **Corrected what a flow spec's `data_source` can name.** The docs claimed it
  had to be a column of the uploaded reference file, so SQL couldn't produce a new
  column to map from — meaning a re-upload (or a whole new connector) for
  something the SQL could do. It's actually validated against the *generated
  output sample*: the SQL can invent output columns freely, it just needs a
  `generate-sample` afterwards so the column list catches up. A webhook connector
  mapping fields pulled out of a JSON body proves it. The CLI's error now points
  at the stale sample instead of at the file.
- **`smart-connectors executions` now shows why a run failed.** The executor's
  own error (the real ClickHouse or validation message) was already in the API
  response but dropped on the floor; it's now an `error` column, truncated in the
  table and complete under `--json` / `--output csv`. This list is the only place
  Kizen exposes it — there's no per-execution endpoint.

- **Spec-file docs (`kizen docs show <shape>`) no longer point back into this
  repo's source tree.** They previously hedged incomplete sections with
  "see `src/kizen_builder/models/spec.py`" — unreachable from an environment
  folder, where these docs are actually read. Removed those pointers, and
  closed the two gaps they were covering for automations: the shape of a
  variable-comparison `condition` step, and `code_step`'s `input_type`/
  `output_type` values, are now documented inline in `docs show automation`.
- **CLI `--help` text no longer shows literal double backticks.** Docstrings
  and `Option(help=...)` strings used RST-style ` `` ` markup that Typer's
  default renderer doesn't interpret, so it showed up verbatim; collapsed to
  single backticks throughout `cli.py`, matching the convention already used
  everywhere else in the file.

### Fixed

- **An `assign_team_member` step now accepts the `type` values the API
  actually takes.** The spec model's enum had been assembled from the *other*
  two team-member selectors, so it rejected three legal values
  (`team_member`, `round_robin_team_members`, `related_team_selector_field`)
  at validation time and waved through four illegal ones (`owner`,
  `last_active`, `last_active_role`, `employees`) that every live create
  rejects with `HTTP 400: type: "…" is not a valid choice`. It now matches
  the endpoint. `kizen docs show automation` gains a table of the six values
  and which id each one needs — `team_member` wants the singular
  `employee_id`, not `employee_ids`.
- **`permissions group --fields` now names contacts custom fields instead of
  showing raw UUIDs.** Field labels were resolved only for the custom objects
  present on the group, but a contacts custom field lives under
  `contacts_section`, not `custom_objects` — so every one of those rows printed
  a bare field id, leaving the one part of the grid you'd want names for as the
  only part without them. They now resolve the same way object fields do. The
  extra lookup is skipped entirely for a group with no contacts custom fields.
- **`permissions group` now reports name-resolution failures instead of
  silently showing raw ids.** Resolution is deliberately best-effort — an
  unreachable object list shouldn't stop the permission grid from rendering —
  but it was wrapped in a bare `except: pass`, which made a failed lookup
  indistinguishable from a field that legitimately has no display name. Failures
  now print as `warning:` lines on stderr (naming the object involved, so they
  stay useful in every output format) and are carried in a `warnings` key in
  `--json`. The view still renders, and still never raises.
- **`seeds add`/`seeds remove` can now target contacts (`client_client`).**
  Object resolution only ever queried custom objects, so `client_client` —
  one of the two seed tables a contact-matching connector actually needs —
  could never be found, raising a plain "not found" `PlanError` regardless of
  `--group`/`--fields`. The same lookup backs load-step `custom_object`
  resolution in `configure-flow` and `create`'s `--object`, so contacts now
  resolve there too.
- **`smart-connectors pull` no longer crashes with a raw `NameError` when
  exporting a seeded object's rows hits a real API error.** The `except`
  clause around the record search named `KizenAPIError` to catch it as a
  per-table warning, but the module never imported that name — so the one
  case it existed to handle (the API rejecting the search) failed with an
  unrelated Python error instead of the intended warning-and-continue.
- **`push` now fails with a clear message if it can't work out which
  connector/script to push**, instead of calling the API with a missing
  value and surfacing whatever error that produces. Only reachable with a
  `.kizen-connector.json` marker missing its usual fields (hand-edited or
  from an older CLI version) plus no explicit `--connector`/`--script`.
- **`push` no longer silently no-ops against a script that's gone live.**
  If the local `.kizen-connector.json` marker's `script_id` had been promoted
  to live behind the CLI's back (e.g. by `publish` run from another session),
  `push` mislabeled the diff header "remote draft `<id>`", PATCHed the live
  script anyway (a 200 that changes nothing), and only then failed at
  `--publish` with a generic "already live" 400. `plan_push` now checks the
  script's actual status up front and fails fast with a clear message instead
  of PATCHing something that can't take the change. Separately, if the
  marker's script is still a draft but no longer the connector's *current*
  one — a stray draft left behind by e.g. `get-file-template` forking a new
  one — `push` now warns rather than silently targeting the wrong script.
- **`push --publish` fails fast when the output sample is missing or stale**,
  instead of writing the SQL and then hitting a raw "Output sample file is not
  generated yet" 400 with no pointer to the fix. It now checks the script's
  `state` right after the PATCH (a sample generated against the *previous*
  SQL doesn't count) and, if it isn't `success`, stops before calling publish
  with a message naming `generate-sample` as the next step.
- **`configure-flow` warns when a `date`/`datetime` execution variable has no
  `output_format`.** Kizen defaults the unset format to `%m/%d/%Y`, which a
  native ISO-only date field then rejects per row — a silent "Partial
  Success" that never appears in `executions --json`, only in an `.xlsx`
  report downloadable from the web UI. The plan now flags it up front so the
  format can be set explicitly before saving.
- **`automations folders update --parent` no longer 500s.** Two stacked bugs:
  the CLI sent the parent as `parent_id`, but the wire field is
  `parent_folder_id` — the wrong name was silently dropped, so `folders
  create --parent` (same field) always landed the new folder at root despite
  a clean-looking plan. Separately, the live PATCH endpoint 500s if
  `parent_folder_id` is sent without `name` in the same body, even though
  both are optional per its own schema — a parent-only change now echoes the
  folder's current name alongside it to route around that. `automations
  folders list`'s `parent_id` column had the same wrong key and always
  rendered blank; fixed to read `parent_folder_id`.

## [0.2.0] — 2026-07-29

First tagged release. `0.1.0` existed in `pyproject.toml` but was never cut;
everything before this point was distributed by cloning the repo. This release
is what makes the tool installable, updatable, and self-describing for someone
who isn't its author.

### Added

- **`kizen docs` command group.** The documentation now ships inside the
  package and is served by the CLI: `docs show <topic>` (`operating`,
  `commands`, `reference`, and one topic per spec-file shape), `docs list`,
  `docs path`. Nothing is copied or symlinked into an environment folder, so
  what you read always matches the version you have installed.
- **`kizen upgrade` and `kizen upgrade --check`.** `upgrade` detects how the
  CLI was installed — editable checkout, `uv tool`, `pipx`, or a direct VCS
  install — and runs the right commands for that shape, with `--dry-run` to
  see them first. For a checkout it pulls **and** re-syncs dependencies.
  `--check` is the session-start form: bounded, cached for a day, and always
  exit 0, so it is safe to put in a startup instruction.
- **`kizen --version`**, single-sourced from installed package metadata.
- **`kizen init --refresh-stubs`**, so an updated stub template can reach
  folders that already have one.
- Headless `kizen init`: `--api-key` / `--business-id` / `--user-id` with
  `KIZEN_*` environment fallbacks, prompting only for what's missing.
- `CHANGELOG.md` (this file) and CI (`test` on 3.12/3.13 plus a `build-smoke`
  job that installs the wheel into a clean environment with no repo on the
  path).

### Changed

- **`kizen init` is real one-command onboarding.** `--profile` is now optional,
  defaulting to a slug of the folder name; prompts fall back to their defaults
  on EOF instead of aborting. It writes a short `CLAUDE.md` / `AGENTS.md` stub
  pointing at `kizen docs show operating`, and clears the dangling symlinks
  left by the old layout.
- **`CLAUDE.md` was split by audience.** The operating model, command map, and
  API reference became `docs/operating.md`, `docs/commands.md`, and
  `docs/reference.md` inside the package; `CLAUDE.md` at the repo root is now
  contributor instructions for developing the CLI.
- Around 40 `--help` epilogs point at `kizen docs show <topic>` instead of a
  `.kizen/specs/<topic>.md` path that no longer exists.
- The session-start instruction is `kizen upgrade` rather than
  `git fetch && git merge`, which was already wrong in an environment folder
  (not a git repo) and nonsense under a wheel.
- `README.md` is rewritten around installing and using the tool — `--help` is 
  the source of truth and drifting copies of it were a standing tax.

### Removed

- **`kizen log`** and the `.kizen/decisions.md` decision log — record-keeping
  is the user's workflow to choose, not something the CLI imposes.
- The `ruamel.yaml` dependency (unimported) and the vestigial
  `EnvConfig.state_file_path`.

### Fixed

- **A wheel built before this release contained no documentation at all** —
  the `package-data` glob pointed at a directory that never existed — and
  `kizen init` silently no-op'd its documentation step outside a checkout,
  reporting success while leaving the folder unguided. Both paths now resolve
  through one chokepoint that raises an actionable error instead of skipping.
- Upgrading a checkout no longer leaves dependencies stale. A new upstream
  dependency previously surfaced later as a bare `ImportError` with nothing
  pointing at the cause.
- `kizen upgrade` no longer plans a command that can't run when the CLI was
  installed with `uv tool install --editable`. uv builds tool environments
  without `pip`, so the reinstall step failed with "No module named pip" —
  after `git pull` had already succeeded. It now uses `uv pip install --python`
  for those, and says so plainly when it has neither tool to work with.
- **`smart-connectors run` / `add-input` now print an install command that
  works.** Missing the optional `connectors` extra used to suggest
  `uv sync --extra connectors`, which only helps if you run the CLI from the
  checkout's own `.venv` — from a `uv tool` or `pipx` install it succeeds,
  installs into an environment your `kizen` never reads, and leaves the same
  error. The command is now resolved against the live install shape, with the
  requirements read from package metadata so it can't drift from
  `pyproject.toml`. `kizen docs show reference` documents the extra: what's in
  it, why it's optional, and which two verbs need it.

## 0.1.0 — unreleased

The tool's history before versioning is recorded in [ROADMAP.md](ROADMAP.md)
under "Shipped before 0.2.0".

[Unreleased]: https://github.com/kizen/builder-cli/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kizen/builder-cli/releases/tag/v0.2.0

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

### Changed

- **Breaking: `records delete` is removed; `records archive` is the one way
  to remove records.** Kizen's delete was always an archive (same restorable
  state, same id), so the two commands did the same thing. `records archive`
  now takes one record UUID, or `--spec-file` / piped stdin with CSV or JSON
  rows that each have an `id`, the same shape as `records update`. Scripts
  that pass several positional ids need updating:

  | Before | After |
  |---|---|
  | `records delete <object> <uuid>` | `records archive <object> <uuid>` |
  | `records delete <object> <uuid> <uuid> …` | `records archive <object> --spec-file F` (or stdin) |
  | `records archive <object> <uuid> <uuid> …` | `records archive <object> --spec-file F` (or stdin) |

  `records delete` now exits 2 with a message naming `records archive`, and
  `records archive` with more than one positional id exits 2 pointing at
  `--spec-file`. `records list --output csv` output is a valid spec file; pass
  a `--limit` above the record count, since it defaults to 100.
- **Breaking: `smart-connectors start-flow --live` is now `--write-records`.**
  `--live` meant "write real records" here and "use the live script" on
  `pull` and `download-sample`. `start-flow --live` now stops with exit 2 and
  names the new flag. `pull` and `download-sample` take `--script live`
  instead. `--force` is split: `--overwrite` for local files (`pull`,
  `download-sample`, `executions download`), and `--ignore-blockers` for
  `send-webhook` and `start-flow`. The old spellings except
  `start-flow --live` still work, with a warning, and will be removed in a
  later release.
- **`records archive` batches its requests and triggers no Kizen emails.** It
  used to send one request per id, and each one emailed you: archiving 3,000
  records meant 3,000 requests and 3,000 emails. It now sends up to 500 ids
  per request with Kizen's email notification turned off, so the same job is
  6 requests and no email. The result reports how many ids Kizen accepted,
  which counts ids sent, not records newly archived.
- **Breaking: `smart-connectors executions` is now a command group, like
  `automations runs`.** Scripts that call the old flat forms need updating:

  | Before | After |
  |---|---|
  | `smart-connectors executions <connector>` | `smart-connectors executions list <connector>` |
  | `smart-connectors execution-sql <connector> <eid>` | `smart-connectors executions sql <connector> <eid>` |

  The old forms are removed, not aliased. `executions <connector>` now exits
  2 with a message naming `executions list <connector>`. `executions list`
  takes the same options and prints the same columns, CSV, and JSON as the
  old command. `start-flow` and `send-webhook` hints point at the new
  commands.
- **The tool is now called Kizen Admin CLI.** `kizen --help`, the README,
  `CONTRIBUTING.md`, and the bundled reference docs all say "Kizen Admin CLI"
  where they said "Kizen Builder", and the repository URLs point at
  `kizen/kizen-admin-cli`. The command is still `kizen` and the installed
  package is still `kizen_builder`, so nothing about invoking or importing
  the tool changes.
- **`smart-connectors set-input` can replace a connector's reference file.**
  It used to refuse, calling the swap a Kizen platform bug. It wasn't: the
  CLI started sample generation without the connector's file id, so the
  script stayed pinned to the first file. `generate-sample` now sends it. A
  replace keeps your draft SQL, regenerates only the config (`--template-sql`
  takes the generated script instead), names any input table the new file
  renamed, and runs the output sample, exiting 1 if that fails.
  **If you passed `--force`, the result is different.** The flag is still
  accepted but has no effect, and a replace no longer does what `--force`
  used to: it keeps the draft SQL instead of writing the template SQL, waits
  for the output sample (up to 300 s), and exits 1 if the sample fails —
  which it will if the kept SQL still reads the old `input.<file>_csv`
  table. To get the old result, pass `--template-sql` instead, and expect
  exit 1 when the sample fails.
- **`smart-connectors push --publish` runs the output sample itself.** It
  writes the SQL, runs the sample on the connector's file, waits for it (up
  to 300 s), and publishes only if it succeeds. It used to check the draft's
  existing sample instead. That check refused every draft a previous publish
  had just forked, and let an edited draft through on a sample of its old SQL.
  If the sample fails or times out, `push --publish` exits 1 with nothing
  published; the draft keeps your SQL. After a publish, `push` moves the
  pull marker onto the new draft the server forks, so the next `push` from
  the same directory works without a re-pull. The success line now says
  "script published — live runs now use it" and shows the connector's status,
  instead of "connector is now live". Plain `push` is unchanged.
- **`smart-connectors activate --status` accepts only `operational` or
  `inactive`**, the only two an update can set. `setup` and `need_attention`
  used to reach the server and 400. The preview also warns when the
  connector has no execution variables, which the server requires.

- **`smart-connectors generate-sample` reports the tables its sample actually
  holds.** The `output tables` line now comes from the sample zip the run just
  produced, with row and column counts (`contacts (4 rows, 12 cols)`), rather
  than from the connector's recognized scopes, which can be stale and made a
  working SQL change look broken. When the two disagree, a yellow line names
  both and notes that `configure-flow` validates against the recognized scopes,
  which refresh on `push --publish`. `--json` gains `outputs`, `sample_file` and
  `warnings`; `scopes` is unchanged. A sample that can't be downloaded or read
  is a warning, not a failure.
- **`smart-connectors seeds add` no longer requires `--group`, and leaving it
  out seeds every record of the object.** That's the safe default: a segment
  seed makes records outside the segment read as "not found" to the SQL, so a
  match-or-create connector re-creates them on every run. When you do pass
  `--group`, the preview now warns (without blocking) if the segment covers
  fewer records than the object has, e.g. "covers 5 of 7 records". `seeds list`
  shows such a seed's filter group as `all records` instead of `—`, and `pull`
  now exports its rows instead of warning you to hand-author the file. The
  preview's `fields` line now says `kizen_id only` when no `--field` is given,
  which is what the server actually exposes; it used to claim "all seedable".

### Added

- **Automation specs can build branch groups, parallel branches and skipped
  conditions.** Two new step types, `branch` and `merge_branches`, and three
  step fields: `is_branch_group_initiator`, `continue_with_branch` and
  `error_notification_severity_level`. `create`, `update` and `diff
  --spec-file` now check the branch-group rules at `--dry-run`, so a merged
  condition with no merge step fails before anything is sent. A step that fans
  out to parallel children without a `branch` step gets a warning in the plan
  preview. Existing valid specs build exactly as before. One that the server
  would reject (a misplaced `initialize_variable`, a go_to to one, duplicate
  step ids) now fails at plan rather than with a 400.
- **`kizen records import <object>`** loads a CSV or JSON spec as one
  server-side job through Kizen's CSV uploader. A per-row `records upsert`
  handles about 2.6 rows/s. An import handles about 25 rows/s when it creates
  records and about 100 rows/s when it updates them. `--mode
  create|upsert|update` picks the behavior. Update matches on an `id` column
  when there is one. An existing `records upsert` spec imports unchanged. The
  server keeps a record even when a cell fails, and it reports the row as a
  success. The command reads the job's failure report, lists those rows, and
  exits 1. In upsert and update modes, a row that matches an archived record
  unarchives it, and the plan preview says so. `kizen docs show records`
  covers matching, blank-cell handling, and value formats.
- **`smart-connectors deactivate <connector>`** sets a connector `inactive`,
  with the same preview, `--dry-run`, `--yes`, and `--json` as `activate`.
  Edits and dry runs still work while inactive, and `activate` brings it back
  with nothing re-done. `kizen docs show smart-connectors` gains a Lifecycle
  section: which verbs need which status, and how to edit a live connector.
- **`kizen smart-connectors executions get <connector> <eid>`** shows one run:
  status, trigger, who started it, the **full** executor error, a step table
  with valid/invalid/total record counts per stage and scope, and which of
  its three files exist. `--json` emits the run as the API returns it, with
  `started_by` flattened to a name. Kizen has no single-execution endpoint,
  so this reads the executions list filtered to that id. `start-flow` now
  prints this command for the run it queued.
- **`kizen smart-connectors executions download <connector> <eid>`** saves a
  run's files without the web UI. `--file report` (the default) is the
  `.xlsx` results workbook, the only place the per-row errors and warnings
  behind a partial success appear. `--file output` is the zip of SQL-output
  CSVs, and `--file input` is the file the run consumed. It writes to `--out`
  or `./<server filename>`, refuses to overwrite without `--overwrite`, and
  exits 1 without writing when the run has no such file (a failed run has no
  report or output zip).
- **`kizen smart-connectors download-sample <connector>`** saves a script's
  output-sample zip (one `<scope>.csv` per output table) without the web UI.
  It takes the latest draft by default, `--script live` for the live script,
  or `--script <id>`, writes to `--out` or `./<server filename>`, and refuses
  to overwrite an existing file without `--overwrite`.

- **Email template `text` blocks are now authored as structured paragraphs,
  not raw HTML — and can carry inline merge fields.** `TextBlockDef.html` is
  removed (a lingering `html` key on a `text` block now fails spec
  validation, naming `paragraphs` as the expected field); each paragraph is
  `{text, size, bold, color, link, align}`. The emitter renders that list
  into the exact canonical markup Kizen's own rich-text editor normalises
  **to** on save (`<p data-line-height="default" style="line-height:
  1.25;">` + `<span style="font-size: Npx;">` + `<strong>` + `<a
  rel="noopener noreferrer nofollow">`), so a template built from a spec no
  longer loses its styling the first time a human opens and saves it in the
  builder — previously, 7 of 9 raw-HTML text blocks in a real template were
  silently rewritten by the builder's own save.

  A paragraph's `text` may also contain `{{ namespace.field }}` merge-field
  tokens — the same syntax automation notify steps already use — rendered
  inline into Kizen's `<span class="kzn-merge-field">` wrapper via the
  shared `tools/merge_fields.py`. `automation_variable.*` tokens are
  rejected at spec-validation time: that namespace only exists inside an
  automation-scoped message, not a portable library template. Under
  `messages templates craft-config`/`--dry-run` (no live calls), every
  namespace still gets a best-effort label, but `data-merge-field-objectname`
  is omitted for custom-object namespaces — there's no live object lookup to
  answer it offline, the same class of preview-vs-real divergence
  `craft-config` already documents for Image blocks.
- **`kizen init` shows the Kizen logo banner before the setup prompts**, when
  run in a real terminal wide and tall enough for it (a compact wordmark or
  a plain tagline show instead on a smaller one). Piped input, `--help`, and
  other non-interactive invocations print nothing new — the banner is gated
  on the same terminal-detection signal the rest of the CLI already relies
  on.
- **`kizen team get <id|name|email>` — the first link in `person -> role ->
  group -> control` is now discoverable from the CLI.** Previously the only
  team-member lookup, `team search`, went through `/api/team/typeahead`,
  which has no role field at all — answering "which role does this person
  have" meant reading it out of the Kizen UI by hand. `team get` resolves a
  team member (by UUID directly, or by a case-insensitive name/email match
  against `team search`, falling back to the single result if nothing
  matches exactly) and shows their role(s) by name, via `GET /api/team/{id}`
  cross-referenced against `GET /api/role` (the retrieve endpoint's `roles`
  field is bare UUIDs, not expanded objects — confirmed live). `team
  search`'s existing output is unchanged. Name/email resolution shares its
  matching logic with the webhook sample tool's team-member lookup
  (`tools/team.team_member_candidates`).

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
- **`kizen permissions group-update <group> --settings-file <f>`** raises or
  lowers object/field/section controls on an *existing* permission group —
  the same op shapes `group-create --settings-file` already accepts, now a
  second consumer. Dry-run shows a `change` (current -> target) per op, read
  from the live group. Object/field ops that target an object the group has
  no entry for **add it at `none` and cannot raise it** (confirmed live: the
  server silently corrects the requested level and reports it in the
  response). Both `group-update` and `group-create --settings-file` catch
  this in two ways: an `object`-op `level` outside the control's own
  `allowed_access` (e.g. `associated_records: none`, which the server would
  silently clamp to `view`) is rejected up front with a `PlanError`; and a
  mismatch that survives that check because a *legal* value still got
  adjusted by a cross-field rule (e.g. `associated_records >= all_records`)
  is reported as an `adjusted` op with a plain-language message — not a
  failure, since the server applied a value the design already delegates to
  it — while a mismatch on a control that had **no entry at all** at plan
  time (the fresh-insert case above) still surfaces as `failed`. See
  `docs/specs/permission-group.md`.

### Fixed

- **Specs accept every api_name Kizen itself produces.** An api_name that
  started with a digit or underscore (`1099_forms`), or that carried Kizen's
  mixed-case collision suffix (`employee_m7SZCzg3`), failed spec validation, so
  `apply` and any spec naming that object as a `target_object` were refused.
  The rule is now letters, digits and underscores only.

- **CLI writes no longer fail on automations with merged branches or skipped
  conditions, and no longer reset step severity.** `roundtrip`, `steps
  add/edit/remove`, `activate`, `deactivate` and `automations move` used to
  fail with HTTP 400 on any automation with a merged condition, goal or Branch
  card, or with a skipped condition or goal. They also silently reset each
  step's error-notification severity to `inherit`. `--dry-run` now flags a
  broken branch group before anything is written. When the server rejects a
  write with a bare list of messages, the CLI prints them instead of just
  `PUT … failed`.

- **Re-running `smart-connectors configure-flow` updates the connector in place.**
  It used to recreate every execution variable, which silently deleted every
  matching and mapping rule that referenced one, and then fail with `An
  execution variable with this name already exists` before it could rebuild
  them. It now sends live variables, load steps and exposed variables back by
  id, so a re-run changes only what the spec changed. A live load step the spec
  no longer lists is deleted, and the plan lists which load steps it will
  update, create and delete, in place of "replacing N existing load step(s)".
  Dropped variables are removed last, and a step's UI-picked automations are
  kept. An exposed-variable name another live step already holds, a variable
  named like a live exposed one, two load steps with one `order`, and a
  reference to a variable that won't exist after the save are now refused at
  plan time instead of failing partway through.
- **A `configure-flow` save that fails partway now says what state it left.**
  If a write fails after an earlier one succeeded, the command re-reads the
  connector and prints which write failed, each load step's state and rule
  counts before, now and in the spec as the re-read shows them, and whether the
  connector is live in that state (JSON with `--json`). Re-running the spec is
  safe.
- **The `permission-group` doc's `section` op example now works.** It showed
  `{"section_key": "automations", "value": true}`, which matches no section
  and crashed `group-update`. The example now uses a real wire key
  (`homepages_section`) with its complete section dict, and the op table
  says a `section` op replaces the whole section. The doc also notes that a
  disabled `automations_section` reads back as just `{"enabled": false}`.
- **A `section` op whose `value` is not a dict is now a `PlanError`**, on
  both `permissions group-create --settings-file` and `group-update`, naming
  the op's index and the expected shape. `group-update` used to fail with an
  `AttributeError` traceback, and `group-create` sent the bad value to the
  server.
- **`smart-connectors push` no longer crashes on bracketed SQL, and code,
  logs and errors print in full.** A changed SQL line containing something
  like `[/.-]` made `push` (with or without `--dry-run`/`--json`) exit 1 with
  a `MarkupError` before showing the diff. The same parsing silently deleted
  anything shaped like `[word]` from text the CLI prints but didn't write:
  `row[field]` in a code_step diff showed as `row`, the step status in
  `automations start --wait` (`[completed]`, `[failed]`) never appeared, and
  server or runner error messages could crash the error handler itself.
  Emoji shortcodes were swapped in too, so an IPv6 address like
  `2001:db8:ab:cd::1` lost its `:cd:`. SQL, code_step values in diffs, run
  logs and tracebacks, `code test` output and HTTP bodies, errors reported
  through the shared `error:` handler, and the `run failed:`, `SQL error:`
  and `plan error:` lines now print character for character, and long SQL,
  JSON and log lines are no longer hard-wrapped at 220 columns. Table cells
  are unchanged.

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
- **`change_field_value.fields_to_clear` and `start_automation.
  automation_variable_overrides` now survive `activate`/`deactivate`,
  `steps add|edit|remove`, and `roundtrip --execute`.** Both are
  expand-on-read keys: GET returns full objects, but write dialect wants
  something narrower, and the translator was handing the read shape straight
  back to PUT. Previously any automation containing either key 400'd on
  every one of those verbs — including on steps nobody touched, since a PUT
  is a full replace — so `automations activate` (the flow `automation.md`
  explicitly recommends) could not complete on such an automation.
  `fields_to_clear` now collapses to bare field UUIDs.
  `automation_variable_overrides` now collapses into the write dialect's
  grouped-by-target-automation shape, narrowing each reference the write
  dialect expects: field references (`context_entity_field`,
  `relationship_field`, `related_record_field`,
  `automation_entity_variable_field`) collapse to bare UUIDs,
  variable references (`variable_to_override`, `automation_variable`,
  `automation_entity_variable`) to bare names, and `specific_value` passes
  through as-is. An earlier build of this fix carried through only two of
  those and silently dropped the override's value whenever `value_source`
  was `specific_value` — confirmed live against a real `boolean`-typed
  override, which collapsed to a payload missing its value entirely while
  `roundtrip` still reported clean.

  Every value key present on an override is carried across, whether or not
  this release has seen its `value_source` before, so an automation that
  uses one stays editable: `activate`, `deactivate` and `steps` all PUT the
  whole automation back, and a step nobody touched has to survive the trip.
  `automations roundtrip` (without `--execute`) now also says plainly that
  its validation is client-side only and does not prove the PUT will
  succeed, rather than reporting "translated + validated" unqualified.

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
- **`kizen init`'s Environment prompt no longer rejects a correctly-typed
  answer just because of its case.** Rich's `choices` matching defaults to
  case-sensitive, so typing `Go` for a `go` business — the natural way to
  capitalize it — looped forever on "please select one of the available
  options" with no indication of why, indistinguishable from the prompt not
  accepting input at all. Matching is now case-insensitive.
- **`kizen init`'s Environment prompt now says what to do when your answer
  genuinely isn't one of the curated names.** Rich's generic rejection
  message never mentioned that `url` itself is the escape hatch to a
  free-text address. The message now says so directly: `Type "url" to enter
  a custom address instead.`

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
- **`kizen automations diff <api_name> --spec-file <path>`** (stdin also
  accepted) previews what `automations update` from that spec would actually
  change on the live automation — trigger/step additions, removals,
  reparenting, and config-field changes — without writing anything. Steps and
  triggers are matched by `id` first, position as a fallback for a spec with
  no `id`s at all; `key`/`parent_key`/`prefix` are excluded from the
  comparison since they're per-side synthetic naming, not automation content,
  so an unchanged spec produces an empty diff instead of showing every step's
  resynthesized `key` as "changed." Each diff line is labelled with the first
  octet of the step/trigger's `id` (matching what's visible in the UI), which
  is unique within a single automation; under `--json`, an addition or removal
  also carries the whole step/trigger including its full `id`.
  `kizen automations get`'s Steps table also
  gains an `id` column (first octet) and shortens `parent` to match, so the
  two can be read against each other without `--json`.
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
  - `start-flow <c> [--write-records]` — queue a run, dry by default, refusing
    to start one that can't work (no published script, no load steps, not
    operational) without `--ignore-blockers`.
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
- **`smart-connectors executions list` now shows why a run failed.** The
  executor's own error (the real ClickHouse or validation message) was already
  in the API response but dropped on the floor; it's now an `error` column,
  truncated in the table and complete under `--json` / `--output csv` and in
  `executions get`.

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
  Success" that never appears in `executions list --json`, only in the run's
  `.xlsx` report (`executions download`). The plan now flags it up front so the
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

[Unreleased]: https://github.com/kizen/kizen-admin-cli/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kizen/kizen-admin-cli/releases/tag/v0.2.0

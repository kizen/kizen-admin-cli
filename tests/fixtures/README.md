# Test fixtures

Real Kizen API output captured via the CLI from internal test environments,
then sanitized (people, emails, env labels, and external product names
replaced; no credentials, business IDs, or signed URLs were ever present).
Internal UUIDs are kept intact so cross-references between files still
resolve (e.g. option UUIDs inside object fields, field UUIDs inside
automation steps).

## Shapes

- `objects/*.json` — `kizen objects get <name> --json` output (tool-level
  shape: `api_name`, `categories`, `fields` with inline `options`).
  `objects/list.json` is `kizen objects list --json`.
- `automations/*.raw.json` — `kizen automations get <name> --raw` output
  (the unmodified API response). `automations/list.json` is the tool-level
  list output.
- `records/`, `executions/`, `team/` — tool-level `--json` output.
  Exceptions: `executions/detail_form_submission.json` and
  `executions/history_form_submission.json` are hand-authored to the OpenAPI
  schemas (`ReadAutomationExecution` / `LightAutomationHistoryWithDescription`)
  rather than captured live. They back `test_runs.py`'s endpoint-path and
  field-mapping regression tests.
- `errors/html_404.html` — the HTML body the API returns on 404 (e.g. a wrong
  path such as the old trailing-slash execution-detail URL), used to test
  error extraction.
- `permissions/permission_group_detail.json` and `permissions_meta_data.json`
  — trimmed live captures (`kizen permissions group --raw` / `meta`),
  2026-09-25. The group carries one section control in each wire dialect
  (`homepages_section.customize_homepages` as a bare bool,
  `dashboards_section.customize_dashboards` as `{view, edit, remove}`) plus
  the contacts block and one custom object, each with `all_records`,
  `associated_records` and its field entries. The captured group is a
  `plan_create_permission_group()` default build, read back as-is, except
  for two values set before the create: `customize_homepages: true` and
  `dashboards_section = {enabled: true, customize_dashboards: {view: true,
  edit: true, remove: false}}`. A plain default build zeroes both, which
  breaks the `describe_group`, section-diff and reset tests that rely on
  them, so recapture with the same two values. `summary` is recounted to
  match the trimmed controls rather than the tenant's. The meta file is the
  matching catalog: those two sections, those two control descriptors, and
  `order` trimmed to match. `permission_group_list.json`, `role_list.json`, and
  `role_detail.json` are still hand-authored and round out the read/resolve
  surface. UUIDs and the group name are replaced with `conftest.py`'s
  `00000000-0000-4000-8000-…` convention and `Sample Group`.

## Coverage

Field types covered across the object fixtures: text, longtext, email,
phonenumber, checkbox, checkboxes, dropdown, status, yesnomaybe, radio,
date, datetime, integer, decimal, money, files, rating, relationship,
team_selector, dynamictags, timezone. (`radio`/`rating` specimens: `patients`
`preferred_contact_method`/`pain_level`, captured live 2026-07-20 — a `rating`
field with no `--option`s given auto-generates a 5-point scale with codes
`"1"`-`"5"`, confirmed live.)

Trigger types covered: manual, new_entity_created, activity_logged,
on_or_around_date, webhook, schedule (unsupported-type error path).
Step types covered: condition, code_step, stop_execution, archive_record,
go_to_automation_step, start_automation, change_field_value,
modify_related_entities, create_related_entity, initialize_variable,
update_variable, call_llm, file_content_extraction, schedule_activity,
delete_scheduled_activity (unsupported-type error path).

## Refreshing

Re-capture with the read-only CLI commands above and re-run the sanitizer
(replacements listed at the top of the script) before committing. Never
commit raw captures directly.

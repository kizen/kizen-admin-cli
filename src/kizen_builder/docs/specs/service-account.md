# Surface: service accounts

**Consumed by:** `kizen service-accounts list|get|history|create|update|delete`.
Flag-driven; no spec shape. Run each command's `--help` for its flags.

A service account is a non-human identity with permission groups and API keys.
A Call LLM step can run its MCP tools as one (`run_tools_as`). The CLI creates
and changes **integration** accounts only, and never reads, reveals or prints a
key.

```bash
kizen service-accounts create --name "Ops Bot" -g "Integrations" --dry-run
kizen service-accounts update "Ops Bot" --group "Integrations" --group "Reporting"
```

Name accounts by **name** in specs and scripts. A CLI rename keeps the
api_name, but any rename that doesn't send it changes it (below).

---

## Endpoints (confirmed live 2026-10-27)

| Call | Used by |
|---|---|
| `GET /api/service-accounts` (page-number pagination, `?search=` by name) | `list`, every name lookup |
| `GET /api/service-accounts/{id}` | `get`, the update/delete planners |
| `POST /api/service-accounts` `{name, permission_groups}` | `create` |
| `PATCH /api/service-accounts/{id}` (only the keys sent change) | `update` |
| `DELETE /api/service-accounts/{id}` | `delete` |
| `GET /api/service-accounts/{id}/history` | `history` |

`?search=` isn't in the schema, but it filters by name.

Read rows: `id, name, api_name, account_type, status, email,
permission_groups[{id, name}], api_token_count, primary_api_token_preview,
secondary_api_token_preview, created, updated, last_login`. A read never
carries a key.

## Account types

- `service_integration`: accounts a business creates. The only type the CLI
  updates or deletes.
- `service_automation`: Kizen's own automation account (`api_name` null).
- `service_automationplugin`: an account a plugin installs. **It isn't in the
  schema's `account_type` enum** (`service_automation`, `service_integration`).

`update` and `delete` refuse the last two at plan time.

## api_name

- **Kizen derives it from `name` on create and ignores a supplied one**
  (`Ops Bot (MCP)!` → `ops_bot_mcp`). The plan previews the prediction; the
  result reports what Kizen derived and flags a difference.
- **Any PATCH that includes `name` re-derives it**, even when the name is
  unchanged. Sending `api_name` in the same body keeps it. `update --name`
  therefore sends the current api_name with the new name, so a rename keeps
  the identifier.
- `PATCH {api_name}` alone sets it, slugified (`"Bad Name!"` → `bad_name`).

## Status follows the group set

No permission groups means `status: "disabled"`; one or more means `active`.
That holds on create and on every PATCH. A `status` key in the body is
ignored silently. `suspended` is in the schema's enum but wasn't seen.

## Duplicates

- **A duplicate `name` on create answers 500**, and no account appears. The
  create planner refuses a name that matches an existing account
  case-insensitively, and a derived api_name another account holds.
- A duplicate api_name, or a rename onto an existing name or api_name, answers
  400 `{"name": ["A service account with this name already exists."]}`. The
  error is keyed `name` even when the clash is the api_name.
- An unknown permission-group id answers 400 under `permission_groups`.

## Delete is a permanent archive

`DELETE` answers 204. Afterwards the account is gone from every read: GET,
list and typeahead give 404 or nothing, and a second DELETE or a PATCH gives
404. Nothing reachable through the API can see or restore it. The same name
can be created again at once.

Not probed: whether its keys stop working, and what an automation whose
`run_tools_as` points at it reads back.

## API keys

- **The create response carries the new key in full**, under a top-level
  `api_key` object the schema doesn't declare. The CLI drops `api_key` from
  the response the moment it arrives, so no plan, result, table, log or
  `--json` output can contain it.
- A read shows `primary_api_token_preview`: the key's first 3 characters
  followed by asterisks, or null with no key. `api_token_count` is the number
  of keys.
- The CLI calls no `…/api-tokens/*` endpoint. To see or rotate a key, use
  Settings in Kizen.

## History

Page-number paginated events in the record-timeline shape (`created`,
`type_name`, `type_display_name`, `initiator{email, first_name, last_name}`,
…). An account created through the API has no events, even after renames and
group changes.

## Run-tools-as

`GET /api/automation2/automations/run-tools-as-options` lists the accounts a
Call LLM step can run its tools as. It includes `service_automation` and every
integration account, **even a disabled one**. It never lists the plugin
account. Whether a disabled account's tool calls succeed wasn't probed.

Service accounts aren't team members: see [`team.md`](team.md).

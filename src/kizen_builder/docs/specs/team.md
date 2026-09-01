# Team members — wire format & API behavior

**Consumed by:** `kizen team search|get`. Flag-driven; no spec shape.

Closes the *first* link in `person -> role -> group -> control`
(`permission-group.md` covers `role -> group -> control`).

## Endpoints (confirmed live 2026-09-01, against a disposable test business)

Three team endpoints, three different shapes:

| Endpoint | Used by | `roles` field |
|---|---|---|
| `GET /api/team/typeahead` | `team search` | absent entirely |
| `GET /api/team` (list) | — | expanded: `[{id, name, default_for_new_users}]` |
| `GET /api/team/{id}` (retrieve) | `team get` | bare UUIDs: `["<role_id>", ...]` |

`/api/team/typeahead` is a narrow, fast-search projection
(`id`/`email`/`first_name`/`last_name`/`display_name`/`account_type` — no
`title`, no `roles`). It has no role field at all, which is what made
`person -> role` undiscoverable before this: it's the only team endpoint this
CLI called.

`/api/team` (the full `team_list` operation, not typeahead) and
`/api/team/{id}` (`team_retrieve`) both exist, are `GET`-only-safe alongside
their write verbs, and both carry a `roles` field on every employee — but the
list endpoint expands each role to `{id, name, default_for_new_users}` while
the retrieve endpoint returns bare role UUIDs. **Do not assume the same shape
from both** — `get_team_member` (`tools/team.py`) resolves the retrieve
endpoint's ids against `GET /api/role` to attach names, the same pattern
`describe_role` uses for permission groups.

`/api/team?search=<uuid>` does **not** match by id (0 results) — only name/email
substrings. `team get` therefore takes a UUID straight to `GET /api/team/{id}`,
and falls back to `/api/team/typeahead` (name/email search) otherwise.

## Command surface

- `kizen team search <name>` — unchanged: id/full_name/email/title via
  `/api/team/typeahead`.
- `kizen team get <id|name|email>` — one team member's roles
  (`{id, name}` each), via `/api/team/{id}` + `/api/role` cross-reference.
  Ambiguous name/email matches list the candidates and ask for the id.

## See also

- [`permission-group.md`](permission-group.md) — `role -> group -> control`,
  the rest of the chain.

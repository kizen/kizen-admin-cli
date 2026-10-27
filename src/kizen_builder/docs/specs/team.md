# Team members — wire format & API behavior

**Consumed by:** `kizen team search|get|update`. Flag-driven; no spec shape.

Closes the *first* link in `person -> role -> group -> control`
(`permission-group.md` covers `role -> group -> control`).

## Endpoints (confirmed live 2026-09-01, against a disposable test business)

Three team endpoints, three different shapes:

| Endpoint | Used by | `roles` field |
|---|---|---|
| `GET /api/team/typeahead` | `team search` | absent entirely |
| `GET /api/team` (list) | `team update` (last-Admin guard) | expanded: `[{id, name, default_for_new_users}]` |
| `GET /api/team/{id}` (retrieve) | `team get`, `team update` | bare UUIDs: `["<role_id>", ...]` |

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
- `kizen team update <id|name|email> --role R … | --add-role R … |
  --remove-role R …` — change one member's roles through the plan →
  preview → confirm flow (`--dry-run`, `--yes`, `--json`). See below.

## Role changes (confirmed live 2026-10-14)

`team update` writes through `POST /api/team/change-roles`:

```json
{"team_member_ids": ["<member>"], "role_ids_to_add": ["<role>"], "role_ids_to_remove": []}
```

- Both lists are required; either may be empty.
- It applies **synchronously**: the next `GET /api/team/{id}` shows the change.
  It answers 200 with an **empty body**, not the echo the OpenAPI schema
  documents.
- Roles named in neither list are left as they are, and no other key on the
  member changes.
- `--role` replaces the set. The planner reads the member's live roles and
  turns the target set into add and remove lists, so the body still names
  only the roles that change. A role already held (add) or not held (remove)
  is dropped from the lists; an empty change is a `skip`.
- **Last-Admin guard:** a plan that removes the role named `Admin` fails at
  plan time unless another member in `GET /api/team` holds Admin with
  `verified: true` and `deleted: false`. The role's `user_count` is not used,
  because it may count deactivated members (unconfirmed: the test business
  has no deactivated member).

`PATCH /api/team/{id}` with `{"roles": [...]}` also exists but is not used: it
sends the whole set, its replace-vs-merge semantics are undocumented, and the
same body carries `fields` and `permission_groups`.

Service accounts are not team members: `/api/team` and its typeahead exclude
them (confirmed live 2026-10-27). See [`service-account.md`](service-account.md).

## See also

- [`permission-group.md`](permission-group.md) — `role -> group -> control`,
  the rest of the chain.

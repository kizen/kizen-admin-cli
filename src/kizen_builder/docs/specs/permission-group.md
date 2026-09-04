# Spec shape: permission-group shaping ops

**Consumed by:** `kizen permissions group-create --settings-file <f>` and
`kizen permissions group-update <group> --settings-file <f>` — same op list,
same file. `group-create` applies it after building a new group;
`group-update` applies it to a group that already exists, and its dry-run
preview shows a `change` (current level -> target level) per op read from
the live group, not just the target.

A group is created at a **base** (`--base default` = fresh group at Kizen's
default levels, or `--base clone --from <group>` = copy an existing group).
The `--settings-file` is an optional **JSON list of shaping ops** applied
*after* creation to raise/lower specific permissions.

> To see the current level structure of a group (section/object/field keys and
> the levels each accepts), read a live one: `kizen permissions group <name>`
> (add `--fields` for per-field rows, `--raw` for wire JSON).

---

## Quick example

```json
[
  { "type": "object",  "object_id": "<object_uuid>", "key": "all_records", "level": "edit" },
  { "type": "field",   "object_id": "<object_uuid>", "field_id": "<field_uuid>", "level": "view" },
  { "type": "section", "section_key": "automations", "value": true }
]
```

`key` must be a real object control key — `all_records`, `associated_records`,
`create_record`, `unarchive_all`, `record_overview_chart_view`,
`default_for_new_field`, and others (`records` is not one; see
`kizen permissions meta` for the full list). *(confirmed live 2026-09-01)*

```bash
kizen permissions group-create --name "Sales Ops" --settings-file ops.json --dry-run
```

## Op shapes

| `type` | Keys | Effect |
|--------|------|--------|
| `object` | `object_id`, `key`, `level` | Set an object-level permission (e.g. `all_records`, `create_record`) to `level`. |
| `field`  | `object_id`, `field_id`, `level` | Set a per-field control to `level`. |
| `section` | `section_key`, `value` | Toggle/set an app-section permission. |

`level` is a level **name** (`none`, `view`, `edit`, `remove`, …) or its integer
index — the valid range per control comes from that control's `allowed_access`
(visible in `permissions group <name>`). Both `group-create --settings-file`
and `group-update` reject an out-of-range `level` for an `object` op at plan
time (e.g. `associated_records: none`, which has no `none` in its
`allowed_access`) with a `PlanError` naming the control and its valid
levels, instead of sending it and letting the server silently clamp it — see
"Write model" below. *(confirmed live 2026-09-01)*

## Gotchas

- **Names, not UUIDs, everywhere they can be** — `--from` takes a group name or
  UUID. But `object_id`/`field_id` inside ops are **UUIDs**; get them from
  `kizen objects get <api_name> -o json` and `kizen permissions group <name>`.
- Roles attach permission groups — see `kizen roles create/update --group ...`.

---

# Wire format & API behavior

Three related surfaces:

- **`/api/role`** — a Role bundles `permissions` (app-level string flags),
  `permission_groups` (a list of group UUIDs), and `default_for_new_users`.
  Roles are what team members are assigned.
- **`/api/permission-group`** — a named bundle of per-entity access levels:
  custom objects + their fields, contacts, and ~20 feature "sections"
  (homepages, dashboards, settings, …).
- **`/api/permissions/meta-data`** — the catalog: which sections/capabilities
  exist, their `label`, `affordance` (`range` slider / `switch` / `checkbox`),
  `allowed_access`, `default`, `category`, display `order`, and the cross-field
  `rule`s.

## Access levels

Integers **0–3 = none / view / edit / remove**, matching the UI columns
None / View / Create·Edit / Delete·All and the group `summary` counts
(`nb_none`/`nb_view`/`nb_edit`/`nb_remove`).

**On the wire a single permission serializes inconsistently** — some as a bare
bool, others as `{"view": bool, "edit": bool, "remove": bool}`. **Never
synthesize the shape.** The CLI reads an existing group as a shape *template*
and only resets leaf values, which is why `group-create` needs a `--base`.

## Write model (verified live)

- **Create needs the FULL structure.** `POST /api/permission-group` with just
  `{name}` 400s (`"Custom Objects missing: …"` / `"enabled: required"`). It must
  include every custom object (plus its fields), every `*_section`, and
  contacts. `kizen permissions group-create` builds this from the meta defaults,
  or `--base clone` copies an existing group as-is.
- **Sections** are written with `PATCH /api/permission-group/{id}` and the
  **complete** section dict — partials 400
  (`"customize_homepages: This field is required."`).
- **Custom object + field perms** go through a different endpoint:
  `PATCH /api/permission-group/{id}/object-update` with
  `{custom_object: {id}, field?: {id}, key?, permission_level: 0-3}`. Three
  distinct outcomes when the response's `permission_level` differs from what
  was requested, all reported via `response.details.message`:
  1. **Out-of-range clamp.** Requesting `associated_records: none` when
     `allowed_access` is `["view","edit","remove"]` comes back
     `permission_level: 1` (`view`). `group-create --settings-file` and
     `group-update` never let this reach the endpoint at all (see "Op
     shapes" above) — rejected as a `PlanError` at plan time instead.
  2. **Cross-field normalization on a control the group already carries.**
     A *request the control's own `allowed_access` says is legal* still gets
     adjusted to satisfy a rule involving another control's value — e.g. on
     a group where `all_records` had just been raised to `remove`,
     requesting `associated_records: view` (legal on its own —
     `allowed_access` includes `view`) came back `permission_level: 3`
     (`remove`), to satisfy `associated_records >= all_records`. The write
     still succeeded — the server picked the nearest legal value for the
     *combined* state, which is exactly the normalization these commands
     delegate to `object-update` for, rather than hand-building a full
     group PUT and enforcing the rules here. Neither command can predict
     this at plan time without reimplementing the server's rule engine, so
     it's only visible in the apply result: reported as `status: "adjusted"`
     (not `"failed"`), with a message like `"requested view, server
     normalized to remove (Permission level was automatically corrected by
     rule.)"` — `kizen apply`'s exit code stays 0.
  3. **Insert of an object with no entry in the group at all.** Always
     lands at `none`, **even when the requested level is in range** — the
     defect this item exists to at least surface honestly. Not a one-time
     insert quirk: a second identical apply against the now-present entry
     is *also* corrected back to `none` — the exact server rule wasn't
     identified, only that it isn't simply "no entry yet" (case 2's example
     above shows a *present* control also being normalized). This is the
     one genuine failure: reported as `status: "failed"`, and `kizen
     apply`'s exit code goes non-zero.

  `group-create --settings-file`/`group-update` distinguish case 2 from
  case 3 using whether the control had a live entry at plan time (recorded
  on the op at plan time, since the group's state can change between ops in
  the same plan — case 2's example needed `all_records` raised by an
  earlier op in the same batch to reproduce). *(confirmed live 2026-09-01)*
- **A full PUT** `/api/permission-group/{id}` replaces the whole structure, but
  is subject to cross-field **rules** — e.g. `associated_records ≥ all_records`,
  `unarchive_all ≤ unarchive_associated`, `create_record` needs
  `associated ≥ edit`, `bulk_data_upload` needs `create_record`. `object-update`
  and section PATCH normalize dependents for you; a hand-built full PUT must
  satisfy them or it 400s. The meta `rule` field encodes these.
- **Role create quirk:** an explicit empty `permissions: []` is rejected
  (`"This list may not be empty."`) — **omit the key** and it stores `[]`.
- **List vs detail:** the list endpoint zeroes `summary` and omits real counts;
  the detail GET has `summary`, while `user_count`/`role_count` come from the
  list entry. Getting both means two calls.

## Command surface

`kizen roles list|get|create|update|delete` and `kizen permissions
groups|group|meta|group-create|group-update|group-delete`. **Names are
accepted anywhere a role or group is referenced** — resolved to a UUID, with
an available-list on a miss. `kizen permissions group <name> [--fields]`
renders the sectioned slider view that mirrors the permission editor.

`group-update` applies shaping ops directly — `object`/`field` ops call
`object-update`, `section` ops call the section PATCH — it never assembles a
full-group PUT, so the server's cross-field-rule normalization on those two
endpoints still applies (see "Write model" above, case 2) — deliberately:
reimplementing that rule engine client-side isn't in scope. Two consequences
in the CLI:
- A `change` preview line for an `object`/`field` op reads e.g. `"Records:
  view -> edit (subject to server rules)"` when the control already has a
  live entry — the target level is what was asked for, not a guarantee, since
  a later op in the same plan (or the group's pre-existing state) can still
  trigger a cross-field normalization that changes the outcome.
- What happens when an `object`/`field` op targets an object the group has
  no entry for: **it adds the object at `none` and cannot raise it** (not
  "does not yet add missing objects") — `group-update` detects this specific
  case (a request that was in range but landed at `none` anyway, on a
  control absent at plan time) and reports it as `failed`. A normalization
  on a control that *was* present is reported as `adjusted` instead — a
  successful write, not a failure — so it doesn't cost `kizen apply` a
  non-zero exit for the server doing exactly what this design delegates to
  it.

## See also

- `kizen permissions meta` — the live catalog (sections, capabilities, defaults).
- `kizen docs show objects` — where object/field UUIDs come from.

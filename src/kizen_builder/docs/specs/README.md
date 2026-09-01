# Surface topics — index

One document per kind of Kizen entity. Each carries **both** halves: the
JSON/CSV a `--spec-file` command expects (example-first — copy it, adjust it,
`--dry-run` it, apply it) **and** that entity's wire formats, endpoints, and
confirmed quirks below a divider.

**How to use:** run `kizen <group> <cmd> --help` first — it's authoritative for
flags, and its epilog names the topic below. Treat the spec templates as
authoritative for shape; if a field is missing, `--dry-run` reports the exact
field Kizen rejected.

## Command → topic

| Command(s) | Topic | Spec shape |
|-----------|-------|-------|
| `objects`/`categories`/`objects stages` (flag-driven) | [objects.md](objects.md) | — |
| `fields create` (bulk) | [field.md](field.md) | `FieldDef` list |
| `records create\|update\|upsert` (bulk), `records set-field` | [records.md](records.md) | CSV / JSON rows |
| `automations create\|update` | [automation.md](automation.md) | `AutomationDef` (triggers + step graph) |
| `automations steps edit\|add` | [automation-step.md](automation-step.md) | one step (patch / insert) |
| `automations start\|runs …` | [automation-runtime.md](automation-runtime.md) | — |
| `activities create`, `activities fields create` | [activity.md](activity.md) | `ActivityDef` / `ActivityFieldDef` |
| `forms create\|update\|set-ui`, `forms fields create` (and `surveys …`) | [form.md](form.md) | `FormDef` / `FormFieldDef` / `form_ui` |
| `dashboards create\|update` | [dashboard.md](dashboard.md) | `DashboardDef` + dashlets |
| `layouts update` | [layout.md](layout.md) | `LayoutDef` (record layout) |
| `filter-groups`, `quick-filters`, `columns` create\|update | [saved-views.md](saved-views.md) | filter DSL / `configuration_json` |
| `permissions group-create`, `roles …` | [permission-group.md](permission-group.md) | shaping-op list |
| `team search\|get` | [team.md](team.md) | — |
| `smart-connectors` (all but `configure-flow`) | [smart-connectors.md](smart-connectors.md) | — |
| `smart-connectors configure-flow` | [smart-connector-flow.md](smart-connector-flow.md) | execution variables + load steps |
| `messages …` (and email template ground truth) | [email-templates.md](email-templates.md) | — |
| — (used by forms, layouts, connectors) | [files.md](files.md) | — |

Topics with no spec shape are read/runtime surfaces or flag-driven commands;
they're here because their wire behavior still has to live somewhere.

## Flag-only commands

**`objects create`/`update`**, **`categories create`**, **`fields create`
(single)**, **`records create` (single, `--field`)**, **`objects stages create`**,
and **`roles create`** are pure flags — see each `--help`. To build an object
with fields: create the object, then `categories create`, then `fields create`.

## Conventions across all specs

- **`field_ref: "<object_api>.<field_api>"`** in automation and saved-view
  configs is resolved to a UUID at apply time and is portable across
  environments. Bare `field_id` UUIDs work too but are env-bound.
- **`api_name`** is `^[a-z][a-z0-9_]*$`. Kizen may rewrite it on create — read
  back with the matching `get` command.
- Everything runs through **plan → preview → confirm → apply**: `--dry-run` to
  preview, `--yes` to apply after approval.

## Cross-cutting topics

- [`../filters.md`](../filters.md) — the filter DSL and wire format, shared by
  six surfaces.
- [`../code-steps.md`](../code-steps.md) — writing the Python in a `code_step`.
- [`../reference.md`](../reference.md) — the router, plus conventions that hold
  across every surface.

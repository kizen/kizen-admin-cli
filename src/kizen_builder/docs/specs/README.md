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
| `records create\|update\|upsert\|import\|archive` (bulk), `records set-field` | [records.md](records.md) | CSV / JSON rows |
| `automations create\|update\|diff\|roundtrip\|llm-models` | [automation.md](automation.md) | `AutomationDef` (triggers + step graph) |
| `automations steps edit\|add` | [automation-step.md](automation-step.md) | one step (patch / insert) |
| `automations start\|runs …\|failures\|modification-history` | [automation-runtime.md](automation-runtime.md) | — |
| `activities create`, `activities fields create` | [activity.md](activity.md) | `ActivityDef` / `ActivityFieldDef` |
| `forms create\|update\|set-ui`, `forms fields create` (and `surveys …`) | [form.md](form.md) | `FormDef` / `FormFieldDef` / `form_ui` |
| `dashboards create\|update\|dashlet-config` | [dashboard.md](dashboard.md) | `DashboardDef` + dashlets |
| `mcp-servers create\|update` | [mcp-server.md](mcp-server.md) | `MCPServerDef` |
| `layouts update` | [layout.md](layout.md) | `LayoutDef` (record layout) |
| `filter-groups`, `quick-filters`, `columns` create\|update | [saved-views.md](saved-views.md) | filter DSL / `configuration_json` |
| `permissions group-create\|group-update`, `roles …` | [permission-group.md](permission-group.md) | shaping-op list (`--settings-file`) |
| `team search\|get\|update` | [team.md](team.md) | — |
| `service-accounts …` (flag-driven) | [service-account.md](service-account.md) | — |
| `smart-connectors` (all but `configure-flow`) | [smart-connectors.md](smart-connectors.md) | — |
| `smart-connectors configure-flow` | [smart-connector-flow.md](smart-connector-flow.md) | execution variables + load steps |
| `messages …`, `messages templates create\|update\|craft-config` (and email template ground truth) | [email-templates.md](email-templates.md) | `EmailTemplateDef` (templates) |
| — (used by forms, layouts, connectors) | [files.md](files.md) | — |

Topics with no spec shape are read/runtime surfaces or flag-driven commands;
they're here because their wire behavior still has to live somewhere.

## Flag-only commands

**`objects create`/`update`**, **`categories create`**, **`fields create`
(single)**, **`records create` (single, `--field`)**, **`objects stages create`**,
and **`roles create`** are pure flags — see each `--help`. To build an object
with fields: create the object, then `categories create`, then `fields create`.

## Cross-cutting topics

- [`../filters.md`](../filters.md) — the filter DSL and wire format, shared by
  six surfaces.
- [`../code-steps.md`](../code-steps.md) — writing the Python in a `code_step`.
- [`../reference.md`](../reference.md) — the router, plus conventions that hold
  across every surface.

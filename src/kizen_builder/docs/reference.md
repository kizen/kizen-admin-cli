# Kizen Admin CLI — reference router & cross-surface conventions

Everything specific to one kind of entity now lives with that entity. This file
holds only what doesn't belong to any single surface: how to find the right
topic, and the conventions that hold across all of them.

## Find the surface

Read the one doc for what you're working on — it carries the spec shape *and*
the wire formats, endpoints, and confirmed quirks together.

| Working on… | `kizen docs show …` |
|---|---|
| Objects, categories, pipeline stages | `objects` |
| Custom fields, relationship fields, options | `field` |
| Records (CRUD, bulk, upsert, set-field) | `records` |
| An automation's definition | `automation` |
| One step of an existing automation | `automation-step` |
| Starting / watching / controlling a run | `automation-runtime` |
| The Python inside a `code_step` | `code-steps` |
| Activity types, logged & scheduled activities | `activity` |
| Forms & surveys (incl. `form_ui` pages) | `form` |
| Dashboards & homepages | `dashboard` |
| Record layouts | `layout` |
| Filter groups, quick filters, column templates | `saved-views` |
| Roles & permission groups | `permission-group` |
| Smart connectors (API + local dev loop) | `smart-connectors` |
| A connector's flow spec (`configure-flow`) | `smart-connector-flow` |
| Email templates & automation messages | `email-templates` |
| Files: upload, reference, download | `files` |
| Filters — one DSL, six surfaces | `filters` |

`kizen docs list` is the live version of this table. Command **syntax** is
always `kizen <group> <cmd> --help`; how to *operate* an environment is
`kizen docs show operating`.

## Conventions that hold everywhere

### Writes take bare ids; reads expand them

Read responses expand references to full objects
(`{id, name, display_name, …}`); writes want the bare UUID string. Same for
values: a bare scalar on write, often `{value: …}` on read.

**This is a strong prior, not a law.** Several fields genuinely want an
`{"id"|"name": …}` object on write, and sending a bare string 400s or 500s —
`search_records`'s `custom_object`/`filter_groups`/`destination_variable` and
`schedule_activity`'s `assigned_to.variable` are the confirmed cases. Others
want the *flat* form where a read is nested (`association_configs`). When wiring
anything new, check the schema first.

### `GET /api/docs/schema` is the authority

The **full** schema at `GET /api/docs/schema` is the one to check for exact wire
shapes — it covers `automation2` and forms/surveys in depth. The *public* schema
at `/api/docs/public/schema` is narrower and doesn't cover forms/surveys at all.

Neither is always right: this tree records a dozen places where a field the
schema marks optional is required in practice, or where a documented shape
returns a 500. Where a surface doc says **confirmed live `<date>`**, that beat
the schema.

### Kizen rewrites api_names

Create with `api_name: invoice` and Kizen may store `invoices`. **Always read
back after writing** and reference the stored name from then on.

### Contacts are `client_client`

Contacts are a records-API object like any other, identified as `client_client`.
They appear in `kizen objects list` alongside custom objects, and `kizen
objects get client_client` returns their schema and option UUIDs normally —
see `kizen docs show objects`. The `/api/client/` endpoint family is
deprecated.

### Trailing slashes

Several endpoints register only the slashless path and 404 with an HTML page if
you add one — automation executions and dashboards among them. Don't add
trailing slashes anywhere.

### Everything mutating is plan → preview → confirm

Not an API property, a tool one, and the reason most of these quirks surface as
a plan-time error instead of a live 400. See `kizen docs show operating`.

## See also

- `kizen docs show commands` — the map of what exists.
- `kizen docs show operating` — the approval gate, live-state rules, conventions.

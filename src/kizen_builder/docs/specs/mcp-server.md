# Spec shape: `MCPServerDef` — MCP servers

**Consumed by:** `kizen mcp-servers create|update <server> --spec-file <f>`
(also stdin). `delete <server>` takes no spec.

An MCP server is a named set of Kizen tools. AI steps and outside MCP clients
call those tools through the server. A server is referenced by UUID or
`api_name`; display names aren't unique, so the CLI never matches them.

Read first: `kizen mcp-servers list`, `get <server>` (add `--raw` for the wire
JSON), and `tools` for the valid `tool_names`.

---

## Quick example

A server with two tools, shared with one service account and nobody else:

```json
{
  "name": "Support agent tools",
  "api_name": "support_agent_tools",
  "description": "What the support agent may read",
  "tool_names": ["listCustomObjects", "searchCatalog"],
  "is_enabled": true,
  "sharing_settings": {
    "private": false,
    "all_team_members": 0,
    "roles": {"view": [], "edit": [], "admin": []},
    "team_members": {"view": ["<service account id>"], "edit": [], "admin": []}
  }
}
```

## `MCPServerDef` fields

Every key is optional, so an update spec carries only what changes. `create`
needs `name` and `api_name`. Any other key is a spec error, and so is `null`
for any key but `description`.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Not unique. |
| `api_name` | string | `^[a-z_][a-z0-9_]*$`, 2–64 characters, unique. Fixed once created: `update` refuses a different one. |
| `description` | string or null | `null` clears it. |
| `tool_names` | list of strings | Names from `kizen mcp-servers tools`. Checked at plan time; duplicates are dropped (first one kept). `[]` is a server with no tools. |
| `is_enabled` | bool | |
| `is_default_for_business` | bool | One default per business. See below. |
| `sharing_settings` | object | The whole block: `private` (bool), `all_team_members` (0–3), `roles` and `team_members`, each `{view, edit, admin}` lists of UUIDs. Read-shape `{id, display_name}` entries are accepted too; an entry without `id` is a spec error. |

**Service accounts are shared as team members.** Put the account's id in
`team_members`. There is no `service_accounts` bucket; the spec rejects one.

**`get --raw` output is a valid spec.** These read-only keys are dropped
before validation, and the plan preview lists them under
`ignored_read_only_keys`: `id`, `tools`, `tool_count`, `is_system_managed`,
`endpoint_url`, `created`, `updated`, `owner`, `employee_access`,
`sharing_stats`.

## What the plan does

- **create** refuses an `api_name` that exists. With no `sharing_settings`,
  it sends "every team member can view": `{"private": false,
  "all_team_members": 1, "roles": {…empty…}, "team_members": {…empty…}}`.
- **update** compares only the keys the spec sets. `tool_names` compares as a
  set, so reordering is a skip. `sharing_settings` compares after both sides
  are normalised to bare UUIDs, so re-sending `get --raw` output unchanged is
  a skip. A changed sharing block is sent whole; the preview lists the
  changed scalars and each grant added (`+roles.view:<id>`) or removed
  (`-team_members.view:<name>`).
- **delete** previews the name, api_name, id and tool count, plus
  `is_business_default: true` when the server is the default.
- **`is_default_for_business: true`** previews `business_default`, naming the
  server that stops being the default and how to restore it.
- **`is_default_for_business: false`** on the current default is refused: set
  another server as the default instead. A business always has one.
- **Kizen-managed servers** (`is_system_managed`, e.g. `readonly`) can't be
  updated or deleted from the CLI, with one exception: an update spec of
  exactly `{"is_default_for_business": true}` makes one the default again:

  ```bash
  echo '{"is_default_for_business": true}' | kizen mcp-servers update readonly --yes
  ```

## Permission

`mcp_servers_section.mcp_server_configuration` takes `{view, edit, remove}`
(labels None / View/Use Shared / Create/Edit All / Delete/All; default
`view`). Set it on a permission group with a `section` op (see
`kizen docs show permission-group`):

```json
[{ "type": "section", "section_key": "mcp_servers_section",
   "value": { "enabled": true,
              "mcp_server_configuration": { "view": true, "edit": true, "remove": false } } }]
```

The CLI doesn't call a server's `endpoint_url` itself. That's the JSON-RPC
endpoint MCP clients use at run time; `get` prints it so you can wire one up.

---

# Wire format & API behavior

Confirmed live 2026-10-27 on a disposable test business.

## Endpoints (no trailing slashes)

```
GET    /api/mcp-servers                       # list (DRF page); rows carry tool_count, not tools
POST   /api/mcp-servers                       # create → 201, the read shape
GET    /api/mcp-servers/{id}                  # one server; tools[] adds group and group_label
PATCH  /api/mcp-servers/{id}                  # partial update
DELETE /api/mcp-servers/{id}                  # hard delete → 204
GET    /api/mcp-servers/tools                 # {groups: [{key, label, tools: [{name, display_name, description, annotations}]}]}
GET    /api/mcp-servers/{id}/events-history   # DRF page, newest first
GET    /api/mcp-servers/step-options          # what a Call LLM step's mcp_server carries
POST   /api/mcp/{api_name}                    # JSON-RPC, for MCP clients (endpoint_url)
```

## Create, update, delete

- `sharing_settings` is required on create (400 without it). A partial block
  (`{"all_team_members": 2}`) 400s with `roles` / `team_members` required.
  `all_team_members: 5` 400s. An empty `roles.admin` is accepted (dashboards
  need the Admin role there; MCP servers don't).
- Without `api_name`, Kizen derives one from `name`. A hyphen 400s:
  `Must start with a letter or underscore and contain only lowercase letters,
  numbers, or underscores.` A taken api_name, including `readonly`, 400s
  `MCP Server 'api name' must be unique.` A name can repeat.
- One unknown tool 400s the whole request: `Unknown MCP tools: <x>`, on
  create and on PATCH.
- PATCH `tool_names` replaces the list in the order given. `[]` is valid.
- PATCH `api_name` 400s: `This field cannot be changed after creation.`
- PATCH ignores read-only keys (`is_system_managed`, `endpoint_url`, `tools`)
  without an error.
- A `service_accounts` key inside `sharing_settings` returns **200 and is
  dropped**. A service account's id in `team_members.view` is saved and reads
  back as `{id, display_name: "<account name>"}`.
- DELETE is permanent. GET, events-history and a second DELETE then 404
  `No MCPServerConfig matches the given query.` The api_name is free again.

## Reads

- Writes take bare UUIDs in sharing; reads return `{id, display_name}`.
- `owner` is the creator (`{id, display_name}`; `{}` on the system server).
  `employee_access` is `Owner` for the creator, `View` on the system server.
  `sharing_stats` counts levels.
- `endpoint_url` is `https://<host>/api/mcp/<api_name>`.
- The tool catalogue on a business with knowledge and custom objects is
  `custom_knowledge` (`listCatalogs`, `searchCatalog`, `getDocument`) and
  `custom_objects` (`listCustomObjects`, `listObjectFields`,
  `listObjectFieldOptions`, `getCustomObject`). All seven are
  `readOnlyHint: true`. The system `readonly` server serves all seven.
- `step-options` is a bare list of `{id, name, api_name, is_enabled, deleted,
  tool_count}`. It lists disabled servers and leaves out deleted ones.

## Events history

- **Written asynchronously.** Read right after a change, it can be empty; a
  second later the events are there.
- Event keys: `id, group_id, business_id, type_name, type_display_name,
  has_relationship, instance, initiator, data, comments, created`. Types:
  `mcp_server_created`, `mcp_server_updated`.
- `data` is `{api_name, mcp_server_name, old_value, new_value}`, each value
  carrying `name, is_enabled, tool_names, description,
  is_default_for_business`. A create has no `old_value`. A sharing change logs
  an update whose old and new values are equal. `history` shows `—` for both.
- `instance.custom_object_id` / `entity_record_name: "Contact"` are noise.
- The system server's history is empty.

## The business default

**Kizen keeps one default MCP server per business.** Creating a server with
`is_default_for_business: true` made it the default and cleared the flag on
the previous default (the system `readonly` server), without changing that
server's `updated`. PATCHing `readonly` back to `is_default_for_business: true`
then cleared it on the other server; `kizen mcp-servers update readonly`
with that one key does the same. Setting the flag to `false` on the current
default was not tried, so the CLI refuses it.

## Permission key

`GET /api/permissions/meta-data` has a section `mcp_servers_section` ("MCP
Servers") with one permission, `mcp_server_configuration` ("MCP Server
Configuration"), `allowed_access` `view/edit/remove`. An Admin group carries
`"mcp_servers_section": {"enabled": true, "mcp_server_configuration":
{"view": true, "edit": true, "remove": true}}`. `kizen permissions group`
renders it as "MCP Servers / MCP Server Configuration", and a `section` op
dry-runs on it, with no MCP-specific code.

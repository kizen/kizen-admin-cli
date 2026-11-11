# Spec shape: `MCPServerDef` — MCP servers

**Consumed by:** `kizen mcp-servers create|update <server> --spec-file <f>`
(also stdin). `delete <server>` takes no spec.

An MCP server is a named set of tools: Kizen tools, plus tools it takes from
upstream servers (outside MCP servers connected to Kizen). AI steps and outside
MCP clients call those tools through the server. A server is referenced by
UUID or `api_name`; display names aren't unique, so the CLI never matches them.

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
| `upstreams` | list of objects | Tools from upstream servers, one entry per server: `server` (api_name or UUID) and either `include_all: true` or `tool_names`. See below. `[]` clears them all. |

### Upstream tools

Each `upstreams` entry picks tools from one upstream server:

```json
"upstreams": [
  {"server": "deep_wiki", "tool_names": ["deep_wiki__ask_wiki_question"]}
]
```

```json
"upstreams": [{"server": "deep_wiki", "include_all": true}]
```

- `tool_names` are the upstream's tool `name`s: its `tool_prefix` plus the
  tool's suffix. Renaming a tool in Kizen changes its `name`. The CLI maps
  names to the ids Kizen stores and refuses an unknown server or tool before
  sending anything. Duplicates are dropped (first one kept).
- `include_all: true` serves every tool the upstream has. Don't give
  `tool_names` with it. An entry with neither serves
  nothing.
- Each server appears once.
- The read shape is accepted too: `server` as a UUID, `server_name` (ignored)
  and `tool_ids` in place of `tool_names`. Give one of `tool_names` and
  `tool_ids`, not both.
- Upstream servers are created and authorized in Kizen (Data › MCP Servers ›
  Upstream Servers), not from the CLI. One that still needs authorization has
  no tools, so only `include_all: true` can pick it.

**Service accounts are shared as team members.** Put the account's id in
`team_members`. There is no `service_accounts` bucket; the spec rejects one.

**`get --raw` output is a valid spec.** These read-only keys are dropped
before validation, and the plan preview lists them under
`ignored_read_only_keys`: `id`, `tools`, `tool_count`, `is_system_managed`,
`endpoint_url`, `created`, `updated`, `owner`, `employee_access`,
`sharing_stats`, and the list rows' `kizen_tool_count`, `upstream_tool_count`
and `upstream_servers`.

## What the plan does

- **create** refuses an `api_name` that exists. With no `sharing_settings`,
  it sends what the UI's New MCP Server modal sends when its sharing controls
  are left alone: `{"all_team_members": 0, "roles": {…empty…},
  "team_members": {…empty…}}`, with no `private` key (the server stores
  `false`) and no Admin role (confirmed in source 2026-11-11).
- **update** compares only the keys the spec sets. `tool_names` compares as a
  set, so reordering is a skip. `sharing_settings` compares after both sides
  are normalised to bare UUIDs, so re-sending `get --raw` output unchanged is
  a skip. A changed sharing block is sent whole; the preview lists the
  changed scalars and each grant added (`+roles.view:<id>`) or removed
  (`-team_members.view:<name>`).
- **`upstreams`**: omitting it keeps the server's upstreams. Given, it compares
  each server's tools as a set, so `get --raw` output is a skip. A changed list
  is sent whole and `[]` clears them all. The preview names each server and
  tool added or removed (`+deep_wiki: deep_wiki__read_wiki_contents`,
  `-slack`) and all-tools switches (`deep_wiki: 1 tool → all tools`). A create
  previews `upstream_tools`, e.g. `{"deep_wiki": "all"}` or `{"deep_wiki": 2}`.
- **delete** previews the name, api_name, id and tool count (Kizen and
  upstream tools), plus
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

Confirmed live 2026-10-27 on a disposable test business. Upstreams confirmed
live 2026-11-11.

## Endpoints (no trailing slashes)

```
GET    /api/mcp-servers                       # list (DRF page); rows carry tool_names, tool_count (total), kizen_tool_count, upstream_tool_count, upstream_servers [{id, name}], not tools
POST   /api/mcp-servers                       # create → 201, the read shape
GET    /api/mcp-servers/{id}                  # one server; tools[] adds group and group_label
PATCH  /api/mcp-servers/{id}                  # partial update
DELETE /api/mcp-servers/{id}                  # hard delete → 204
GET    /api/mcp-servers/tools                 # {groups: [{key, label, tools: [{name, display_name, description, annotations}]}]}
GET    /api/mcp-servers/{id}/events-history   # DRF page, newest first
GET    /api/mcp-servers/step-options          # what a Call LLM step's mcp_server carries
POST   /api/mcp/{api_name}                    # JSON-RPC, for MCP clients (endpoint_url)
GET    /api/mcp-upstream-servers              # upstream servers (DRF page)
GET    /api/mcp-upstream-servers/{id}/tools   # {tool_count, last_discovered_at, tools: [{id, name, suffix, upstream_name, annotations, is_enabled, is_new, description}]}
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

## Upstreams

- The detail read carries `upstreams: [{server: <upstream id>, server_name,
  include_all, tool_ids: [<tool id>]}]`, `[]` when there are none. Its
  `tools[]` also lists the selected upstream tools, in group `upstream`
  ("Upstream Servers") with `annotations: null`. `tool_names` holds Kizen
  tools only.
- `tool_ids` are the `id`s from `GET /api/mcp-upstream-servers/{id}/tools`. A
  tool's `name` is the upstream's `tool_prefix` plus its `suffix`, e.g.
  `deep_wiki__` + `ask_wiki_question`.
- An upstream server row is `{id, name, api_name, description, url,
  is_enabled, auth: {type: none|oauth, …}, tool_prefix, connection: {status,
  …}, tool_count, last_discovered_at, …}`. One that needs OAuth starts at
  `connection.status: needs_authorization` with `tool_count: null`, and its
  `/tools` returns `tools: []`.
- A PATCH without `upstreams` keeps them. A PATCH with `upstreams` replaces the
  whole list: dropping one of two servers removes it, `[]` clears all. POST
  accepts `upstreams`.
- `include_all` defaults to false. `include_all: true` reads back `tool_ids:
  []`, even when tool ids were sent with it, and `tools[]` then lists every
  upstream tool. `{include_all: false, tool_ids: []}` is stored as-is and
  serves nothing. An unauthorized upstream with no tools is accepted with
  `include_all: true`. A read-shape `server_name` in the body is ignored.
- 400s: a tool name in `tool_ids` or an api_name in `server`: `Must be a valid
  UUID.` An unknown server UUID: `Unknown upstream servers: [...]`. A tool of
  another server: `A tool does not belong to its upstream server.` A server
  listed twice: `Each upstream server may appear once.` An upstream tool name
  in `tool_names`: `Unknown MCP tools: <name>`.

## Reads

- Writes take bare UUIDs in sharing; reads return `{id, display_name}`.
- `owner` is the creator (`{id, display_name}`; `{}` on the system server).
  `employee_access` is `Owner` for the creator, `View` on the system server.
  `sharing_stats` counts levels.
- `endpoint_url` is `https://<host>/api/mcp/<api_name>`.
- The catalogue varies by business and release; list it with `kizen
  mcp-servers tools`. For an AI step that searches a catalog, see `kizen docs
  show automation`, "Searching a knowledge catalog".
- `step-options` is a bare list of `{id, name, api_name, is_enabled, deleted,
  tool_count, has_user_level_upstreams}`. It lists disabled servers and leaves
  out deleted ones.

## Events history

- **Written asynchronously.** Read right after a change, it can be empty; a
  second later the events are there.
- Event keys: `id, group_id, business_id, type_name, type_display_name,
  has_relationship, instance, initiator, data, comments, created`. Types:
  `mcp_server_created`, `mcp_server_updated`.
- `data` is `{api_name, mcp_server_name, old_value, new_value}`, each value
  carrying `name, is_enabled, tool_names, description,
  is_default_for_business, upstreams`. A create has no `old_value`. A
  sharing change logs an update whose old and new values are equal.
  `history` shows `—` for both.
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

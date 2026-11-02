# Writing `code_step` scripts

A `code_step` runs a Python snippet inside a Kizen automation. The loop is:
write it against the code-step namespace → unit-test it standalone with
`kizen code test` → wire it into an automation. No automation round-trips until
the script works.

- **The `code test` flags** → `kizen code test --help` (authoritative).
- **The `code_step` JSON shape** (inputs/outputs binding, `secrets`, `is_list`)
  → `kizen docs show automation`.
- **Everything about writing the script itself** → this file.

## The code-step namespace

Available inside a real `code_step` and inside `kizen code test` alike:

- `inputs.<name>` — read an input value.
- `outputs.<name> = …` — set an output.
- `outputs.log("…")` — emit a debug line (single string arg). This is the
  **only** thing that populates the response `logs` array; plain `print()` is
  NOT captured.
- `kizen.api.get/post/patch/put/delete(path, json=…)` — call the Kizen API
  with auth auto-injected and scoped to the env; all five verbs exist
  (confirmed live 2026-10-30). **Paths are relative to `/api`** — use
  `/custom-objects`, not `/api/custom-objects` (the latter 404s as
  `/api/api/...`). The return is a `requests` response object
  (`.status_code`, `.json()`, `.text`), not a parsed dict, and a 4xx does not
  raise. Every outbound call is captured in the response `http_requests` with
  secrets `[REDACTED]`. For records, see
  [Reading and writing records from a script](#reading-and-writing-records-from-a-script).
- `secrets["<name>"]` — read a secret the step declares in its `secrets` list.
  Subscript, not attribute access. Use this for *third-party* credentials;
  reach for `kizen.api` rather than hand-rolling a session against a stored
  Kizen API key.

Secrets are env-specific: the names go in the automation spec, the values live
on the environment. A step referencing a secret the env doesn't have fails at
runtime, so surface that when authoring.

## Inputs arrive as typed Python objects, not strings

The runtime coerces each input by its declared `data_type` before your script
sees it:

| declared | arrives as |
|---|---|
| `number` | int / float |
| `boolean` | bool |
| `entity` | a single `uuid.UUID` — **not** a list, even for a to-many field |
| `string` on a date field | `datetime.date`, **not** a string |

`str(...)` is the safe normalizer either way — on a `datetime.date` it gives
`"YYYY-MM-DD"`, on a `uuid.UUID` the bare id. Anything unset arrives as `None`,
so null-check before arithmetic or `.strip()`.

Carried over from earlier field notes and not re-verified against a live run:
`kizen code test` can't confirm this table, because there you supply the values
yourself and no coercion happens. Treat it as a starting point, and
`outputs.log(repr(inputs.<name>))` if a step misbehaves on a value you expected
to be a string.

A set relationship field **is** in the record response, as the related
records' `{id, name, display_name}`, and an empty one is left out (see
[Reading and writing records from a script](#reading-and-writing-records-from-a-script),
confirmed live 2026-10-30). To read one field of the related record without a
call, bind it directly as a `related_field` step input; see
`kizen docs show automation`.

## Testing it: `kizen code test`

`POST /api/coderunner/run`. Runs your script in the same Lambda as a real
`code_step`; `kizen.api` calls in it use real credentials and can write live
data. Asks y/N first (`--yes` skips). The server holds the request open until
the run ends, and ends any run at about 55 s with `script error: … Task timed
out after 55.00 seconds` (observed 2026-10-16). `kizen.api` writes made before
the cut still land, so check live state before rerunning. The command waits up
to 2 minutes, so you see that server message rather than a network error.

```
kizen code test --script my_step.py \
  --input n=21:number --input who=world:string \
  --declare-output doubled:number --declare-output greeting:string \
  [--secret MY_API_KEY] [--runtime python-3-13]

# many inputs → JSON files (mirrors what the code-step UI sends):
kizen code test --script my_step.py \
  --inputs-file inputs.json --outputs-file outputs.json
# inputs.json:  {"n": {"type": "number", "value": 21}, "who": {"type": "string", "value": "world"}}
#               (or a bare {"n": 21, "who": "world"} — type defaults to string)
# outputs.json: {"doubled": "number", "greeting": "string"}
# --inputs-file accepts '-' for stdin (when --script is a file); individual
# --input / --output flags override same-named file entries.
```

Read the script from `--script <file>` or stdin. A script or `--inputs-file -`
read from stdin leaves nothing to answer the prompt with, so pass `--yes`.
Output shows `values`, the `logs`, an `http_requests` audit, and — on a script
error — the full traceback (a raised script returns HTTP 400 with the run
envelope; the command renders it and exits non-zero). `--json` emits the raw
response. An unsupported `--runtime` fails client-side (supported:
`python-3-12`, `python-3-13`; default `python-3-13`).

### Debugging `kizen.api` calls

The `http_requests` audit renders as a compact table (method / url / status /
ms) by default. `--http-detail` (`-v`) expands each call with its request
headers, request body, and response body — a call that failed (status ≥ 400 or
a transport error) auto-expands even without the flag. Secrets stay
`[REDACTED]`. Note the sandbox caps each captured `responseBody` at ~1KB, so a
large response is truncated mid-JSON in the audit (the detail view flags this);
the script itself still receives the full response — only the *audit copy* is
capped. `--json` has whatever the sandbox captured.

### Declaring types on the command line

Give a `data_type` **name** (`number`, `datetime`, …) or its short code (`n`,
`dt`, …) — both work. The CLI resolves names→codes exactly as Kizen's own
coderunner client does (`remoteRunner.ts` `toKizenType` / `DATA_TYPE_TO_KIZEN`)
and defaults anything unrecognized to `s`. The wire `t` is a short-code
registry, **not** a raw `field_type` — `integer` maps to `n` for you, and
passing `field_type` names straight through would be rejected with
`Invalid Kizen type '…'`. Scalar values are sent as raw **strings**; the server
coerces by `t` (`n=21:number` → `{t:"n", v:"21"}` → `42.0`). Codes (all
confirmed live):

| name → code | value format |
|-------------|--------------|
| `string`/`text` → `s` | any string |
| `number`/`integer` → `n` | number; comes back stringified (`"42.0"`) |
| `boolean` → `b` | `true`/`false` |
| `phone`/`phonenumber` → `p` | e.g. `+15551234567` |
| `uuid` → `u` | a **bare** id string (not `{id: …}`) |
| `list` → `l` | `v` is a list of `{t, v}` dicts, not raw scalars |
| `date` → `d` | ISO `YYYY-MM-DD` (slashes rejected) |
| `datetime` → `dt` | ISO 8601 (`2026-01-01T12:00:00`, space/`Z` ok) |
| `file` → `f` | a file uuid the env can access |

No dedicated email or time-only code exists — use `string`.

## Reading and writing records from a script

The endpoint table, the `fields` write shape, the search body and the bulk
endpoints are in `kizen docs show records`. This is what a script sends and
gets back through `kizen.api`, with `obj` an object's api_name and `rid` a
record id. Confirmed live 2026-10-30, from `kizen code test` and from an
automation run.

| To… | Call | Answers |
|---|---|---|
| read one record | `get(f"/records/{obj}/{rid}")` | 200, the record |
| create one | `post(f"/records/{obj}/add", json={"fields": [...]})` | 201, the new record with its `id` |
| update one | `patch(f"/records/{obj}/{rid}", json={"fields": [...]})` | 200, the updated record |
| find records | `post(f"/records/{obj}/search", json={"query": [], "and": True, "field_names": [...]})` | 200, `{count, next, previous, errors, results}` |
| archive one | `delete(f"/records/{obj}/{rid}")` | 204 |

A record's `fields` is a **dict keyed by field UUID**, and each value carries
its api_name:

```json
{ "id": "<record uuid>", "object_type": "standard", "access": {"view": true, "edit": true, "remove": true},
  "client_info": null,
  "fields": {
    "<notes field uuid>": { "id": "<notes field uuid>", "name": "request_notes", "field_type": "longtext",
                      "display_name": "Request Notes", "value": "Order a red widget and a blue gadget." },
    "<primary field uuid>": { "id": "<primary field uuid>", "name": "primary_item", "field_type": "relationship",
                      "display_name": "Primary Item",
                      "value": { "id": "<item uuid>", "name": "Red widget", "display_name": "Red widget" } },
    "<items field uuid>": { "id": "<items field uuid>", "name": "request_items", "field_type": "relationship",
                      "display_name": "Request Items",
                      "value": [ { "id": "<item uuid>", "name": "Red widget", "display_name": "Red widget" },
                                 { "id": "<item uuid>", "name": "Blue gadget", "display_name": "Blue gadget" } ] }
  } }
```

The example shows only the custom fields. Every read also returns the
built-in ones, such as `name`, `owner` and `created`.

- **Key it by api_name** before reading:
  `values = {f["name"]: f["value"] for f in resp.json()["fields"].values()}`.
- **An empty field is either left out or `null`.** An empty custom field is
  left out, relationships included, whether it was never set or was cleared.
  Most built-in fields, such as `owner` or a contact's `email`, come back with
  `"value": null` instead, but empty `tags` and `titles` are left out. Read
  with `values.get("…")` and treat a missing field and a null one the same.
- **A to-one relationship** is the related record's `{id, name,
  display_name}`. **A to-many** is a list of them. The mirror field on the
  other object reads the same way.
- **Create and update** take the `fields` write shape.
  A missing required field answers 400 with the field named, e.g.
  `{"name":["This field is required"]}`. PATCH changes only the fields you
  send, and ignores an `archived` key.
- **Search results** each carry a `fields` dict in the same shape as a read,
  and they paginate.
- **Archive** through `DELETE` (there is no hard delete), or through the
  bulk-archive endpoint with `send_email_notification: false`, which answers
  200 `{"number_archived": 1, "async": true}`.
- A 4xx comes back as a response; it does not raise. Check `.status_code`
  yourself.

### Worked example: an AI step's list becomes records

A `call_llm` step writes a JSON list into a string variable, and a code step
creates one `items` record per element. The automation runs on a `requests`
record:

```json
[ { "key": "items_json", "parent_key": null, "step_type": "initialize_variable", "order": 0,
    "action_initialize_variable": { "sources": [],
      "variable": { "name": "items_json", "data_type": "string" } } },
  { "key": "list_items", "parent_key": "items_json", "step_type": "call_llm", "order": 1,
    "action_call_llm": { "model_name": "kizen/lite",
      "merge_field_validation": "error_if_required",
      "prompt": "List each item this request asks for. Reply with only a JSON array, no code fences, where each element is {\"name\": <short item name>, \"note\": <one-line note>}.\nRequest: {{ custom_objects.request_notes }}",
      "destinations": [ { "variable": "items_json" } ] } },
  { "key": "create_items", "parent_key": "list_items", "step_type": "code_step", "order": 2,
    "action_code_step": { "script": "<the script below, as one JSON string>",
      "inputs": [ { "name": "items_json", "input_type": "variable", "variable_name": "items_json" } ],
      "outputs": [] } } ]
```

```python
import json

items = json.loads(inputs.items_json or "[]")
for item in items:
    resp = kizen.api.post(
        "/records/items/add",
        json={
            "fields": [
                {"name": "name", "value": item["name"]},
                {"name": "item_note", "value": item.get("note", "")},
            ]
        },
    )
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"Creating item {item['name']!r} failed: {resp.status_code} {resp.text[:200]}"
        )
    outputs.log(f"Created {item['name']}: {resp.json()['id']}")
```

Confirmed live 2026-10-30:

- The variable reached the script as a `str` holding the model's reply, the
  bare JSON array the prompt asked for, so `json.loads` takes it as is. That
  was one run. If a model wraps its reply in code fences anyway, strip them
  before `json.loads`.
- The run created one record per element, and `kizen automations runs logs
  <execution_id>` showed each `Created …` line.
- A raised exception fails the step. With a blank name, the step read `failed`
  with the exception's message as its error, while the run itself read
  `completed`. Records created before the failing element stay.

## Reading a Contact's newest inbound email

An `email_received` trigger starts a Contacts automation, but the run gets the
Contact, not the email: no step can read the subject or body from the trigger.
A code step reads the email from the Contact's timeline instead. It takes the
newest inbound email from that Contact, which is usually the one that started
the run, but not after two quick replies or on a run started by hand. The
automation that hands its text to an AI step is in `kizen docs show
automation`, "An AI step reads an emailed reply".

**Which emails.** Kizen's UI code suggests the trigger fires only for email
that arrives in a connected (integrated) inbox, with `receiving_inbox` picking
among those inboxes, and that a reply to mail Kizen sent from its own sending
service goes to a reply-to address that isn't synced, so it never reaches the
timeline. Both come from reading the UI code, not from a live run. A synced
email shows on the Contact's timeline as an `integrated_inbox_email_received`
event.

**A run started by the trigger hasn't been seen reading its own email.** If
the inbox sync lags behind the trigger, the event isn't on the timeline yet:
the script finds no inbound email and outputs `""`, and nothing fails. A short
`delay` step before the code step is a hedge.

**Bind the Contact's email, not its id.** On a Contacts automation, the
Contact's id never reaches a script: the merge fields that would carry it
arrive empty (see `kizen docs show automation`, "Merge-field namespace tokens
by step type"). An input with `input_type: "field"` and `field_ref:
"client_client.email"` arrives as the email, so the script looks the Contact up
by it. Confirmed live 2026-10-30.

**The three calls:**

| Call | Answers |
|---|---|
| `post("/records/client_client/search", json=…)`, an `=` filter on `email` | 200, the matching Contacts under `results` |
| `get(f"/records/client_client/{rid}/timeline", params={"size": 50})` | 200, `{count, links, prev_cursor, next_cursor, events}`, newest first |
| `get(f"/synced-email/{sid}/full_response")` | 200, `{from, to, reply_to, subject, text_plain, text_html}` |

- `size` caps the page, and `count` is that page's length, not a total. Older
  events are behind `next_cursor`. Confirmed live 2026-11-02.
- Each event has `type_name`, `created` and `data` (confirmed live
  2026-11-02). An `integrated_inbox_email_received` event's `data` carries
  the synced email's id, the `sid` the body read takes. Its raw key name is
  inferred from Kizen's UI code, which camel-cases it, so the script takes
  `synced_email_id` or `syncedEmailId` and fails the step if neither is there.
- `text_plain` can be empty with the whole reply in `text_html`, so the
  script falls back to the HTML with its tags stripped.

The script, with a `contact_email` input and an `email_body` output:

```python
# Read the newest email this Contact sent to a connected inbox, as plain text.
import html
import re


def ok(resp, what):
    if resp.status_code != 200:
        raise Exception(f"{what} failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


email = (inputs.contact_email or "").strip()
if not email:
    raise Exception("the Contact has no email to look it up by")
match = {"type": "fields", "subtype": "non_custom", "field": "email"}
query = [{"and": True, "filters": [{**match, "condition": "=", "value": email}]}]
found = ok(
    kizen.api.post(
        "/records/client_client/search",
        json={"query": query, "and": True, "field_names": ["email"]},
    ),
    "searching Contacts",
)["results"]
if len(found) != 1:
    raise Exception(f"{len(found)} Contacts have email {email}, not 1")

events = ok(
    kizen.api.get(
        f"/records/client_client/{found[0]['id']}/timeline", params={"size": 50}
    ),
    "reading the timeline",
)["events"]
inbound = [e for e in events if e["type_name"] == "integrated_inbox_email_received"]
outputs.email_body = ""
if not inbound:
    outputs.log(f"no inbound email in the Contact's last {len(events)} timeline events")
else:
    data = max(inbound, key=lambda e: e["created"])["data"]
    sid = data.get("synced_email_id") or data.get("syncedEmailId")
    if not sid:
        raise Exception(f"no synced email id in the event's data: {sorted(data)}")
    body = ok(kizen.api.get(f"/synced-email/{sid}/full_response"), f"reading {sid}")
    text = body.get("text_plain") or ""
    if not text.strip():
        markup = body.get("text_html") or ""
        markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
        markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", markup)
        text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    outputs.email_body = text.strip()
    outputs.log(f"read email {sid}: {len(outputs.email_body)} characters")
```

With no inbound email, the step succeeds with `email_body` set to `""` and a
log line, so the steps after it still run. An email no Contact has fails the
step with the count. Confirmed live 2026-11-02, with `kizen code test` and in
an automation run, on a Contact with no inbound email.

## Wiring it into an automation

Once it passes, add it as a `code_step` with `kizen automations steps
add`/`edit` and bind the step's inputs/outputs to fields or variables. The
automation step carries the code in a field named **`script`**, while
coderunner's is `user_script` — the same code either way, under a different key.

Filters inside a production script should use the `kizen_builder.filtering` DSL
(`Field`, `All`/`Any`, `as_search_body`, `as_filter_config`) rather than
hand-written clause dicts; see `kizen docs show filters`.

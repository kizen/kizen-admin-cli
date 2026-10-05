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
- `kizen.api.get/post(...)` — call the Kizen API with auth auto-injected and
  scoped to the env. **Paths are relative to `/api`** — use `/custom-objects`,
  not `/api/custom-objects` (the latter 404s as `/api/api/...`). The return is
  a response object (`.status_code`, `.json()`), not a parsed dict. Every
  outbound call is captured in the response `http_requests` with secrets
  `[REDACTED]`.
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

Relationship fields are **not** included in the main record response — reading
one takes a second `kizen.api.get` against the relationship endpoint. A field
from a related record can also be bound directly as a step input, which avoids
the extra call entirely; see `kizen docs show automation`.

## Testing it: `kizen code test`

Runs against the live sandbox (`POST /api/coderunner/run` — the same secure
Lambda sandbox as a real `code_step`), creates nothing, and needs no
confirmation.

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

Read the script from `--script <file>` or stdin. Output shows `values`, the
`logs`, an `http_requests` audit, and — on a script error — the full traceback
(a raised script returns HTTP 400 with the run envelope; the command renders it
and exits non-zero). `--json` emits the raw response. An unsupported
`--runtime` fails client-side (supported: `python-3-12`, `python-3-13`; default
`python-3-13`).

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

## Wiring it into an automation

Once it passes, add it as a `code_step` with `kizen automations steps
add`/`edit` and bind the step's inputs/outputs to fields or variables. The
automation step carries the code in a field named **`script`**, while
coderunner's is `user_script` — the same code either way, under a different key.

Filters inside a production script should use the `kizen_builder.filtering` DSL
(`Field`, `All`/`Any`, `as_search_body`, `as_filter_config`) rather than
hand-written clause dicts; see `kizen docs show filters`.

# Environments: profiles, credentials and app-builder

Which Kizen environment a command talks to, and where its credentials come
from. The approval gate and the conventions are in `kizen docs show
operating`.

## One env per working directory

Environment selection is *positional* — the working directory is pinned to one
profile via a committed `.kizen/profile` file that records the expected
`business_id`; the credentials themselves live centrally in
`~/.config/kizen/credentials.toml` (0600), keyed by profile name. Commands
resolve the profile from the pin automatically — no env label argument — and
refuse to run if the resolved profile's `business_id` doesn't match the pin. A
second environment is a second folder with its own pin.

Profile-name resolution order: `--profile`/`-p` > `$KIZEN_PROFILE` >
`.kizen/profile` pin > `$KIZEN_ENV` (legacy label); the credentials for that
name come from the central store. `kizen envs list` shows what the current
directory resolves to.

`kizen init` asks which Kizen environment you're on — `go`, `fmo`, `staging`,
or `integration` — and resolves the name to its API host itself, so nothing
gets pointed at the wrong host by hand; free-text URL entry is still there for
self-hosted or one-off setups, reached with a deliberate `url` choice, not a
default.

## Under app-builder, there is no profile

App-builder is the npm package `@kizenapps/cli` (`appbuilder admin`). Every
`kizen <args>` in these docs is `npx @kizenapps/cli admin <args>` there
(`kizen docs show operating`).

The wrapper launches `kizen` with these variables in the process environment
(a `.env` file can't supply them):

| Variable | Value |
|---|---|
| `KIZEN_AUTH_HELPER` | A command that prints `{"Authorization": "Bearer <token>"}` on one line. Split like a shell word list but run without a shell, with a 15s timeout. Its presence turns this mode on. |
| `KIZEN_BASE_URL` | The https origin, shaped like a profile's `base_url` (`https://staging.kizen.com`, no `/api`). |
| `KIZEN_BUSINESS_ID`, `KIZEN_USER_ID` | The identity. The directory pin's `business_id` is still enforced against `KIZEN_BUSINESS_ID`. |

All four are required. The credential store is never read. `--profile` or
`$KIZEN_PROFILE` alongside them is an error. Requests carry only the Bearer
header. The helper runs on the first request; whenever Kizen answers 401 it
runs again and that request is resent once. The Kizen user needs the "OAuth
authorizations" permission; without it every request is a 403. `kizen init`,
`kizen envs list` and `kizen upgrade` refuse, because app-builder owns the
credentials and the installation; `kizen upgrade --check` says the
installation is managed by app-builder and exits 0.

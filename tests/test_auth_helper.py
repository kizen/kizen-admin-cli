"""Helper mode: config from KIZEN_* variables, Bearer auth from a helper command.

The helper is a real script under ``tmp_path``, run through ``sys.executable``,
so every subprocess path (exit codes, stderr, timeouts, no shell) is the real
one. HTTP goes through respx.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from kizen_builder import auth_helper, config, profiles
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.auth_helper import AuthHelperError
from kizen_builder.cli import app
from kizen_builder.config import ConfigError, HelperAuth, load_env_config
from tests.conftest import FAKE_BUSINESS_ID, FAKE_USER_ID

SECRET = "SECRET-TOKEN"
BASE = "https://helper.kizen.test"
THING = f"{BASE}/api/thing"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No cached header or --profile override leaks between tests, and the
    conftest's KIZEN_ENV (a profile-mode fallback) stays set, as a wrapper's
    environment might carry it."""
    auth_helper.clear_cache()
    config.set_profile_override(None)
    for var in config._HELPER_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    auth_helper.clear_cache()
    config.set_profile_override(None)


def _script(tmp_path: Path, body: str, name: str = "helper.py") -> Path:
    """Write a helper script that appends one line to ``calls.log`` per run,
    then runs ``body``."""
    log = tmp_path / "calls.log"
    path = tmp_path / name
    path.write_text(
        "import sys\n"
        f"with open({str(log)!r}, 'a') as fh:\n"
        "    fh.write('run\\n')\n" + body
    )
    return path


def _calls(tmp_path: Path) -> int:
    log = tmp_path / "calls.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def _ok_body(token: str = SECRET) -> str:
    return f"print({json.dumps(json.dumps({'Authorization': f'Bearer {token}'}))})\n"


def _helper_env(monkeypatch, script: Path, *extra: str) -> None:
    command = shlex.join([sys.executable, str(script), *extra])
    monkeypatch.setenv("KIZEN_AUTH_HELPER", command)
    monkeypatch.setenv("KIZEN_BASE_URL", BASE)
    monkeypatch.setenv("KIZEN_BUSINESS_ID", FAKE_BUSINESS_ID)
    monkeypatch.setenv("KIZEN_USER_ID", FAKE_USER_ID)


@pytest.fixture
def ok_helper(tmp_path, monkeypatch) -> Path:
    script = _script(tmp_path, _ok_body())
    _helper_env(monkeypatch, script)
    return script


def _pin(monkeypatch, business_id: str | None) -> None:
    pin = profiles.Pin(profile="alpha", business_id=business_id, path=Path("/x/p"))
    monkeypatch.setattr(profiles, "load_pin", lambda start=None: pin)


# --- config resolution ------------------------------------------------------


def test_helper_mode_builds_config_from_env(ok_helper):
    cfg = load_env_config()
    assert cfg.base_url == BASE
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.user_id == FAKE_USER_ID
    assert isinstance(cfg.auth, HelperAuth)
    assert cfg.auth.argv == (sys.executable, str(ok_helper))


def test_helper_mode_never_opens_credential_store(ok_helper, tmp_path, monkeypatch):
    """(b) A malformed credentials.toml would raise if anything parsed it."""
    xdg = tmp_path / "xdg"
    (xdg / "kizen").mkdir(parents=True)
    (xdg / "kizen" / "credentials.toml").write_text("[[[ not toml")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    with pytest.raises(Exception):  # noqa: B017 - prove the store is poisoned
        profiles.list_profiles()

    cfg = load_env_config()
    assert cfg.business_id == FAKE_BUSINESS_ID
    with respx.mock:
        respx.get(THING).respond(json={"ok": True})
        with KizenClient(cfg) as client:
            assert client.get("/api/thing") == {"ok": True}


def test_helper_mode_pin_match_passes(ok_helper, monkeypatch):
    _pin(monkeypatch, FAKE_BUSINESS_ID)
    assert load_env_config().business_id == FAKE_BUSINESS_ID


def test_helper_mode_pin_mismatch_refuses(ok_helper, monkeypatch):
    """(c)"""
    _pin(monkeypatch, "ZZZZ")
    with pytest.raises(ConfigError, match="Refusing") as exc:
        load_env_config()
    assert "KIZEN_BUSINESS_ID" in str(exc.value)


@pytest.mark.parametrize(
    "unset", ["KIZEN_BASE_URL", "KIZEN_BUSINESS_ID", "KIZEN_USER_ID"]
)
def test_helper_mode_partial_vars_raise_naming_missing(ok_helper, monkeypatch, unset):
    """(d) partial set."""
    monkeypatch.delenv(unset)
    with pytest.raises(ConfigError) as exc:
        load_env_config()
    assert unset in str(exc.value)
    others = {"KIZEN_BASE_URL", "KIZEN_BUSINESS_ID", "KIZEN_USER_ID"} - {unset}
    missing_clause = str(exc.value).split(" missing")[0]
    assert not any(o in missing_clause for o in others)


def test_helper_mode_empty_helper_counts_as_missing(ok_helper, monkeypatch):
    monkeypatch.setenv("KIZEN_AUTH_HELPER", "  ")
    with pytest.raises(ConfigError, match="KIZEN_AUTH_HELPER is missing"):
        load_env_config()


def test_helper_mode_with_kizen_profile_is_ambiguous(ok_helper, monkeypatch):
    """(d) mixed with a profile."""
    monkeypatch.setenv("KIZEN_PROFILE", "testenv")
    with pytest.raises(ConfigError, match=r"\$KIZEN_PROFILE"):
        load_env_config()


def test_helper_mode_with_profile_flag_is_ambiguous(ok_helper):
    config.set_profile_override("testenv")
    with pytest.raises(ConfigError, match="--profile"):
        load_env_config()


def test_helper_mode_with_explicit_env_name_is_ambiguous(ok_helper):
    with pytest.raises(ConfigError, match="explicit environment name"):
        load_env_config("testenv")


def test_helper_mode_profile_flag_via_cli_is_ambiguous(ok_helper):
    result = runner.invoke(app, ["--profile", "testenv", "objects", "list"])
    assert result.exit_code == 1
    assert "Use one or the other" in result.output


@pytest.mark.parametrize(
    "url",
    [
        "http://helper.kizen.test",
        "https://helper.kizen.test/api",
        "helper.kizen.test",
        "https://helper.kizen.test?x=1",
    ],
)
def test_helper_mode_rejects_non_https_origin(ok_helper, monkeypatch, url):
    monkeypatch.setenv("KIZEN_BASE_URL", url)
    with pytest.raises(ConfigError, match="https origin"):
        load_env_config()


def test_helper_mode_trailing_slash_is_normalized(ok_helper, monkeypatch):
    monkeypatch.setenv("KIZEN_BASE_URL", BASE + "/")
    assert load_env_config().base_url == BASE


def test_helper_from_dotenv_is_ignored(tmp_path, monkeypatch):
    """A .env file can't turn helper mode on (it would run its command)."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("KIZEN_AUTH_HELPER=touch pwned\n")
    cfg = load_env_config(dotenv_path=dotenv)
    assert cfg.name == "testenv"
    assert not config.helper_mode()


def test_profile_mode_unaffected_by_stray_helper_vars(monkeypatch):
    """Without KIZEN_AUTH_HELPER, the identity variables (also `kizen init`'s
    envvars) don't change profile resolution."""
    monkeypatch.setenv("KIZEN_BUSINESS_ID", "other")
    monkeypatch.setenv("KIZEN_BASE_URL", BASE)
    cfg = load_env_config()
    assert cfg.name == "testenv"
    assert cfg.business_id == FAKE_BUSINESS_ID


# --- client -----------------------------------------------------------------


@respx.mock
def test_helper_mode_sends_bearer_and_no_x_headers(ok_helper):
    """(g)"""
    route = respx.route(url__startswith=BASE).respond(json={"ok": True})
    with KizenClient(load_env_config()) as client:
        client.get("/api/thing")
        client.post("/api/thing", json={"a": 1})
        client.patch("/api/thing", json={"a": 1})
        client.put("/api/thing", json={"a": 1})
        client.delete("/api/thing")
        client.get_bytes("/api/thing")
    assert route.call_count == 6
    for call in route.calls:
        sent = call.request.headers
        assert sent["Authorization"] == f"Bearer {SECRET}"
        for name in ("X-API-KEY", "X-USER-ID", "X-BUSINESS-ID"):
            assert name not in sent


@respx.mock
def test_helper_runs_once_per_process(ok_helper, tmp_path):
    """(f) N requests across several clients run the helper once."""
    route = respx.get(THING).respond(json={})
    cfg = load_env_config()
    for _ in range(3):
        with KizenClient(cfg) as client:
            client.get("/api/thing")
            client.get("/api/thing")
    assert route.call_count == 6
    assert _calls(tmp_path) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_401_then_200_refreshes_once_and_succeeds(ok_helper, tmp_path, method):
    """(e) 401 → helper again → resend → 200."""
    with respx.mock:
        route = respx.route(method=method, url=THING).mock(
            side_effect=[
                httpx.Response(401, json={"detail": "expired"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        with KizenClient(load_env_config()) as client:
            result = (
                client.get("/api/thing")
                if method == "GET"
                else client.post("/api/thing", json={"name": "x"})
            )
    assert result == {"ok": True}
    assert _calls(tmp_path) == 2
    assert route.call_count == 2
    if method == "POST":
        assert [json.loads(c.request.content) for c in route.calls] == [
            {"name": "x"},
            {"name": "x"},
        ]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_401_then_401_runs_helper_twice_then_errors(ok_helper, tmp_path, method):
    """(e) A second 401 surfaces as the auth error; no third send."""
    with respx.mock:
        route = respx.route(method=method, url=THING).respond(
            401, json={"detail": "bad token"}
        )
        with (
            KizenClient(load_env_config()) as client,
            pytest.raises(KizenAPIError) as exc,
        ):
            if method == "GET":
                client.get("/api/thing")
            else:
                client.post("/api/thing", json={})
    assert exc.value.status_code == 401
    assert "bad token" in exc.value.message
    assert "auth helper" in exc.value.message
    assert "kizen init" not in exc.value.message
    assert route.call_count == 2
    assert _calls(tmp_path) == 2


def test_401_refresh_composes_with_get_retry(ok_helper, tmp_path, monkeypatch):
    """A 503 retry after a refreshed 401 reuses the refreshed header."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    with respx.mock:
        route = respx.get(THING).mock(
            side_effect=[
                httpx.Response(401),
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        with KizenClient(load_env_config()) as client:
            assert client.get("/api/thing") == {"ok": True}
    assert route.call_count == 3
    assert _calls(tmp_path) == 2


def test_403_oauth_permission_denied_message(ok_helper):
    body = {
        "detail": "You do not have permission to use oauth authorization.",
        "code": "permission_denied",
    }
    with respx.mock:
        respx.get(THING).respond(403, json=body)
        with (
            KizenClient(load_env_config()) as client,
            pytest.raises(KizenAPIError) as exc,
        ):
            client.get("/api/thing")
    assert exc.value.status_code == 403
    assert "OAuth authorizations permission" in exc.value.message


def test_403_other_permission_denied_keeps_server_message(ok_helper):
    body = {
        "detail": "You do not have permission to perform this action.",
        "code": "permission_denied",
    }
    with respx.mock:
        respx.get(THING).respond(403, json=body)
        with (
            KizenClient(load_env_config()) as client,
            pytest.raises(KizenAPIError) as exc,
        ):
            client.get("/api/thing")
    assert exc.value.message == body["detail"]


# --- helper failures (i) ----------------------------------------------------

FAILURES = {
    "nonzero_exit": (
        "sys.stderr.write('login expired\\n'); sys.exit(3)\n",
        "exited with status 3",
    ),
    "nonzero_exit_with_token_on_stdout": (
        _ok_body() + "sys.stderr.write('login expired\\n'); sys.exit(1)\n",
        "exited with status 1",
    ),
    "empty_stdout_exit_0": (
        "sys.stderr.write('login expired\\n')\n",
        "printed nothing on stdout",
    ),
    "not_json": (
        f"print('Bearer {SECRET}'); sys.stderr.write('login expired\\n')\n",
        "other than JSON",
    ),
    "json_not_object": (
        f"print('[\"Bearer {SECRET}\"]'); sys.stderr.write('login expired\\n')\n",
        "printed JSON that isn't an object",
    ),
    "missing_authorization": (
        f"print('{{\"token\": \"{SECRET}\"}}'); sys.stderr.write('login expired\\n')\n",
        "no Authorization key",
    ),
    "not_bearer": (
        f'print(\'{{"Authorization": "Basic {SECRET}"}}\'); '
        "sys.stderr.write('login expired\\n')\n",
        "isn't a Bearer token",
    ),
    "bearer_with_newline": (
        f'print(\'{{"Authorization": "Bearer {SECRET}\\\\nX: y"}}\'); '
        "sys.stderr.write('login expired\\n')\n",
        "isn't a Bearer token",
    ),
    "timeout": (
        f'print(\'{{"Authorization": "Bearer {SECRET}"}}\', flush=True); '
        "sys.stderr.write('login expired\\n'); sys.stderr.flush()\n"
        "import time; time.sleep(30)\n",
        "timed out after",
    ),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_helper_failure_is_clean_error_with_stderr(case, tmp_path, monkeypatch):
    """(i) + (a): each failure names the problem and the helper's stderr, and
    never the token."""
    body, expected = FAILURES[case]
    _helper_env(monkeypatch, _script(tmp_path, body))
    if case == "timeout":
        monkeypatch.setattr(auth_helper, "TIMEOUT_S", 1.0)
    with respx.mock:
        respx.get(THING).respond(json={})
        with (
            KizenClient(load_env_config()) as client,
            pytest.raises(AuthHelperError) as exc,
        ):
            client.get("/api/thing")
    text = str(exc.value)
    assert expected in text
    assert "login expired" in text
    assert SECRET not in text
    assert SECRET not in repr(exc.value)


def test_helper_not_found_is_clean_error(monkeypatch, tmp_path):
    """(i) command not found."""
    _helper_env(monkeypatch, tmp_path / "x.py")
    monkeypatch.setenv("KIZEN_AUTH_HELPER", str(tmp_path / "no-such-helper"))
    with (
        respx.mock,
        KizenClient(load_env_config()) as client,
        pytest.raises(AuthHelperError, match="was not found"),
    ):
        client.get("/api/thing")


def test_helper_not_executable_is_clean_error(monkeypatch, tmp_path):
    """(i) An OSError other than not-found (here EACCES) is a clean error."""
    script = tmp_path / "helper.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    _helper_env(monkeypatch, script)
    monkeypatch.setenv("KIZEN_AUTH_HELPER", str(script))
    with (
        respx.mock,
        KizenClient(load_env_config()) as client,
        pytest.raises(AuthHelperError, match=r"could not be run \(") as exc,
    ):
        client.get("/api/thing")
    assert "was not found" not in str(exc.value)


@pytest.mark.parametrize("case", ["nonzero_exit_with_token_on_stdout", "not_bearer"])
def test_cli_helper_failure_has_no_traceback_or_token(case, tmp_path, monkeypatch):
    """(a) + (i) through the CLI: one error line, exit 1, no token anywhere."""
    _helper_env(monkeypatch, _script(tmp_path, FAILURES[case][0]))
    monkeypatch.delenv("KIZEN_DEBUG", raising=False)
    with respx.mock:
        respx.route(url__startswith=BASE).respond(json={"results": [], "next": None})
        result = runner.invoke(app, ["objects", "list"])
    assert result.exit_code == 1
    assert "error: auth helper" in result.output
    assert "Traceback" not in result.output
    assert SECRET not in result.output
    assert SECRET not in repr(result.exception)


def test_token_absent_from_output_on_api_failures(ok_helper, monkeypatch):
    """(a) 401 twice, 403 and a network error through the CLI never print it."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    for response in (
        httpx.Response(401, json={"detail": "nope"}),
        httpx.Response(403, json={"detail": "oauth", "code": "permission_denied"}),
        httpx.ConnectError("boom"),
    ):
        auth_helper.clear_cache()
        with respx.mock:
            route = respx.route(url__startswith=BASE)
            if isinstance(response, Exception):
                route.mock(side_effect=response)
            else:
                route.mock(return_value=response)
            result = runner.invoke(app, ["objects", "list"])
        assert result.exit_code == 1, result.output
        assert SECRET not in result.output
        assert SECRET not in repr(result.exception)


def test_config_and_auth_repr_hide_secrets(ok_helper):
    cfg = load_env_config()
    with KizenClient(cfg) as client:
        assert SECRET not in repr(client._client.auth)
    assert SECRET not in repr(cfg)
    profile_cfg = config._load_profile("testenv")
    assert "test-api-key" not in repr(profile_cfg)


# --- no shell (h) -----------------------------------------------------------


@pytest.mark.parametrize("arg", ["; touch {p}", "$(touch {p})", "`touch {p}`"])
def test_helper_runs_without_a_shell(tmp_path, monkeypatch, arg):
    """Shell syntax in the command reaches the helper as literal argv."""
    marker = tmp_path / "pwned"
    argv_log = tmp_path / "argv.json"
    script = _script(
        tmp_path,
        f"import json; open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        + _ok_body(),
    )
    _helper_env(monkeypatch, script)
    literal = arg.format(p=marker)
    monkeypatch.setenv(
        "KIZEN_AUTH_HELPER",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {literal}",
    )
    with respx.mock:
        respx.get(THING).respond(json={})
        with KizenClient(load_env_config()) as client:
            client.get("/api/thing")
    assert not marker.exists()
    assert json.loads(argv_log.read_text()) == shlex.split(literal)


# --- commands that manage what app-builder owns -----------------------------


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["upgrade"],
            "error: `kizen upgrade` is unavailable here: "
            "app-builder manages this installation.",
        ),
        (
            ["init", "--profile", "x", "--skip-validation"],
            "error: `kizen init` is unavailable here: "
            "app-builder manages this environment's credentials.",
        ),
        (
            ["envs", "list"],
            "error: `kizen envs list` is unavailable here: "
            "app-builder manages the environment.",
        ),
    ],
)
def test_store_and_install_commands_refuse_in_helper_mode(
    ok_helper, argv, message, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    before = profiles.credentials_path().read_bytes()
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert result.output.strip() == message
    assert profiles.credentials_path().read_bytes() == before
    assert not (tmp_path / ".kizen").exists()
    assert _calls(tmp_path) == 0


def test_upgrade_check_in_helper_mode_notes_managed_and_exits_0(
    ok_helper, tmp_path, monkeypatch
):
    """`--check` keeps its always-exit-0 contract: one quiet note, and no
    install detection, version lookup, git or network access."""
    from kizen_builder import upgrade as upgrade_mod

    def explode(*args, **kwargs):
        raise AssertionError("--check must not look anything up in helper mode")

    monkeypatch.setattr(upgrade_mod, "detect_install", explode)
    monkeypatch.setattr(upgrade_mod, "check_latest", explode)
    monkeypatch.setattr(upgrade_mod, "run_steps", explode)
    monkeypatch.setattr("subprocess.run", explode)
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(500)
        result = runner.invoke(app, ["upgrade", "--check"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ("this installation is managed by app-builder.")
    assert any_http.call_count == 0
    assert _calls(tmp_path) == 0

"""API-key mode: KIZEN_* config, X-API-KEY auth from $KICODE_API_KEY.

The env key, the store's key and every ``.env`` value differ, so a test can
tell which source a value came from. HTTP goes through respx.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from kizen_builder import config, profiles
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.cli import app
from kizen_builder.config import ApiKeyAuth, ConfigError, load_env_config
from tests.conftest import FAKE_BUSINESS_ID, FAKE_USER_ID, OTHER_BUSINESS_ID

KEY = "sig:HS256:KEYMODE-SECRET-9f3a"
STORE_KEY = "test-api-key"  # conftest's "testenv" profile
BASE = "https://keymode.kizen.test"
OTHER_USER_ID = "00000000-0000-4000-8000-0000000009e7"
runner = CliRunner()
KEY_AUTH = ApiKeyAuth(KEY, env_var="KICODE_API_KEY")

_ALL_VARS = (
    *config._HELPER_VARS,
    config.KICODE_KEY_ENV,
    "KIZEN_API_KEY",
    "KIZEN_PROFILE",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Start with no wrapper variable set and no --profile override. setenv
    before delenv makes monkeypatch remove anything a ``.env`` load writes."""
    config.set_profile_override(None)
    for var in _ALL_VARS:
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    yield
    config.set_profile_override(None)


@pytest.fixture
def key_env(monkeypatch) -> None:
    monkeypatch.setenv("KICODE_API_KEY", KEY)
    monkeypatch.setenv("KIZEN_BASE_URL", BASE)
    monkeypatch.setenv("KIZEN_BUSINESS_ID", FAKE_BUSINESS_ID)
    monkeypatch.setenv("KIZEN_USER_ID", FAKE_USER_ID)


def _pin(monkeypatch, business_id: str | None) -> None:
    pin = profiles.Pin(profile="alpha", business_id=business_id, path=Path("/x/p"))
    monkeypatch.setattr(profiles, "load_pin", lambda start=None: pin)


def _error(env_name: str | None = None) -> str:
    with pytest.raises(ConfigError) as exc:
        load_env_config(env_name)
    return str(exc.value)


# --- invariants -------------------------------------------------------------


@respx.mock
def test_api_key_mode_uses_env_values_and_api_key_auth(key_env):
    cfg = load_env_config()
    assert cfg.name == "keymode.kizen.test"
    assert cfg.base_url == BASE
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.user_id == FAKE_USER_ID
    assert cfg.auth == KEY_AUTH

    route = respx.route(url__startswith=BASE).respond(json={"ok": True})
    with KizenClient(cfg) as client:
        client.get("/api/thing")
        client.post("/api/thing", json={"a": 1})
        client.patch("/api/thing", json={"a": 1})
        client.put("/api/thing", json={"a": 1})
        client.delete("/api/thing")
        client.get_bytes("/api/thing")
    assert route.call_count == 6
    for call in route.calls:
        sent = call.request.headers
        assert sent["X-API-KEY"] == KEY
        assert sent["X-BUSINESS-ID"] == FAKE_BUSINESS_ID
        assert sent["X-USER-ID"] == FAKE_USER_ID
        assert "Authorization" not in sent
    assert KEY not in repr(cfg)


def test_api_key_mode_never_opens_credential_store(key_env, tmp_path, monkeypatch):
    """A store holding a different, selectable profile and key is ignored,
    and nothing reads it at all."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    profiles.write_profile(
        profiles.ProfileCreds(
            name="other",
            api_key="STORE-KEY-must-not-be-used",
            business_id=OTHER_BUSINESS_ID,
            user_id=OTHER_USER_ID,
            base_url="https://store.kizen.test",
        )
    )
    monkeypatch.setenv("KIZEN_ENV", "other")

    def explode(*args, **kwargs):
        raise AssertionError("API-key mode must not read the credential store")

    monkeypatch.setattr(profiles, "get_profile", explode)
    monkeypatch.setattr(profiles, "list_profiles", explode)
    monkeypatch.setattr(profiles, "_read_profiles_table", explode)

    cfg = load_env_config()
    assert cfg.auth == KEY_AUTH
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.user_id == FAKE_USER_ID
    assert cfg.base_url == BASE


def test_api_key_mode_ignores_dotenv(key_env, tmp_path, monkeypatch):
    """A .env in the working directory changes nothing, and isn't loaded."""
    (tmp_path / ".env").write_text(
        "KIZEN_PROFILE=testenv\n"
        "KIZEN_ENV=testenv\n"
        "KICODE_API_KEY=DOTENV-KEY\n"
        "KIZEN_API_KEY=DOTENV-INIT-KEY\n"
        "KIZEN_BASE_URL=https://dotenv.kizen.test\n"
        f"KIZEN_BUSINESS_ID={OTHER_BUSINESS_ID}\n"
        f"KIZEN_USER_ID={OTHER_USER_ID}\n"
        "KIZEN_AUTH_HELPER=touch pwned\n"
    )
    monkeypatch.chdir(tmp_path)

    cfg = load_env_config()
    assert cfg.auth == KEY_AUTH
    assert cfg.base_url == BASE
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.user_id == FAKE_USER_ID
    assert "KIZEN_PROFILE" not in os.environ
    assert "KIZEN_AUTH_HELPER" not in os.environ
    assert "KIZEN_API_KEY" not in os.environ


def test_dotenv_cannot_enable_api_key_mode(tmp_path):
    """A .env carrying the whole key-mode contract, under the switch and under
    `kizen init`'s KIZEN_API_KEY, still resolves the profile with the store's
    key, and leaves the mode off afterwards."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "KICODE_API_KEY=DOTENV-KEY\n"
        "KIZEN_API_KEY=DOTENV-INIT-KEY\n"
        "KIZEN_BASE_URL=https://dotenv.kizen.test\n"
        f"KIZEN_BUSINESS_ID={OTHER_BUSINESS_ID}\n"
        f"KIZEN_USER_ID={OTHER_USER_ID}\n"
    )
    cfg = load_env_config(dotenv_path=dotenv)
    assert cfg.name == "testenv"
    assert cfg.auth == ApiKeyAuth(STORE_KEY)
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert not config.api_key_mode()
    assert config.wrapper() is None
    assert "KICODE_API_KEY" not in os.environ


def test_dotenv_kizen_api_key_is_not_popped(tmp_path):
    """Only the switch is dropped after a .env load; KIZEN_API_KEY keeps its
    shipped behaviour and stays in the environment."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("KIZEN_API_KEY=DOTENV-INIT-KEY\n")
    cfg = load_env_config(dotenv_path=dotenv)
    assert cfg.auth == ApiKeyAuth(STORE_KEY)
    assert os.environ["KIZEN_API_KEY"] == "DOTENV-INIT-KEY"


def test_exported_kizen_api_key_keeps_init_fallback(tmp_path, monkeypatch):
    """KIZEN_API_KEY alone doesn't switch modes: profile resolution is
    unchanged, and `kizen init` still takes it as the API key."""
    monkeypatch.setenv("KIZEN_API_KEY", "EXPORTED-INIT-KEY")
    assert not config.api_key_mode()
    assert config.wrapper() is None
    cfg = load_env_config()
    assert cfg.name == "testenv"
    assert cfg.auth == ApiKeyAuth(STORE_KEY)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "init",
            "--profile",
            "fresh",
            "--business-id",
            OTHER_BUSINESS_ID,
            "--user-id",
            OTHER_USER_ID,
            "--base-url",
            "https://init.kizen.test",
            "--skip-validation",
        ],
        input="",
    )
    assert result.exit_code == 0, result.output
    stored = profiles.get_profile("fresh")
    assert stored is not None
    assert stored.api_key == "EXPORTED-INIT-KEY"
    assert stored.business_id == OTHER_BUSINESS_ID


def test_api_key_never_in_error_messages(key_env, monkeypatch):
    """Every key-mode ConfigError path, with a distinctive key in the env."""
    messages: list[str] = []

    monkeypatch.delenv("KIZEN_USER_ID")
    messages.append(_error())
    monkeypatch.setenv("KIZEN_USER_ID", FAKE_USER_ID)

    monkeypatch.setenv("KIZEN_BASE_URL", "http://keymode.kizen.test/api")
    messages.append(_error())
    monkeypatch.setenv("KIZEN_BASE_URL", BASE)

    monkeypatch.setenv("KIZEN_AUTH_HELPER", "helper")
    messages.append(_error())
    monkeypatch.delenv("KIZEN_AUTH_HELPER")

    messages.append(_error("testenv"))
    monkeypatch.setenv("KIZEN_PROFILE", "testenv")
    config.set_profile_override("testenv")
    messages.append(_error())
    monkeypatch.delenv("KIZEN_PROFILE")
    config.set_profile_override(None)

    _pin(monkeypatch, OTHER_BUSINESS_ID)
    messages.append(_error())

    assert len(messages) == 6
    for message in messages:
        assert KEY not in message
        assert "KEYMODE-SECRET" not in message


def test_api_key_never_in_cli_output_on_auth_failure(key_env, monkeypatch):
    """A 401 through the CLI prints one error line that blames the variable,
    not `kizen init`, and never the key."""
    monkeypatch.delenv("KIZEN_DEBUG", raising=False)
    with respx.mock:
        respx.route(url__startswith=BASE).respond(401, json={"detail": "bad key"})
        result = runner.invoke(app, ["objects", "list"])
    assert result.exit_code == 1, result.output
    assert "the API key in $KICODE_API_KEY may be invalid" in result.output
    assert "kizen init" not in result.output
    assert KEY not in result.output
    assert KEY not in repr(result.exception)


@pytest.mark.parametrize("status", [401, 403])
def test_api_key_mode_auth_failure_hint(key_env, status):
    with respx.mock:
        respx.get(f"{BASE}/api/thing").respond(status, json={"detail": "nope"})
        with (
            KizenClient(load_env_config()) as client,
            pytest.raises(KizenAPIError) as exc,
        ):
            client.get("/api/thing")
    assert str(exc.value) == (
        f"HTTP {status}: nope — the API key in $KICODE_API_KEY may be invalid, "
        "expired or revoked."
    )


@pytest.mark.parametrize(
    "sep",
    ["\n", "\r", "\r\n", "\x01", "\x7f", " ", "\u00e9"],
    ids=["lf", "cr", "crlf", "ctrl", "del", "space", "non-ascii"],
)
def test_malformed_key_never_echoed(key_env, monkeypatch, sep):
    """A key with anything but visible ASCII is refused at the config boundary,
    naming the variable only. (A NUL can't reach here: no environment can hold
    one.)"""
    monkeypatch.delenv("KIZEN_DEBUG", raising=False)
    monkeypatch.setenv("KICODE_API_KEY", f"BADKEY{sep}Z9QX")
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(json={"results": [], "next": None})
        result = runner.invoke(app, ["objects", "list"], terminal_width=200)
    assert result.exit_code == 1
    assert " ".join(result.output.split()) == (
        "error: KICODE_API_KEY is invalid: it may contain only visible "
        "ASCII characters (no spaces, tabs, line breaks or control characters)."
    )
    for text in (result.output, repr(result.exception)):
        assert "BADKEY" not in text
        assert "Z9QX" not in text
    assert any_http.call_count == 0


@pytest.mark.parametrize("var", ["KIZEN_BUSINESS_ID", "KIZEN_USER_ID"])
def test_malformed_identity_names_variable_only(key_env, monkeypatch, var):
    monkeypatch.setenv(var, "ID-FRAGMENT\nQ7")
    message = _error()
    assert message == (
        f"{var} is invalid: it may contain only visible ASCII characters "
        "(no spaces, tabs, line breaks or control characters)."
    )
    assert "ID-FRAGMENT" not in message


def test_malformed_vars_listed_together(key_env, monkeypatch):
    monkeypatch.setenv("KICODE_API_KEY", "BAD KEY")
    monkeypatch.setenv("KIZEN_USER_ID", "bad id")
    assert _error().startswith(
        "KICODE_API_KEY, KIZEN_USER_ID are invalid: each may contain only"
    )


def test_auth_hint_comes_from_config_not_environment(key_env, monkeypatch):
    """The client decides from the config it was built with: a key-mode config
    keeps its hint with the env cleared, and a profile config never gets it."""
    key_cfg = load_env_config()
    monkeypatch.delenv("KICODE_API_KEY")
    profile_cfg = load_env_config()
    monkeypatch.setenv("KICODE_API_KEY", KEY)
    with respx.mock:
        respx.get(f"{BASE}/api/thing").respond(401, json={"detail": "nope"})
        respx.get("https://kizen.test/api/thing").respond(401, json={"detail": "nope"})
        for cfg, expected in (
            (key_cfg, "the API key in $KICODE_API_KEY"),
            (profile_cfg, "Run `kizen init --env testenv`"),
        ):
            monkeypatch.delenv("KICODE_API_KEY", raising=False)
            if cfg is profile_cfg:
                monkeypatch.setenv("KICODE_API_KEY", KEY)
            with (
                KizenClient(cfg) as client,
                pytest.raises(KizenAPIError) as exc,
            ):
                client.get("/api/thing")
            assert expected in str(exc.value)


# --- failure modes ----------------------------------------------------------


_COMPANIONS = ("KIZEN_BASE_URL", "KIZEN_BUSINESS_ID", "KIZEN_USER_ID")


@pytest.mark.parametrize(
    ("var", "value"),
    [(var, value) for var in _COMPANIONS for value in (None, "", "   ")]
    + [("KICODE_API_KEY", ""), ("KICODE_API_KEY", "   ")],
)
def test_api_key_mode_missing_vars_lists_names(key_env, monkeypatch, var, value):
    """Unset or blank companions, and a blank key (unset turns the mode off)."""
    if value is None:
        monkeypatch.delenv(var)
    else:
        monkeypatch.setenv(var, value)
    message = _error()
    assert message == (
        f"$KICODE_API_KEY is set, but {var} is missing or empty. "
        "Running under kicode needs all of KICODE_API_KEY, "
        "KIZEN_BASE_URL, KIZEN_BUSINESS_ID, KIZEN_USER_ID."
    )


def test_api_key_mode_missing_vars_lists_every_name(monkeypatch):
    monkeypatch.setenv("KICODE_API_KEY", KEY)
    message = _error()
    assert "KIZEN_BASE_URL, KIZEN_BUSINESS_ID, KIZEN_USER_ID are missing" in message


def test_api_key_and_helper_both_set_refused(key_env, monkeypatch):
    monkeypatch.setenv("KIZEN_AUTH_HELPER", "helper")
    assert _error() == (
        "$KIZEN_AUTH_HELPER and $KICODE_API_KEY are both set, so there are two "
        "ways to authenticate. Use one or the other."
    )


def test_api_key_and_helper_both_set_refused_even_when_incomplete(monkeypatch):
    """Neither mode wins, even before its own variables are checked."""
    monkeypatch.setenv("KIZEN_AUTH_HELPER", "helper")
    monkeypatch.setenv("KICODE_API_KEY", KEY)
    assert "are both set" in _error()


@pytest.mark.parametrize(
    ("selector", "label"),
    [
        ("env_name", "an explicit environment name"),
        ("--profile", "--profile"),
        ("KIZEN_PROFILE", "$KIZEN_PROFILE"),
    ],
)
def test_api_key_mode_refuses_profile_selectors(key_env, monkeypatch, selector, label):
    env_name = None
    if selector == "env_name":
        env_name = "testenv"
    elif selector == "--profile":
        config.set_profile_override("testenv")
    else:
        monkeypatch.setenv("KIZEN_PROFILE", "testenv")
    assert _error(env_name) == (
        "$KICODE_API_KEY is set, so kicode supplies the environment, "
        f"but {label} also selects a profile. Use one or the other."
    )


def test_api_key_mode_profile_flag_via_cli_refused(key_env):
    result = runner.invoke(app, ["--profile", "testenv", "objects", "list"])
    assert result.exit_code == 1
    assert "Use one or the other" in result.output
    assert KEY not in result.output


def test_api_key_mode_pin_mismatch_refused(key_env, monkeypatch):
    _pin(monkeypatch, OTHER_BUSINESS_ID)
    assert _error() == (
        f"Directory pinned to profile 'alpha' (business_id {OTHER_BUSINESS_ID}), "
        f"but KIZEN_BUSINESS_ID is {FAKE_BUSINESS_ID}. Refusing.\n"
        "To retarget this directory, edit /x/p."
    )


def test_api_key_mode_pin_match_passes(key_env, monkeypatch):
    _pin(monkeypatch, FAKE_BUSINESS_ID)
    cfg = load_env_config()
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.auth == KEY_AUTH


@pytest.mark.parametrize(
    "url",
    [
        "http://keymode.kizen.test",
        "https://keymode.kizen.test/api",
        "keymode.kizen.test",
        "https://keymode.kizen.test?x=1",
    ],
)
def test_api_key_mode_base_url_validated(key_env, monkeypatch, url):
    monkeypatch.setenv("KIZEN_BASE_URL", url)
    with pytest.raises(ConfigError, match="https origin"):
        load_env_config()


def test_api_key_mode_base_url_trailing_slash_normalized(key_env, monkeypatch):
    monkeypatch.setenv("KIZEN_BASE_URL", BASE + "/")
    assert load_env_config().base_url == BASE


def test_profile_mode_unchanged_without_api_key(monkeypatch):
    """Without KICODE_API_KEY, the identity variables (also `kizen init`'s
    envvars) don't change profile resolution or its key."""
    monkeypatch.setenv("KIZEN_BASE_URL", BASE)
    monkeypatch.setenv("KIZEN_BUSINESS_ID", OTHER_BUSINESS_ID)
    monkeypatch.setenv("KIZEN_USER_ID", OTHER_USER_ID)
    cfg = load_env_config()
    assert cfg.name == "testenv"
    assert cfg.business_id == FAKE_BUSINESS_ID
    assert cfg.user_id == FAKE_USER_ID
    assert cfg.auth == ApiKeyAuth(STORE_KEY)
    assert config.wrapper() is None


# --- commands that manage what the wrapper owns -----------------------------

_OWNER = "kicode"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["upgrade"],
            f"error: `kizen upgrade` is unavailable here: {_OWNER} manages "
            "this installation.",
        ),
        (
            ["init", "--profile", "x", "--skip-validation"],
            f"error: `kizen init` is unavailable here: {_OWNER} manages "
            "this environment's credentials.",
        ),
        (
            ["envs", "list"],
            f"error: `kizen envs list` is unavailable here: {_OWNER} manages "
            "the environment.",
        ),
    ],
)
def test_store_and_install_commands_refuse_in_api_key_mode(
    key_env, argv, message, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    before = profiles.credentials_path().read_bytes()
    result = runner.invoke(app, argv, terminal_width=200)
    assert result.exit_code == 1
    assert " ".join(result.output.split()) == message
    assert profiles.credentials_path().read_bytes() == before
    assert not (tmp_path / ".kizen").exists()
    assert KEY not in result.output


def test_upgrade_check_in_api_key_mode_notes_managed_and_exits_0(key_env, monkeypatch):
    from kizen_builder import upgrade as upgrade_mod

    def explode(*args, **kwargs):
        raise AssertionError("--check must not look anything up in API-key mode")

    monkeypatch.setattr(upgrade_mod, "detect_install", explode)
    monkeypatch.setattr(upgrade_mod, "check_latest", explode)
    monkeypatch.setattr(upgrade_mod, "run_steps", explode)
    monkeypatch.setattr("subprocess.run", explode)
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(500)
        result = runner.invoke(app, ["upgrade", "--check"], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"this installation is managed by {_OWNER}."
    assert any_http.call_count == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["upgrade"],
        ["upgrade", "--check"],
        ["init", "--profile", "x", "--skip-validation"],
        ["envs", "list"],
        ["objects", "list"],
    ],
)
def test_both_wrappers_set_refused_by_every_command(
    key_env, argv, tmp_path, monkeypatch
):
    """Neither wrapper's message wins; `upgrade --check` doesn't call the
    installation managed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KIZEN_AUTH_HELPER", "helper")
    result = runner.invoke(app, argv, terminal_width=200)
    assert result.exit_code == 1, result.output
    assert " ".join(result.output.split()) == (
        "error: $KIZEN_AUTH_HELPER and $KICODE_API_KEY are both set, so "
        "there are two ways to authenticate. Use one or the other."
    )
    assert KEY not in result.output


def test_wrapper_names_the_mode(key_env, monkeypatch):
    assert config.wrapper() == _OWNER
    monkeypatch.delenv("KICODE_API_KEY")
    monkeypatch.setenv("KIZEN_AUTH_HELPER", "helper")
    assert config.wrapper() == "app-builder"

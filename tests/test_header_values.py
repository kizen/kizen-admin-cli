"""A profile's key and ids are sent as headers, and httpx's error for an
illegal header value quotes the value. Profile mode and `kizen init` refuse
such a value up front, naming the field and never the value.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from kizen_builder import config, profiles
from kizen_builder.cli import app
from tests.conftest import FAKE_BASE_URL, FAKE_BUSINESS_ID, FAKE_USER_ID

runner = CliRunner()

# Each pair is the store's TOML escape and a label; "BADKEY" and "Z9QX" flank it.
SEPARATORS = [
    ("\\n", "lf"),
    ("\\r", "cr"),
    ("\\u0000", "nul"),
    (" ", "space"),
    ("\\u00e9", "non-ascii"),
]
_FIELDS = {
    "api_key": "test-api-key",
    "business_id": FAKE_BUSINESS_ID,
    "user_id": FAKE_USER_ID,
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    config.set_profile_override(None)
    monkeypatch.delenv("KIZEN_DEBUG", raising=False)
    yield
    config.set_profile_override(None)


def _store_testenv(**override: str) -> None:
    """Rewrite the conftest store by hand, so a field can hold a TOML escape
    that decodes to a character outside visible ASCII."""
    row = {**_FIELDS, "base_url": FAKE_BASE_URL}
    fields = {k: json.dumps(v) for k, v in row.items()}
    fields.update({k: f'"{v}"' for k, v in override.items()})
    body = "".join(f"{k} = {v}\n" for k, v in fields.items())
    profiles.credentials_path().write_text(f"[profiles.testenv]\n{body}")


def _run_objects_list():
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(json={"results": [], "next": None})
        result = runner.invoke(app, ["objects", "list"], terminal_width=300)
    return result, any_http


@pytest.mark.parametrize(("sep", "label"), SEPARATORS, ids=[s[1] for s in SEPARATORS])
def test_profile_malformed_key_never_echoed(sep, label):
    _store_testenv(api_key=f"BADKEY{sep}Z9QX")
    stored = profiles.get_profile("testenv")
    assert stored is not None
    assert not config.header_value_ok(stored.api_key)  # the escape decoded

    result, any_http = _run_objects_list()
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert flat.startswith(f"error: Profile 'testenv' in {profiles.credentials_path()}")
    assert flat.endswith(
        "has an invalid api_key: it may contain only visible ASCII characters "
        "(no spaces, tabs, line breaks or control characters). "
        "Run `kizen init --profile testenv` to re-enter it."
    )
    for text in (result.output, repr(result.exception)):
        assert "BADKEY" not in text
        assert "Z9QX" not in text
    assert any_http.call_count == 0


@pytest.mark.parametrize("field", ["business_id", "user_id"])
@pytest.mark.parametrize(("sep", "label"), SEPARATORS, ids=[s[1] for s in SEPARATORS])
def test_profile_malformed_identity_never_echoed(field, sep, label):
    _store_testenv(**{field: f"BADID{sep}Z9QX"})
    result, any_http = _run_objects_list()
    assert result.exit_code == 1
    assert f"has an invalid {field}: it may contain only visible ASCII" in (
        " ".join(result.output.split())
    )
    for text in (result.output, repr(result.exception)):
        assert "BADID" not in text
        assert "Z9QX" not in text
    assert any_http.call_count == 0


def test_profile_malformed_fields_named_together():
    _store_testenv(api_key="BAD KEY", user_id="bad id")
    result, _ = _run_objects_list()
    assert "has an invalid api_key, user_id: each may contain only" in " ".join(
        result.output.split()
    )
    assert "re-enter them." in result.output


@pytest.mark.parametrize(
    ("source", "flag"),
    [
        ("flag", "--api-key"),
        ("env", "--api-key"),
        # Rich's password prompt can't hide input under CliRunner; harmless.
        pytest.param(
            "prompt",
            "--api-key",
            marks=pytest.mark.filterwarnings("ignore::getpass.GetPassWarning"),
        ),
        ("flag", "--business-id"),
        ("flag", "--user-id"),
    ],
)
def test_init_rejects_malformed_key_before_sending(source, flag, tmp_path, monkeypatch):
    """Rejected before the live validation call and before the store or the
    pin is written, whichever way the value arrived."""
    monkeypatch.chdir(tmp_path)
    bad = "BADKEY Z9QX" if source == "prompt" else "BADKEY\nZ9QX"
    values = {
        "--api-key": "GOOD-KEY",
        "--business-id": FAKE_BUSINESS_ID,
        "--user-id": FAKE_USER_ID,
    }
    argv = ["init", "--profile", "fresh", "--base-url", "https://init.kizen.test"]
    stdin = None
    if source == "env":
        monkeypatch.setenv("KIZEN_API_KEY", bad)
        del values[flag]
    elif source == "prompt":
        del values[flag]
        stdin = bad + "\n"
    else:
        values[flag] = bad
    for name, value in values.items():
        argv += [name, value]

    before = profiles.credentials_path().read_bytes()
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(json={"results": []})
        result = runner.invoke(app, argv, input=stdin, terminal_width=300)
    assert result.exit_code == 2, result.output
    flat = " ".join(result.output.replace("│", " ").split())
    assert flag in flat
    assert (
        f"Invalid value for {flag}: it may contain only visible ASCII characters "
        "(no spaces, tabs, line breaks or control characters)." in flat
    )
    for text in (result.output, repr(result.exception)):
        assert "BADKEY" not in text
        assert "Z9QX" not in text
    assert any_http.call_count == 0
    assert profiles.credentials_path().read_bytes() == before
    assert profiles.get_profile("fresh") is None
    assert not (tmp_path / ".kizen").exists()


@pytest.mark.filterwarnings("ignore::getpass.GetPassWarning")
def test_init_empty_key_is_required_not_invalid(tmp_path, monkeypatch):
    """A bare Enter at the key prompt (a new profile has no default) is a
    missing value, not a malformed one, and nothing is sent or stored."""
    monkeypatch.chdir(tmp_path)
    before = profiles.credentials_path().read_bytes()
    with respx.mock(assert_all_called=False) as router:
        any_http = router.route().respond(json={"results": []})
        result = runner.invoke(
            app,
            [
                "init",
                "--profile",
                "fresh",
                "--business-id",
                FAKE_BUSINESS_ID,
                "--user-id",
                FAKE_USER_ID,
                "--base-url",
                "https://init.kizen.test",
            ],
            input="\n",
            terminal_width=300,
        )
    assert result.exit_code == 2, result.output
    flat = " ".join(result.output.replace("│", " ").split())
    assert "--api-key" in flat
    assert "is required." in flat
    assert "it may contain only" not in flat
    assert any_http.call_count == 0
    assert profiles.credentials_path().read_bytes() == before


def test_init_accepts_a_wellformed_key(tmp_path, monkeypatch):
    """The check doesn't reject an ordinary key, a signed one included."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "init",
            "--profile",
            "fresh",
            "--api-key",
            "sig:HS256:abc.def-ghi_jkl",
            "--business-id",
            FAKE_BUSINESS_ID,
            "--user-id",
            FAKE_USER_ID,
            "--base-url",
            "https://init.kizen.test",
            "--skip-validation",
        ],
    )
    assert result.exit_code == 0, result.output
    stored = profiles.get_profile("fresh")
    assert stored is not None
    assert stored.api_key == "sig:HS256:abc.def-ghi_jkl"

"""Profile resolution: precedence ladder, business_id checksum, missing profile.

These exercise the anti-drift guarantees in ``config.load_env_config`` and the
IO in ``profiles``. The autouse ``fake_env`` fixture points XDG_CONFIG_HOME at
an empty temp dir and disables the directory pin, so each test builds exactly
the state it needs.
"""

from __future__ import annotations

import os
import stat

import pytest
import tomli_w

from kizen_builder import config, profiles
from kizen_builder.config import ConfigError, load_env_config

# Captured before the autouse fixture patches it, so pin-IO tests can restore
# the real directory walk.
_REAL_FIND_PIN = profiles.find_pin


@pytest.fixture(autouse=True)
def _reset_override():
    """Keep the module-global CLI override from leaking between tests."""
    config.set_profile_override(None)
    yield
    config.set_profile_override(None)


def _store(name: str, business_id: str) -> None:
    profiles.write_profile(
        profiles.ProfileCreds(
            name=name,
            api_key=f"key-{name}",
            business_id=business_id,
            user_id=f"user-{name}",
            base_url="https://app.go.kizen.com",
        )
    )


def _pin(monkeypatch, profile: str, business_id: str | None) -> None:
    pin = profiles.Pin(
        profile=profile, business_id=business_id, path="/x/.kizen/profile"
    )
    monkeypatch.setattr(profiles, "load_pin", lambda start=None: pin)


# --- credential store IO ----------------------------------------------------


def test_write_then_read_profile_roundtrips():
    _store("alpha", "AAAA")
    got = profiles.get_profile("alpha")
    assert got is not None
    assert got.business_id == "AAAA"
    assert got.api_key == "key-alpha"


def test_write_profile_is_upsert_not_clobber():
    # fake_env has already seeded a "testenv" profile; writing alpha and beta
    # must add to the store, not replace what's there.
    _store("alpha", "AAAA")
    _store("beta", "BBBB")
    names = [p.name for p in profiles.list_profiles()]
    assert names.count("alpha") == 1
    assert names.count("beta") == 1
    assert "testenv" in names


@pytest.fixture
def own_store(tmp_path, monkeypatch):
    """A fresh store under this test's own temp XDG_CONFIG_HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = profiles.credentials_path()
    assert path.is_relative_to(tmp_path)
    return path


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_profile_creates_the_store_at_0600(own_store):
    _store("alpha", "AAAA")
    assert _mode(own_store) == 0o600


def test_write_profile_tightens_an_existing_0644_store(own_store):
    _store("alpha", "AAAA")
    own_store.chmod(0o644)
    _store("beta", "BBBB")
    assert _mode(own_store) == 0o600
    assert {p.name for p in profiles.list_profiles()} == {"alpha", "beta"}


def test_write_profile_serializes_into_a_file_already_0600(own_store, monkeypatch):
    real_dump = tomli_w.dump
    modes = []

    def checking_dump(data, fh):
        modes.append(stat.S_IMODE(os.fstat(fh.fileno()).st_mode))
        real_dump(data, fh)

    monkeypatch.setattr(tomli_w, "dump", checking_dump)
    _store("alpha", "AAAA")
    assert modes == [0o600]


def test_failed_write_leaves_the_store_untouched(own_store, monkeypatch):
    _store("alpha", "AAAA")
    before = own_store.read_bytes()

    def boom(data, fh):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tomli_w, "dump", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        _store("beta", "BBBB")

    assert own_store.read_bytes() == before
    assert list(own_store.parent.glob(".credentials.*.tmp")) == []


def test_write_profile_through_a_symlink_keeps_the_link(own_store, tmp_path):
    real = tmp_path / "elsewhere" / "credentials.toml"
    real.parent.mkdir()
    own_store.parent.mkdir(parents=True)
    own_store.symlink_to(real)

    _store("alpha", "AAAA")

    assert own_store.is_symlink()
    assert _mode(real) == 0o600
    assert profiles.get_profile("alpha") is not None


def test_write_pin_then_load_pin_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "find_pin", _REAL_FIND_PIN)  # real walk for this test
    path = profiles.write_pin("alpha", "AAAA", tmp_path)
    loaded = profiles.load_pin(tmp_path)
    assert loaded is not None
    assert loaded.profile == "alpha"
    assert loaded.business_id == "AAAA"
    assert loaded.path == path


# --- resolution precedence --------------------------------------------------


def test_kizen_env_selects_profile(monkeypatch):
    _store("alpha", "AAAA")
    monkeypatch.setenv("KIZEN_ENV", "alpha")
    assert load_env_config().business_id == "AAAA"


def test_kizen_profile_beats_kizen_env(monkeypatch):
    _store("alpha", "AAAA")
    _store("beta", "BBBB")
    monkeypatch.setenv("KIZEN_ENV", "alpha")
    monkeypatch.setenv("KIZEN_PROFILE", "beta")
    assert load_env_config().name == "beta"


def test_cli_override_beats_kizen_profile(monkeypatch):
    _store("alpha", "AAAA")
    _store("beta", "BBBB")
    monkeypatch.setenv("KIZEN_PROFILE", "beta")
    config.set_profile_override("alpha")
    assert load_env_config().name == "alpha"


def test_pin_beats_kizen_env(monkeypatch):
    _store("alpha", "AAAA")
    _store("beta", "BBBB")
    monkeypatch.setenv("KIZEN_ENV", "alpha")
    _pin(monkeypatch, "beta", "BBBB")
    assert load_env_config().name == "beta"


# --- hard-pin checksum ------------------------------------------------------


def test_checksum_refuses_pin_profile_business_id_mismatch(monkeypatch):
    _store("alpha", "AAAA")
    _pin(monkeypatch, "alpha", "ZZZZ")  # pin claims a different identity
    with pytest.raises(ConfigError, match="Refusing"):
        load_env_config()


def test_checksum_refuses_override_into_pinned_directory(monkeypatch):
    # The agent-drift case: directory pinned to alpha, command forces beta.
    _store("alpha", "AAAA")
    _store("beta", "BBBB")
    _pin(monkeypatch, "alpha", "AAAA")
    config.set_profile_override("beta")
    with pytest.raises(ConfigError, match="Refusing"):
        load_env_config()


def test_checksum_passes_when_identity_matches(monkeypatch):
    _store("alpha", "AAAA")
    _pin(monkeypatch, "alpha", "AAAA")
    assert load_env_config().business_id == "AAAA"


# --- missing profile ---------------------------------------------------------


def test_unknown_profile_raises(monkeypatch):
    # KIZEN_ENV names a profile that was never stored.
    monkeypatch.setenv("KIZEN_ENV", "does-not-exist")
    with pytest.raises(ConfigError, match="No profile named 'does-not-exist'"):
        load_env_config()


def test_no_env_at_all_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("KIZEN_ENV", raising=False)
    monkeypatch.delenv("KIZEN_PROFILE", raising=False)
    # Explicit path bypasses find_dotenv so the repo's own .env can't leak in.
    with pytest.raises(ConfigError, match="No environment specified"):
        load_env_config(dotenv_path=tmp_path / "nonexistent.env")


# Docs are no longer materialized into env folders — they ship inside the
# package and are served by `kizen docs show`. See tests/test_docs.py.

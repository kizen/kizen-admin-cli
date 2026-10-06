"""Shared fixtures for the kizen-builder test suite.

Fixture data under tests/fixtures/ is real API output captured via the CLI
from test environments, sanitized (people, env labels, external names
replaced). Object files are `kizen objects get --json` output; automation
`.raw.json` files are `kizen automations get --raw` output (the unmodified
API response).

No test may hit the network: credentials come from a fake profile in an
isolated credential store (BASE_URL points at https://kizen.test, which respx
intercepts where needed), and the live-lookup seams (`get_object`,
`list_automations`, LiveContext field lookups) are monkeypatched to serve from
fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

FAKE_BUSINESS_ID = "00000000-0000-4000-8000-00000000b1d0"
FAKE_USER_ID = "00000000-0000-4000-8000-0000000005e7"
FAKE_BASE_URL = "https://kizen.test"


def load_fixture(rel_path: str) -> Any:
    """Load a JSON fixture by path relative to tests/fixtures/."""
    return json.loads((FIXTURES / rel_path).read_text())


@pytest.fixture(autouse=True)
def fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Isolate every config lookup from the developer's real machine state.

    XDG_CONFIG_HOME is redirected to an empty temp dir so no test reads or
    writes the developer's real central credential store, then a "testenv"
    profile is written into that (fake) store so any code path that calls
    `load_env_config()` — directly, or transitively through a CLI/planner
    function — resolves real, non-network credentials (BASE_URL points at
    https://kizen.test, which respx intercepts where needed). Tests that need
    to see a genuinely empty or differently-populated store, such as the IO
    tests in test_config_profiles.py, repoint XDG_CONFIG_HOME at their own temp
    dir. XDG_CACHE_HOME is redirected for the same reason as XDG_CONFIG_HOME:
    the upgrade check caches there, and a test must never read or overwrite the
    developer's own. KIZEN_ENV names the profile most tests resolve to;
    resolution-order tests are free to override it.
    """
    from kizen_builder import profiles

    monkeypatch.setenv("KIZEN_ENV", "testenv")
    monkeypatch.delenv("KIZEN_PROFILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))
    profiles.write_profile(
        profiles.ProfileCreds(
            name="testenv",
            api_key="test-api-key",
            business_id=FAKE_BUSINESS_ID,
            user_id=FAKE_USER_ID,
            base_url=FAKE_BASE_URL,
        )
    )
    # No directory pin by default; pin-specific tests re-patch this.
    monkeypatch.setattr("kizen_builder.profiles.find_pin", lambda start=None: None)


@pytest.fixture
def env_config():
    from kizen_builder.config import load_env_config

    return load_env_config()


def fake_get_object(api_name: str) -> dict[str, Any]:
    """get_object() stand-in serving tests/fixtures/objects/<api_name>.json."""
    path = FIXTURES / "objects" / f"{api_name}.json"
    if not path.exists():
        raise LookupError(f"object '{api_name}' not found (no fixture)")
    obj = json.loads(path.read_text())
    obj.setdefault("raw", None)  # CLI --json output strips this key
    return obj


def fake_get_automation(api_name: str) -> dict[str, Any]:
    """get_automation() stand-in serving tests/fixtures/automations/*.raw.json.

    Fixture filenames don't match api_names, so scan for the record whose
    api_name matches. Only the keys plan_update_automation touches are
    surfaced (it reads the full response via "raw").
    """
    for path in (FIXTURES / "automations").glob("*.raw.json"):
        raw = json.loads(path.read_text())
        if raw.get("api_name") == api_name:
            return {
                "id": raw.get("id"),
                "api_name": api_name,
                "revision": raw.get("revision"),
                "raw": raw,
            }
    raise LookupError(f"no automation with api_name '{api_name}' (no fixture)")


def pytest_collection_modifyitems(config, items):
    """Filter tests marked `live` came from kznclient's --online mode; this
    repo has no online test path, so they always skip."""
    skip_live = pytest.mark.skip(reason="requires a live API (not supported here)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(name="kizen")
def filtering_with_stub():
    """Install the offline schema stub as the filtering default client.

    Named `kizen` for continuity with the kznclient test suite these filter
    tests were ported from; yields the filtering module.
    """
    from kizen_builder import filtering
    from tests.filter_stub import StubClient

    filtering.set_default_client(StubClient())
    yield filtering
    filtering.set_default_client(None)


@pytest.fixture
def patch_live_lookups(monkeypatch: pytest.MonkeyPatch):
    """Serve all live-state lookups used by the plan builders from fixtures."""
    import kizen_builder.tools.planners.automations as ma
    import kizen_builder.tools.planners.fields as mf
    import kizen_builder.tools.planners.objects as mo
    import kizen_builder.tools.planners.pipeline_stages as mp
    import kizen_builder.tools.planners.records as mr

    automations = load_fixture("automations/list.json")

    for mod in (ma, mf, mo, mp, mr):
        if hasattr(mod, "get_object"):
            monkeypatch.setattr(mod, "get_object", fake_get_object)
        if hasattr(mod, "list_automations"):
            monkeypatch.setattr(mod, "list_automations", lambda: automations)
        if hasattr(mod, "get_automation"):
            monkeypatch.setattr(mod, "get_automation", fake_get_automation)
        if hasattr(mod, "list_objects"):
            monkeypatch.setattr(
                mod, "list_objects", lambda: load_fixture("objects/list.json")
            )

    # LiveContext fetches options through the /fields endpoint; the object
    # fixtures already carry options inline, so serve those.
    monkeypatch.setattr(
        ma.LiveContext,
        "_fields_with_options",
        lambda self, object_api_name: fake_get_object(object_api_name)["fields"],
    )
    return fake_get_object


def plain(text: str) -> str:
    """Strip ANSI codes and collapse the wrapping a prompt gets at 80 columns."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """CliRunner's stdin is never a terminal; pretend it is so a y/N prompt runs."""
    from kizen_builder.cli import _mutations

    monkeypatch.setattr(_mutations, "_stdin_is_terminal", lambda: True)

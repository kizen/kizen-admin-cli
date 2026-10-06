"""Fixtures for the live drift suite.

Everything in ``tests/drift/`` talks to a real Kizen environment, which makes
it the exact opposite of the rest of this suite. Two consequences handled here:

1. **The autouse credential faker is switched off.** ``tests/conftest.py``
   installs ``fake_env`` for every test in the repo. Overriding the fixture by
   name in this directory turns it off for the drift tests only — the rest of
   the suite is untouched.
2. **Nothing runs without an explicit opt-in.** ``$KIZEN_DRIFT_PROFILE`` names
   the profile to target. Unset, every drift test skips with instructions
   rather than erroring.

The target environment is never named in this repo. Point it at a **disposable
business you are willing to see junk created and deleted in** — the round-trip
half writes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.drift.contracts import UPDATE_ENV_VAR

PROFILE_ENV_VAR = "KIZEN_DRIFT_PROFILE"

_SETUP_HELP = f"""\
The drift suite needs a live Kizen environment and none is configured.

    1. Pick or create a DISPOSABLE business — the round-trip tests create and
       delete real entities in it. Never point this at a customer environment.
    2. `kizen init --profile <name>` to store its credentials, then
       `kizen envs list` to confirm.
    3. Re-run with the profile named:

           {PROFILE_ENV_VAR}=<name> uv run pytest -m drift

"""


# ---------------------------------------------------------------------------
# Opt-in gate
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items) -> None:
    """Skip — never error — when no drift environment is configured.

    Runs *after* the repo-root hook of the same name; that one only touches
    `live`-marked tests, so the two don't interact. Keyed off the `drift`
    marker, not the directory: `tests/drift/test_contracts.py` exercises the
    extraction helpers with synthetic dicts and needs no environment, so it
    carries no `drift` marker and is untouched by this gate.
    """
    if os.environ.get(PROFILE_ENV_VAR):
        return
    skip = pytest.mark.skip(reason=_SETUP_HELP)
    for item in items:
        if item.get_closest_marker("drift") is not None:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def fake_env() -> None:
    """Override the repo-wide credential faker: drift tests need real ones."""
    return


@pytest.fixture(scope="session")
def drift_profile() -> str:
    name = os.environ.get(PROFILE_ENV_VAR)
    if not name:
        pytest.skip(_SETUP_HELP)
    return name


@pytest.fixture(scope="session")
def drift_config(drift_profile: str):
    """Resolve the drift profile through the CLI's own config machinery.

    Two deliberate moves:

    * ``$KIZEN_PROFILE`` is set for the process so the ~30 no-arg
      ``load_env_config()`` call sites inside the planners and ``apply_plan``
      resolve to the same environment as the client fixtures. Without this the
      planners would silently target whatever the cwd resolves to.
    * ``profiles.find_pin`` is neutralized. A ``.kizen/profile`` pin exists to
      stop a *directory* from acting on the wrong environment; here the
      environment is named explicitly and the identity is re-checked below, so
      the pin would only mean "the drift suite can't run from a pinned folder".
    """
    from kizen_builder import config as config_mod
    from kizen_builder import profiles
    from kizen_builder.config import ConfigError, load_env_config

    try:
        explicit = load_env_config(drift_profile)
    except ConfigError as exc:
        pytest.skip(f"profile {drift_profile!r} is not usable: {exc}\n\n{_SETUP_HELP}")

    real_find_pin = profiles.find_pin
    prev_profile = os.environ.get("KIZEN_PROFILE")
    profiles.find_pin = lambda start=None: None  # type: ignore[assignment]
    os.environ["KIZEN_PROFILE"] = drift_profile
    config_mod.set_profile_override(None)
    try:
        # Identity checksum: the ambient resolution the planners will use must
        # land on the same business as the profile we were told to target.
        ambient = load_env_config()
        assert ambient.business_id == explicit.business_id, (
            "ambient config resolution does not match "
            f"{PROFILE_ENV_VAR}={drift_profile!r}: "
            f"{ambient.business_id} != {explicit.business_id}. Refusing to write."
        )
        yield explicit
    finally:
        profiles.find_pin = real_find_pin  # type: ignore[assignment]
        if prev_profile is None:
            os.environ.pop("KIZEN_PROFILE", None)
        else:
            os.environ["KIZEN_PROFILE"] = prev_profile


@pytest.fixture(scope="session")
def drift_client(drift_config):
    """A client bound to the drift environment, open for the whole session."""
    from kizen_builder.api.client import KizenClient

    with KizenClient(drift_config, timeout=120.0) as client:
        yield client


@pytest.fixture(scope="session")
def openapi_schema(drift_client) -> dict[str, Any]:
    """``GET /api/docs/schema`` — fetched once per session (~1.5 MB)."""
    from kizen_builder.api.client import KizenAPIError

    try:
        schema = drift_client.get("/api/docs/schema")
    except KizenAPIError as exc:  # pragma: no cover - live-only path
        pytest.fail(
            f"GET /api/docs/schema failed ({exc}).\n"
            "That endpoint is the whole basis of the schema-diff half; if it has "
            "moved or been withdrawn, that is itself the finding — say so rather "
            "than working around it."
        )
    if not isinstance(schema, dict) or "paths" not in schema:
        pytest.fail(
            "GET /api/docs/schema did not return an OpenAPI document "
            f"(got {type(schema).__name__}). See the note above."
        )
    return schema


@pytest.fixture(scope="session")
def update_snapshot() -> bool:
    return os.environ.get(UPDATE_ENV_VAR, "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Teardown-guaranteed scratch registry
# ---------------------------------------------------------------------------

#: Prefix on every entity the drift suite creates. Anything in the target
#: environment carrying this is debris from an aborted run and safe to delete.
DEBRIS_PREFIX = "zz-drift-check"


def debris_name(what: str) -> str:
    """A name a human can identify at a glance as drift-test debris."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{DEBRIS_PREFIX} {what} {stamp}"


def debris_api_name(what: str) -> str:
    """api_name form of the same (lowercase, underscores)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{DEBRIS_PREFIX.replace('-', '_')}_{what}_{stamp}"


@dataclass
class Scratch:
    """Registry of live entities to destroy, whatever else happens.

    Every create in the round-trip half registers its deleter *immediately
    after* the POST returns, before any assertion runs. Teardown then runs in
    reverse creation order at session end, independent of test outcome — an
    assertion failure, an exception mid-fixture, or a ``KeyboardInterrupt``
    all still tear down. Deletion failures are collected and reported rather
    than swallowed, because a silent failure here means junk left in someone's
    environment.
    """

    _entries: list[tuple[str, str, Callable[[], Any]]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def track(self, kind: str, ident: str, delete: Callable[[], Any]) -> str:
        self._entries.append((kind, ident, delete))
        return ident

    def sweep(self) -> None:
        while self._entries:
            kind, ident, delete = self._entries.pop()
            try:
                delete()
            except Exception as exc:  # noqa: BLE001 - must not mask other deletes
                self.failures.append(f"{kind} {ident}: {exc!r}")


@pytest.fixture(scope="session")
def scratch(drift_client):
    """Session-wide create/destroy ledger. See :class:`Scratch`."""
    s = Scratch()
    try:
        yield s
    finally:
        s.sweep()
    if s.failures:
        pytest.fail(
            "drift-test cleanup FAILED — the following entities may still exist "
            "in the target environment and must be removed by hand:\n  "
            + "\n  ".join(s.failures)
        )


# ---------------------------------------------------------------------------
# Live entities shared by more than one test module
# ---------------------------------------------------------------------------
#
# These live here rather than in a test module because a fixture defined
# inside a test module is only visible to that module — and `drift_object`,
# `drift_related_object` and `drift_automation` are each used from several.


def create_field_on(
    drift_client, scratch: Scratch, obj: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """Create one field on a drift object via the real planner payload.

    ``obj`` is a fixture dict in the shape :func:`drift_object` returns
    (``uuid`` / ``api_name`` / ``categories``). The deleter is registered
    before anything else looks at the result, per :class:`Scratch`.
    """
    from kizen_builder.api import custom_objects as co_api
    from kizen_builder.tools.planners.fields import plan_create_field

    plan = plan_create_field(
        obj["api_name"], spec, category=obj["categories"][0]["name"]
    )
    payload = plan.operations[0].payload
    created = co_api.create_field(drift_client, obj["uuid"], payload)
    scratch.track(
        "field",
        created["id"],
        lambda: co_api.delete_field(drift_client, obj["uuid"], created["id"]),
    )
    return created


def _create_drift_object(drift_client, scratch: Scratch, what: str) -> dict[str, Any]:
    """Create one throwaway standard custom object from the planner payload."""
    from kizen_builder.api import custom_objects as co_api
    from kizen_builder.models.spec import ObjectDef
    from kizen_builder.tools.planners.objects import _build_object_payload

    spec = ObjectDef(
        name=debris_name(what),
        api_name=debris_api_name(what),
        object_type="standard",
        description="Created by the kizen-builder drift suite. Safe to delete.",
    )
    payload = _build_object_payload(spec)
    created = co_api.create_object(drift_client, payload)
    scratch.track(
        "custom object",
        created["id"],
        lambda: co_api.delete_object(drift_client, created["id"]),
    )
    live = co_api.get_object(drift_client, created["id"])
    return {
        "sent": payload,
        "created": created,
        "live": live,
        "uuid": created["id"],
        # Kizen derives the api_name server-side and returns it as `name`;
        # `object_name` carries the display name we sent.
        "api_name": live["name"],
        "categories": co_api.list_categories(drift_client, created["id"]),
    }


@pytest.fixture(scope="session")
def drift_object(drift_client, scratch, drift_config) -> dict[str, Any]:
    """A throwaway standard custom object, built by the real planner payload."""
    return _create_drift_object(drift_client, scratch, "object")


@pytest.fixture(scope="session")
def drift_related_object(drift_client, scratch, drift_object) -> dict[str, Any]:
    """A second object, related to :func:`drift_object` by a real relationship.

    The relationship field is created *on the related object*, pointing at
    ``drift_object``; Kizen mirrors it with an inverse field on
    ``drift_object`` (that is what ``relation.related_name`` names). The
    inverse is the one automation steps need: ``modify_related_entities``'s
    ``automation_target_relationship_fields`` and
    ``create_related_entity``'s ``context_entity_field`` are both hops
    *from* the automation's target_object, so they are returned here as
    ``hop_field`` rather than made every caller re-derive them.
    """
    from kizen_builder.api import custom_objects as co_api

    related = _create_drift_object(drift_client, scratch, "relobject")
    rel_field = create_field_on(
        drift_client,
        scratch,
        related,
        {
            "name": "Drift Parent Record",
            "api_name": debris_api_name("parent"),
            "field_type": "relationship",
            "relation": {
                "target_object": drift_object["api_name"],
                "relation_type": "many_to_one",
                "related_name": "Drift Child Records",
            },
        },
    )
    text_field = create_field_on(
        drift_client,
        scratch,
        related,
        {
            "name": "Drift Related Note",
            "api_name": debris_api_name("relnote"),
            "field_type": "text",
        },
    )
    # The mirrored field on drift_object — find it by target rather than by
    # name, since the server derives the api_name.
    parent_fields = co_api.list_fields(drift_client, drift_object["uuid"])
    hop = next(
        (
            f
            for f in parent_fields
            if (f.get("relation") or {}).get("related_object") == related["uuid"]
        ),
        None,
    )
    assert hop is not None, (
        "creating a relationship field on the related object did not mirror an "
        "inverse field back onto drift_object — every hop-based automation step "
        "(modify_related_entities, create_related_entity) depends on that mirror"
    )
    return {
        **related,
        "relationship_field": rel_field,
        "text_field": text_field,
        "hop_field": hop,
    }


@pytest.fixture(scope="session")
def drift_automation(drift_client, scratch, drift_object) -> dict[str, Any]:
    """A throwaway automation: condition step with a step on each branch.

    Same shape as ``BRANCHING_SPEC`` in ``tests/test_automation_payloads.py``,
    which asserts the *payload* offline; this asserts the API accepts it and
    round-trips the branch linkage. Asserted by
    ``test_roundtrip_automations.py``; also referenced by
    ``test_roundtrip_drift.py``'s cross-surface omissions test.
    """
    from kizen_builder.api import automations as auto_api
    from kizen_builder.models.spec import AutomationDef
    from kizen_builder.tools.planners.automations import (
        LiveContext,
        _build_automation_payload,
    )

    api_name = debris_api_name("auto")
    spec = AutomationDef.model_validate(
        {
            "api_name": api_name,
            "name": debris_name("automation"),
            "type": "record_based",
            "target_object": drift_object["api_name"],
            "active": False,
            "triggers": [
                {
                    "trigger_type": "new_entity_created",
                    "order": 0,
                    "trigger_new_entity_created": {},
                }
            ],
            "steps": [
                {
                    "key": "check",
                    "step_type": "condition",
                    "order": 0,
                    "parent_key": None,
                    "step_condition": {
                        "type": "custom_filter",
                        "filter_config": {
                            "all": [{"field": "name", "op": "not_blank"}]
                        },
                    },
                },
                {
                    "key": "stop_yes",
                    "step_type": "stop_execution",
                    "order": 1,
                    "parent_key": "check",
                    "parent_branch": "yes",
                },
                {
                    "key": "stop_no",
                    "step_type": "stop_execution",
                    "order": 2,
                    "parent_key": "check",
                    "parent_branch": "no",
                },
            ],
        }
    )
    payload = _build_automation_payload(spec, LiveContext())
    created = auto_api.create_automation(drift_client, payload)
    scratch.track(
        "automation",
        created["id"],
        lambda: auto_api.delete_automation(drift_client, created["id"]),
    )
    return {
        "sent": payload,
        "live": auto_api.get_automation(drift_client, created["id"]),
        "uuid": created["id"],
        "api_name": api_name,
    }

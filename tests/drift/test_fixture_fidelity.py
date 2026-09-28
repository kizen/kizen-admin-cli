"""Do the offline fixtures still look like what the live API returns?

The default (offline) suite fakes every live-state lookup by serving
pre-captured JSON from ``tests/fixtures/``. Most of those files are real
sanitized captures, but nothing in this repo has ever checked whether they
still match live — ``tests/fixtures/permissions/*.json`` in particular were
hand-authored until their 2026-09-25 live recapture (see
``tests/fixtures/README.md``).

This module is a **structural key-shape diff**, deliberately not a value
diff — UUIDs, timestamps, and generated names always differ between a fixture
and anything freshly created. For every key a fixture carries, we assert the
same path exists in a live read of the equivalent entity. We do *not* fail
when live carries additional keys the fixture omits: the fixtures are
intentionally trimmed captures (again, see the README), so live having more
is expected, not a regression.

This is the mirror image of ``tests/drift/contracts.py``'s philosophy, which
fails symmetrically in both directions over a hand-curated, complete omissions
list. These fixtures are deliberately partial captures rather than a complete
contract, so the asymmetric rule is the correct one here — don't copy the
bidirectional pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import load_fixture

# `drift_object` and `drift_automation` are conftest.py fixtures, auto-visible
# here. `drift_permission_group` still lives in test_roundtrip_drift.py.
from tests.drift.test_roundtrip_drift import drift_permission_group  # noqa: F401

pytestmark = pytest.mark.drift


# ---------------------------------------------------------------------------
# The one helper
# ---------------------------------------------------------------------------


def assert_keys_present(fixture: Any, live: Any, path: str = "") -> None:
    """Every key present in ``fixture`` must exist at the same path in ``live``.

    Recurses into nested dicts, and into the first element of a list of dicts
    (so e.g. one ``fields[0]`` entry's keys, one ``options[0]`` entry's keys
    get checked too). Collects every miss before failing, so one run reports
    everything stale rather than stopping at the first.

    A key ``live`` doesn't have at all is a real finding: the fixture is
    either stale or was never accurate. A key ``live`` has that the fixture
    doesn't mention is not checked at all — that's expected, not a problem.
    """
    missing = _missing_keys(fixture, live, path)
    assert not missing, "\n".join(f"{m} missing from live" for m in missing)


def _missing_keys(fixture: Any, live: Any, path: str) -> list[str]:
    if not isinstance(fixture, dict):
        return []
    if not isinstance(live, dict):
        # Fixture expected an object here; live has something else (or
        # nothing checkable) — every key underneath is unverifiable, which
        # for our purposes counts as missing.
        return [f"{path}.{k}" if path else k for k in fixture]

    out: list[str] = []
    for key, fval in fixture.items():
        key_path = f"{path}.{key}" if path else key
        if key not in live:
            out.append(key_path)
            continue
        lval = live[key]
        if isinstance(fval, dict):
            out += _missing_keys(fval, lval, key_path)
        elif (
            isinstance(fval, list)
            and fval
            and isinstance(fval[0], dict)
            and isinstance(lval, list)
            and lval
            and isinstance(lval[0], dict)
        ):
            out += _missing_keys(fval[0], lval[0], f"{key_path}[0]")
        # else: fixture's list is empty/not-a-list-of-dicts, or live's is —
        # nothing to recurse into. The key itself was present, which is enough.
    return out


# ---------------------------------------------------------------------------
# 1. permissions/permission_group_detail.json
# ---------------------------------------------------------------------------


def test_permission_group_detail_fixture_fidelity(drift_permission_group):  # noqa: F811
    """The highest-value check here: this fixture was hand-authored until it
    was recaptured live on 2026-09-25, and three of its original control keys
    were nowhere in live.

    Checks the container-level shape (always present regardless of the
    business's permission catalog) plus, for whichever `*_section` keys the
    fixture and a freshly built default group actually have in common, the
    inner shape — including the two wire dialects the fixture claims coexist:
    `homepages_section.customize_homepages` as a bare bool and
    `dashboards_section.customize_dashboards` as a `{view, edit, remove}` dict.

    A `*_section` key in the fixture but absent from live is *not* asserted as
    a failure — section availability is driven by the business's own
    permission catalog, so that is environment variance, not fixture
    staleness. Only the overlap is checked, per the task's design.
    """
    fixture = load_fixture("permissions/permission_group_detail.json")
    live = drift_permission_group["live"]

    # Collect every finding into one report instead of stopping at the first
    # — a single failure hiding the next one behind it would waste a
    # maintainer's time.
    problems: list[str] = []

    # Container-level keys every permission group carries, whatever the
    # business's catalog looks like.
    core = {
        k: fixture[k]
        for k in ("id", "name", "summary", "contacts_section", "custom_objects")
    }
    problems += [f"{m} missing from live" for m in _missing_keys(core, live, "")]

    fixture_sections = {
        k for k in fixture if k.endswith("_section") and k != "contacts_section"
    }
    live_sections = {
        k for k in live if k.endswith("_section") and k != "contacts_section"
    }
    overlap = fixture_sections & live_sections
    assert overlap, (
        "no `*_section` keys overlap between the fixture and a live default "
        f"permission group at all (fixture: {sorted(fixture_sections)}, live: "
        f"{sorted(live_sections)}) — the fixture may be entirely disconnected "
        "from the live shape"
    )
    for key in sorted(overlap):
        problems += [
            f"{m} missing from live"
            for m in _missing_keys({key: fixture[key]}, live, "")
        ]

    # The fixture's two headline claims about wire dialects. Only assertable
    # for whichever of the two sections actually overlaps live.
    if "homepages_section" in overlap:
        live_val = live["homepages_section"].get("customize_homepages")
        if not isinstance(live_val, bool):
            problems.append(
                "homepages_section.customize_homepages is no longer a bare "
                f"bool live (got {live_val!r}, {type(live_val).__name__}) — the "
                "fixture's bare-bool dialect claim for this key does not hold"
            )
    if "dashboards_section" in overlap:
        live_val = live["dashboards_section"].get("customize_dashboards")
        if not isinstance(live_val, dict):
            problems.append(
                "dashboards_section.customize_dashboards is no longer a "
                f"{{view, edit, remove}} dict live (got {live_val!r}, "
                f"{type(live_val).__name__}) — the fixture's dict-dialect claim "
                "for this key does not hold"
            )
        else:
            missing_dialect_keys = {"view", "edit", "remove"} - set(live_val)
            if missing_dialect_keys:
                problems.append(
                    "dashboards_section.customize_dashboards is missing "
                    f"{sorted(missing_dialect_keys)} from its "
                    "{view, edit, remove} dialect"
                )

    assert not problems, "fixture vs. live mismatches:\n" + "\n".join(
        f"  - {p}" for p in problems
    )


# ---------------------------------------------------------------------------
# 2. objects/client_client.json
# ---------------------------------------------------------------------------


def test_client_client_fixture_fidelity(drift_config):
    """`client_client` (Contacts) is Kizen's built-in object, present in every
    business — safe to read without creating anything."""
    from kizen_builder.tools.objects import get_object

    fixture = load_fixture("objects/client_client.json")
    live = get_object("client_client")

    assert_keys_present(fixture, live)

    fixture_fields = {f.get("api_name"): f for f in fixture.get("fields") or []}
    live_fields = {f.get("api_name"): f for f in live.get("fields") or []}
    shared = [
        n for n in ("first_name", "email") if n in fixture_fields and n in live_fields
    ]
    assert shared, (
        "neither `first_name` nor `email` is present in both the fixture and "
        "the live field list — can't do a named-field shape check"
    )
    for name in shared:
        assert_keys_present(
            fixture_fields[name], live_fields[name], path=f"fields[{name!r}]"
        )


# ---------------------------------------------------------------------------
# 3. automations/list.json
# ---------------------------------------------------------------------------


def test_automations_list_fixture_fidelity(drift_automation, drift_config):  # noqa: F811
    """Top-level per-automation entry keys. `drift_automation` guarantees at
    least one automation exists in the target business."""
    from kizen_builder.tools.automations import list_automations

    fixture = load_fixture("automations/list.json")
    assert fixture, "fixture automations/list.json is empty — nothing to diff"
    live = list_automations()
    assert live, (
        "live list_automations() returned nothing (expected at least drift_automation)"
    )

    assert_keys_present(fixture[0], live[0])


# ---------------------------------------------------------------------------
# 4. automations/condition_roundtrip.raw.json vs. drift_automation
# ---------------------------------------------------------------------------


def test_condition_automation_raw_fixture_fidelity(drift_automation):  # noqa: F811
    """`condition_roundtrip.raw.json`'s step composition (one `condition` step
    with a `stop_execution` on each branch) matches `drift_automation`'s
    shape most closely of the captured `.raw.json` fixtures."""
    fixture = load_fixture("automations/condition_roundtrip.raw.json")
    live = drift_automation["live"]

    # `steps` and `triggers` are compared below by type rather than by list
    # position: `drift_automation` always carries an extra auto-prepended
    # `manual` trigger the fixture's single-trigger automation doesn't, which
    # shifts list order and would make a blind `[0]` comparison meaningless.
    top_level = {k: v for k, v in fixture.items() if k not in ("steps", "triggers")}
    assert_keys_present(top_level, live)

    fixture_trigger = next(
        t for t in fixture["triggers"] if t.get("trigger_type") == "new_entity_created"
    )
    live_trigger = next(
        t for t in live["triggers"] if t.get("trigger_type") == "new_entity_created"
    )
    assert_keys_present(
        fixture_trigger, live_trigger, path="triggers[new_entity_created]"
    )

    fixture_condition = next(
        s for s in fixture["steps"] if s.get("step_type") == "condition"
    )
    live_condition = next(s for s in live["steps"] if s.get("step_type") == "condition")
    assert_keys_present(fixture_condition, live_condition, path="steps[condition]")

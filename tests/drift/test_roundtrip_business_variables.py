"""Live round-trip for business variables.

Kizen saves variables only as the business's whole list, so a wrong payload
silently deletes or rewrites the business's other variables. These tests run
the real planners and ``apply_plan``, then re-read the list to check that only
the planned variable changed and that ids survive an update.
"""

from __future__ import annotations

from typing import Any

import pytest

from kizen_builder.api import business as business_api
from kizen_builder.tools.planners.business_variables import (
    plan_delete_business_variable,
    plan_set_business_variable,
)
from kizen_builder.tools.plans import apply_plan
from tests.drift.conftest import debris_api_name, debris_name

pytestmark = pytest.mark.drift


def _live(client, config) -> list[dict[str, Any]]:
    return business_api.list_business_variables(client, config.business_id)


def _others(variables: list[dict[str, Any]], api_name: str) -> list[dict[str, Any]]:
    return sorted(
        (v for v in variables if v["api_name"] != api_name), key=lambda v: v["id"]
    )


def _track(client, config, scratch, api_name: str) -> None:
    """Register a deleter by api_name before the create, so a failed apply
    still cleans up. Removing a variable that isn't there is a no-op."""

    def delete() -> None:
        live = _live(client, config)
        if any(v["api_name"] == api_name for v in live):
            business_api.replace_business_variables(
                client,
                config.business_id,
                [v for v in live if v["api_name"] != api_name],
            )

    scratch.track("business variable", api_name, delete)


def test_set_and_delete_touch_only_that_variable(drift_client, drift_config, scratch):
    api_name = debris_api_name("bv")
    before = _live(drift_client, drift_config)
    _track(drift_client, drift_config, scratch, api_name)

    created = apply_plan(
        plan_set_business_variable(
            api_name, name=debris_name("bv"), value="https://example.com/a"
        )
    )
    assert created.all_ok, [r.message for r in created.results]
    after_create = _live(drift_client, drift_config)
    (var,) = [v for v in after_create if v["api_name"] == api_name]
    assert var["value_string"] == "https://example.com/a"
    assert _others(after_create, api_name) == _others(before, api_name)

    # An empty value saves, and an update keeps the variable's id.
    updated = apply_plan(plan_set_business_variable(api_name, value=""))
    assert updated.all_ok, [r.message for r in updated.results]
    after_update = _live(drift_client, drift_config)
    (same,) = [v for v in after_update if v["api_name"] == api_name]
    assert same["id"] == var["id"]
    assert same["value_string"] == ""
    assert _others(after_update, api_name) == _others(before, api_name)

    deleted = apply_plan(plan_delete_business_variable(api_name))
    assert deleted.all_ok, [r.message for r in deleted.results]
    after_delete = _live(drift_client, drift_config)
    assert not any(v["api_name"] == api_name for v in after_delete)
    assert _others(after_delete, api_name) == _others(before, api_name)


def test_apply_refuses_a_plan_made_stale_by_another_write(
    drift_client, drift_config, scratch
):
    target = debris_api_name("bv_target")
    added = debris_api_name("bv_added")
    _track(drift_client, drift_config, scratch, target)
    _track(drift_client, drift_config, scratch, added)

    plan = plan_set_business_variable(
        target, name=debris_name("bv target"), value="planned"
    )
    # Another write lands between planning and apply.
    live = _live(drift_client, drift_config)
    business_api.replace_business_variables(
        drift_client,
        drift_config.business_id,
        [
            *live,
            {"name": debris_name("bv added"), "api_name": added, "value_string": "x"},
        ],
    )

    (result,) = apply_plan(plan).results

    assert result.status == "failed"
    assert "changed since this plan was built" in (result.message or "")
    after = _live(drift_client, drift_config)
    assert any(v["api_name"] == added for v in after), (
        "the stale plan's PATCH ran and deleted the variable added after planning"
    )
    assert not any(v["api_name"] == target for v in after)

"""Live guard for `smart-connectors set-sql-params`.

The offline tests in ``tests/test_smart_connectors_tools.py`` pin the PATCH
body against mocked responses. This proves the real API stores it: it creates
a throwaway spreadsheet connector, sets parameters through the planner, and
re-reads the connector. It also confirms the server fact the merge relies on:
a PATCH of ``sql_parameters`` replaces the whole hash.
"""

from __future__ import annotations

import pytest

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.tools import smart_connectors as sct
from tests.drift.conftest import debris_name

pytestmark = pytest.mark.drift


def _set(connector: str, params: dict[str, str]) -> dict[str, str]:
    plan = sct.plan_set_sql_params(connector, params)
    assert plan["changed"], plan
    return sct.apply_set_sql_params(plan)["sql_parameters"]


def test_set_sql_params_stores_and_merges(drift_client, scratch, drift_object):
    plan = sct.plan_create_connector(
        name=debris_name("sql params"),
        custom_object=drift_object["uuid"],
        connector_type="spreadsheet",
    )
    created = sc_api.create_smart_connector(drift_client, plan["payload"])
    connector = created["id"]
    scratch.track(
        "smart connector",
        connector,
        lambda: drift_client.delete(f"/api/smart-connectors/{connector}"),
    )
    assert not created.get("sql_parameters"), created.get("sql_parameters")

    first = {"drift_param_a": "test value", "drift_param_b": "two"}
    assert _set(connector, first) == first

    # Only drift_param_a is passed; drift_param_b must survive the write.
    merged = {"drift_param_a": "changed", "drift_param_b": "two"}
    assert _set(connector, {"drift_param_a": "changed"}) == merged
    live = sc_api.get_smart_connector(drift_client, connector)["sql_parameters"]
    assert live == merged

    # Why the merge exists: a bare PATCH drops every name it doesn't carry.
    sc_api.update_smart_connector(
        drift_client, connector, {"sql_parameters": {"drift_param_c": "x"}}
    )
    live = sc_api.get_smart_connector(drift_client, connector)["sql_parameters"]
    assert live == {"drift_param_c": "x"}, (
        "PATCH merged sql_parameters instead of replacing them — the "
        "read-and-merge in plan_set_sql_params is no longer needed"
    )

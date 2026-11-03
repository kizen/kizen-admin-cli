"""`kizen dashboards dashlet-config --filter`: usage errors exit 2 before any
live call; a filter spec lands in the printed config."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools import dashlet_templates as tpl
from tests.filter_stub import POLICIES_OBJ_ID

runner = CliRunner()
MINE = '{"all": [{"field": "owner", "op": "is_me"}]}'


def _stderr(result) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stderr).strip()


def test_filter_without_object_exits_2():
    result = runner.invoke(
        cli.app,
        ["dashboards", "dashlet-config", "--type", "field_sum", "--filter", MINE],
    )
    assert result.exit_code == 2
    assert _stderr(result) == (
        "error: --filter names fields, so it needs --object to resolve them against"
    )


def test_filter_on_a_metric_type_exits_2():
    result = runner.invoke(
        cli.app,
        [
            "dashboards",
            "dashlet-config",
            "--type",
            "activity_metric",
            "--object",
            "call",
            "--filter",
            MINE,
        ],
    )
    assert result.exit_code == 2
    assert _stderr(result) == (
        "error: dashlet type 'activity_metric' has no record filter. --filter "
        "works on table_of_records, field_breakdown, field_sum, "
        "field_range_breakdown and pivot_table."
    )


def test_filter_and_filter_file_together_exit_2(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(MINE)
    result = runner.invoke(
        cli.app,
        [
            "dashboards",
            "dashlet-config",
            "--type",
            "field_sum",
            "--filter",
            MINE,
            "--filter-file",
            str(path),
        ],
    )
    assert result.exit_code == 2
    assert _stderr(result) == "error: pass --filter or --filter-file, not both."


@pytest.mark.parametrize(
    "filters",
    [
        {"field": "owner", "op": "is_me"},
        {"custom_filters": {"and": True, "query": []}, "in_group_ids": []},
        [],
    ],
)
def test_filter_that_is_neither_a_spec_nor_a_wire_block_exits_2(filters):
    result = runner.invoke(
        cli.app,
        [
            "dashboards",
            "dashlet-config",
            "--type",
            "field_sum",
            "--object",
            "policies",
            "--filter",
            json.dumps(filters),
        ],
    )
    assert result.exit_code == 2
    assert _stderr(result) == (
        'error: --filter must be a filter spec ({"all"|"any": [...]}) or a wire '
        "block with exactly one key: custom_filters, in_group_ids or "
        "not_in_group_ids"
    )


def test_a_wire_filter_block_passes_through(monkeypatch, kizen):
    obj = {"id": POLICIES_OBJ_ID, "api_name": "policies", "fields": []}
    monkeypatch.setattr(tpl, "get_object", lambda ref: obj)
    wire = {"in_group_ids": ["a0000000-0000-4000-8000-000000000001"]}
    result = runner.invoke(
        cli.app,
        [
            "dashboards",
            "dashlet-config",
            "--type",
            "field_sum",
            "--object",
            "policies",
            "--filter",
            json.dumps(wire),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["filters"] == wire


@pytest.mark.parametrize("via_file", [False, True])
def test_filter_spec_lands_in_config_filters(monkeypatch, kizen, tmp_path, via_file):
    obj = {"id": POLICIES_OBJ_ID, "api_name": "policies", "fields": []}
    monkeypatch.setattr(tpl, "get_object", lambda ref: obj)
    args = [
        "dashboards",
        "dashlet-config",
        "--type",
        "field_sum",
        "--object",
        "policies",
    ]
    if via_file:
        path = tmp_path / "f.json"
        path.write_text(MINE)
        args += ["--filter-file", str(path)]
    else:
        args += ["--filter", MINE]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    (clause,) = json.loads(result.stdout)["filters"]["custom_filters"]["query"][0][
        "filters"
    ]
    assert clause == {
        "type": "fields",
        "subtype": "non_custom",
        "field": "owner",
        "condition": "=",
        "value": "is_me",
    }


def test_without_filter_the_output_is_unchanged():
    result = runner.invoke(
        cli.app, ["dashboards", "dashlet-config", "--type", "field_sum"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == tpl.build_dashlet_config("field_sum")

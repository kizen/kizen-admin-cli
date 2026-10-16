"""`forms|surveys|activities|dashboards duplicate`, end to end at the wire.

Each `/duplicate` endpoint ignores its body (confirmed live 2026-10-06), so
`--name` (and a dashboard's sharing) must reach the copy by a PATCH after the
POST. These run the real command with `--yes` and pin every write it sends.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools.form_ui import build_form_ui, simple_form_page
from tests.conftest import FAKE_BASE_URL

runner = CliRunner()

SOURCE_ID = "00000000-0000-4000-8000-0000000007a1"
COPY_ID = "00000000-0000-4000-8000-0000000007c1"
ROLE_ID = "00000000-0000-4000-8000-0000000007d1"
FIELD = {"id": "00000000-0000-4000-8000-0000000007f1", "name": "email"}

READ_SHARING = {
    "private": False,
    "all_team_members": 1,
    "roles": {
        "view": [],
        "edit": [],
        "admin": [{"id": ROLE_ID, "display_name": "Admin"}],
    },
    "team_members": {"view": [], "edit": [], "admin": []},
}
WRITE_SHARING = {**READ_SHARING, "roles": {"view": [], "edit": [], "admin": [ROLE_ID]}}

# (argv prefix, collection path, extra flags, expected PATCH body)
CASES = [
    pytest.param(["forms"], "/api/forms", [], {"name": "X"}, id="form"),
    pytest.param(["surveys"], "/api/surveys", [], {"name": "X"}, id="survey"),
    pytest.param(["activities"], "/api/activities", [], {"name": "X"}, id="activity"),
    pytest.param(
        ["dashboards"],
        "/api/dashboards",
        ["--api-name", "x_board"],
        {"sharing_settings": WRITE_SHARING, "name": "X", "api_name": "x_board"},
        id="dashboard",
    ),
]


def _mock_source(mock: respx.MockRouter, base: str) -> None:
    # A form with its one field placed on a page, so a rename can't lose it.
    mock.get(f"{FAKE_BASE_URL}{base}/{SOURCE_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": SOURCE_ID,
                "name": "Source",
                "api_name": "source",
                "sharing_settings": READ_SHARING,
                "form_ui": build_form_ui([simple_form_page([FIELD])]),
            },
        )
    )
    mock.get(f"{FAKE_BASE_URL}{base}/{SOURCE_ID}/fields").mock(
        return_value=httpx.Response(200, json=[FIELD])
    )
    mock.get(f"{FAKE_BASE_URL}{base}/{COPY_ID}").mock(
        return_value=httpx.Response(
            200, json={"id": COPY_ID, "sharing_settings": READ_SHARING}
        )
    )
    mock.post(f"{FAKE_BASE_URL}{base}/{SOURCE_ID}/duplicate").mock(
        return_value=httpx.Response(
            200, json={"id": COPY_ID, "api_name": "source_copy_1"}
        )
    )
    mock.patch(f"{FAKE_BASE_URL}{base}/{COPY_ID}").mock(
        return_value=httpx.Response(200, json={"id": COPY_ID})
    )


def _writes(mock: respx.MockRouter) -> list[tuple[str, str, Any]]:
    return [
        (c.request.method, c.request.url.path, json.loads(c.request.content))
        for c in mock.calls
        if c.request.method != "GET"
    ]


@pytest.mark.parametrize("group, base, extra, patched", CASES)
def test_duplicate_with_name_posts_then_patches_the_copy(group, base, extra, patched):
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _mock_source(mock, base)
        result = runner.invoke(
            cli.app, [*group, "duplicate", SOURCE_ID, "--name", "X", *extra, "--yes"]
        )
        writes = _writes(mock)

    assert result.exit_code == 0, result.output
    assert writes == [
        ("POST", f"{base}/{SOURCE_ID}/duplicate", {}),
        ("PATCH", f"{base}/{COPY_ID}", patched),
    ]


@pytest.mark.parametrize(
    "group, base",
    [(["forms"], "/api/forms"), (["activities"], "/api/activities")],
)
def test_duplicate_without_name_posts_alone(group, base):
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _mock_source(mock, base)
        result = runner.invoke(cli.app, [*group, "duplicate", SOURCE_ID, "--yes"])
        writes = _writes(mock)

    assert result.exit_code == 0, result.output
    assert writes == [("POST", f"{base}/{SOURCE_ID}/duplicate", {})]


def test_dashboard_duplicate_without_name_still_patches_the_sharing():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _mock_source(mock, "/api/dashboards")
        result = runner.invoke(cli.app, ["dashboards", "duplicate", SOURCE_ID, "--yes"])
        writes = _writes(mock)

    assert result.exit_code == 0, result.output
    assert writes == [
        ("POST", f"/api/dashboards/{SOURCE_ID}/duplicate", {}),
        ("PATCH", f"/api/dashboards/{COPY_ID}", {"sharing_settings": WRITE_SHARING}),
    ]


def test_dashboard_duplicate_json_reports_the_sharing_read_back():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _mock_source(mock, "/api/dashboards")
        result = runner.invoke(
            cli.app, ["dashboards", "duplicate", SOURCE_ID, "--yes", "--json"]
        )

    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.stdout)["results"]
    assert row["raw"]["sharing_settings"] == READ_SHARING

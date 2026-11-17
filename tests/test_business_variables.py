"""Business variables: the whole-list payload each plan builds, and the apply
guard that refuses a plan whose other variables changed since planning.

The planners build their own `KizenClient`, mocked via `respx` against
`FAKE_BASE_URL`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from kizen_builder import cli
from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.planners.business_variables import (
    plan_delete_business_variable,
    plan_set_business_variable,
)
from kizen_builder.tools.plans import PlanError
from tests.conftest import FAKE_BASE_URL, FAKE_BUSINESS_ID, load_fixture

URL = f"{FAKE_BASE_URL}/api/business/{FAKE_BUSINESS_ID}"
LIVE = load_fixture("business/variables.json")
FIRM, FACEBOOK, SURVEY = LIVE


def _mock(variables=LIVE):
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, json={"id": FAKE_BUSINESS_ID, "business_variables": variables}
        )
    )
    # Planners never write: a call here fails the test.
    return respx.patch(URL).mock(return_value=httpx.Response(500))


@respx.mock
def test_set_existing_changes_only_that_value():
    write = _mock()

    (op,) = plan_set_business_variable(
        "ma_survey_link", value="https://example.com/survey"
    ).operations

    assert op.action == "update"
    assert op.existing_uuid == FAKE_BUSINESS_ID
    assert op.payload == {
        "business_variables": [
            FIRM,
            FACEBOOK,
            {**SURVEY, "value_string": "https://example.com/survey"},
        ]
    }
    assert not write.called


@respx.mock
def test_set_new_appends_it_without_an_id():
    _mock()

    (op,) = plan_set_business_variable(
        "intake_link", name="Intake Link", value="https://example.com/intake"
    ).operations

    assert op.action == "create"
    assert op.payload["business_variables"] == [
        *LIVE,
        {
            "name": "Intake Link",
            "api_name": "intake_link",
            "value_string": "https://example.com/intake",
        },
    ]


@respx.mock
def test_set_new_without_name_is_a_plan_error():
    _mock()

    with pytest.raises(PlanError, match="pass --name and --value"):
        plan_set_business_variable("intake_link", value="x")


@respx.mock
def test_set_same_value_is_a_skip():
    _mock()

    (op,) = plan_set_business_variable(
        "ma_survey_link", value="http://google.com"
    ).operations

    assert op.action == "skip"
    assert op.payload == {}


@respx.mock
def test_set_name_taken_by_another_variable_is_a_plan_error():
    _mock()

    with pytest.raises(PlanError, match="already named 'Facebook Review Link'"):
        plan_set_business_variable("ma_survey_link", name="Facebook Review Link")


@respx.mock
def test_delete_sends_the_list_without_it():
    _mock()

    (op,) = plan_delete_business_variable("facebook_review_link").operations

    assert op.action == "delete"
    assert op.payload == {"business_variables": [FIRM, SURVEY]}


@respx.mock
def test_delete_unknown_is_a_plan_error():
    _mock()

    with pytest.raises(PlanError, match="'nope' not found"):
        plan_delete_business_variable("nope")


@respx.mock
def test_apply_patches_and_reports_the_variable_id():
    _mock()
    plan = plan_set_business_variable("ma_survey_link", value="https://example.com/s")
    saved = plan.operations[0].payload["business_variables"]
    write = respx.patch(URL).mock(
        return_value=httpx.Response(
            200, json={"id": FAKE_BUSINESS_ID, "business_variables": saved}
        )
    )

    (result,) = plan_tools.apply_plan(plan).results

    assert result.status == "ok", result.message
    assert result.server_uuid == SURVEY["id"]
    assert json.loads(write.calls.last.request.content) == {"business_variables": saved}


@respx.mock
def test_apply_refuses_when_another_variable_changed_since_planning():
    _mock()
    plan = plan_set_business_variable("ma_survey_link", value="https://example.com/s")
    added = {
        "id": "00000000-0000-4000-8000-0000000000aa",
        "name": "Added Later",
        "api_name": "added_later",
        "value_string": "x",
    }
    write = _mock([*LIVE, added])

    (result,) = plan_tools.apply_plan(plan).results

    assert result.status == "failed"
    assert "changed since this plan was built" in (result.message or "")
    assert not write.called


def test_cli_set_needs_value_or_name():
    result = CliRunner().invoke(cli.app, ["business-variables", "set", "x"])

    assert result.exit_code == 2
    assert "--value and/or --name" in result.output

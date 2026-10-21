"""apply_plan dispatch for form/survey op kinds.

Forms and surveys share one api/tools/planner implementation parameterized
by base path; these tests lock in that both `kind="form"` and
`kind="survey"` op kinds route to the right URL (``/api/forms/...`` vs
``/api/surveys/...``) via the shared dispatch in ``tools/plans.py``.
"""

from __future__ import annotations

import httpx
import respx

from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.plans import Plan, PlanOperation
from tests.conftest import FAKE_BASE_URL


@respx.mock
def test_form_create_posts_to_forms_base_path():
    route = respx.post(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=httpx.Response(200, json={"id": "form-uuid"})
    )
    op = PlanOperation(
        action="create", kind="form", key="form:x", preview={}, payload={"name": "X"}
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok
    assert result.results[0].server_uuid == "form-uuid"


@respx.mock
def test_survey_create_posts_to_surveys_base_path():
    route = respx.post(f"{FAKE_BASE_URL}/api/surveys").mock(
        return_value=httpx.Response(200, json={"id": "survey-uuid"})
    )
    op = PlanOperation(
        action="create",
        kind="survey",
        key="survey:x",
        preview={},
        payload={"name": "X"},
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_form_update_patches_existing():
    route = respx.patch(f"{FAKE_BASE_URL}/api/forms/form-uuid").mock(
        return_value=httpx.Response(200, json={"id": "form-uuid"})
    )
    op = PlanOperation(
        action="update",
        kind="form",
        key="form:x",
        preview={},
        payload={"description": "new"},
        existing_uuid="form-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_form_delete():
    route = respx.delete(f"{FAKE_BASE_URL}/api/forms/form-uuid").mock(
        return_value=httpx.Response(204)
    )
    op = PlanOperation(
        action="delete",
        kind="form",
        key="form:x",
        preview={},
        existing_uuid="form-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_survey_duplicate_posts_to_duplicate_endpoint():
    route = respx.post(f"{FAKE_BASE_URL}/api/surveys/survey-uuid/duplicate").mock(
        return_value=httpx.Response(200, json={"id": "new-survey-uuid"})
    )
    op = PlanOperation(
        action="duplicate",
        kind="survey",
        key="survey:x:duplicate",
        preview={},
        existing_uuid="survey-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok
    assert result.results[0].server_uuid == "new-survey-uuid"


@respx.mock
def test_form_field_create_uses_parent_object_uuid():
    route = respx.post(f"{FAKE_BASE_URL}/api/forms/form-uuid/fields").mock(
        return_value=httpx.Response(200, json={"id": "field-uuid"})
    )
    op = PlanOperation(
        action="create",
        kind="form_field",
        key="form:x.field:summary",
        preview={},
        payload={"display_name": "Summary", "field_type": "text"},
        parent_object_uuid="form-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_survey_field_delete_uses_surveys_path():
    route = respx.delete(
        f"{FAKE_BASE_URL}/api/surveys/survey-uuid/fields/field-uuid"
    ).mock(return_value=httpx.Response(204))
    op = PlanOperation(
        action="delete",
        kind="survey_field",
        key="survey:x.field:summary",
        preview={},
        existing_uuid="field-uuid",
        parent_object_uuid="survey-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    assert result.all_ok


@respx.mock
def test_form_field_option_add():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/forms/form-uuid/fields/field-uuid/options"
    ).mock(return_value=httpx.Response(200, json={"id": "opt-uuid"}))
    op = PlanOperation(
        action="create",
        kind="form_field_option",
        key="form:x.field:outcome.option:A",
        preview={},
        payload={"field_id": "field-uuid", "name": "A"},
        parent_object_uuid="form-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert route.call_count == 1
    body = __import__("json").loads(route.calls[0].request.content)
    assert body == {"name": "A"}
    assert result.all_ok


@respx.mock
def test_survey_field_option_remove_with_remap_replaces_then_deletes():
    replace_route = respx.post(
        f"{FAKE_BASE_URL}/api/surveys/survey-uuid/fields/field-uuid/options/old-opt/replace"
    ).mock(return_value=httpx.Response(200, json={}))
    delete_route = respx.delete(
        f"{FAKE_BASE_URL}/api/surveys/survey-uuid/fields/field-uuid/options/old-opt"
    ).mock(return_value=httpx.Response(204))
    op = PlanOperation(
        action="delete",
        kind="survey_field_option",
        key="survey:x.field:outcome.option:A",
        preview={},
        payload={"field_id": "field-uuid", "remap_to": "new-opt"},
        existing_uuid="old-opt",
        parent_object_uuid="survey-uuid",
    )
    result = plan_tools.apply_plan(Plan.build(env="t", summary="t", operations=[op]))
    assert replace_route.call_count == 1
    assert delete_route.call_count == 1
    assert result.all_ok

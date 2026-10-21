"""`cli/activities.py`'s object resolution must resolve Contacts
(`client_client`), not just custom objects.

`_resolve_object_id` depends on `tools/objects.py::list_objects()` for its
candidate list. Before that function included built-ins, `client_client`
resolved to nothing and this raised `object 'client_client' not found` — a
functional break for `kizen activities list --object client_client` and for
associating an activity type with Contacts via `--object`. No test
covered this path before this change (confirmed: `test_activities_tool.py`,
`test_activity_plans.py`, and `test_cli.py` have no `client_client` coverage
for activities).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
import typer
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli.activities import _resolve_linked_field, _resolve_object_id
from tests.conftest import FAKE_BASE_URL

OBJ_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"
CONTACTS_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"

OBJECT_LIST_WITH_CONTACTS = {
    "results": [
        {
            "id": OBJ_ID,
            "name": "policies_policy",
            "object_name": "Policies",
            "is_custom": True,
        },
        {
            "id": CONTACTS_ID,
            "name": "client_client",
            "object_name": "Contacts",
            "is_custom": False,
        },
    ],
    "next": None,
}


@respx.mock
def test_resolve_object_id_resolves_client_client():
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    assert _resolve_object_id("client_client") == CONTACTS_ID
    assert _resolve_object_id("policies_policy") == OBJ_ID


@respx.mock
def test_resolve_object_id_still_raises_for_truly_unknown_object():
    """Unaffected behavior: an api_name absent from the (now custom+built-in)
    list still raises, listing what's actually available."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )

    with pytest.raises(typer.BadParameter, match="nope"):
        _resolve_object_id("nope")


@pytest.mark.parametrize("status", [401, 500])
def test_resolve_linked_field_does_not_report_api_errors_as_not_found(
    monkeypatch, status
):
    def get_object(_api_name):
        raise KizenAPIError(status, "boom")

    monkeypatch.setattr("kizen_builder.tools.objects.get_object", get_object)
    with pytest.raises(KizenAPIError) as info:
        _resolve_linked_field("policies_policy.total")
    assert info.value.status_code == status


def test_resolve_linked_field_reports_404_as_not_found(monkeypatch):
    def get_object(_api_name):
        raise KizenAPIError(404, "gone")

    monkeypatch.setattr("kizen_builder.tools.objects.get_object", get_object)
    with pytest.raises(typer.BadParameter, match="object 'policies_policy' not found"):
        _resolve_linked_field("policies_policy.total")


@pytest.mark.parametrize(
    ("argv", "planner", "expected"),
    [
        (
            ["activities", "create", "--name", "Visit"],
            "plan_create_activity",
            {"name": "Visit", "selected_object_ids": [CONTACTS_ID, OBJ_ID]},
        ),
        (
            ["activities", "update", "visit"],
            "plan_update_activity",
            {"selected_object_ids": [CONTACTS_ID, OBJ_ID]},
        ),
    ],
)
@respx.mock
def test_object_flag_resolves_to_selected_object_ids(
    monkeypatch, argv, planner, expected
):
    """`--object` hands the planner ids; the planner builds `associated_objects`."""
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )
    seen: list[dict] = []
    monkeypatch.setattr(
        f"kizen_builder.tools.planners.activities.{planner}",
        lambda *args: seen.append(args[-1]),
    )
    monkeypatch.setattr(
        "kizen_builder.cli.activities._run_mutation", lambda build, **_: build()
    )

    result = CliRunner().invoke(
        cli.app, [*argv, "--object", "client_client", "--object", OBJ_ID]
    )

    assert result.exit_code == 0, result.output
    assert seen == [expected]


@pytest.mark.parametrize("via", ["--object", "--spec-file"])
@respx.mock
def test_update_keeps_the_flags_of_objects_already_associated(
    monkeypatch, tmp_path, via
):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects").mock(
        return_value=httpx.Response(200, json=OBJECT_LIST_WITH_CONTACTS)
    )
    respx.get(f"{FAKE_BASE_URL}/api/activities/visit").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "00000000-0000-4000-8000-000000000a01",
                "name": "Visit",
                "api_name": "visit",
                "association_mode": "selected_objects_associated",
                "associated_objects": [
                    {
                        "custom_object": {"id": OBJ_ID, "name": "policies_policy"},
                        "is_required": True,
                        "is_readonly": False,
                        "order": 0,
                    },
                    {
                        "custom_object": {"id": CONTACTS_ID, "name": "client_client"},
                        "is_required": False,
                        "is_readonly": True,
                        "order": 1,
                    },
                ],
            },
        )
    )
    plans = []
    monkeypatch.setattr(
        "kizen_builder.cli.activities._run_mutation",
        lambda build, **_: plans.append(build()),
    )
    if via == "--object":
        args = ["--object", "policies_policy", "--object", "client_client"]
    else:
        spec = tmp_path / "changes.json"
        spec.write_text(json.dumps({"selected_object_ids": [OBJ_ID, CONTACTS_ID]}))
        args = ["--spec-file", str(spec)]

    result = CliRunner().invoke(cli.app, ["activities", "update", "visit", *args])

    assert result.exit_code == 0, result.output
    (op,) = plans[0].operations
    assert op.payload == {
        "associated_objects": [
            {"custom_object": {"id": OBJ_ID}, "order": 0},
            {"custom_object": {"id": CONTACTS_ID}, "order": 1},
        ]
    }
    labels = "policies_policy (required), client_client (read-only)"
    assert op.preview["diff"] == {"associated_objects": f"{labels} → {labels}"}

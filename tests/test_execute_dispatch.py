"""Every (kind, action) branch of `tools/plans.py:_execute`, pinned at the wire.

Each row applies a one-op plan through `apply_plan` against one catch-all
route and asserts the exact ordered (method, path, JSON body) list it sent.
The expected paths and bodies are written out from `api/*.py`, never computed
by calling the wrapper under test. Every id is a distinct UUID, so a branch that
swaps `parent_object_uuid` and `existing_uuid` (or `field_id` and `remap_to`)
fails its row.

Adding a `Kind` without a row here fails `test_every_kind_has_a_row`. A new
action branch on an existing kind does not: add its row by hand.
"""

from __future__ import annotations

import json
import typing
from typing import Any

import httpx
import pytest
import respx

from kizen_builder.tools import plans as plan_tools
from kizen_builder.tools.plans import Kind, Plan, PlanOperation
from tests.conftest import FAKE_BASE_URL

P = "00000000-0000-4000-8000-0000000000a1"  # parent_object_uuid
E = "00000000-0000-4000-8000-0000000000e1"  # existing_uuid
F = "00000000-0000-4000-8000-0000000000f1"  # payload["field_id"]
R = "00000000-0000-4000-8000-0000000000c1"  # payload["remap_to"]
N = "00000000-0000-4000-8000-0000000000d1"  # payload["new_stage_id"]
AUTOMATION_ID = "00000000-0000-4000-8000-0000000000b1"
TEMPLATE_ID = "00000000-0000-4000-8000-0000000000b2"
TARGET_IDS = [
    "00000000-0000-4000-8000-0000000000b3",
    "00000000-0000-4000-8000-0000000000b4",
]
ROLE_ADDED = "00000000-0000-4000-8000-0000000000b5"
ROLE_REMOVED = "00000000-0000-4000-8000-0000000000b6"
SERVER_ID = "00000000-0000-4000-8000-0000000000ff"

BODY = {"name": "Thing", "api_name": "thing"}
FIELDS = [{"name": "name", "value": "Acme"}]
TEMPLATE = {
    "id": TEMPLATE_ID,
    "name": "Welcome",
    "type": "email",
    "subject": "Hi",
    "content": "<p>Hi</p>",
    "craft_json": {"ROOT": {}},
    "html_content": "<p>Hi</p>",
}

Request = tuple[str, str, Any]


def _row(
    kind: str,
    action: str,
    op: dict[str, Any],
    expected: list[Request],
    label: str = "",
) -> Any:
    return pytest.param(
        kind, action, op, expected, id=f"{kind}-{action}{label and '-' + label}"
    )


def _crud(
    kind: str,
    collection: str,
    item: str,
    *,
    parent: bool,
    update_method: str = "PATCH",
) -> list[Any]:
    ids = {"parent_object_uuid": P} if parent else {}
    return [
        _row(kind, "create", {**ids, "payload": BODY}, [("POST", collection, BODY)]),
        _row(
            kind,
            "update",
            {**ids, "existing_uuid": E, "payload": BODY},
            [(update_method, item, BODY)],
        ),
        _row(kind, "delete", {**ids, "existing_uuid": E}, [("DELETE", item, None)]),
    ]


def _options(kind: str, field_base: str, replace_key: str | None) -> list[Any]:
    """Option create/delete/delete-with-remap under ``field_base/F/options``.

    ``replace_key`` None means the custom-object shape: the replace POST alone,
    body ``{"id": R}``. Otherwise the activity/form shape: replace with
    ``{replace_key: R}``, then DELETE (plans.py's comment says why).
    """
    options = f"{field_base}/{F}/options"
    if replace_key is None:
        remapped = [("POST", f"{options}/{E}/replace", {"id": R})]
    else:
        remapped = [
            ("POST", f"{options}/{E}/replace", {replace_key: R}),
            ("DELETE", f"{options}/{E}", None),
        ]
    return [
        _row(
            kind,
            "create",
            {"parent_object_uuid": P, "payload": {"field_id": F, "name": "Opt"}},
            [("POST", options, {"name": "Opt"})],
        ),
        _row(
            kind,
            "delete",
            {"parent_object_uuid": P, "existing_uuid": E, "payload": {"field_id": F}},
            [("DELETE", f"{options}/{E}", None)],
        ),
        _row(
            kind,
            "delete",
            {
                "parent_object_uuid": P,
                "existing_uuid": E,
                "payload": {"field_id": F, "remap_to": R},
            },
            remapped,
            label="remap_to",
        ),
    ]


def _duplicate(kind: str, base: str, *, read_back: bool = False) -> list[Any]:
    """``/duplicate`` ignores its body, so the payload is PATCHed onto the copy.

    ``read_back``: the copy is then read with a GET (dashboards, whose PATCH
    response misreports sharing).
    """
    posted = ("POST", f"{base}/{E}/duplicate", {})
    patched = [("PATCH", f"{base}/{SERVER_ID}", BODY)]
    if read_back:
        patched.append(("GET", f"{base}/{SERVER_ID}", None))
    return [
        _row(
            kind, "duplicate", {"existing_uuid": E, "payload": BODY}, [posted, *patched]
        ),
        _row(kind, "duplicate", {"existing_uuid": E}, [posted], label="no_payload"),
    ]


def _form_like(kind: str, base: str) -> list[Any]:
    item = f"{base}/{E}"
    return [
        *_crud(kind, base, item, parent=False),
        *_duplicate(kind, base),
        *_crud(
            f"{kind}_field", f"{base}/{P}/fields", f"{base}/{P}/fields/{E}", parent=True
        ),
        *_options(f"{kind}_field_option", f"{base}/{P}/fields", "option_id"),
    ]


def _saved_view(kind: str, base: str, served: set[str]) -> list[Any]:
    collection = f"/api/custom-objects/{P}/{base}"
    item = f"{collection}/{E}"
    applies = [
        ("roles", "apply-to-roles", "role_ids"),
        ("users", "apply-to-users", "team_member_ids"),
        ("permission_groups", "apply-to-permission-groups", "permission_group_ids"),
    ]
    return [
        *_crud(kind, collection, item, parent=True),
        *[
            _row(
                kind,
                "apply",
                {
                    "parent_object_uuid": P,
                    "existing_uuid": E,
                    "payload": {"target": target, "ids": TARGET_IDS},
                },
                [("POST", f"{item}/{suffix}", {body_key: TARGET_IDS})],
                # Unserved rows pin known-wrong behaviour: api/saved_views.py
                # has no such endpoint for this kind, and no planner emits the
                # op. A guard that rejects them should replace these rows.
                label=target if target in served else f"{target}-unserved",
            )
            for target, suffix, body_key in applies
        ],
    ]


ROWS = [
    *_crud("object", "/api/custom-objects", f"/api/custom-objects/{E}", parent=False),
    *_crud(
        "category",
        f"/api/custom-objects/{P}/categories",
        f"/api/custom-objects/{P}/categories/{E}",
        parent=True,
    ),
    *_crud(
        "field",
        f"/api/custom-objects/{P}/fields",
        f"/api/custom-objects/{P}/fields/{E}",
        parent=True,
    ),
    *_options("field_option", f"/api/custom-objects/{P}/fields", None),
    _row(
        "record",
        "create",
        {"parent_object_uuid": P, "payload": {"fields": FIELDS}},
        [("POST", f"/api/records/{P}/add", {"fields": FIELDS})],
    ),
    _row(
        "record",
        "upsert",
        {
            "parent_object_uuid": P,
            "payload": {"lookup_value": "Acme", "fields": FIELDS},
        },
        [
            (
                "POST",
                f"/api/records/{P}/upsert",
                {"lookup_value": "Acme", "fields": FIELDS},
            )
        ],
    ),
    _row(
        "record",
        "upsert",
        {
            "parent_object_uuid": P,
            "payload": {
                "lookup_value": "Acme",
                "fields": FIELDS,
                "oncreate_unarchive": "unarchive",
                "onupdate_archived_conflict": "overwrite",
            },
        },
        [
            (
                "POST",
                f"/api/records/{P}/upsert",
                {
                    "lookup_value": "Acme",
                    "fields": FIELDS,
                    "oncreate_unarchive": "unarchive",
                    "onupdate_archived_conflict": "overwrite",
                },
            )
        ],
        label="conflict_flags",
    ),
    _row(
        "record",
        "update",
        {"parent_object_uuid": P, "existing_uuid": E, "payload": {"fields": FIELDS}},
        [("PATCH", f"/api/records/{P}/{E}", {"fields": FIELDS})],
    ),
    _row(
        "record_archive",
        "update",
        {"parent_object_uuid": P, "payload": {"record_ids": TARGET_IDS}},
        [
            (
                "POST",
                f"/api/custom-objects/{P}/bulk-archive-entity-record",
                {"record_ids": TARGET_IDS, "send_email_notification": False},
            )
        ],
    ),
    _row(
        "record_unarchive",
        "update",
        {"parent_object_uuid": P, "existing_uuid": E},
        [("PATCH", f"/api/records/{P}/{E}/unarchive", None)],
    ),
    _row(
        "automation",
        "create",
        {"payload": BODY},
        [("POST", "/api/automation2/automations", BODY)],
    ),
    _row(
        "automation",
        "update",
        {"existing_uuid": E, "payload": {**BODY, "last_revision": 7}},
        [("PUT", f"/api/automation2/automations/{E}", {**BODY, "last_revision": 7})],
        label="last_revision",
    ),
    # A plan saved by an older build has no last_revision: one GET refetches it
    # (the catch-all answers with no "revision", so it falls back to 0).
    _row(
        "automation",
        "update",
        {"existing_uuid": E, "payload": BODY},
        [
            ("GET", f"/api/automation2/automations/{E}", None),
            ("PUT", f"/api/automation2/automations/{E}", {**BODY, "last_revision": 0}),
        ],
        label="refetch_revision",
    ),
    _row(
        "automation",
        "delete",
        {"existing_uuid": E},
        [("DELETE", f"/api/automation2/automations/{E}", None)],
    ),
    _row(
        "automation",
        "duplicate",
        {"existing_uuid": E, "payload": BODY},
        [("POST", f"/api/automation2/automations/{E}/duplicate", BODY)],
    ),
    _row(
        "automation_message",
        "create",
        {"payload": {"automation_id": AUTOMATION_ID, "template": TEMPLATE}},
        [
            (
                "POST",
                f"/api/messages/automations/automation/{AUTOMATION_ID}",
                {
                    "name": "Welcome",
                    "type": "email",
                    "subject": "Hi",
                    "content": "<p>Hi</p>",
                    "base_message_id": TEMPLATE_ID,
                    "from_name_type": "default",
                    "sender_type": "last_team_member",
                    "craft_json": {"ROOT": {}},
                    "html_content": "<p>Hi</p>",
                },
            )
        ],
    ),
    *_crud(
        "email_template",
        "/api/messages/templates",
        f"/api/messages/templates/{E}",
        parent=False,
    ),
    *_crud(
        "automation_folder",
        "/api/automation2/folders",
        f"/api/automation2/folders/{E}",
        parent=False,
    ),
    *_crud("dashboard", "/api/dashboards", f"/api/dashboards/{E}", parent=False)[:2],
    *_duplicate("dashboard", "/api/dashboards", read_back=True),
    *_crud(
        "dashlet",
        f"/api/dashboards/{P}/dashlet",
        f"/api/dashboards/{P}/dashlet/{E}",
        parent=True,
    )[:2],
    _row(
        "layout",
        "update",
        {"parent_object_uuid": P, "existing_uuid": E, "payload": BODY},
        [("PUT", f"/api/custom-objects/{P}/layouts/{E}", BODY)],
    ),
    *_crud("activity", "/api/activities", f"/api/activities/{E}", parent=False),
    *_duplicate("activity", "/api/activities"),
    *_crud(
        "activity_field",
        f"/api/activities/{P}/fields",
        f"/api/activities/{P}/fields/{E}",
        parent=True,
    ),
    *_options("activity_field_option", f"/api/activities/{P}/fields", "option_id"),
    *_crud(
        "stage",
        f"/api/pipelines/{P}/stages",
        f"/api/pipelines/{P}/stages/{E}",
        parent=True,
    )[:2],
    _row(
        "stage",
        "delete",
        {"parent_object_uuid": P, "existing_uuid": E, "payload": {"new_stage_id": N}},
        [
            (
                "POST",
                f"/api/pipelines/{P}/stages/remove-stage",
                {"id": E, "new_stage_id": N},
            )
        ],
    ),
    _row(
        "record_move",
        "update",
        {"parent_object_uuid": P, "existing_uuid": E, "payload": {"stage": N}},
        [("PATCH", f"/api/records/{P}/{E}/move", {"stage": N})],
    ),
    *_form_like("form", "/api/forms"),
    *_form_like("survey", "/api/surveys"),
    *_crud("role", "/api/role", f"/api/role/{E}", parent=False),
    *_crud(
        "service_account",
        "/api/service-accounts",
        f"/api/service-accounts/{E}",
        parent=False,
    ),
    _row(
        "team_member",
        "update",
        {
            "existing_uuid": E,
            "payload": {
                "team_member_ids": [E],
                "role_ids_to_add": [ROLE_ADDED],
                "role_ids_to_remove": [ROLE_REMOVED],
            },
        },
        [
            (
                "POST",
                "/api/team/change-roles",
                {
                    "team_member_ids": [E],
                    "role_ids_to_add": [ROLE_ADDED],
                    "role_ids_to_remove": [ROLE_REMOVED],
                },
            )
        ],
    ),
    *_crud(
        "permission_group",
        "/api/permission-group",
        f"/api/permission-group/{E}",
        parent=False,
        update_method="PUT",
    ),
    _row(
        "permission_setting",
        "update",
        {
            "parent_object_uuid": P,
            "payload": {
                "mode": "object_update",
                "control_present": True,
                "body": {"custom_object_id": E, "permission_level": 2},
            },
        },
        [
            (
                "PATCH",
                f"/api/permission-group/{P}/object-update",
                {"custom_object_id": E, "permission_level": 2},
            )
        ],
        label="object_update",
    ),
    _row(
        "permission_setting",
        "update",
        {
            "parent_object_uuid": P,
            "payload": {"mode": "section", "body": {"contacts": {"view": True}}},
        },
        [("PATCH", f"/api/permission-group/{P}", {"contacts": {"view": True}})],
        label="section",
    ),
    _row(
        "record_bulk_field_value",
        "update",
        {
            "parent_object_uuid": P,
            "payload": {"record_ids": TARGET_IDS, "field_id": F, "field_value": R},
        },
        [
            (
                "POST",
                f"/api/custom-objects/{P}/bulk-change-field-value",
                {"record_ids": TARGET_IDS, "field_id": F, "field_value": R},
            )
        ],
    ),
    *_saved_view("filter_group", "filter-groups", served=set()),
    *_saved_view("quick_filter", "quick-filters", served={"roles", "users"}),
    *_saved_view(
        "column_template",
        "columns",
        served={"roles", "users", "permission_groups"},
    ),
]


def _sent(call: Any) -> Request:
    req = call.request
    return (
        req.method,
        req.url.path,
        json.loads(req.content) if req.content else None,
    )


@pytest.mark.parametrize("kind, action, op_kwargs, expected", ROWS)
def test_execute_sends_exactly(kind, action, op_kwargs, expected):
    op = PlanOperation(kind=kind, action=action, key="k", **op_kwargs)
    with respx.mock(assert_all_mocked=True) as mock:
        mock.route(url__startswith=FAKE_BASE_URL).mock(
            return_value=httpx.Response(200, json={"id": SERVER_ID})
        )
        result = plan_tools.apply_plan(
            Plan.build(env="testenv", summary="t", operations=[op])
        )
        sent = [_sent(c) for c in mock.calls]

    (r,) = result.results
    assert r.status == "ok", r.message
    assert sent == expected


def test_every_kind_has_a_row():
    kinds = set(typing.get_args(Kind))
    covered = {p.values[0] for p in ROWS}
    # record_import's upload/submit/poll/download sequence is pinned in
    # tests/test_records_import.py, not here.
    assert covered | {"record_import"} == kinds, (
        f"Kind {sorted(kinds - covered - {'record_import'})} has no row in "
        "tests/test_execute_dispatch.py. Add a _row(kind, action, op_kwargs, "
        "expected) to ROWS for each action _execute dispatches for it, with the "
        "(method, path, body) read from api/*.py."
    )


@pytest.mark.parametrize(
    "kind, base, recovery",
    [
        ("form", "/api/forms", f"`kizen forms delete {SERVER_ID}`"),
        ("survey", "/api/surveys", f"`kizen surveys delete {SERVER_ID}`"),
        ("activity", "/api/activities", f"`kizen activities delete {SERVER_ID}`"),
        ("dashboard", "/api/dashboards", f"`kizen dashboards update {SERVER_ID}`"),
    ],
)
def test_failed_patch_after_duplicate_names_the_copy(kind, base, recovery):
    op = PlanOperation(
        kind=kind, action="duplicate", key="k", existing_uuid=E, payload=BODY
    )
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(f"{FAKE_BASE_URL}{base}/{E}/duplicate").mock(
            return_value=httpx.Response(
                200, json={"id": SERVER_ID, "api_name": "thing_copy_1"}
            )
        )
        mock.patch(f"{FAKE_BASE_URL}{base}/{SERVER_ID}").mock(
            return_value=httpx.Response(400, json={"name": ["bad"]})
        )
        result = plan_tools.apply_plan(
            Plan.build(env="testenv", summary="t", operations=[op])
        )

    (r,) = result.results
    assert r.status == "failed"
    assert SERVER_ID in r.message and "thing_copy_1" in r.message
    assert recovery in r.message


def test_unresolved_deferred_ref_fails_without_a_request():
    ops = [
        PlanOperation(action="skip", kind="object", key="x", existing_uuid=E),
        PlanOperation(
            action="create",
            kind="field",
            key="x.f",
            payload=BODY,
            deferred_parent_object_key="x",
        ),
    ]
    with respx.mock(assert_all_mocked=True) as mock:
        result = plan_tools.apply_plan(
            Plan.build(env="testenv", summary="t", operations=ops)
        )
        sent = list(mock.calls)

    skipped, failed = result.results
    assert skipped.status == "skipped"
    assert failed.status == "failed"
    assert failed.message.startswith("unresolved deferred ref")
    assert sent == []


@pytest.mark.parametrize(
    "stored, message",
    [
        ("invoices", "api_name invoices"),
        (
            "invoices_AHpZedMd",
            "api_name invoices_AHpZedMd (the plan predicted invoices)",
        ),
    ],
)
def test_object_create_reports_the_api_name_kizen_stored(stored, message):
    op = PlanOperation(
        action="create",
        kind="object",
        key="invoices",
        payload={"object_name": "Invoices"},
    )
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(url__startswith=FAKE_BASE_URL).mock(
            return_value=httpx.Response(201, json={"id": SERVER_ID, "name": stored})
        )
        result = plan_tools.apply_plan(
            Plan.build(env="testenv", summary="t", operations=[op])
        )

    (r,) = result.results
    assert r.status == "ok"
    assert r.message == message

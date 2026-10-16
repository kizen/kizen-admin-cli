"""tools/forms.py: the read layer over forms/surveys, identifier resolution,
and the form_ui spec resolver (``build_form_ui_from_spec`` and its private
block/row/section/page helpers).

Everything that builds its own client (``list_forms``, ``get_form``,
``build_form_ui_from_spec``) goes through respx against FAKE_BASE_URL, the
same seam other tools tests use. ``resolve_form_id`` and
``_enrich_custom_object_fields`` take a client directly, so those tests build
one locally via the `client` fixture, matching `tests/test_records_api.py`.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.tools import forms as form_tools
from tests.conftest import FAKE_BASE_URL

FORM_ID = "00000000-0000-4000-8000-000000000f01"
OBJ_ID = "00000000-0000-4000-8000-000000000f10"
FIELD_ID = "00000000-0000-4000-8000-000000000f02"
COF_ID = "00000000-0000-4000-8000-000000000f20"

BASE_PATHS = ["/api/forms", "/api/surveys"]


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


# ---------------------------------------------------------------------------
# list_forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_path", BASE_PATHS)
@respx.mock
def test_list_forms_summarizes_each_record(base_path):
    respx.get(f"{FAKE_BASE_URL}{base_path}").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": FORM_ID,
                        "name": "Contact Us",
                        "api_name": "contact_us",
                        "template_type": "modern",
                        "number_submissions": 3,
                        "related_object": {"id": OBJ_ID},
                        "created": "2026-01-01T00:00:00Z",
                    }
                ],
                "next": None,
            },
        )
    )
    out = form_tools.list_forms(base_path=base_path)
    assert out == [
        {
            "env": "testenv",
            "id": FORM_ID,
            "name": "Contact Us",
            "api_name": "contact_us",
            "template_type": "modern",
            "n_submissions": 3,
            "related_object": {"id": OBJ_ID},
            "deleted": False,
            "created": "2026-01-01T00:00:00Z",
        }
    ]


@respx.mock
def test_list_forms_passes_through_search():
    route = respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    form_tools.list_forms(search="contact")
    assert route.calls[0].request.url.params["search"] == "contact"


@respx.mock
def test_list_forms_preserves_explicit_deleted_flag():
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(
            200, json={"results": [{"id": FORM_ID, "deleted": True}], "next": None}
        )
    )
    out = form_tools.list_forms()
    assert out[0]["deleted"] is True


# ---------------------------------------------------------------------------
# resolve_form_id
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_form_id_direct_hit_by_uuid(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}").mock(
        return_value=Response(200, json={"id": FORM_ID, "name": "Contact Us"})
    )
    form_id, name = form_tools.resolve_form_id(client, "/api/forms", FORM_ID)
    assert (form_id, name) == (FORM_ID, "Contact Us")


@respx.mock
def test_resolve_form_id_falls_back_to_list_scan_on_404(client):
    """The direct GET can 404 when `identifier` is an api_name rather than a
    UUID (the detail endpoint only accepts a UUID) — fall back to scanning
    the list endpoint by id/api_name/name."""
    respx.get(f"{FAKE_BASE_URL}/api/forms/contact_us").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"id": FORM_ID, "api_name": "contact_us", "name": "Contact Us"}
                ],
                "next": None,
            },
        )
    )
    form_id, name = form_tools.resolve_form_id(client, "/api/forms", "contact_us")
    assert (form_id, name) == (FORM_ID, "Contact Us")


@respx.mock
def test_resolve_form_id_fallback_matches_by_display_name(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms/Contact%20Us").mock(
        return_value=Response(404, json={})
    )
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(
            200,
            json={
                "results": [{"id": FORM_ID, "api_name": None, "name": "Contact Us"}],
                "next": None,
            },
        )
    )
    form_id, name = form_tools.resolve_form_id(client, "/api/forms", "Contact Us")
    assert form_id == FORM_ID


@respx.mock
def test_resolve_form_id_raises_lookup_error_when_nowhere_found(client):
    respx.get(f"{FAKE_BASE_URL}/api/forms/nope").mock(
        return_value=Response(404, json={})
    )
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    with pytest.raises(LookupError, match="'nope' not found under /api/forms"):
        form_tools.resolve_form_id(client, "/api/forms", "nope")


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
def test_resolve_form_id_does_not_report_other_errors_as_not_found(client, status):
    respx.get(f"{FAKE_BASE_URL}/api/forms/contact_us").mock(
        return_value=Response(status, json={"detail": "boom"})
    )
    list_route = respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    with pytest.raises(KizenAPIError) as exc:
        form_tools.resolve_form_id(client, "/api/forms", "contact_us")
    assert exc.value.status_code == status
    assert list_route.call_count == 0


# ---------------------------------------------------------------------------
# get_form / _normalize_field
# ---------------------------------------------------------------------------


@respx.mock
def test_get_form_includes_normalized_fields_by_default():
    # The identifier goes into the detail path as given; the *fields* call
    # then uses the UUID off the detail response, not the identifier.
    respx.get(f"{FAKE_BASE_URL}/api/forms/contact_us").mock(
        return_value=Response(
            200,
            json={
                "id": FORM_ID,
                "name": "Contact Us",
                "api_name": "contact_us",
                "related_object": {"id": OBJ_ID},
                "number_submissions": 1,
            },
        )
    )
    fields_route = respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": FIELD_ID,
                    "name": "summary",
                    "display_name": "Summary",
                    "field_type": "dropdown",
                    "is_required": True,
                    "options": [{"id": "o1", "name": "A", "code": "A"}],
                },
                {
                    "id": "field-2",
                    "name": "notes",
                    "display_name": "Notes",
                    "field_type": "text",
                    "options": [],
                },
            ],
        )
    )
    out = form_tools.get_form("contact_us")
    assert fields_route.call_count == 1
    assert out["env"] == "testenv"
    assert out["id"] == FORM_ID
    assert out["n_submissions"] == 1
    assert out["fields"][0] == {
        "id": FIELD_ID,
        "api_name": "summary",
        "display_name": "Summary",
        "field_type": "dropdown",
        "is_required": True,
        "is_read_only": None,
        "is_hidden": None,
        "is_deletable": None,
        "order": None,
        "options": [{"id": "o1", "name": "A", "code": "A"}],
    }
    # Empty `options` list normalizes to None, not [].
    assert out["fields"][1]["options"] is None
    assert out["raw"]["id"] == FORM_ID


@respx.mock
def test_get_form_skips_fields_fetch_when_not_requested():
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}").mock(
        return_value=Response(200, json={"id": FORM_ID, "name": "Contact Us"})
    )
    fields_route = respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(200, json=[])
    )
    out = form_tools.get_form(FORM_ID, include_fields=False)
    assert out["fields"] == []
    assert fields_route.call_count == 0


# ---------------------------------------------------------------------------
# _resolve_block
# ---------------------------------------------------------------------------


def test_resolve_block_custom_field_looks_up_by_api_name():
    """The resolver's job is only to swap the spec's `field` api_name for the
    live field record. Translating that into a `CustomField`/`FormField` wire
    node happens later, in form_ui's tree assembly."""
    field = {"id": FIELD_ID, "name": "summary", "display_name": "Summary"}
    block = form_tools._resolve_block(
        {"kind": "custom_field", "field": "summary"}, {"summary": field}
    )
    assert block == {"kind": "custom_field", "field": field}


def test_resolve_block_custom_field_missing_raises_lookup_error():
    with pytest.raises(LookupError, match="no field named 'missing'"):
        form_tools._resolve_block({"kind": "custom_field", "field": "missing"}, {})


def test_resolve_block_text_and_html_stay_distinct_kinds():
    """`text` and `html` are different node types on the wire (`custom.text`
    vs `props.htmlContent`), so the resolver must preserve which one was
    asked for rather than collapsing both to "some markup"."""
    text = form_tools._resolve_block({"kind": "text", "html": "<p>hi</p>"}, {})
    html = form_tools._resolve_block({"kind": "html", "html": "<div/>"}, {})
    assert text == {"kind": "text", "html": "<p>hi</p>"}
    assert html == {"kind": "html", "html": "<div/>"}


def test_resolve_block_button_defaults():
    block = form_tools._resolve_block({"kind": "button"}, {})
    assert block == {
        "kind": "button",
        "label": "Submit",
        "action": "submit",
        "url": "",
        "color": None,
    }


def test_resolve_block_button_passes_url_action_through():
    block = form_tools._resolve_block(
        {"kind": "button", "label": "Go", "action": "url", "url": "https://x"}, {}
    )
    assert block["label"] == "Go"
    assert block["action"] == "url"
    assert block["url"] == "https://x"


def test_resolve_block_divider():
    block = form_tools._resolve_block({"kind": "divider", "color": "#000"}, {})
    assert block == {"kind": "divider", "color": "#000"}


def test_resolve_block_image_passes_dimensions_through():
    block = form_tools._resolve_block(
        {
            "kind": "image",
            "file_id": "f1",
            "src": "https://x/img.png",
            "name": "img.png",
            "width": 200,
            "natural_width": 400,
            "natural_height": 300,
        },
        {},
    )
    assert block == {
        "kind": "image",
        "file_id": "f1",
        "src": "https://x/img.png",
        "name": "img.png",
        "width": 200,
        "natural_width": 400,
        "natural_height": 300,
    }


def test_resolve_block_unknown_kind_raises_value_error():
    with pytest.raises(ValueError, match="unknown block kind 'bogus'"):
        form_tools._resolve_block({"kind": "bogus"}, {})


# ---------------------------------------------------------------------------
# _resolve_row / _resolve_section / _resolve_page
# ---------------------------------------------------------------------------


def test_resolve_row_builds_one_cell_per_spec():
    row = form_tools._resolve_row(
        {
            "cells": [
                {"blocks": [{"kind": "text", "html": "left"}]},
                {"blocks": [{"kind": "text", "html": "right"}]},
            ],
            "columns": [0.3, 0.7],
        },
        {},
    )
    assert row["columns"] == [0.3, 0.7]
    assert len(row["cells"]) == 2
    assert row["cells"][0]["blocks"] == [{"kind": "text", "html": "left"}]


def test_resolve_row_columns_default_to_none_when_unspecified():
    row = form_tools._resolve_row({"cells": [{"blocks": [{"kind": "divider"}]}]}, {})
    assert row["columns"] is None


def test_resolve_section_default_background_color():
    section = form_tools._resolve_section(
        {"rows": [{"cells": [{"blocks": [{"kind": "text", "html": "x"}]}]}]}, {}
    )
    assert section["background_color"] == "#FFFFFF"
    assert len(section["rows"]) == 1


def test_resolve_section_honors_explicit_background_color():
    section = form_tools._resolve_section({"background_color": "#EEE", "rows": []}, {})
    assert section["background_color"] == "#EEE"


def test_resolve_page_simple_true_orders_fields_and_missing_raises():
    field = {"id": FIELD_ID, "name": "summary", "display_name": "Summary"}
    page = form_tools._resolve_page(
        {"simple": True, "fields": ["summary"], "heading": "Hi"}, {"summary": field}
    )
    assert page["page_name"] == "Form Page"
    assert page["is_form_page"] is True

    with pytest.raises(LookupError, match="no field named 'missing'"):
        form_tools._resolve_page({"simple": True, "fields": ["missing"]}, {})


def test_resolve_page_sections_tree_and_non_form_page_flags():
    page = form_tools._resolve_page(
        {
            "name": "Thanks",
            "is_form_page": False,
            "hidden": True,
            "deletable": True,
            "sections": [{"rows": [{"cells": [{"blocks": [{"kind": "divider"}]}]}]}],
        },
        {},
    )
    assert page["page_name"] == "Thanks"
    assert page["is_form_page"] is False
    assert page["hidden"] is True
    assert page["is_deletable"] is True


# ---------------------------------------------------------------------------
# _enrich_custom_object_fields
# ---------------------------------------------------------------------------


def test_enrich_custom_object_fields_noop_without_related_object():
    raw = [{"id": FIELD_ID, "custom_object_field": {"id": COF_ID}}]
    assert (
        form_tools._enrich_custom_object_fields(
            raw, client=None, related_object_id=None
        )
        is raw
    )


@respx.mock
def test_enrich_custom_object_fields_replaces_skinny_stub_with_full_record(client):
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": COF_ID,
                    "name": "summary",
                    "display_name": "Summary",
                    "category": "cat-1",
                    "is_hidden": False,
                }
            ],
        )
    )
    raw = [
        {
            "id": FIELD_ID,
            "custom_object_field": {"id": COF_ID, "display_name": "stub"},
        },
        {"id": "field-2", "custom_object_field": None},
        {"id": "field-3", "custom_object_field": {"id": "unmatched"}},
    ]
    out = form_tools._enrich_custom_object_fields(raw, client, OBJ_ID)
    assert out[0]["custom_object_field"]["category"] == "cat-1"
    assert out[1]["custom_object_field"] is None
    # An id with no match in the related object's own field list is left as-is.
    assert out[2]["custom_object_field"] == {"id": "unmatched"}


# ---------------------------------------------------------------------------
# build_form_ui_from_spec — end to end through the live-client seam
# ---------------------------------------------------------------------------


def _mock_build_ui_deps(*, related_object=None, fields=None):
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": FORM_ID,
                "name": "Contact Us",
                "related_object": related_object,
            },
        )
    )
    respx.get(f"{FAKE_BASE_URL}/api/forms/{FORM_ID}/fields").mock(
        return_value=Response(200, json=fields or [])
    )


@respx.mock
def test_build_form_ui_from_spec_appends_thank_you_page_by_default():
    _mock_build_ui_deps(fields=[{"id": FIELD_ID, "name": "summary"}])
    result = form_tools.build_form_ui_from_spec(
        FORM_ID, {"pages": [{"simple": True, "fields": ["summary"]}]}
    )
    assert len(result["pages"]) == 2
    assert result["pages"][1]["is_form_page"] is False


@respx.mock
def test_build_form_ui_from_spec_skip_thank_you_flag_honored():
    _mock_build_ui_deps(fields=[{"id": FIELD_ID, "name": "summary"}])
    result = form_tools.build_form_ui_from_spec(
        FORM_ID,
        {
            "pages": [{"simple": True, "fields": ["summary"]}],
            "skip_thank_you": True,
        },
    )
    assert len(result["pages"]) == 1


@respx.mock
def test_build_form_ui_from_spec_no_extra_page_when_one_already_non_form():
    _mock_build_ui_deps(fields=[{"id": FIELD_ID, "name": "summary"}])
    result = form_tools.build_form_ui_from_spec(
        FORM_ID,
        {
            "pages": [
                {"simple": True, "fields": ["summary"]},
                {"is_form_page": False, "sections": []},
            ]
        },
    )
    assert len(result["pages"]) == 2


@respx.mock
def test_build_form_ui_from_spec_enriches_linked_fields_before_resolving():
    _mock_build_ui_deps(
        related_object={"id": OBJ_ID},
        fields=[
            {
                "id": FIELD_ID,
                "name": "summary",
                "display_name": "Summary",
                "custom_object_field": {"id": COF_ID, "display_name": "stub"},
            }
        ],
    )
    respx.get(f"{FAKE_BASE_URL}/api/custom-objects/{OBJ_ID}/fields").mock(
        return_value=Response(
            200,
            json=[
                {"id": COF_ID, "display_name": "Summary (full)", "field_type": "text"}
            ],
        )
    )
    result = form_tools.build_form_ui_from_spec(
        FORM_ID, {"pages": [{"simple": True, "fields": ["summary"]}]}
    )
    # simple_form_page -> one row with the custom_field block, whose enriched
    # customObjectField carries the full record fetched from the related object.
    import json as _json

    page_data = _json.loads(result["pages"][0]["page_data"])
    field_nodes = [
        n for n in page_data.values() if n["type"]["resolvedName"] == "CustomField"
    ]
    assert field_nodes[0]["props"]["field"]["customObjectField"]["displayName"] == (
        "Summary (full)"
    )


@respx.mock
def test_build_form_ui_from_spec_requires_at_least_one_page():
    _mock_build_ui_deps(fields=[])
    with pytest.raises(ValueError, match="at least one page"):
        form_tools.build_form_ui_from_spec(FORM_ID, {"pages": []})


@respx.mock
def test_build_form_ui_from_spec_resolves_identifier_via_fallback():
    """`identifier` may be an api_name; resolve_form_id's fallback scan is
    exercised end-to-end here rather than assuming the identifier is a UUID."""
    respx.get(f"{FAKE_BASE_URL}/api/forms/contact_us").mock(
        return_value=Response(404, json={})
    )
    respx.get(f"{FAKE_BASE_URL}/api/forms").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"id": FORM_ID, "api_name": "contact_us", "name": "Contact Us"}
                ],
                "next": None,
            },
        )
    )
    _mock_build_ui_deps(fields=[{"id": FIELD_ID, "name": "summary"}])
    result = form_tools.build_form_ui_from_spec(
        "contact_us", {"pages": [{"simple": True, "fields": ["summary"]}]}
    )
    assert len(result["pages"]) == 2

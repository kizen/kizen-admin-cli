"""Automation-scoped messages: template lookup and creation.

Regression coverage for a live-confirmed quirk: Kizen's builder UI "select
email" picker on a notify_member_via_email step only recognizes a message as
selected when it was created from a real template (base_message_id set) — a
message created from raw content alone is accepted by the wire format but
shows as unselected in the UI.
"""

from __future__ import annotations

import httpx
import respx

from kizen_builder.api import messages as messages_api
from kizen_builder.api.client import KizenClient
from kizen_builder.tools.messages import (
    craft_summary,
    create_automation_message,
    resolve_template,
)
from tests.conftest import FAKE_BASE_URL

TEMPLATE_ID = "7cb5ce29-bf20-4f0f-bdc9-412a8c777ff8"
AUTOMATION_ID = "aba65b8f-946a-4113-8b69-cbbfb6257a1f"

TEMPLATE = {
    "id": TEMPLATE_ID,
    "name": "one more email",
    "subject": "Fresh subject",
    "content": "<p>Real template content</p>",
    "type": "email",
}


def _client() -> KizenClient:
    from kizen_builder.config import load_env_config

    return KizenClient(load_env_config())


@respx.mock
def test_resolve_template_by_uuid_fetches_directly():
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
        return_value=httpx.Response(200, json=TEMPLATE)
    )
    with _client() as client:
        tmpl = resolve_template(client, TEMPLATE_ID)
    assert tmpl["id"] == TEMPLATE_ID


@respx.mock
def test_resolve_template_by_name_matches_case_insensitively_and_fetches_full_detail():
    """The list endpoint's items omit content/craft_json — resolving by name
    must follow up with a detail GET, not return the list item as-is."""
    list_item = {
        "id": TEMPLATE_ID,
        "name": "one more email",
        "subject": "Fresh subject",
    }
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates").mock(
        return_value=httpx.Response(200, json={"results": [list_item], "next": None})
    )
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
        return_value=httpx.Response(200, json=TEMPLATE)
    )
    with _client() as client:
        tmpl = resolve_template(client, "ONE MORE EMAIL")
    assert tmpl["id"] == TEMPLATE_ID
    assert tmpl["content"] == "<p>Real template content</p>"


@respx.mock
def test_resolve_template_by_name_not_found_lists_available():
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates").mock(
        return_value=httpx.Response(200, json={"results": [TEMPLATE], "next": None})
    )
    with _client() as client:
        try:
            resolve_template(client, "does not exist")
            raise AssertionError("expected LookupError")
        except LookupError as e:
            assert "one more email" in str(e)


@respx.mock
def test_resolve_template_by_name_ambiguous_raises():
    dup = {**TEMPLATE, "id": "11111111-1111-1111-1111-111111111111"}
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates").mock(
        return_value=httpx.Response(
            200, json={"results": [TEMPLATE, dup], "next": None}
        )
    )
    with _client() as client:
        try:
            resolve_template(client, "one more email")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "2 templates" in str(e)


@respx.mock
def test_create_automation_message_from_template_sets_base_message_id():
    route = respx.post(
        f"{FAKE_BASE_URL}/api/messages/automations/automation/{AUTOMATION_ID}"
    ).mock(return_value=httpx.Response(201, json={"id": "new-message-id", **TEMPLATE}))
    with _client() as client:
        messages_api.create_automation_message_from_template(
            client, AUTOMATION_ID, TEMPLATE
        )
    sent = respx.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body["base_message_id"] == TEMPLATE_ID
    assert body["subject"] == "Fresh subject"
    assert body["content"] == "<p>Real template content</p>"
    assert route.called


@respx.mock
def test_create_automation_message_tool_resolves_and_creates(monkeypatch):
    monkeypatch.setattr(
        "kizen_builder.tools.messages.get_automation",
        lambda api_name: {"id": AUTOMATION_ID},
    )
    respx.get(f"{FAKE_BASE_URL}/api/messages/templates/{TEMPLATE_ID}").mock(
        return_value=httpx.Response(200, json=TEMPLATE)
    )
    respx.post(
        f"{FAKE_BASE_URL}/api/messages/automations/automation/{AUTOMATION_ID}"
    ).mock(return_value=httpx.Response(201, json={**TEMPLATE, "id": "new-message-id"}))
    result = create_automation_message("some_automation", TEMPLATE_ID)
    assert result["id"] == "new-message-id"
    assert result["automation_api_name"] == "some_automation"


# ---------------------------------------------------------------------------
# craft_json / content drift detection
#
# The two fields are stored independently and the server compiles neither
# from the other — confirmed live 2026-08-25 by PATCHing a modified
# craft_json alone and reading `content` back byte-identical. So a template
# can look right in the builder and send something else, and the only way to
# notice is to compare the two.
# ---------------------------------------------------------------------------


def _node(resolved_name, **extra):
    return {
        "type": {"resolvedName": resolved_name},
        "props": {},
        "custom": {},
        "nodes": [],
        "linkedNodes": {},
        **extra,
    }


def _template(nodes, content):
    return {"id": TEMPLATE_ID, "name": "t", "craft_json": nodes, "content": content}


SECTION_ID = "4e583d9c82fb4255dd004b11"
ROW_ID = "ff17e4ea3375d1a2f1cc2a52"


def test_craft_summary_reports_coupled_when_both_fields_agree():
    tmpl = _template(
        {
            "ROOT": _node("Root"),
            SECTION_ID: _node("Section"),
            ROW_ID: _node("Row"),
            "t1": _node("Text", custom={"text": "<p>Hello there</p>"}),
        },
        f'<div class="section-{SECTION_ID}"></div>'
        f'<div class="section-{ROW_ID}"><p>Hello there</p></div>',
    )
    summary = craft_summary(tmpl)
    assert summary["structure_coupled"] is True
    assert summary["text_in_sync"] is True
    assert summary["coupled"] is True
    assert summary["node_types"]["Text"] == 1


def test_craft_summary_flags_a_section_with_no_class_in_the_compiled_html():
    """A Section/Row added to the tree without recompiling `content` is
    structural drift — the builder shows it, recipients never see it."""
    tmpl = _template(
        {"ROOT": _node("Root"), SECTION_ID: _node("Section"), ROW_ID: _node("Row")},
        f'<div class="section-{SECTION_ID}"></div>',
    )
    summary = craft_summary(tmpl)
    assert summary["structure_coupled"] is False
    assert summary["containers_without_class"] == [ROW_ID]
    assert summary["coupled"] is False


def test_craft_summary_flags_edited_copy_that_never_reached_the_compiled_html():
    """Text drift leaves the structure untouched, so the node-id check passes
    and only the text check catches it — this is the failure mode a
    craft_json-only PATCH actually produces."""
    tmpl = _template(
        {
            "ROOT": _node("Root"),
            SECTION_ID: _node("Section"),
            "t1": _node("Text", custom={"text": "<p>Edited in the builder</p>"}),
        },
        f'<div class="section-{SECTION_ID}"><p>The original copy</p></div>',
    )
    summary = craft_summary(tmpl)
    assert summary["structure_coupled"] is True
    assert summary["text_in_sync"] is False
    assert summary["text_nodes_missing_from_content"] == ["t1"]
    assert summary["coupled"] is False


def test_craft_summary_does_not_report_drift_for_inline_markup_or_merge_fields():
    """`content` embeds a Text node's markup verbatim, so both sides must be
    tag-stripped before comparing — otherwise a styled span or a merge field
    reads as drift. Regression for a live false positive."""
    text = (
        '<p style="line-height: 1.25"><span style="font-size: 18px">Hi '
        '<span class="kzn-merge-field" data-merge-field-relationship='
        '"team_member.email">{{ team_member.email }}</span></span></p>'
    )
    tmpl = _template(
        {
            "ROOT": _node("Root"),
            SECTION_ID: _node("Section"),
            "t1": _node("Text", custom={"text": text}),
        },
        f'<div class="section-{SECTION_ID}">{text}</div>',
    )
    assert craft_summary(tmpl)["text_in_sync"] is True


def test_craft_summary_compares_multi_paragraph_text_per_paragraph():
    """A multi-paragraph node flattens to a string that never appears
    contiguously in the rendered HTML, because the renderer puts markup
    between the paragraphs. Regression for a live false positive."""
    tmpl = _template(
        {
            "ROOT": _node("Root"),
            SECTION_ID: _node("Section"),
            "t1": _node("Text", custom={"text": "<p>First para</p><p>Second para</p>"}),
        },
        f'<div class="section-{SECTION_ID}">'
        "<td><p>First para</p></td><td><p>Second para</p></td></div>",
    )
    assert craft_summary(tmpl)["text_in_sync"] is True

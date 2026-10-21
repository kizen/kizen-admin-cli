"""Tests for the forms/surveys page-layout (form_ui) builder.

Pins wire-format rules confirmed live 2026-07-21/22 against a real form
(see `kizen docs show reference`, "Forms & Surveys API"):
  * page_data is a JSON-ENCODED STRING, distinct from the layout/dashboard
    surfaces which embed the tree as a nested object;
  * a linked field (has custom_object_field) renders as a CustomField node
    borrowing displayName/fieldType from the linked record; an unlinked
    field renders as a DIFFERENT node type, FormField, with an explicit
    customObjectField: null, a slimmer access dict, and no labelText —
    using CustomField for an unlinked field crashes the real Kizen builder;
  * HTMLBlock is a distinct node type from Text, content in
    props.htmlContent not custom.text.
The builders are pure, so no live-API stubbing is needed.
"""

from __future__ import annotations

import json

from kizen_builder.tools.form_ui import (
    build_content_tree,
    build_form_ui,
    button_block,
    cell,
    custom_field_block,
    custom_field_prop,
    divider_block,
    form_field_prop,
    html_block,
    image_block,
    page,
    row,
    section,
    simple_form_page,
    text_block,
    thank_you_page,
    unplaced_fields,
)

LINKED_FIELD = {
    "id": "field-own-id",
    "name": "hours_spent_5821e5",
    "display_name": "Hours Spent",
    "is_default": False,
    "field_type": "decimal",
    "is_required": False,
    "order": 3,
    "meta": {},
    "properties": {"type": "float", "target_column": "value_decimal"},
    "options": [],
    "relation": None,
    "rating": None,
    "phonenumber_options": None,
    "money_options": None,
    "decimal_options": None,
    "custom_object_field": {
        "id": "cof-id",
        "name": "hours_spent",
        "category": "cat-id",
        "display_name": "Hours Spent",
        "canonical_display_name": "Hours Spent",
        "is_default": False,
        "field_type": "decimal",
        "is_required": False,
        "is_read_only": False,
        "is_hidden": False,
        "is_deletable": True,
        "is_hideable": True,
        "is_suppressed": False,
        "include_in_short_form": False,
        "allows_nulls": True,
        "allows_empty": False,
        "order": 4,
        "meta": {"cols": 1},
        "description": "",
        "description_visibility": "all",
        "properties": {"type": "float", "target_column": "value_decimal"},
        "access": {"view": True, "edit": True, "remove": False},
        "options": [],
        "relation": None,
        "rating": None,
        "phonenumber_options": None,
        "money_options": None,
        "decimal_options": None,
    },
}

UNLINKED_FIELD = {
    "id": "form-only-id",
    "name": "test_form_field_3f2de0",
    "display_name": "TEST FORM FIELD",
    "is_default": False,
    "field_type": "yesnomaybe",
    "is_required": True,
    "order": 4,
    "meta": {},
    "properties": {"type": "UUID", "target_column": "value_uuid"},
    "options": [{"id": "opt-1", "code": "yes", "name": "Yes", "order": 1}],
    "relation": None,
    "rating": None,
    "phonenumber_options": None,
    "money_options": None,
    "decimal_options": None,
    "custom_object_field": None,
}


# ---------------------------------------------------------------------------
# custom_field_prop / form_field_prop
# ---------------------------------------------------------------------------


def test_custom_field_prop_borrows_display_name_and_field_type_from_linked_record():
    prop = custom_field_prop(LINKED_FIELD)
    assert prop["displayName"] == "Hours Spent"
    assert prop["fieldType"] == "decimal"
    assert prop["labelText"] == "Hours Spent"
    assert prop["customObjectField"]["id"] == "cof-id"
    assert prop["customObjectField"]["canonicalDisplayName"] == "Hours Spent"
    assert prop["access"] == {"view": True, "edit": True, "remove": False}
    assert prop["isNew"] is False


def test_form_field_prop_has_no_labelText_and_explicit_null_customObjectField():
    prop = form_field_prop(UNLINKED_FIELD)
    assert prop["customObjectField"] is None
    assert prop["access"] == {"edit": True, "view": True}
    assert "labelText" not in prop
    assert prop["placeholder"] == "Choose Option"


# ---------------------------------------------------------------------------
# Block assembler dispatch: CustomField vs FormField vs HTMLBlock
# ---------------------------------------------------------------------------


def test_linked_field_renders_as_customfield_node():
    tree = build_content_tree(
        [section([row([cell([custom_field_block(LINKED_FIELD)])])])]
    )
    node = next(
        n
        for n in tree.values()
        if n.get("type", {}).get("resolvedName") == "CustomField"
    )
    assert node["props"]["field"]["customObjectField"]["id"] == "cof-id"
    assert not any(
        n.get("type", {}).get("resolvedName") == "FormField" for n in tree.values()
    )


def test_unlinked_field_renders_as_formfield_node_not_customfield():
    tree = build_content_tree(
        [section([row([cell([custom_field_block(UNLINKED_FIELD)])])])]
    )
    assert not any(
        n.get("type", {}).get("resolvedName") == "CustomField" for n in tree.values()
    )
    node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "FormField"
    )
    assert node["props"]["field"]["customObjectField"] is None


def test_html_block_is_distinct_node_type_from_text_block():
    tree = build_content_tree(
        [
            section(
                [row([cell([text_block("<p>rich</p>"), html_block("<div>raw</div>")])])]
            )
        ]
    )
    text_node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Text"
    )
    html_node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "HTMLBlock"
    )
    assert text_node["custom"]["text"] == "<p>rich</p>"
    assert "htmlContent" not in text_node["props"]
    assert html_node["props"]["htmlContent"] == "<div>raw</div>"
    assert html_node["custom"] == {}


# ---------------------------------------------------------------------------
# Row/Cell/Section/Root tree shape
# ---------------------------------------------------------------------------


def test_row_columns_default_to_equal_shares_and_linked_nodes_by_column():
    tree = build_content_tree(
        [
            section(
                [
                    row(
                        [
                            cell([text_block("a")]),
                            cell([text_block("b")]),
                            cell([text_block("c")]),
                        ]
                    )
                ]
            )
        ]
    )
    row_node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Row"
    )
    assert row_node["props"]["columns"] == [1 / 3, 1 / 3, 1 / 3]
    assert row_node["nodes"] == []
    assert sorted(row_node["linkedNodes"].keys()) == [
        "column-1",
        "column-2",
        "column-3",
    ]


def test_explicit_row_columns_override_default():
    r = row([cell([text_block("a")]), cell([text_block("b")])], columns=[0.7, 0.3])
    tree = build_content_tree([section([r])])
    row_node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Row"
    )
    assert row_node["props"]["columns"] == [0.7, 0.3]


def test_button_block_submit_sets_custom_flag():
    tree = build_content_tree([section([row([cell([button_block("Submit")])])])])
    btn = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Button"
    )
    assert btn["props"]["action"] == "submit"
    assert btn["custom"] == {"isSubmitButton": True}


def test_button_block_url_action_has_no_submit_flag():
    tree = build_content_tree(
        [section([row([cell([button_block("Go", action="url", url="https://x")])])])]
    )
    btn = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Button"
    )
    assert btn["props"]["action"] == "url"
    assert btn["props"]["url"] == "https://x"
    assert btn["custom"] == {}


def test_divider_and_image_blocks_render():
    tree = build_content_tree(
        [
            section(
                [
                    row([cell([divider_block()])]),
                    row(
                        [
                            cell(
                                [
                                    image_block(
                                        "file-id",
                                        "https://x/img.png",
                                        "img.png",
                                        width=200,
                                    )
                                ]
                            )
                        ]
                    ),
                ]
            )
        ]
    )
    assert any(
        n.get("type", {}).get("resolvedName") == "Divider" for n in tree.values()
    )
    image_node = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Image"
    )
    assert image_node["props"]["fileId"] == "file-id"
    assert image_node["props"]["width"] == 200


def test_root_props_default_and_overridable():
    tree = build_content_tree([section([row([cell([text_block("x")])])])])
    assert tree["ROOT"]["props"]["backgroundColor"] == "#F8FAFF"
    assert "containerBackgroundColor" in tree["ROOT"]["props"]

    custom_root = {"backgroundColor": "#000000"}
    tree2 = build_content_tree(
        [section([row([cell([text_block("x")])])])], root_props=custom_root
    )
    assert tree2["ROOT"]["props"] == custom_root


# ---------------------------------------------------------------------------
# page() / thank_you_page() / build_form_ui()
# ---------------------------------------------------------------------------


def test_page_wraps_page_data_as_json_string_not_nested_object():
    p = page("Form Page", [section([row([cell([text_block("hi")])])])])
    assert isinstance(p["page_data"], str)
    parsed = json.loads(p["page_data"])
    assert parsed["ROOT"]["type"]["resolvedName"] == "Root"
    assert p["is_form_page"] is True
    assert p["is_hideable"] is True
    assert p["is_deletable"] is False


def test_thank_you_page_defaults_not_hideable_not_deletable_not_form_page():
    ty = thank_you_page()
    assert ty["is_form_page"] is False
    assert ty["is_hideable"] is False
    assert ty["is_deletable"] is False
    parsed = json.loads(ty["page_data"])
    text_node = next(
        n for n in parsed.values() if n.get("type", {}).get("resolvedName") == "Text"
    )
    assert "Thank you" in text_node["custom"]["text"]


def test_build_form_ui_wraps_pages_and_defaults_business_merge_fields():
    ui = build_form_ui([page("Form Page", [section([row([cell([text_block("x")])])])])])
    assert list(ui.keys()) == ["pages", "business_merge_fields"]
    assert ui["business_merge_fields"] == []
    assert len(ui["pages"]) == 1


def test_simple_form_page_lays_out_heading_fields_and_submit_button_one_per_row():
    p = simple_form_page(
        [LINKED_FIELD, UNLINKED_FIELD], heading="Contact Us", submit_label="Send"
    )
    tree = json.loads(p["page_data"])
    fields = [
        n
        for n in tree.values()
        if n.get("type", {}).get("resolvedName") in ("CustomField", "FormField")
    ]
    assert len(fields) == 2
    btn = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Button"
    )
    assert btn["props"]["label"] == "Send"
    heading = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Text"
    )
    assert "Contact Us" in heading["custom"]["text"]


def test_unplaced_fields_are_those_on_no_page():
    ui = build_form_ui([simple_form_page([LINKED_FIELD]), thank_you_page()])
    assert unplaced_fields(ui, [LINKED_FIELD, UNLINKED_FIELD]) == [UNLINKED_FIELD]
    assert unplaced_fields(ui, [LINKED_FIELD]) == []
    assert unplaced_fields({}, [LINKED_FIELD]) == [LINKED_FIELD]
    assert unplaced_fields(None, [LINKED_FIELD]) == [LINKED_FIELD]

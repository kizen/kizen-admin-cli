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
from dataclasses import replace

from kizen_builder.tools.form_ui import (
    FORM_PAGE_DEFAULTS,
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


def test_root_props_come_from_the_defaults_passed():
    custom_root = {"backgroundColor": "#000000"}
    tree = build_content_tree(
        [section([row([cell([text_block("x")])])])],
        defaults=replace(FORM_PAGE_DEFAULTS, root=custom_root),
    )
    assert tree["ROOT"]["props"] == custom_root


# ---------------------------------------------------------------------------
# Creation defaults: each node carries exactly the props the Kizen form
# builder writes when a user adds it. Expected values are transcribed from
# the Kizen web app source (react-app), 2026-11-11, not from the module's
# own tables, so a drifted table fails here.
# ---------------------------------------------------------------------------

# packages/page-builder/nodes/containers/ElementContainer.tsx:14-37
ELEMENT_CONTAINER = {
    "containerBackgroundColor": "inherit",
    "containerBackgroundImageName": "",
    "containerBackgroundPositionX": "0%",
    "containerBackgroundPositionY": "0%",
    "containerBackgroundSize": "auto",
    "containerBackgroundRepeat": "repeat",
    "containerBorderColor": "#4A5660",
    "containerBorderStyle": "solid",
    "containerBorderWidth": "0",
    "containerBorderRadius": True,
    "containerBorderTopLeftRadius": "4",
    "containerBorderTopRightRadius": "4",
    "containerBorderBottomLeftRadius": "4",
    "containerBorderBottomRightRadius": "4",
    "containerMarginTop": "0",
    "containerMarginRight": "0",
    "containerMarginBottom": "0",
    "containerMarginLeft": "0",
    "containerPaddingTop": "0",
    "containerPaddingRight": "0",
    "containerPaddingBottom": "0",
    "containerPaddingLeft": "0",
}
PADDING_10 = {
    "containerPaddingTop": "10",
    "containerPaddingRight": "10",
    "containerPaddingBottom": "10",
    "containerPaddingLeft": "10",
}
# What the form/survey Section and Row settings panel writes when it opens on
# a newly added node (settings/SectionSettings.jsx:31-40,
# settings/RowSettings.jsx:30-39), maxWidth being the form Root's "900".
FORM_PANEL_SIZING = {"width": "100", "maxWidth": "900", "alignment": "center"}
# SectionContainer.tsx:27-34 (background inherit), then the panel's keys
FORM_SECTION = {**ELEMENT_CONTAINER, **PADDING_10, **FORM_PANEL_SIZING}
# packages/page-builder/gen/defaults.ts:25-50 (undefined keys dropped)
GEN_CONTAINER = {
    **ELEMENT_CONTAINER,
    "containerBackgroundColor": "rgba(0,0,0,0)",
    "containerBorderColor": "rgba(74,86,96,1)",
}
# packages/page-builder/nodes/Button/props.ts:10-36 + ButtonContainer.tsx:22-28
FORM_BUTTON = {
    "color": "#4BC7B4",
    "label": "Submit",
    "textColor": "#FFFFFF",
    "action": "submit",
    "url": "",
    "borderSize": "0",
    "borderRadius": "8",
    "borderColor": "#2E373E",
    "paddingTop": "10",
    "paddingRight": "20",
    "paddingBottom": "10",
    "paddingLeft": "20",
    "fontSize": "12",
    "textStyles": [],
    "alignment": "center",
    "fontFamily": "Arial",
    **ELEMENT_CONTAINER,
    "containerPaddingTop": "10",
    "containerPaddingRight": "0",
    "containerPaddingBottom": "10",
    "containerPaddingLeft": "0",
    "enableRecaptcha": False,
}

STRUCTURAL_KEYS = {
    "type",
    "isCanvas",
    "props",
    "displayName",
    "custom",
    "parent",
    "hidden",
    "nodes",
    "linkedNodes",
}


def _only(tree: dict, resolved_name: str) -> dict:
    (node,) = [n for n in tree.values() if n["type"]["resolvedName"] == resolved_name]
    return node


def _every_kind_tree() -> dict:
    return build_content_tree(
        [
            section(
                [
                    row(
                        [
                            cell([text_block("<p>t</p>")]),
                            cell([custom_field_block(LINKED_FIELD)]),
                        ]
                    ),
                    row([cell([custom_field_block(UNLINKED_FIELD)])]),
                    row([cell([html_block("<div>h</div>")])]),
                    row([cell([button_block("Send")])]),
                    row([cell([divider_block()])]),
                    row([cell([image_block("file-id", "https://x/i.png", "i.png")])]),
                ]
            )
        ]
    )


def test_form_nodes_carry_exactly_the_form_builder_creation_defaults():
    tree = _every_kind_tree()

    section_props = _only(tree, "Section")["props"]
    assert section_props == FORM_SECTION
    assert list(section_props) == list(FORM_SECTION)
    # RowContainer.tsx:27-33 after `columns` (Row.jsx:318-325), then the
    # panel's keys
    first_row = next(
        n
        for n in tree.values()
        if n["type"]["resolvedName"] == "Row" and len(n["props"]["columns"]) == 2
    )
    expected_row = {
        "columns": [0.5, 0.5],
        **ELEMENT_CONTAINER,
        **PADDING_10,
        **FORM_PANEL_SIZING,
    }
    assert first_row["props"] == expected_row
    assert list(first_row["props"]) == list(expected_row)
    # TextContainer.tsx:19-25
    assert _only(tree, "Text")["props"] == {**ELEMENT_CONTAINER, **PADDING_10}
    # CustomField/props.js:3-9, FormField/props.js:3-9
    for name in ("CustomField", "FormField"):
        props = dict(_only(tree, name)["props"])
        props.pop("field")
        assert props == {**ELEMENT_CONTAINER, **PADDING_10}
    # HTMLBlockContainer.tsx:15-22
    assert _only(tree, "HTMLBlock")["props"] == {
        **ELEMENT_CONTAINER,
        **PADDING_10,
        "htmlContent": "<div>h</div>",
    }
    # Button/props.ts:33-36: a submit button on a form page
    assert _only(tree, "Button")["props"] == {**FORM_BUTTON, "label": "Send"}
    # DividerContainer.tsx:15-22 + Divider/props.js:7-14
    assert _only(tree, "Divider")["props"] == {
        **ELEMENT_CONTAINER,
        "containerBorderRadius": False,
        **PADDING_10,
        "alignment": "center",
        "borderStyle": "solid",
        "color": "#A8B3BC",
        "size": "1",
        "width": "100",
    }
    # ImageContainer.tsx:14 + utils/image.ts:33-47, no natural size known
    assert _only(tree, "Image")["props"] == {
        **ELEMENT_CONTAINER,
        "src": "https://x/i.png",
        "name": "i.png",
        "fileId": "file-id",
        "size": "auto",
        "unit": "pixel",
        "height": None,
        "width": None,
        "display": "flex",
        "position": "center",
        "alt": "",
        "link": "",
    }


def test_form_nodes_keep_every_structural_key():
    for node in _every_kind_tree().values():
        assert set(node) == STRUCTURAL_KEYS
        assert node["displayName"] == node["type"]["resolvedName"]


def test_form_page_root_matches_a_new_form_page():
    # gen/defaults.ts:144-148 compiled with PageBuilder/utils/pages.js:11-14
    tree = build_content_tree([section([row([cell([text_block("x")])])])])
    assert tree["ROOT"]["props"] == {
        **GEN_CONTAINER,
        "backgroundColor": "#F8FAFF",
        "width": "100",
        "maxWidth": "900",
        "alignment": "center",
        "mobileBreak": "414",
        "color": "rgba(74,86,96,1)",
        "fontFamily": "Arial",
        "fontSize": "14",
        "linkColor": "rgba(82,142,249,1)",
        "lineHeight": "1.25",
        "tabletBreak": "768",
    }


def test_explicit_spec_values_win_over_defaults_including_falsy_ones():
    tree = build_content_tree(
        [
            section(
                [
                    row(
                        [
                            cell([button_block("Go", color="")]),
                            cell([divider_block("")]),
                        ],
                        columns=[0.7, 0.3],
                    ),
                    row(
                        [cell([image_block("f", "https://x/i.png", "i.png", width=0)])]
                    ),
                ],
                background_color="",
            )
        ]
    )
    assert _only(tree, "Section")["props"]["containerBackgroundColor"] == ""
    assert _only(tree, "Button")["props"]["color"] == ""
    assert _only(tree, "Divider")["props"]["color"] == ""
    image = _only(tree, "Image")["props"]
    assert (image["size"], image["width"], image["dimension"]) == (
        "dynamic",
        0,
        "width",
    )
    assert any(
        n["props"].get("columns") == [0.7, 0.3]
        for n in tree.values()
        if n["type"]["resolvedName"] == "Row"
    )


def test_explicit_colours_and_sizes_are_sent_as_given():
    tree = build_content_tree(
        [
            section(
                [
                    row([cell([button_block("Go", color="#123456")])]),
                    row([cell([divider_block("#654321")])]),
                    row(
                        [
                            cell(
                                [
                                    image_block(
                                        "f",
                                        "https://x/i.png",
                                        "i.png",
                                        width=200,
                                        natural_width=400,
                                        natural_height=100,
                                    )
                                ]
                            )
                        ]
                    ),
                ],
                background_color="#ABCDEF",
            )
        ]
    )
    assert _only(tree, "Section")["props"]["containerBackgroundColor"] == "#ABCDEF"
    assert _only(tree, "Button")["props"]["color"] == "#123456"
    assert _only(tree, "Divider")["props"]["color"] == "#654321"
    image = _only(tree, "Image")["props"]
    assert image["width"] == 200
    assert (image["naturalWidth"], image["naturalHeight"]) == (400, 100)


def test_url_button_opens_in_new_tab_only_where_the_ui_writes_it():
    # A form page's button starts as submit, and switching it to URL writes
    # openLinkInNewTab (ButtonSettings.jsx:261-263). A non-form page's button
    # starts as URL (Button/props.ts:38-42), so nothing is written.
    def button(is_form_page: bool, action: str) -> dict:
        p = page(
            "P",
            [
                section(
                    [row([cell([button_block("B", action=action, url="https://x")])])]
                )
            ],
            is_form_page=is_form_page,
        )
        return _only(json.loads(p["page_data"]), "Button")["props"]

    assert button(True, "url")["openLinkInNewTab"] is True
    assert "openLinkInNewTab" not in button(True, "submit")
    assert "openLinkInNewTab" not in button(False, "url")


def test_nodes_get_their_own_copy_of_mutable_defaults():
    tree = build_content_tree(
        [section([row([cell([button_block("A"), button_block("B")])])])]
    )
    first, second = (n for n in tree.values() if n["type"]["resolvedName"] == "Button")
    first["props"]["textStyles"].append("bold")
    assert second["props"]["textStyles"] == []
    assert FORM_PAGE_DEFAULTS.button["textStyles"] == []


def test_thank_you_page_text_uses_the_form_builder_text_defaults():
    parsed = json.loads(thank_you_page()["page_data"])
    assert _only(parsed, "Text")["props"] == {**ELEMENT_CONTAINER, **PADDING_10}


def test_section_and_row_carry_the_settings_panel_sizing_on_every_form_page():
    # The form and survey builders map Section/Row to the same settings panels
    # on form and non-form pages (presets/node-settings.js:37-45), and both
    # page kinds use the same Root maxWidth.
    for is_form_page in (True, False):
        p = page(
            "P", [section([row([cell([text_block("x")])])])], is_form_page=is_form_page
        )
        tree = json.loads(p["page_data"])
        assert _only(tree, "Section")["props"] == FORM_SECTION
        row_props = _only(tree, "Row")["props"]
        assert {k: row_props[k] for k in FORM_PANEL_SIZING} == FORM_PANEL_SIZING
        assert list(row_props)[-3:] == ["width", "maxWidth", "alignment"]
    ty = json.loads(thank_you_page()["page_data"])
    assert _only(ty, "Section")["props"] == FORM_SECTION


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

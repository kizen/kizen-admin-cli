"""Tests for the record-layout `custom_content` block builder.

Pins the shape confirmed live 2026-07-21 from a real layout authored
through the actual Kizen layout builder (see `kizen docs show reference`,
"Record Layout API" -> "custom_content blocks"):
  * the wrapper is {id, label, type: "custom_content", metadata: {blockJson}}
    with NO internalName/displayName keys (those are `fields`-block-specific);
  * blockJson is a NESTED OBJECT, not a JSON-encoded string (unlike a
    form/survey's page_data);
  * the tree's Root node carries no container*-prefixed props at all,
    unlike a form page's Root.
The builder is pure, so no live-API stubbing is needed.
"""

from __future__ import annotations

from kizen_builder.tools.form_ui import (
    build_content_tree,
    button_block,
    cell,
    divider_block,
    html_block,
    row,
    section,
    text_block,
)
from kizen_builder.tools.layouts import custom_content_block


def test_custom_content_block_wrapper_shape():
    block = custom_content_block([section([row([cell([text_block("<p>hi</p>")])])])])
    assert block["type"] == "custom_content"
    assert block["label"] == "Custom Content"
    assert "internalName" not in block
    assert "displayName" not in block
    assert isinstance(block["id"], str) and block["id"]
    assert isinstance(block["metadata"]["blockJson"], dict)


def test_custom_content_root_has_no_container_prefixed_props():
    block = custom_content_block([section([row([cell([text_block("<p>hi</p>")])])])])
    root_props = block["metadata"]["blockJson"]["ROOT"]["props"]
    assert not any(k.startswith("container") for k in root_props)
    assert root_props["backgroundColor"] == "rgba(255,255,255,1)"
    assert root_props["hasShadow"] is True


# Expected values transcribed from the Kizen web app source (react-app),
# 2026-11-11: the layout builder uses the homepage/static-block creators
# (PageBuilder/presets/creator-elements.js:59-67), not the form ones.
GEN_CONTAINER_NO_RADIUS_PADDED = {
    # gen/defaults.ts:25-50, 85-94, 178-187 (DefaultHomepageProps.Section/Row)
    "containerBackgroundColor": "rgba(0,0,0,0)",
    "containerBackgroundImageName": "",
    "containerBackgroundPositionX": "0%",
    "containerBackgroundPositionY": "0%",
    "containerBackgroundSize": "auto",
    "containerBackgroundRepeat": "repeat",
    "containerBorderColor": "rgba(74,86,96,1)",
    "containerBorderStyle": "solid",
    "containerBorderWidth": "0",
    "containerBorderRadius": False,
    "containerBorderTopLeftRadius": "4",
    "containerBorderTopRightRadius": "4",
    "containerBorderBottomLeftRadius": "4",
    "containerBorderBottomRightRadius": "4",
    "containerMarginTop": "0",
    "containerMarginRight": "0",
    "containerMarginBottom": "0",
    "containerMarginLeft": "0",
    "containerPaddingTop": "10",
    "containerPaddingRight": "10",
    "containerPaddingBottom": "10",
    "containerPaddingLeft": "10",
    "maxWidth": "1372",
    "width": "100",
    "alignment": "center",
}


def _tree(blocks: list) -> dict:
    block = custom_content_block([section([row([cell(blocks)])])])
    return block["metadata"]["blockJson"]


def _only(tree: dict, resolved_name: str) -> dict:
    (node,) = [n for n in tree.values() if n["type"]["resolvedName"] == resolved_name]
    return node


def test_custom_content_root_matches_a_new_static_block():
    # gen/defaults.ts:165-177, 190-195 (DefaultStaticBlockProps.Root)
    root_props = _tree([text_block("<p>hi</p>")])["ROOT"]["props"]
    assert root_props == {
        "backgroundColor": "rgba(255,255,255,1)",
        "color": "rgba(74,86,96,1)",
        "fontFamily": "Arial",
        "fontSize": "14",
        "linkColor": "rgba(82,142,249,1)",
        "lineHeight": "1.25",
        "height": 400,
        "maxWidth": 1372,
        "hasShadow": True,
        "tabletBreak": "768",
        "mobileBreak": "414",
    }


def test_custom_content_section_and_row_use_the_static_block_defaults_not_a_forms():
    tree = _tree([text_block("<p>hi</p>")])
    assert _only(tree, "Section")["props"] == GEN_CONTAINER_NO_RADIUS_PADDED
    assert _only(tree, "Row")["props"] == {
        "columns": [1.0],
        **GEN_CONTAINER_NO_RADIUS_PADDED,
    }


def test_custom_content_button_starts_from_the_static_page_button():
    # Button/props.ts:38-42: the default action is already URL, so a URL
    # button gets no openLinkInNewTab (unlike one switched to URL on a form).
    tree = _tree([button_block("Go", action="url", url="https://x")])
    props = _only(tree, "Button")["props"]
    assert props["color"] == "#4BC7B4"
    assert props["fontSize"] == "12"
    assert props["borderColor"] == "#2E373E"
    assert props["containerPaddingRight"] == "0"
    assert "openLinkInNewTab" not in props


def test_custom_content_leaf_blocks_share_the_form_builder_leaf_defaults():
    # The layout builder adds Text/Divider/Image/HTML with the same creators
    # as the form builder (creator-elements.js:37-67).
    blocks = [text_block("t"), divider_block(), html_block("<b>h</b>")]
    layout_tree = _tree(blocks)
    form_tree = build_content_tree([section([row([cell(blocks)])])])
    for name in ("Text", "Divider", "HTMLBlock"):
        assert _only(layout_tree, name)["props"] == _only(form_tree, name)["props"]


def test_custom_content_label_is_overridable():
    block = custom_content_block(
        [section([row([cell([text_block("<p>hi</p>")])])])], label="My Block"
    )
    assert block["label"] == "My Block"

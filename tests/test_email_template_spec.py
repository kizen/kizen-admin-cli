"""Tests for `models.spec.email_templates.EmailTemplateDef` and friends.

Pins the acceptance criteria the model alone is responsible for: the row
layout is a closed enum (an invalid preset is unrepresentable, not just a
validation error to catch after the fact), and `create`'s spec has no way to
smuggle a raw `craft_json`/`content` value past validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kizen_builder.models.spec.email_templates import (
    ButtonBlockDef,
    DividerBlockDef,
    EmailTemplateDef,
    ImageBlockDef,
    PaddingDef,
    RowDef,
    SectionDef,
)


def _spec(**overrides):
    base = {
        "name": "Newsletter",
        "subject": "Hello",
        "sections": [
            {
                "rows": [
                    {
                        "layout": "1 Column",
                        "cells": [{"blocks": [{"kind": "text", "html": "<p>hi</p>"}]}],
                    }
                ]
            }
        ],
    }
    base.update(overrides)
    return base


def test_minimal_valid_spec():
    spec = EmailTemplateDef.model_validate(_spec())
    assert spec.name == "Newsletter"
    assert spec.sections[0].rows[0].layout == "1 Column"


def test_subject_defaults_to_empty_string():
    spec = EmailTemplateDef.model_validate({"name": "t", "sections": []})
    assert spec.subject == ""


@pytest.mark.parametrize(
    "layout",
    [
        "1 Column",
        "2 Columns",
        "2 Columns (1/3 and 2/3)",
        "2 Columns (2/3 and 1/3)",
    ],
)
def test_v1_layout_names_are_accepted(layout):
    spec_dict = _spec()
    spec_dict["sections"][0]["rows"][0]["layout"] = layout
    EmailTemplateDef.model_validate(spec_dict)  # must not raise


@pytest.mark.parametrize(
    "layout", ["3 Columns", "4 Columns", "Two Columns", "50/50", "1column"]
)
def test_invalid_or_out_of_scope_layout_names_are_unrepresentable(layout):
    spec_dict = _spec()
    spec_dict["sections"][0]["rows"][0]["layout"] = layout
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(spec_dict)


@pytest.mark.parametrize("kind", ["text", "image", "button", "divider"])
def test_v1_block_kinds_are_accepted(kind):
    block = {
        "text": {"kind": "text", "html": "<p>hi</p>"},
        "image": {"kind": "image", "file": "/tmp/x.png"},
        "button": {"kind": "button", "label": "Go", "url": "https://x"},
        "divider": {"kind": "divider"},
    }[kind]
    spec_dict = _spec()
    spec_dict["sections"][0]["rows"][0]["cells"][0]["blocks"] = [block]
    EmailTemplateDef.model_validate(spec_dict)  # must not raise


@pytest.mark.parametrize("kind", ["attachments", "html", "custom_field", "video"])
def test_unsupported_block_kinds_are_unrepresentable(kind):
    spec_dict = _spec()
    spec_dict["sections"][0]["rows"][0]["cells"][0]["blocks"] = [{"kind": kind}]
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(spec_dict)


def test_no_raw_craft_json_key_anywhere_in_the_model():
    """The foot-gun this whole surface exists to close: create must not
    accept a hand-authored craft_json that can drift from `content`."""
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(_spec(craft_json={}))


def test_no_raw_content_key_anywhere_in_the_model():
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(_spec(content="<p>hand-authored</p>"))


def test_no_sender_type_key_in_the_model():
    """sender_type is hard-coded "business" by the planner — not a spec key."""
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(_spec(sender_type="business"))


def test_grep_confirms_neither_craft_json_nor_content_is_a_model_field():
    field_names = set(EmailTemplateDef.model_fields)
    assert "craft_json" not in field_names
    assert "content" not in field_names


# ---------------------------------------------------------------------------
# Layout props (BCLI-024) — defaults reproduce today's hardcoded emitter
# output; explicit values round-trip through the model unchanged.
# ---------------------------------------------------------------------------


def test_padding_def_defaults_to_uniform_10_on_all_four_sides():
    p = PaddingDef()
    assert (p.top, p.right, p.bottom, p.left) == ("10", "10", "10", "10")


def test_padding_def_sides_are_independently_settable():
    p = PaddingDef.model_validate(
        {"top": "10", "right": "40", "bottom": "10", "left": "40"}
    )
    assert (p.top, p.right, p.bottom, p.left) == ("10", "40", "10", "40")


def test_section_def_layout_defaults_reproduce_todays_hardcoded_emitter_output():
    """`max_width` defaults to `900` (today's `form_ui._assemble_section`
    hardcode), not the reference template's `600` — see this item's
    Implementation notes for why the acceptance criteria's literal default
    was corrected. `container_width`/`padding` default to `None`, meaning
    "no override", matching that `containerWidth` is absent and padding is
    the uniform `10` today."""
    s = SectionDef.model_validate({"rows": []})
    assert s.max_width == "900"
    assert s.container_width is None
    assert s.padding is None


def test_section_def_layout_props_are_independently_settable():
    s = SectionDef.model_validate(
        {
            "rows": [],
            "max_width": "600",
            "container_width": "900",
            "padding": {"top": "0", "right": "0", "bottom": "0", "left": "0"},
        }
    )
    assert s.max_width == "600"
    assert s.container_width == "900"
    assert s.padding == PaddingDef(top="0", right="0", bottom="0", left="0")


def test_row_def_layout_defaults_reproduce_todays_hardcoded_emitter_output():
    r = RowDef.model_validate({"cells": []})
    assert r.width == "100"
    assert r.container_width is None
    assert r.padding is None


def test_row_def_layout_props_are_independently_settable():
    """`Row.width`/`container_width`/`padding` are not derived from the
    parent `Section` — the reference template shows them varying
    row-to-row with no clean formula (BCLI-024 Context)."""
    r = RowDef.model_validate(
        {
            "cells": [],
            "width": "75",
            "container_width": "580",
            "padding": {"top": "10", "right": "40", "bottom": "10", "left": "40"},
        }
    )
    assert r.width == "75"
    assert r.container_width == "580"
    assert r.padding == PaddingDef(top="10", right="40", bottom="10", left="40")


def test_divider_block_def_size_defaults_to_todays_hardcoded_3():
    d = DividerBlockDef()
    assert d.size == "3"


def test_button_block_def_layout_props_default_to_todays_hardcoded_values():
    b = ButtonBlockDef(label="Go", url="https://x")
    assert (b.border_radius, b.padding_left, b.padding_right, b.alignment) == (
        "8",
        "20",
        "20",
        "center",
    )


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
def test_button_block_def_alignment_accepts_the_closed_enum(alignment):
    b = ButtonBlockDef(label="Go", url="https://x", alignment=alignment)
    assert b.alignment == alignment


def test_button_block_def_alignment_rejects_values_outside_the_closed_enum():
    with pytest.raises(ValidationError):
        ButtonBlockDef(label="Go", url="https://x", alignment="justify")


def test_image_block_def_layout_props_default_to_none_matching_absent_keys_today():
    img = ImageBlockDef(file="/tmp/x.png")
    assert img.container_width is None
    assert img.max_width is None
    assert img.max_height is None


def test_image_block_def_layout_props_are_independently_settable():
    img = ImageBlockDef(
        file="/tmp/x.png", container_width="580", max_width="300", max_height="200"
    )
    assert img.container_width == "580"
    assert img.max_width == "300"
    assert img.max_height == "200"

"""Tests for `models.spec.email_templates.EmailTemplateDef` and friends.

Pins the acceptance criteria the model alone is responsible for: the row
layout is a closed enum (an invalid preset is unrepresentable, not just a
validation error to catch after the fact), and `create`'s spec has no way to
smuggle a raw `craft_json`/`content` value past validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kizen_builder.models.spec.email_templates import EmailTemplateDef


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

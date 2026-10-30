"""Shared helpers in kizen_builder.utils."""

from __future__ import annotations

import pytest

from kizen_builder.utils import match_option

_OPTIONS = [
    {"id": "aaaaaaaa-0000-4000-8000-000000000001", "name": "Open", "code": "new"},
    {"id": "aaaaaaaa-0000-4000-8000-000000000002", "name": "New", "code": "fresh"},
    {"id": "aaaaaaaa-0000-4000-8000-000000000003", "name": "Closed", "code": None},
]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("aaaaaaaa-0000-4000-8000-000000000003", "Closed"),
        ("closed", "Closed"),
        ("OPEN", "Open"),
        ("fresh", "New"),
        # A name match wins over another option's code.
        ("new", "New"),
    ],
)
def test_match_option_finds(value, expected):
    match = match_option(_OPTIONS, value)
    assert match is not None and match["name"] == expected


@pytest.mark.parametrize("value", ["Nope", "", None, 3, {"id": "x"}])
def test_match_option_misses(value):
    assert match_option(_OPTIONS, value) is None


def test_match_option_without_options():
    assert match_option(None, "Open") is None

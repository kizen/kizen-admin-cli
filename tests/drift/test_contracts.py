"""Offline unit tests for the extraction helpers in ``contracts.py``.

Unlike the rest of ``tests/drift/``, this module never reaches a live
environment — it feeds ``_type_of``/``_shape`` synthetic, OpenAPI-shaped dicts
directly. No ``drift`` marker, no ``KIZEN_DRIFT_PROFILE``; runs in the default
``uv run pytest`` suite.

Covers the ``$ref``-to-bare-enum resolution added for the enum-values-in-the-
snapshot change: a ref to a named enum component embeds its values the same
way an inline enum already did; a ref to a real object schema is unaffected.

Also covers :func:`match_endpoint`, which finds a contract's endpoint by path
shape so a renamed path parameter still resolves.
"""

from __future__ import annotations

import pytest

from tests.drift.contracts import _shape, _type_of, diff, match_endpoint


def _components() -> dict:
    return {
        "StatusEnum": {
            "type": "string",
            "enum": ["open", "closed"],
            "description": "* `open` - Open\n* `closed` - Closed",
        },
        "WidgetRequest": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    }


def test_ref_to_bare_enum_embeds_its_values():
    node = {"$ref": "#/components/schemas/StatusEnum"}
    assert _type_of(node, _components()) == "StatusEnum{open,closed}"


def test_ref_to_object_schema_is_unaffected():
    node = {"$ref": "#/components/schemas/WidgetRequest"}
    assert _type_of(node, _components()) == "WidgetRequest"


def test_ref_resolution_is_opt_in_and_backward_compatible():
    """No ``components`` passed => today's behavior, byte-identical."""
    node = {"$ref": "#/components/schemas/StatusEnum"}
    assert _type_of(node) == "StatusEnum"


def test_ref_to_unknown_component_falls_back_to_name_only():
    node = {"$ref": "#/components/schemas/DoesNotExist"}
    assert _type_of(node, _components()) == "DoesNotExist"


def test_enum_component_with_extra_structural_key_is_not_bare():
    """A ref target that merely has an ``enum`` key but is also, say, an
    object with ``properties`` is not a bare enum — only the ref name is
    recorded, exactly as before this change."""
    components = {
        "NotABareEnum": {
            "type": "object",
            "enum": ["a", "b"],
            "properties": {"x": {"type": "string"}},
        },
    }
    node = {"$ref": "#/components/schemas/NotABareEnum"}
    assert _type_of(node, components) == "NotABareEnum"


def test_shape_threads_components_into_nested_properties():
    node = {
        "type": "object",
        "properties": {
            "status": {"$ref": "#/components/schemas/StatusEnum"},
            "widget": {"$ref": "#/components/schemas/WidgetRequest"},
        },
    }
    shape = _shape(node, _components())
    assert shape["properties"]["status"] == "StatusEnum{open,closed}"
    assert shape["properties"]["widget"] == "WidgetRequest"


def test_inline_enum_formatting_is_unchanged():
    """The pre-existing inline-enum branch (no ``$ref``) — untouched by this
    change, asserted so a future edit to the ref branch can't silently also
    change this one."""
    node = {"type": "string", "enum": ["a", "b"]}
    assert _type_of(node, _components()) == "string{a,b}"


def _paths() -> dict:
    return {
        "/api/things/{thing_identifier}/parts/{id}": {
            "patch": {"operationId": "parts_partial_update"},
        },
        "/api/things/{thing_pk}/widgets": {"post": {"operationId": "widgets_create"}},
    }


def test_renamed_placeholder_resolves_by_shape():
    match = match_endpoint(_paths(), "/api/things/{thing_pk}/parts/{part_pk}", "patch")
    assert match == (
        "/api/things/{thing_identifier}/parts/{id}",
        {"operationId": "parts_partial_update"},
    )


def test_changed_literal_segment_does_not_resolve():
    assert (
        match_endpoint(_paths(), "/api/things/{thing_pk}/pieces/{id}", "patch") is None
    )


def test_method_absent_at_the_matched_path_does_not_resolve():
    assert match_endpoint(_paths(), "/api/things/{x}/parts/{id}", "delete") is None


def test_exact_hit_wins_over_a_shape_match():
    paths = _paths() | {
        "/api/things/{thing_pk}/parts/{id}": {"patch": {"operationId": "exact"}},
    }
    match = match_endpoint(paths, "/api/things/{thing_pk}/parts/{id}", "patch")
    assert match == ("/api/things/{thing_pk}/parts/{id}", {"operationId": "exact"})


def test_two_same_shape_paths_with_the_method_raise():
    paths = {
        "/api/plugin-apps/{identifier}": {"delete": {}},
        "/api/plugin-apps/{id}": {"delete": {}},
    }
    with pytest.raises(ValueError) as exc:
        match_endpoint(paths, "/api/plugin-apps/{app_pk}", "delete")
    message = str(exc.value)
    assert "DELETE /api/plugin-apps/{app_pk}" in message
    assert "/api/plugin-apps/{identifier}" in message
    assert "/api/plugin-apps/{id}" in message


def test_diff_ignores_schema_path():
    old = {"DELETE /x/{id}": {"present": True, "schema_path": "/x/{id}"}}
    new = {"DELETE /x/{id}": {"present": True, "schema_path": "/x/{pk}"}}
    assert not diff(old, new)

"""Builders for a form/survey's ``form_ui`` — the visual, submittable page layout.

``form_ui`` is a separate concern from field *schema* (``forms fields ...``):
a form/survey can have valid fields and still render as an empty "drag and
drop" page in the Kizen UI until ``form_ui`` is set. This module builds that
structure from friendly Python calls instead of hand-authoring the opaque
craft.js-style JSON.

Confirmed live 2026-07-21 against a real "Activity Submission" form — a
*real, UI-built* form with live submissions, not a guess reverse-engineered
from a single GET. Key structural facts, all confirmed from that live
example:

- ``form_ui`` is ``{"pages": [...], "business_merge_fields": [...]}``. Each
  page wrapper is **snake_case** (`page_name`, `is_form_page`, `is_hideable`,
  `is_deletable`, `hidden`, `id`) but its `page_data` value is a **JSON-encoded
  string** (not a nested object) whose *contents* are entirely **camelCase**
  craft.js keys — a different casing convention from the page wrapper, and
  from the snake_case ``content`` dict dashboard static-content dashlets use
  (``tools/dashboards.py::html_dashlet_config``). Mixing these up is exactly
  what broke earlier reverse-engineering attempts.
- A real form has at least two pages: one ``is_form_page=True`` page (the
  submittable form; `is_hideable=True`, `is_deletable=False`) and one
  ``is_form_page=False`` "Thank You Page" (`is_hideable=False`,
  `is_deletable=False`) shown post-submit.
- Inside `page_data`, the node graph is ``Root`` (props keyed by
  ``containerBorderRadius`` etc; children in ``nodes``) → ``Section``
  (children in ``nodes``) → ``Row`` (``props.columns`` is a list of
  fractional widths; children referenced via ``linkedNodes`` as
  ``{"column-N": cellId}``, own ``nodes`` stays ``[]``) → ``Cell``
  (``isCanvas: true``, ``props: {}``, children in ``nodes``) → a leaf block:
  ``Text``, ``CustomField``, ``FormField``, ``Button``, ``Divider``, or
  ``Image``.
- **A form-field input block is one of TWO different node types depending on
  whether the field is linked to the related object** — confirmed live
  2026-07-21 the hard way (a real `TypeError: undefined is not an object
  (evaluating 'f.customObjectField.id')` builder crash). A field with a
  linked `custom_object_field` (the common case — every field surfaced from
  a form's `related_object` carries this backlink) uses ``CustomField``,
  whose `props.field` embeds the form field's own data (id, name/api_name,
  required/hidden/etc, order, meta, properties, options — deep-camelCased
  from its own GET/list response) but **borrows `displayName`/`fieldType`
  from the linked `customObjectField`**, and sets `labelText` to the form
  field's own `display_name` (the editable on-page label, distinct from
  schema `displayName`). See :func:`custom_field_prop`. A field with **no**
  linked custom-object field (created directly on the form) uses
  ``FormField`` instead, whose `props.field` has an explicit
  `customObjectField: null`, a slimmer `access` (`{"edit", "view"}` only, no
  `remove`), and no `labelText` at all. See :func:`form_field_prop`. The
  block assembler in this module picks the right one automatically based on
  whether `form_field["custom_object_field"]` is set — always go through
  :func:`custom_field_block`, never construct these by hand.

Usage pattern::

    from kizen_builder.tools.form_ui import (
        text_block, custom_field_block, button_block, cell, row, section,
        page, thank_you_page, build_form_ui, simple_form_page,
    )
    from kizen_builder.tools.forms import FORMS_BASE_PATH
    from kizen_builder.api.forms import list_form_fields
    from kizen_builder.api.client import KizenClient
    from kizen_builder.config import load_env_config

    config = load_env_config()
    with KizenClient(config) as client:
        fields = list_form_fields(client, FORMS_BASE_PATH, form_id)

    form_ui = build_form_ui([
        simple_form_page(fields, heading="Contact Us"),
        thank_you_page(),
    ])
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# snake_case -> camelCase
# ---------------------------------------------------------------------------


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest if w)


def _deep_camel(value: Any) -> Any:
    if isinstance(value, dict):
        return {_camel(k): _deep_camel(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_camel(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# CustomField.props.field
# ---------------------------------------------------------------------------

_DEFAULT_ACCESS: dict[str, bool] = {"view": True, "edit": True, "remove": False}

_PLACEHOLDER_BY_TYPE: dict[str, str] = {
    "text": "Enter Text",
    "longtext": "Enter Text",
    "wysiwyg": "Enter Text",
    "email": "Email@Domain.com",
    "decimal": "0.00",
    "integer": "0",
    "money": "0.00",
    "date": "MM/DD/YYYY",
    "datetime": "MM/DD/YYYY hh:mm AM/PM",
    "phonenumber": "Phone Number",
    "dropdown": "Choose Option",
    "radio": "Choose Option",
    "choices": "Choose Option",
    "selector": "Choose Option",
    "status": "Choose Option",
    "checkboxes": "Choose Option",
    "dynamictags": "Choose Option",
    "yesnomaybe": "Choose Option",
    "relationship": "Choose Option",
    "team_selector": "Choose Option",
    "timezone": "Choose Option",
}


def custom_field_prop(form_field: dict[str, Any]) -> dict[str, Any]:
    """Build the deep-camelCased ``field`` prop for a page ``CustomField`` block.

    ``form_field`` is one raw (snake_case) item as returned by
    ``kizen_builder.api.forms.list_form_fields``, with its ``custom_object_field``
    key (if any) *enriched* to the full custom-object field record — see
    ``tools.forms._enrich_custom_object_fields``. Passing the skinny 6-key
    stub ``list_form_fields`` returns natively (id/name/display_name/
    field_type/is_default/custom_object only) renders fine on the public
    submit page but breaks the Kizen page-builder when reopening the form
    for editing (confirmed live 2026-07-21) — always enrich first.
    """
    cof = form_field.get("custom_object_field")
    own = _deep_camel(
        {k: v for k, v in form_field.items() if k != "custom_object_field"}
    )

    if cof:
        camel_cof = _deep_camel(cof)
        camel_cof.setdefault("access", dict(_DEFAULT_ACCESS))
        camel_cof.setdefault("uiDefaultValue", None)
        camel_cof.setdefault("allowOnForms", True)
        own["customObjectField"] = camel_cof
        display_name = cof.get("display_name") or form_field.get("display_name")
        field_type = cof.get("field_type") or form_field.get("field_type")
    else:
        display_name = form_field.get("display_name")
        field_type = form_field.get("field_type")

    own["displayName"] = display_name
    own["fieldType"] = field_type
    own["access"] = dict(_DEFAULT_ACCESS)
    own["placeholder"] = _PLACEHOLDER_BY_TYPE.get(field_type or "", "Enter Text")
    own["labelText"] = form_field.get("display_name")
    own["isNew"] = False
    return own


def form_field_prop(form_field: dict[str, Any]) -> dict[str, Any]:
    """Build the ``field`` prop for a page ``FormField`` block — the
    counterpart to :func:`custom_field_prop` for a field with **no** linked
    custom-object field (``custom_object_field`` is ``None`` — a field
    created directly on the form, not mirrored from its ``related_object``).

    Confirmed live 2026-07-21 from a real save: the user added a plain
    ``yesnomaybe`` field through the actual Kizen builder and it serialized
    as a *different node type* (``FormField``, not ``CustomField``) with a
    slimmer ``access`` (``{"edit": true, "view": true}`` — no ``remove``),
    an explicit ``customObjectField: null`` (not omitted), and **no**
    ``labelText`` key at all (unlike ``CustomField``, which mirrors its own
    ``display_name`` there). Using the ``CustomField`` node type for an
    unlinked field is the bug that broke the builder in the first place:
    its renderer unconditionally reads ``field.customObjectField.id``,
    which throws when that key is missing (``TypeError: undefined is not
    an object (evaluating 'f.customObjectField.id')`` — confirmed live).
    The ``FormField`` renderer apparently never touches that path.
    """
    own = _deep_camel(
        {k: v for k, v in form_field.items() if k != "custom_object_field"}
    )
    own["customObjectField"] = None
    own["access"] = {"edit": True, "view": True}
    own["placeholder"] = _PLACEHOLDER_BY_TYPE.get(
        form_field.get("field_type") or "", "Enter Text"
    )
    own["isNew"] = False
    return own


# ---------------------------------------------------------------------------
# Container style defaults (camelCase — distinct from dashboards.py's
# snake_case _CONTAINER_DEFAULTS, same idea)
# ---------------------------------------------------------------------------

_CONTAINER_DEFAULTS: dict[str, Any] = {
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
}

_ROOT_PROPS: dict[str, Any] = {
    **_CONTAINER_DEFAULTS,
    "containerBorderRadius": True,
    "containerPaddingTop": "0",
    "containerPaddingRight": "0",
    "containerPaddingBottom": "0",
    "containerPaddingLeft": "0",
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


def _new_id() -> str:
    """A 24-hex-char node id, matching the id style seen in every live example."""
    return uuid.uuid4().hex[:24]


# ---------------------------------------------------------------------------
# Block specs (friendly builders — dicts consumed by the tree assembler)
# ---------------------------------------------------------------------------


def text_block(html: str) -> dict[str, Any]:
    """A rich-text block (WYSIWYG-authored). ``html`` is raw HTML; content
    lives in ``custom.text`` on the wire."""
    return {"kind": "text", "html": html}


def html_block(html: str) -> dict[str, Any]:
    """A raw-HTML block — a *different* node type from :func:`text_block`
    (``HTMLBlock``, content in ``props.htmlContent``, not ``custom.text``).
    Confirmed live 2026-07-21 from a real save: the user added one through
    the actual Kizen builder (a ``<div style="height:10px;"></div>`` spacer)
    and it serialized with a distinct ``resolvedName`` and prop key from the
    rich-text ``Text`` block. Useful for markup a WYSIWYG editor won't
    produce (custom CSS, tables, etc.) — ``no <script> tags`` per the live
    example, though script-stripping isn't independently confirmed here."""
    return {"kind": "html", "html": html}


def custom_field_block(form_field: dict[str, Any]) -> dict[str, Any]:
    """A form-field input block. ``form_field`` is a raw item from
    ``list_form_fields`` (enriched — see ``tools.forms._enrich_custom_object_fields``).
    Renders as a ``CustomField`` or ``FormField`` node depending on whether
    the field has a linked custom-object field — see
    :func:`custom_field_prop`/:func:`form_field_prop`."""
    return {"kind": "custom_field", "field": form_field}


def button_block(
    label: str = "Submit",
    *,
    action: str = "submit",
    url: str = "",
    color: str | None = None,
) -> dict[str, Any]:
    """A button block. ``action`` is ``"submit"`` (the form's submit button,
    confirmed live) or ``"url"`` (an external link, by analogy with the
    dashboard static-content Button block)."""
    return {
        "kind": "button",
        "label": label,
        "action": action,
        "url": url,
        "color": color,
    }


def divider_block(color: str | None = None) -> dict[str, Any]:
    return {"kind": "divider", "color": color}


def image_block(
    file_id: str,
    src: str,
    name: str,
    *,
    width: int | None = None,
    natural_width: int | None = None,
    natural_height: int | None = None,
) -> dict[str, Any]:
    """An image block. ``file_id``/``src`` come from a prior file upload —
    not wired into this CLI slice yet, so this is only usable with a file
    already uploaded some other way (e.g. copied from a live example)."""
    return {
        "kind": "image",
        "file_id": file_id,
        "src": src,
        "name": name,
        "width": width,
        "natural_width": natural_width,
        "natural_height": natural_height,
    }


def cell(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """One cell within a row, containing one or more stacked blocks."""
    return {"blocks": blocks}


def row(
    cells: list[dict[str, Any]], columns: list[float] | None = None
) -> dict[str, Any]:
    """One row, split into ``len(cells)`` equal-width columns unless
    ``columns`` (fractions summing to 1) is given explicitly."""
    return {"cells": cells, "columns": columns}


def section(
    rows: list[dict[str, Any]], *, background_color: str = "#FFFFFF"
) -> dict[str, Any]:
    """One section (a bordered container of rows)."""
    return {"rows": rows, "background_color": background_color}


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _assemble_block(
    block: dict[str, Any], parent_id: str, content: dict[str, Any]
) -> str:
    kind = block["kind"]
    node_id = _new_id()

    if kind == "text":
        node = {
            "type": {"resolvedName": "Text"},
            "isCanvas": False,
            "props": dict(_CONTAINER_DEFAULTS),
            "displayName": "Text",
            "custom": {"text": block["html"]},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    elif kind == "custom_field":
        # Linked (mirrors a field on the form's related_object) vs. unlinked
        # (form-only) fields are two DIFFERENT node types — see
        # custom_field_prop()/form_field_prop() docstrings. Using the wrong
        # one for an unlinked field crashes the Kizen builder (confirmed live).
        is_linked = bool(block["field"].get("custom_object_field"))
        resolved_name = "CustomField" if is_linked else "FormField"
        field_prop = (
            custom_field_prop(block["field"])
            if is_linked
            else form_field_prop(block["field"])
        )
        node = {
            "type": {"resolvedName": resolved_name},
            "isCanvas": False,
            "props": {**_CONTAINER_DEFAULTS, "field": field_prop},
            "displayName": resolved_name,
            "custom": {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    elif kind == "button":
        is_submit = block["action"] == "submit"
        node = {
            "type": {"resolvedName": "Button"},
            "isCanvas": False,
            "props": {
                **_CONTAINER_DEFAULTS,
                "color": block.get("color") or "rgba(0,51,160,1)",
                "label": block["label"],
                "textColor": "rgba(255,255,255,1)",
                "action": block["action"],
                "url": block.get("url", ""),
                "borderSize": "0",
                "borderRadius": "8",
                "borderColor": "rgba(0,0,0,1)",
                "paddingTop": "10",
                "paddingRight": "20",
                "paddingBottom": "10",
                "paddingLeft": "20",
                "fontSize": "16",
                "textStyles": [],
                "alignment": "center",
                "fontFamily": "Arial",
                "openLinkInNewTab": True,
                "enableRecaptcha": False,
            },
            "displayName": "Button",
            "custom": {"isSubmitButton": is_submit} if is_submit else {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    elif kind == "html":
        node = {
            "type": {"resolvedName": "HTMLBlock"},
            "isCanvas": False,
            "props": {**_CONTAINER_DEFAULTS, "htmlContent": block["html"]},
            "displayName": "HTMLBlock",
            "custom": {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    elif kind == "divider":
        node = {
            "type": {"resolvedName": "Divider"},
            "isCanvas": False,
            "props": {
                **_CONTAINER_DEFAULTS,
                "alignment": "center",
                "borderStyle": "solid",
                "color": block.get("color") or "rgba(78,193,145,1)",
                "size": "3",
                "width": "100",
            },
            "displayName": "Divider",
            "custom": {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    elif kind == "image":
        node = {
            "type": {"resolvedName": "Image"},
            "isCanvas": False,
            "props": {
                **_CONTAINER_DEFAULTS,
                "size": "dynamic",
                "unit": "pixel",
                "height": None,
                "width": block.get("width") or 150,
                "display": "flex",
                "position": "center",
                "alt": "",
                "link": "",
                "src": block["src"],
                "name": block["name"],
                "fileId": block["file_id"],
                "naturalHeight": block.get("natural_height"),
                "naturalWidth": block.get("natural_width"),
                "dimension": "width",
            },
            "displayName": "Image",
            "custom": {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    else:
        raise ValueError(f"unknown form-page block kind: {kind!r}")

    content[node_id] = node
    return node_id


def _assemble_cell(
    cell_spec: dict[str, Any],
    parent_id: str,
    content: dict[str, Any],
    *,
    width: float | None = None,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str] | None = None,
) -> str:
    """``cell_props``/``block_assembler`` are additive hooks: both default to
    today's behaviour (``props: {}``, this module's own block dispatch), so
    forms/layouts callers that don't pass them are unaffected. A surface with
    a different ``Cell.props`` shape (email's ``{"__width": <fraction>}`` —
    see ``tools.email_craft``) or different leaf-block prop shapes passes its
    own callables instead of forking this function."""
    assemble = block_assembler or _assemble_block
    cell_id = _new_id()
    block_ids = [assemble(b, cell_id, content) for b in cell_spec["blocks"]]
    content[cell_id] = {
        "type": {"resolvedName": "Cell"},
        "isCanvas": True,
        "props": cell_props(width) if cell_props else {},
        "displayName": "Cell",
        "custom": {},
        "parent": parent_id,
        "hidden": False,
        "nodes": block_ids,
        "linkedNodes": {},
    }
    return cell_id


def _assemble_row(
    row_spec: dict[str, Any],
    parent_id: str,
    content: dict[str, Any],
    *,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str] | None = None,
) -> str:
    row_id = _new_id()
    cells = row_spec["cells"]
    n = len(cells) or 1
    columns = row_spec.get("columns") or [1.0 / n] * n
    cell_ids = [
        _assemble_cell(
            c,
            row_id,
            content,
            width=columns[i] if i < len(columns) else None,
            cell_props=cell_props,
            block_assembler=block_assembler,
        )
        for i, c in enumerate(cells)
    ]
    content[row_id] = {
        "type": {"resolvedName": "Row"},
        "isCanvas": False,
        "props": {
            "columns": columns,
            **_CONTAINER_DEFAULTS,
            "maxWidth": "900",
            "width": "100",
            "alignment": "center",
        },
        "displayName": "Row",
        "custom": {},
        "parent": parent_id,
        "hidden": False,
        "nodes": [],
        "linkedNodes": {f"column-{i + 1}": cid for i, cid in enumerate(cell_ids)},
    }
    return row_id


def _assemble_section(
    section_spec: dict[str, Any],
    parent_id: str,
    content: dict[str, Any],
    *,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str] | None = None,
) -> str:
    section_id = _new_id()
    row_ids = [
        _assemble_row(
            r,
            section_id,
            content,
            cell_props=cell_props,
            block_assembler=block_assembler,
        )
        for r in section_spec["rows"]
    ]
    content[section_id] = {
        "type": {"resolvedName": "Section"},
        "isCanvas": True,
        "props": {
            **_CONTAINER_DEFAULTS,
            "containerBackgroundColor": section_spec.get("background_color", "#FFFFFF"),
            "maxWidth": "900",
            "width": "100",
            "alignment": "center",
        },
        "displayName": "Section",
        "custom": {},
        "parent": parent_id,
        "hidden": False,
        "nodes": row_ids,
        "linkedNodes": {},
    }
    return section_id


def build_content_tree(
    sections: list[dict[str, Any]],
    *,
    root_props: dict[str, Any] | None = None,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Assemble a ``Root`` → ``Section`` → ``Row`` → ``Cell`` → block
    camelCase craft.js tree from :func:`section`/:func:`row`/:func:`cell`/
    ``*_block()`` specs, returning the raw content dict.

    Callers serialize as their surface requires — a form/survey's
    ``page_data`` is a **JSON-encoded string** of this dict (see
    :func:`page`), while a record layout's ``custom_content`` block embeds
    it as a **nested object** directly (``metadata.blockJson`` — confirmed
    live 2026-07-21; see ``tools/layouts.py::custom_content_block``).
    ``root_props`` defaults to the forms/surveys Root shape (which carries
    ``container*``-prefixed keys); pass the record-layout Root shape
    instead for a ``custom_content`` block — its Root has NO
    ``container*``-prefixed props at all, just ``backgroundColor``/
    ``color``/``fontFamily``/``fontSize``/``linkColor``/``lineHeight``/
    ``height``/``maxWidth``/``hasShadow``/``tabletBreak``/``mobileBreak``
    (matching the dashboard static-content dashlet's Root shape, just
    camelCased instead of snake_case).

    ``cell_props``/``block_assembler`` are additive hooks for a surface whose
    ``Cell.props`` or leaf-block prop shapes differ from this module's forms
    defaults — see ``tools.email_craft``, which needs ``Cell.props`` to carry
    ``{"__width": <fraction>}`` and its own Button/Divider prop construction.
    Both default to ``None``, which reproduces today's exact output — the
    call sites in this file and in ``tools/layouts.py`` are unaffected.
    """
    content: dict[str, Any] = {}
    section_ids = [
        _assemble_section(
            s, "ROOT", content, cell_props=cell_props, block_assembler=block_assembler
        )
        for s in sections
    ]
    content["ROOT"] = {
        "type": {"resolvedName": "Root"},
        "isCanvas": True,
        "props": dict(root_props if root_props is not None else _ROOT_PROPS),
        "displayName": "Root",
        "custom": {},
        "parent": None,
        "hidden": False,
        "nodes": section_ids,
        "linkedNodes": {},
    }
    return content


def _build_page_data(sections: list[dict[str, Any]]) -> str:
    return json.dumps(build_content_tree(sections))


# ---------------------------------------------------------------------------
# Pages / top-level form_ui
# ---------------------------------------------------------------------------


def page(
    name: str,
    sections: list[dict[str, Any]],
    *,
    is_form_page: bool = True,
    hidden: bool = False,
    hideable: bool | None = None,
    deletable: bool = False,
    page_id: str | None = None,
) -> dict[str, Any]:
    """One page of ``form_ui.pages``.

    Defaults for ``hideable``/``deletable`` match the live example: the form
    page itself is hideable but not deletable; a non-form (e.g. thank-you)
    page defaults to neither.
    """
    if hideable is None:
        hideable = bool(is_form_page)
    return {
        "id": page_id or _new_id(),
        "hidden": hidden,
        "page_data": _build_page_data(sections),
        "page_name": name,
        "is_hideable": hideable,
        "is_deletable": deletable,
        "is_form_page": is_form_page,
    }


def thank_you_page(
    message_html: str | None = None, *, name: str = "Thank You Page"
) -> dict[str, Any]:
    """The conventional second page shown after a successful submission —
    every live example has exactly one of these. Not itself a form page."""
    html = message_html or (
        '<p style="line-height: 1.25; text-align: center" '
        'data-line-height="default"><span style="font-size: 20px">'
        "Thank you for submitting the form.</span></p>"
    )
    return page(
        name,
        [section([row([cell([text_block(html)])])])],
        is_form_page=False,
        hideable=False,
        deletable=False,
    )


def build_form_ui(
    pages: list[dict[str, Any]], business_merge_fields: list[Any] | None = None
) -> dict[str, Any]:
    """Assemble the full ``form_ui`` value from a list of :func:`page` dicts."""
    return {"pages": pages, "business_merge_fields": business_merge_fields or []}


# ---------------------------------------------------------------------------
# Convenience: a plain one-field-per-row form page
# ---------------------------------------------------------------------------


def simple_form_page(
    form_fields: list[dict[str, Any]],
    *,
    heading: str | None = None,
    subheading: str | None = None,
    submit_label: str = "Submit",
    name: str = "Form Page",
) -> dict[str, Any]:
    """A straightforward form page: optional heading text, one field per row
    (in the given order), then a centered submit button.

    ``form_fields`` are raw items from ``list_form_fields`` — pass them in
    the order they should appear. For a denser layout, build the page with
    :func:`page`/:func:`section`/:func:`row`/:func:`cell` directly instead.
    """
    rows: list[dict[str, Any]] = []
    if heading:
        rows.append(row([cell([text_block(f"<p><strong>{heading}</strong></p>")])]))
    if subheading:
        rows.append(row([cell([text_block(f"<p>{subheading}</p>")])]))
    for f in form_fields:
        rows.append(row([cell([custom_field_block(f)])]))
    rows.append(row([cell([button_block(submit_label, action="submit")])]))
    return page(name, [section(rows)], is_form_page=True)

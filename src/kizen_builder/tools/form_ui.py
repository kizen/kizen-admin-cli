"""Builders for a form/survey's ``form_ui`` — the visual, submittable page layout.

``form_ui`` is a separate concern from field *schema* (``forms fields ...``):
a form/survey can have valid fields and still render as an empty "drag and
drop" page in the Kizen UI until ``form_ui`` is set. This module builds that
structure from friendly Python calls instead of hand-authoring the opaque
craft.js-style JSON.

Confirmed live 2026-07-21 against a real "Activity Submission" form — a
*real, UI-built* form with live submissions, not a guess reverse-engineered
from a single GET. That form had been styled by hand, so node style props
are not copied from it: they are the builder's creation defaults (see
:class:`BuilderDefaults`). Key structural facts, all confirmed from that live
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

import copy
import functools
import json
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
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
# Node defaults: the props the Kizen page builder writes when a user adds each
# node, per builder. Read from the Kizen web app source (react-app), 2026-11-11.
# Paths below are relative to that repo. KDS colour tokens are resolved through
# packages/kds/src/theme.ts: teal-300 #4BC7B4 (:10), gray-50 #FFFFFF (:61),
# gray-600 #A8B3BC (:67), gray-800 #4A5660 (:69), gray-900 #2E373E (:70),
# aliceblue-25 #F8FAFF (:128).
#
# A tray drop runs apps/react-app/src/components/PageBuilder/nodes/
# useAddNewNode.jsx:18-33, which saves exactly the props the node's Create*
# component passes; craft.js adds only a component's `craft.props`/
# `craft.defaultProps`, and Row (`columns`) is the only node with any
# (nodes/Row/Row.jsx:318-325). Which Create* each builder uses is set in
# PageBuilder/presets/creator-elements.js:37-67.
#
# Keys the builder writes later from a measured size (`containerWidth` on
# Section, Row and Image: nodes/Section/Section.jsx:41-48, nodes/Row/Row.jsx:
# 233-240, nodes/Image/Image.jsx:77-84) are not sent; the builder adds them
# when the page is next opened.
# ---------------------------------------------------------------------------


def _padding(top: str, right: str, bottom: str, left: str) -> dict[str, str]:
    return {
        "containerPaddingTop": top,
        "containerPaddingRight": right,
        "containerPaddingBottom": bottom,
        "containerPaddingLeft": left,
    }


_PADDING_10 = _padding("10", "10", "10", "10")

# packages/page-builder/nodes/containers/ElementContainer.tsx:14-37
# (DEFAULT_ELEMENT_CONTAINER_PROPS): the base of every node the tray adds.
_ELEMENT_CONTAINER: dict[str, Any] = {
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
    **_padding("0", "0", "0", "0"),
}

# packages/page-builder/gen/defaults.ts:25-50 (DefaultContainerProps): the
# base of the compile-time defaults behind a page's Root and the homepage /
# static-block Section and Row. Its two `undefined` keys never reach the JSON.
_GEN_CONTAINER: dict[str, Any] = {
    "containerBackgroundColor": "rgba(0,0,0,0)",
    "containerBackgroundImageName": "",
    "containerBackgroundPositionX": "0%",
    "containerBackgroundPositionY": "0%",
    "containerBackgroundSize": "auto",
    "containerBackgroundRepeat": "repeat",
    "containerBorderColor": "rgba(74,86,96,1)",
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
    **_padding("0", "0", "0", "0"),
}

# Root text settings shared by every Root: gen/defaults.ts:61-72
# (CommonRootProps) and :165-177 (DefaultHomepageProps.Root). Root.jsx's own
# DEFAULT_FORM_ROOT_PROPS (linkColor blue-500) applies only to a page saved
# with no data at all (PageBuilderCanvas.jsx:190), never on creation.
_ROOT_TEXT: dict[str, Any] = {
    "color": "rgba(74,86,96,1)",
    "fontFamily": "Arial",
    "fontSize": "14",
    "linkColor": "rgba(82,142,249,1)",
    "lineHeight": "1.25",
}

# A form/survey page's Root: gen/defaults.ts:144-148 (DefaultFormProps.Root)
# compiled with the backgroundColor/maxWidth the form builder passes for a
# new page (PageBuilder/utils/pages.js:11-33, createNewPage) and a new form
# (packages/page-builder/utils/build.ts:49,57,153-185).
_FORM_ROOT: dict[str, Any] = {
    **_GEN_CONTAINER,
    "backgroundColor": "#F8FAFF",
    "width": "100",
    "maxWidth": "900",
    "alignment": "center",
    "mobileBreak": "414",
    **_ROOT_TEXT,
    "tabletBreak": "768",
}

# A homepage/dashboard static-content block's Root: gen/defaults.ts:165-177
# (DefaultHomepageProps.Root), compiled by build.ts:253-267
# (getDefaultHomepageTemplate) when the block is created.
_HOMEPAGE_ROOT: dict[str, Any] = {
    "backgroundColor": "rgba(255,255,255,1)",
    **_ROOT_TEXT,
    "height": 335,
    "maxWidth": 1372,
    "hasShadow": True,
    "tabletBreak": "768",
    "mobileBreak": "414",
}

# A record layout's custom content block Root: gen/defaults.ts:190-195
# (DefaultStaticBlockProps.Root), compiled by build.ts:269-313
# (getDefaultStaticBlockContent, StaticBlockBuilder.tsx:120).
_STATIC_BLOCK_ROOT: dict[str, Any] = {**_HOMEPAGE_ROOT, "height": 400}


def _settings_sizing(root: Mapping[str, Any]) -> dict[str, Any]:
    """The keys a Section or Row settings panel writes when they're absent.

    Adding a node makes it the active node (nodes/useAddNewNode.jsx:35),
    which opens its settings (builder/PageBuilderTray.jsx:115-118). The
    Section and Row panels then write width, maxWidth and alignment, in that
    order, after the creation props (settings/SectionSettings.jsx:31-40,
    settings/RowSettings.jsx:30-39, through settings/useCraftProps.js), with
    maxWidth taken from the Root (nodes/Section/SectionSettings.jsx:18,38,
    nodes/Row/RowSettings.jsx:14,33).
    """
    return {"width": "100", "maxWidth": root["maxWidth"], "alignment": "center"}


# Form and survey builder Section, on every page: SectionContainer.tsx:27-34
# via Section/props.js:11 (CreateSection.js:27-29), then the settings panel
# (presets/node-settings.js:37-45, FormSectionSettings on form and non-form
# pages alike).
_FORM_SECTION: dict[str, Any] = {
    **_ELEMENT_CONTAINER,
    "containerBackgroundColor": "inherit",
    **_PADDING_10,
    **_settings_sizing(_FORM_ROOT),
}

# Form and survey builder Row: RowContainer.tsx:27-33 via Row/props.js:11
# (CreateRow.jsx:39-41), then FormRowSettings the same way.
_FORM_ROW: dict[str, Any] = {
    **_ELEMENT_CONTAINER,
    **_PADDING_10,
    **_settings_sizing(_FORM_ROOT),
}

# Homepage and static-block Section/Row: gen/defaults.ts:74-94 (CommonRowProps,
# CommonSectionProps) overridden at :178-187 (DefaultHomepageProps), passed by
# CreateSection.js:31-33 and CreateRow.jsx:43-45.
_HOMEPAGE_SECTION: dict[str, Any] = {
    **_GEN_CONTAINER,
    "containerBorderRadius": False,
    **_PADDING_10,
    "maxWidth": "1372",
    "width": "100",
    "alignment": "center",
}
_HOMEPAGE_ROW: dict[str, Any] = dict(_HOMEPAGE_SECTION)

# Text: TextContainer.tsx:19-25 via Text/props.js:11 (CreateText.jsx:44-46).
_TEXT: dict[str, Any] = {**_ELEMENT_CONTAINER, **_PADDING_10}

# CustomField and FormField: CustomField/props.js:3-9 and FormField/props.js:3-9.
_FIELD: dict[str, Any] = {**_ELEMENT_CONTAINER, **_PADDING_10}

# HTML block: HTMLBlockContainer.tsx:15-22 (CreateHTMLBlock.jsx:24-29).
_HTML_BLOCK: dict[str, Any] = {
    **_ELEMENT_CONTAINER,
    "containerBackgroundColor": "inherit",
    **_PADDING_10,
}

# Divider: DividerContainer.tsx:15-22 + Divider/props.js:7-14
# (CreateDivider.js:26-28).
_DIVIDER: dict[str, Any] = {
    **_ELEMENT_CONTAINER,
    "containerBorderRadius": False,
    **_PADDING_10,
    "alignment": "center",
    "borderStyle": "solid",
    "color": "#A8B3BC",
    "size": "1",
    "width": "100",
}

# Image: ImageContainer.tsx:14 via Image/props.js:11, followed by the sizing
# half of packages/page-builder/utils/image.ts:33-47 (getDefaultImageProps),
# which CreateImage.jsx:23-31 passes after the file's src/name/fileId and its
# measured naturalHeight/naturalWidth.
_IMAGE: dict[str, Any] = dict(_ELEMENT_CONTAINER)
_IMAGE_SIZING: dict[str, Any] = {
    "size": "auto",
    "unit": "pixel",
    "height": None,
    "width": None,
    "display": "flex",
    "position": "center",
    "alt": "",
    "link": "",
}

# Button on a form page: packages/page-builder/nodes/Button/props.ts:10-36
# (commonProps, DEFAULT_FORM_BUTTON_PROPS) with ButtonContainer.tsx:22-28.
# borderColor is gray-900 here; gen/defaults.ts's rgba(0,0,0,1) is only used
# for template buttons, never a tray drop.
_FORM_BUTTON: dict[str, Any] = {
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
    **_ELEMENT_CONTAINER,
    **_padding("10", "0", "10", "0"),
    "enableRecaptcha": False,
}

# Button on a non-form page (the thank-you page), a homepage block or a
# static block: Button/props.ts:38-42 (DEFAULT_STATIC_PAGE_BUTTON_PROPS),
# via CreateButton.js:31-40.
_STATIC_PAGE_BUTTON: dict[str, Any] = {
    **_FORM_BUTTON,
    "label": "Click Here",
    "action": "url",
}

# --- The email builder (MessageBuilder.jsx:824 mounts creator-elements.js:27-35).
# Every email creator lays these over the form builder's props:
# packages/page-builder/nodes/containers/ElementContainer.tsx:39-46
# (EMAIL_ELEMENT_CONTAINER_PROPS_OVERRIDES).
_EMAIL_OVERRIDES: dict[str, Any] = {
    "containerBackgroundPositionX": "center",
    "containerBackgroundPositionY": "top",
    "containerBorderTopLeftRadius": "0",
    "containerBorderTopRightRadius": "0",
    "containerBorderBottomLeftRadius": "0",
    "containerBorderBottomRightRadius": "0",
}

# A new email template's Root: gen/defaults.ts:157 (DefaultEmailProps.Root,
# no tabletBreak) compiled with packages/page-builder/utils/build.ts:49,57
# (backgroundColor aliceblue-25) by getDefaultEmailTemplate (:189-203), which
# the template library calls for a new template (MessageBuilder/
# useDefaultTemplate.js:27-52, pages/Library/pages/Templates/index.jsx:55).
_EMAIL_ROOT: dict[str, Any] = {
    **_GEN_CONTAINER,
    "backgroundColor": "#F8FAFF",
    "width": "100",
    "maxWidth": "900",
    "alignment": "center",
    "mobileBreak": "414",
    **_ROOT_TEXT,
}

# Email Section and Row settings write the same three keys (EmailSectionSettings,
# EmailRowSettings; see _settings_sizing), maxWidth being this Root's.
_EMAIL_SIZING: dict[str, Any] = _settings_sizing(_EMAIL_ROOT)

# Section: nodes/Section/props.js:6-9 (DEFAULT_EMAIL_SECTION_PROPS).
_EMAIL_SECTION: dict[str, Any] = {**_FORM_SECTION, **_EMAIL_OVERRIDES, **_EMAIL_SIZING}

# Row: nodes/Row/props.js:6-9 (DEFAULT_EMAIL_ROW_PROPS).
_EMAIL_ROW: dict[str, Any] = {**_FORM_ROW, **_EMAIL_OVERRIDES, **_EMAIL_SIZING}

# Text: nodes/Text/props.js:6-9 (DEFAULT_EMAIL_TEXT_PROPS).
_EMAIL_TEXT: dict[str, Any] = {**_TEXT, **_EMAIL_OVERRIDES}

# Divider: nodes/Divider/props.js:16-19 (DEFAULT_EMAIL_DIVIDER_PROPS).
_EMAIL_DIVIDER: dict[str, Any] = {**_DIVIDER, **_EMAIL_OVERRIDES}

# Image: nodes/Image/props.js:6-9 (DEFAULT_EMAIL_IMAGE_PROPS), then the same
# getDefaultImageProps sizing as a form image (CreateImage.jsx:23-31).
_EMAIL_IMAGE: dict[str, Any] = {**_IMAGE, **_EMAIL_OVERRIDES}

# Button: packages/page-builder/nodes/Button/props.ts:44-49
# (DEFAULT_EMAIL_BUTTON_PROPS): commonProps (:10-31, the form button without
# enableRecaptcha) with the email overrides, label "Click Here", action url.
_EMAIL_BUTTON: dict[str, Any] = {
    **{k: v for k, v in _FORM_BUTTON.items() if k != "enableRecaptcha"},
    **_EMAIL_OVERRIDES,
    "label": "Click Here",
    "action": "url",
}


class _NotInBuilder(Mapping[str, Any]):
    """The default for a block a builder's tray doesn't offer: any read
    raises instead of building a node the UI never could."""

    def __init__(self, builder: str, block: str) -> None:
        self._message = f"the {builder} builder has no {block} block"

    def __getitem__(self, key: str) -> Any:
        raise ValueError(self._message)

    def __iter__(self) -> Iterator[str]:
        raise ValueError(self._message)

    def __len__(self) -> int:
        raise ValueError(self._message)


@dataclass(frozen=True)
class BuilderDefaults:
    """The props one Kizen page builder writes for each node it creates.

    Builders that share a default in the UI share the table here. Read-only:
    every node takes a deep copy (see :func:`_props`).
    """

    root: Mapping[str, Any]
    section: Mapping[str, Any]
    row: Mapping[str, Any]
    text: Mapping[str, Any]
    field: Mapping[str, Any]
    html_block: Mapping[str, Any]
    button: Mapping[str, Any]
    divider: Mapping[str, Any]
    image: Mapping[str, Any]


# The form/survey builder on a form page (creator-elements.js:37-47).
FORM_PAGE_DEFAULTS = BuilderDefaults(
    root=_FORM_ROOT,
    section=_FORM_SECTION,
    row=_FORM_ROW,
    text=_TEXT,
    field=_FIELD,
    html_block=_HTML_BLOCK,
    button=_FORM_BUTTON,
    divider=_DIVIDER,
    image=_IMAGE,
)
# The same builder on a non-form page: only the button differs
# (buildFormButtonCreator(isFormPage), CreateButton.js:31-36).
FORM_STATIC_PAGE_DEFAULTS = replace(FORM_PAGE_DEFAULTS, button=_STATIC_PAGE_BUTTON)
# Homepage / dashboard static-content blocks (creator-elements.js:49-57).
HOMEPAGE_DEFAULTS = replace(
    FORM_STATIC_PAGE_DEFAULTS,
    root=_HOMEPAGE_ROOT,
    section=_HOMEPAGE_SECTION,
    row=_HOMEPAGE_ROW,
)
# Record-layout custom content blocks (creator-elements.js:59-67): the
# homepage set with a taller Root.
STATIC_BLOCK_DEFAULTS = replace(HOMEPAGE_DEFAULTS, root=_STATIC_BLOCK_ROOT)
# The email template builder (creator-elements.js:27-35). Its tray has no
# field or HTML block, so reading either default raises.
EMAIL_DEFAULTS = BuilderDefaults(
    root=_EMAIL_ROOT,
    section=_EMAIL_SECTION,
    row=_EMAIL_ROW,
    text=_EMAIL_TEXT,
    field=_NotInBuilder("email", "field"),
    html_block=_NotInBuilder("email", "HTML"),
    button=_EMAIL_BUTTON,
    divider=_EMAIL_DIVIDER,
    image=_EMAIL_IMAGE,
)


def _props(base: Mapping[str, Any]) -> dict[str, Any]:
    """A node's own copy of a default table (``textStyles`` is a list)."""
    return copy.deepcopy(dict(base))


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
    dashboard static-content Button block). ``color`` ``None`` keeps the
    builder's own button colour."""
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
    rows: list[dict[str, Any]], *, background_color: str | None = None
) -> dict[str, Any]:
    """One section (a container of rows). ``background_color`` ``None``
    keeps the builder's own Section background (see :class:`BuilderDefaults`)."""
    return {"rows": rows, "background_color": background_color}


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _leaf_node(
    resolved_name: str,
    props: dict[str, Any],
    parent_id: str,
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": {"resolvedName": resolved_name},
        "isCanvas": False,
        "props": props,
        "displayName": resolved_name,
        "custom": custom if custom is not None else {},
        "parent": parent_id,
        "hidden": False,
        "nodes": [],
        "linkedNodes": {},
    }


def _button_props(block: dict[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    props = _props(base)
    if block.get("color") is not None:
        props["color"] = block["color"]
    props["label"] = block["label"]
    props["action"] = block["action"]
    props["url"] = block.get("url", "")
    if block["action"] == "url" and base["action"] != "url":
        # Switching a button's Action to URL writes this
        # (PageBuilder/settings/ButtonSettings.jsx:261-263).
        props["openLinkInNewTab"] = True
    return props


def _image_props(block: dict[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    props = _props(base)
    props.update(
        {"src": block["src"], "name": block["name"], "fileId": block["file_id"]}
    )
    # The UI measures these when the file loads and leaves them out when it
    # can't (utils/image.ts:25-31); the CLI sends only what the spec gives.
    if block.get("natural_height") is not None:
        props["naturalHeight"] = block["natural_height"]
    if block.get("natural_width") is not None:
        props["naturalWidth"] = block["natural_width"]
    props.update(_props(_IMAGE_SIZING))
    if block.get("width") is not None:
        # A fixed width is what the Size setting writes when a user picks one
        # (PageBuilder/settings/ImageSettings/ImageSettings.jsx:501-518).
        props["size"] = "dynamic"
        props["width"] = block["width"]
        props["dimension"] = "width"
    return props


def _assemble_block(
    block: dict[str, Any],
    parent_id: str,
    content: dict[str, Any],
    *,
    defaults: BuilderDefaults = FORM_PAGE_DEFAULTS,
) -> str:
    kind = block["kind"]
    node_id = _new_id()

    if kind == "text":
        node = _leaf_node(
            "Text", _props(defaults.text), parent_id, {"text": block["html"]}
        )
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
        node = _leaf_node(
            resolved_name, {**_props(defaults.field), "field": field_prop}, parent_id
        )
    elif kind == "button":
        is_submit = block["action"] == "submit"
        node = _leaf_node(
            "Button",
            _button_props(block, defaults.button),
            parent_id,
            {"isSubmitButton": True} if is_submit else {},
        )
    elif kind == "html":
        node = _leaf_node(
            "HTMLBlock",
            {**_props(defaults.html_block), "htmlContent": block["html"]},
            parent_id,
        )
    elif kind == "divider":
        props = _props(defaults.divider)
        if block.get("color") is not None:
            props["color"] = block["color"]
        node = _leaf_node("Divider", props, parent_id)
    elif kind == "image":
        node = _leaf_node("Image", _image_props(block, defaults.image), parent_id)
    else:
        raise ValueError(f"unknown form-page block kind: {kind!r}")

    content[node_id] = node
    return node_id


def _assemble_cell(
    cell_spec: dict[str, Any],
    parent_id: str,
    content: dict[str, Any],
    *,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str],
    width: float | None = None,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
) -> str:
    """``cell_props`` is an additive hook: it defaults to ``props: {}``. A
    surface with a different ``Cell.props`` shape (email's
    ``{"__width": <fraction>}`` — see ``tools.email_craft``) passes its own
    callable instead of forking this function."""
    cell_id = _new_id()
    block_ids = [block_assembler(b, cell_id, content) for b in cell_spec["blocks"]]
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
    defaults: BuilderDefaults,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str],
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    row_props: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
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
    # `columns` leads because craft.js lays Row.craft.defaultProps down first
    # (nodes/Row/Row.jsx:318-325).
    props = {"columns": columns, **_props(defaults.row)}
    if row_props:
        props.update(row_props(row_spec))
    content[row_id] = {
        "type": {"resolvedName": "Row"},
        "isCanvas": False,
        "props": props,
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
    defaults: BuilderDefaults,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str],
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    section_props: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    row_props: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> str:
    section_id = _new_id()
    row_ids = [
        _assemble_row(
            r,
            section_id,
            content,
            defaults=defaults,
            cell_props=cell_props,
            block_assembler=block_assembler,
            row_props=row_props,
        )
        for r in section_spec["rows"]
    ]
    props = _props(defaults.section)
    if section_spec.get("background_color") is not None:
        props["containerBackgroundColor"] = section_spec["background_color"]
    if section_props:
        props.update(section_props(section_spec))
    content[section_id] = {
        "type": {"resolvedName": "Section"},
        "isCanvas": True,
        "props": props,
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
    defaults: BuilderDefaults = FORM_PAGE_DEFAULTS,
    cell_props: Callable[[float | None], dict[str, Any]] | None = None,
    block_assembler: Callable[[dict[str, Any], str, dict[str, Any]], str] | None = None,
    section_props: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    row_props: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a ``Root`` → ``Section`` → ``Row`` → ``Cell`` → block
    camelCase craft.js tree from :func:`section`/:func:`row`/:func:`cell`/
    ``*_block()`` specs, returning the raw content dict.

    Callers serialize as their surface requires — a form/survey's
    ``page_data`` is a **JSON-encoded string** of this dict (see
    :func:`page`), while a record layout's ``custom_content`` block embeds
    it as a **nested object** directly (``metadata.blockJson`` — confirmed
    live 2026-07-21; see ``tools/layouts.py::custom_content_block``).

    ``defaults`` is the builder whose creation defaults every node starts
    from — :data:`FORM_PAGE_DEFAULTS`, :data:`FORM_STATIC_PAGE_DEFAULTS`,
    :data:`STATIC_BLOCK_DEFAULTS` or :data:`EMAIL_DEFAULTS`. Spec values (a section's
    ``background_color``, a button's or divider's ``color``, an image's
    ``width``, a row's ``columns``) replace the matching default.

    ``cell_props``/``block_assembler`` are additive hooks for a surface whose
    ``Cell.props`` or leaf-block prop shapes differ — see
    ``tools.email_craft``, which needs ``Cell.props`` to carry
    ``{"__width": <fraction>}`` and its own Button/Divider prop construction.
    ``section_props``/``row_props`` are the same shape of hook for
    ``Section``/``Row`` props — each takes the section/row spec dict and
    returns a props-override dict merged over ``defaults``.
    """
    assemble = block_assembler or functools.partial(_assemble_block, defaults=defaults)
    content: dict[str, Any] = {}
    section_ids = [
        _assemble_section(
            s,
            "ROOT",
            content,
            defaults=defaults,
            cell_props=cell_props,
            block_assembler=assemble,
            section_props=section_props,
            row_props=row_props,
        )
        for s in sections
    ]
    content["ROOT"] = {
        "type": {"resolvedName": "Root"},
        "isCanvas": True,
        "props": _props(defaults.root),
        "displayName": "Root",
        "custom": {},
        "parent": None,
        "hidden": False,
        "nodes": section_ids,
        "linkedNodes": {},
    }
    return content


def _build_page_data(sections: list[dict[str, Any]], defaults: BuilderDefaults) -> str:
    return json.dumps(build_content_tree(sections, defaults=defaults))


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
    page defaults to neither. Nodes start from the form builder's defaults
    for that kind of page (:data:`FORM_PAGE_DEFAULTS` or
    :data:`FORM_STATIC_PAGE_DEFAULTS`).
    """
    if hideable is None:
        hideable = bool(is_form_page)
    defaults = FORM_PAGE_DEFAULTS if is_form_page else FORM_STATIC_PAGE_DEFAULTS
    return {
        "id": page_id or _new_id(),
        "hidden": hidden,
        "page_data": _build_page_data(sections, defaults),
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


def unplaced_fields(
    form_ui: dict[str, Any] | None, fields: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The ``fields`` (each with an ``id``) that no ``form_ui`` page places.

    Any update to a form, ``set-ui`` included, deletes these fields
    (confirmed live 2026-10-16). A placed field is a ``CustomField`` or
    ``FormField`` node whose ``props.field.id`` is the field's id.
    """
    placed: set[Any] = set()
    for pg in (form_ui or {}).get("pages") or []:
        data = pg.get("page_data")
        nodes = json.loads(data) if isinstance(data, str) else data or {}
        for node in nodes.values():
            field = (node.get("props") or {}).get("field")
            if isinstance(field, dict):
                placed.add(field.get("id"))
    return [f for f in fields if f.get("id") not in placed]


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

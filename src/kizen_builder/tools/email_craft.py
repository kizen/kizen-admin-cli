"""Build an email template's `craft_json` and compiled `content` from one spec.

**Why this has to be one function, not two.** `craft_json` (the editable
craft.js tree the builder shows) and `content` (the Outlook-safe HTML that
actually gets sent) are stored as independent fields — the server compiles
neither from the other, confirmed live both for `PATCH` and `POST` (see
`docs/specs/email-templates.md`). The compiled HTML carries a
`section-<nodeId>` class for every `Section` **and** every `Row` node, with
no orphans on either side in every live capture. So the two fields are
coupled by node id, not merely parallel: build the tree once, mint each id
once, and derive both outputs from that single pass. Building the tree and
then compiling HTML in a second pass that mints its *own* ids produces a
template whose builder view and real output silently disagree — the exact
failure this module exists to make impossible.

`build_email_content()` is the one entry point that upholds that invariant.
Everything else here is either structural reuse of `tools.form_ui` (the
`Root`/`Section`/`Row`/`Cell` assembly is identical topology, threaded
through the `cell_props`/`block_assembler` hooks added there for this
module) or email-specific: this surface's own `Text`/`Image`/`Button`/
`Divider` prop shapes (email's `Button`/`Divider` props differ from the
forms surface's — see `docs/specs/email-templates.md`), the v1 column-preset
table (byte-exact `columns`/`__width` fractions and compiled-HTML markup,
confirmed live 2026-08-25), and image upload (`api/files.py::upload_file`
with `source="public_image"`, plus reading `naturalWidth`/`naturalHeight`
straight from the uploaded file's own header bytes).

v1 scope only: `Text`, `Image`, `Button`, `Divider` leaf blocks, and the 4
column presets in `COLUMN_LAYOUT` below (`1 Column`, `2 Columns`, `2 Columns
(1/3 and 2/3)`, `2 Columns (2/3 and 1/3)`). `Attachments` and the other 5
presets (`3`/`4`/`5`/`6 Columns`, `3 Columns (gutters)`) are confirmed live
but out of scope — anything using them fails loudly rather than silently
degrading. There is no raw-HTML escape hatch on this surface (no
`HTMLBlock`, confirmed live directly against the builder).
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from kizen_builder.api import files as files_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec.email_templates import (
    ButtonBlockDef,
    DividerBlockDef,
    EmailTemplateDef,
    ImageBlockDef,
    TextBlockDef,
)
from kizen_builder.tools import form_ui

# ---------------------------------------------------------------------------
# Root/container prop shapes
# ---------------------------------------------------------------------------

# Confirmed live 2026-08-25: a form page's Root props minus `tabletBreak`,
# in two independent captures. Kept as its own literal (not derived from
# form_ui's private _ROOT_PROPS) so this module documents its own contract
# against docs/specs/email-templates.md rather than silently tracking
# whatever forms does next.
EMAIL_ROOT_PROPS: dict[str, Any] = {
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
}

# Same `container*` vocabulary every leaf block on this surface carries,
# same values form_ui._CONTAINER_DEFAULTS uses for forms — no live evidence
# these differ per block on the email surface.
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

# ---------------------------------------------------------------------------
# v1 column presets — byte-exact, confirmed live 2026-08-25. Do not round or
# recompute; see the work item's "Live probe findings".
# ---------------------------------------------------------------------------


class ColumnLayout:
    __slots__ = ("preset", "columns", "classes", "media_widths", "mso_widths_px")

    def __init__(
        self,
        preset: str,
        columns: tuple[float, ...],
        classes: tuple[str, ...],
        media_widths: tuple[str, ...],
        mso_widths_px: tuple[float, ...],
    ) -> None:
        self.preset = preset
        self.columns = columns
        self.classes = classes
        self.media_widths = media_widths
        self.mso_widths_px = mso_widths_px


# 880px content width in every case observed (900 Root maxWidth - 20px padding).
CONTENT_WIDTH_PX = 880.0

COLUMN_LAYOUTS: dict[str, ColumnLayout] = {
    "1 Column": ColumnLayout(
        "1 Column",
        (1,),
        ("mj-column-per-100",),
        ("100%",),
        (880.0,),
    ),
    "2 Columns": ColumnLayout(
        "2 Columns",
        (0.5, 0.5),
        ("mj-column-per-50", "mj-column-per-50"),
        ("50%", "50%"),
        (440.0, 440.0),
    ),
    "2 Columns (1/3 and 2/3)": ColumnLayout(
        "2 Columns (1/3 and 2/3)",
        (0.3333333333333333, 0.6666666666666666),
        ("mj-column-per-33-333332", "mj-column-per-66-666664"),
        ("33.333332%", "66.666664%"),
        (293.3333, 586.6666),
    ),
    "2 Columns (2/3 and 1/3)": ColumnLayout(
        "2 Columns (2/3 and 1/3)",
        (0.6666666666666666, 0.3333333333333333),
        ("mj-column-per-66-666664", "mj-column-per-33-333332"),
        ("66.666664%", "33.333332%"),
        (586.6666, 293.3333),
    ),
}

# The other 5 presets are pre-captured groundwork for a follow-on item, not
# built here. Naming one is a clear, immediate error, not a silent skip.
_OUT_OF_SCOPE_LAYOUTS = (
    "3 Columns",
    "3 Columns (gutters)",
    "4 Columns",
    "5 Columns",
    "6 Columns",
)


def known_layouts() -> list[str]:
    """The v1 closed enum of layout names, in display order."""
    return list(COLUMN_LAYOUTS)


def known_block_kinds() -> list[str]:
    """The v1 closed set of leaf block kinds this emitter supports."""
    return ["text", "image", "button", "divider"]


# ---------------------------------------------------------------------------
# Spec-builder helpers — plain dicts consumed by build_email_content().
# Mirrors tools.form_ui's cell()/row()/section() naming, but this surface's
# own leaf-block shapes (email Button/Divider props differ from forms' —
# see the module docstring) and its own layout validation.
# ---------------------------------------------------------------------------


def text_block(html: str) -> dict[str, Any]:
    return {"kind": "text", "html": html}


def image_block(
    *,
    file_id: str,
    src: str,
    name: str,
    alt: str = "",
    link: str = "",
    width: int | None = None,
    natural_width: int | None = None,
    natural_height: int | None = None,
) -> dict[str, Any]:
    return {
        "kind": "image",
        "file_id": file_id,
        "src": src,
        "name": name,
        "alt": alt,
        "link": link,
        "width": width,
        "natural_width": natural_width,
        "natural_height": natural_height,
    }


def button_block(label: str, url: str, *, color: str | None = None) -> dict[str, Any]:
    return {"kind": "button", "label": label, "url": url, "color": color}


def divider_block(color: str | None = None) -> dict[str, Any]:
    return {"kind": "divider", "color": color}


def cell(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"blocks": blocks}


def row(cells: list[dict[str, Any]], layout: str = "1 Column") -> dict[str, Any]:
    """One row using a v1 column preset by name.

    Raises ``ValueError`` — never a silent reshape — for an unknown preset
    name or a cell count that doesn't match it, naming the valid presets or
    the expected count. The planner (``tools.planners.messages``) catches
    this and re-raises as a ``PlanError``.
    """
    if layout in _OUT_OF_SCOPE_LAYOUTS:
        raise ValueError(
            f"layout {layout!r} is confirmed live but out of v1 scope "
            f"(see the work item's pre-captured groundwork). Supported: "
            f"{', '.join(known_layouts())}"
        )
    preset = COLUMN_LAYOUTS.get(layout)
    if preset is None:
        raise ValueError(
            f"unknown row layout {layout!r}. Supported: {', '.join(known_layouts())}"
        )
    if len(cells) != len(preset.columns):
        raise ValueError(
            f"layout {layout!r} needs {len(preset.columns)} cell(s), got {len(cells)}"
        )
    return {"cells": cells, "columns": list(preset.columns), "layout": layout}


def section(
    rows: list[dict[str, Any]], *, background_color: str = "#FFFFFF"
) -> dict[str, Any]:
    return {"rows": rows, "background_color": background_color}


# ---------------------------------------------------------------------------
# Image upload + header-byte pixel dimensions
# ---------------------------------------------------------------------------


def _png_dimensions(data: bytes) -> tuple[int, int]:
    # Signature (8 bytes) + IHDR chunk: length(4) type(4) width(4) height(4).
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a valid PNG (bad signature)")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


# JPEG SOF (start-of-frame) markers that carry dimensions. Excludes DHT
# (0xC4), JPG (0xC8), DAC (0xCC) — same-range bytes that are NOT SOF markers.
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
# Markers with no following length/payload — skip straight past them.
_JPEG_STANDALONE_MARKERS = frozenset({0x01, 0xD8, 0xD9} | set(range(0xD0, 0xD8)))


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        raise ValueError("not a valid JPEG (bad SOI marker)")
    pos = 2
    n = len(data)
    while pos < n - 1:
        if data[pos] != 0xFF:
            raise ValueError("malformed JPEG: expected a marker")
        marker = data[pos + 1]
        pos += 2
        while marker == 0xFF and pos < n:  # padding fill bytes between markers
            marker = data[pos]
            pos += 1
        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        if pos + 2 > n:
            break
        seg_len = int.from_bytes(data[pos : pos + 2], "big")
        if marker in _JPEG_SOF_MARKERS:
            if pos + 7 > n:
                break
            height = int.from_bytes(data[pos + 3 : pos + 5], "big")
            width = int.from_bytes(data[pos + 5 : pos + 7], "big")
            return width, height
        pos += seg_len
    raise ValueError("no SOF0/SOF2 segment found in JPEG")


def read_image_dimensions(data: bytes) -> tuple[int, int, str]:
    """Return ``(width, height, content_type)`` read from the file's own
    header bytes. PNG and JPEG only — both are real cases on this surface
    (every image already stored in the target environment is PNG, but the
    browser trace that settled the ``source`` question was a JPEG upload).
    GIF/WebP/SVG fail loudly as unsupported rather than being silently
    mis-parsed; SVG especially has no pixel dimensions to read this way at
    all.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = _png_dimensions(data)
        return w, h, "image/png"
    if data[:3] == b"\xff\xd8\xff":
        w, h = _jpeg_dimensions(data)
        return w, h, "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        raise ValueError("GIF is not supported on this surface — PNG or JPEG only")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        raise ValueError("WebP is not supported on this surface — PNG or JPEG only")
    if data[:5] == b"<?xml" or data.lstrip()[:4] == b"<svg":
        raise ValueError(
            "SVG has no pixel dimensions to read (no fixed naturalWidth/"
            "naturalHeight) and is not supported on this surface"
        )
    raise ValueError("unrecognized image format — only PNG and JPEG are supported")


def upload_email_image(
    client: KizenClient, base_url: str, path: str | Path
) -> dict[str, Any]:
    """Upload a local PNG/JPEG for use in an Image block and return the
    resolved block fields (``file_id``, ``src``, ``name``, ``natural_width``,
    ``natural_height``).

    A real write — reuses ``api.files.upload_file`` with
    ``source=files_api.PUBLIC_IMAGE`` and ``is_public=True``, confirmed live
    2026-08-25 (without ``is_public``, the upload defaults to non-public and
    the resulting `src` 404s for any recipient without an authenticated
    session — see `docs/specs/email-templates.md`). Callers outside
    ``tools/planners/`` only (planners never write — see ``CLAUDE.md``); the
    CLI only calls this for a real apply — under ``--dry-run`` it calls
    :func:`offline_resolve_spec_images` instead, which uploads nothing.
    ``base_url`` is the target env's own base URL (``EnvConfig.base_url``) —
    ``Image.src`` is host-absolute, confirmed live, so a template is
    environment-bound.
    """
    src_path = Path(path)
    data = src_path.read_bytes()
    width, height, _content_type = read_image_dimensions(data)
    registered = files_api.upload_file(
        client, src_path, source=files_api.PUBLIC_IMAGE, is_public=True
    )
    file_id = registered["id"]
    src = f"{base_url}/api/files/{file_id}/download"
    return {
        "file_id": file_id,
        "src": src,
        "name": src_path.name,
        "natural_width": width,
        "natural_height": height,
    }


# ---------------------------------------------------------------------------
# craft_json assembly — reuses tools.form_ui's Root/Section/Row/Cell shell
# via the cell_props/block_assembler hooks added there for this module.
# ---------------------------------------------------------------------------


def _cell_props(width: float | None) -> dict[str, Any]:
    # Confirmed live: Cell.props is {"__width": <fraction>}, redundant with
    # (and must agree with) the parent Row's own columns entry.
    return {"__width": width}


def _assemble_email_block(
    block: dict[str, Any], parent_id: str, content: dict[str, Any]
) -> str:
    kind = block["kind"]
    node_id = form_ui._new_id()

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
                "alt": block.get("alt", ""),
                "link": block.get("link", ""),
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
    elif kind == "button":
        node = {
            "type": {"resolvedName": "Button"},
            "isCanvas": False,
            "props": {
                **_CONTAINER_DEFAULTS,
                "url": block.get("url", ""),
                "label": block["label"],
                "action": "url",
                "color": block.get("color") or "rgba(0,51,160,1)",
                "textColor": "rgba(255,255,255,1)",
                "fontSize": "16",
                "fontFamily": "Arial",
                "alignment": "center",
                "borderSize": "0",
                "borderColor": "rgba(0,0,0,1)",
                "borderRadius": "8",
                "paddingTop": "10",
                "paddingLeft": "20",
                "paddingRight": "20",
                "paddingBottom": "10",
                "textStyles": [],
                "openLinkInNewTab": True,
            },
            "displayName": "Button",
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
                "size": "3",
                "color": block.get("color") or "rgba(78,193,145,1)",
                "width": "100",
                "alignment": "center",
                "borderStyle": "solid",
            },
            "displayName": "Divider",
            "custom": {},
            "parent": parent_id,
            "hidden": False,
            "nodes": [],
            "linkedNodes": {},
        }
    else:
        raise ValueError(
            f"unsupported email block kind: {kind!r}. Supported: "
            f"{', '.join(known_block_kinds())}"
        )

    content[node_id] = node
    return node_id


# ---------------------------------------------------------------------------
# content (compiled HTML) — walks the SAME craft_json dict build_content_tree
# just returned, using its dict keys as node ids. No second id-minting pass.
# ---------------------------------------------------------------------------


def _resolved_name(node: dict[str, Any]) -> str:
    t = node.get("type")
    return str(t.get("resolvedName")) if isinstance(t, dict) else str(t)


def _layout_for_columns(columns: list[float]) -> ColumnLayout:
    for layout in COLUMN_LAYOUTS.values():
        if list(layout.columns) == list(columns):
            return layout
    raise ValueError(f"no v1 column layout matches columns={columns!r}")


def _render_button(node: dict[str, Any]) -> str:
    p = node["props"]
    return (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'style="border-collapse:separate;">'
        "<tr><td "
        f'style="border-radius:{p["borderRadius"]}px;background:{p["color"]};'
        f'text-align:center;" '
        f'bgcolor="{p["color"]}">'
        f'<a href="{escape(p["url"], quote=True)}" target="_blank" '
        f'style="display:inline-block;background:{p["color"]};color:{p["textColor"]};'
        f"font-family:{p['fontFamily']};font-size:{p['fontSize']}px;"
        f"padding:{p['paddingTop']}px {p['paddingRight']}px "
        f"{p['paddingBottom']}px {p['paddingLeft']}px;border-radius:{p['borderRadius']}px;"
        'text-decoration:none;">'
        f"{escape(p['label'])}</a></td></tr></table>"
    )


def _render_divider(node: dict[str, Any]) -> str:
    p = node["props"]
    return (
        f'<p style="border-top:{p["size"]}px {p["borderStyle"]} {p["color"]};'
        f'font-size:1px;margin:0 auto;width:{p["width"]}%;"> </p>'
    )


def _render_image(node: dict[str, Any]) -> str:
    p = node["props"]
    img = (
        f'<img src="{escape(p["src"], quote=True)}" alt="{escape(p.get("alt") or "")}" '
        f'width="{p["width"]}" style="display:block;width:{p["width"]}px;'
        'max-width:100%;height:auto;border:0;" '
        f'data-natural-width="{p.get("naturalWidth")}" '
        f'data-natural-height="{p.get("naturalHeight")}">'
    )
    link = p.get("link")
    if link:
        return f'<a href="{escape(link, quote=True)}" target="_blank">{img}</a>'
    return img


def _render_block(node: dict[str, Any]) -> str:
    name = _resolved_name(node)
    if name == "Text":
        # Embedded verbatim, not stripped — see craft_summary()'s _plain_text,
        # which tag-strips both sides before comparing.
        return f'<div style="font-size:14px;">{node["custom"]["text"]}</div>'
    if name == "Image":
        return _render_image(node)
    if name == "Button":
        return _render_button(node)
    if name == "Divider":
        return _render_divider(node)
    raise ValueError(f"cannot compile HTML for unsupported node type {name!r}")


def _render_cell(cell_id: str, craft_json: dict[str, Any]) -> str:
    node = craft_json[cell_id]
    return "".join(_render_block(craft_json[bid]) for bid in node["nodes"])


def _render_row(row_id: str, craft_json: dict[str, Any]) -> tuple[str, str]:
    """Return (body_html, style_rule) for one Row."""
    node = craft_json[row_id]
    columns = node["props"]["columns"]
    layout = _layout_for_columns(columns)
    cell_ids = [node["linkedNodes"][f"column-{i + 1}"] for i in range(len(columns))]

    parts = [f'<div class="section-{row_id}">']
    parts.append(
        '<!--[if mso | IE]><table align="center" border="0" cellpadding="0" '
        'cellspacing="0" role="presentation" '
        f'style="width:{CONTENT_WIDTH_PX}px;"><tr>'
    )
    columns_data = zip(cell_ids, layout.classes, layout.mso_widths_px, strict=True)
    for i, (cid, cls, mso_w) in enumerate(columns_data):
        if i == 0:
            parts.append(
                f'<td style="vertical-align:top;width:{mso_w}px;"><![endif]-->'
            )
        else:
            parts.append(
                f'<!--[if mso | IE]></td><td style="vertical-align:top;width:{mso_w}px;">'
                "<![endif]-->"
            )
        parts.append(
            f'<div class="mj-outlook-group-fix {cls}" style="font-size:0px;'
            "text-align:left;direction:ltr;display:inline-block;vertical-align:top;"
            'width:100%;">'
        )
        parts.append(_render_cell(cid, craft_json))
        parts.append("</div>")
    parts.append("<!--[if mso | IE]></td></tr></table><![endif]-->")
    parts.append("</div>")

    style_rule = f".section-{row_id} {{ max-width:{CONTENT_WIDTH_PX}px; }}"
    return "".join(parts), style_rule


def _render_section(
    section_id: str, craft_json: dict[str, Any]
) -> tuple[str, list[str]]:
    node = craft_json[section_id]
    bg = node["props"].get("containerBackgroundColor", "#FFFFFF")
    rows_html: list[str] = []
    style_rules = [f".section-{section_id} {{ background-color:{bg}; }}"]
    for row_id in node["nodes"]:
        row_html, row_style = _render_row(row_id, craft_json)
        rows_html.append(row_html)
        style_rules.append(row_style)
    body = f'<div class="section-{section_id}">' + "".join(rows_html) + "</div>"
    return body, style_rules


def _column_base_width_rules(craft_json: dict[str, Any]) -> list[str]:
    """Unconditional `.mj-column-per-N` width rules — MJML's own convention.

    Each column `<div>` also carries a hardcoded inline `width:100%` (see
    `_render_row`), which is the fallback for clients that ignore `<style>`
    entirely. This unconditional rule is what overrides that fallback for
    every CSS-aware client at normal viewport widths, so multi-column rows
    render side by side by default; `_media_query_rules` below is what
    collapses them back to full width on narrow viewports.
    """
    seen: dict[str, str] = {}
    for node in craft_json.values():
        if _resolved_name(node) != "Row":
            continue
        columns = node["props"]["columns"]
        layout = _layout_for_columns(columns)
        for cls, media_w in zip(layout.classes, layout.media_widths, strict=True):
            seen[cls] = media_w
    return [
        f".{cls} {{ width:{w} !important; max-width:{w}; }}"
        for cls, w in sorted(seen.items())
    ]


def _media_query_rules(craft_json: dict[str, Any]) -> list[str]:
    """`max-width:480px` rules that collapse every column to full width, so
    a narrow viewport stacks instead of staying multi-column."""
    classes: set[str] = set()
    for node in craft_json.values():
        if _resolved_name(node) != "Row":
            continue
        classes.update(_layout_for_columns(node["props"]["columns"]).classes)
    return [
        f".{cls} {{ width:100% !important; max-width:100%; }}"
        for cls in sorted(classes)
    ]


def _compile_html(craft_json: dict[str, Any]) -> str:
    root = craft_json["ROOT"]
    bodies: list[str] = []
    style_rules: list[str] = []
    for section_id in root["nodes"]:
        body, rules = _render_section(section_id, craft_json)
        bodies.append(body)
        style_rules.extend(rules)

    column_rules = _column_base_width_rules(craft_json)
    media_rules = _media_query_rules(craft_json)
    style_block = (
        '<style type="text/css">'
        ".mj-outlook-group-fix{width:100% !important;}"
        + "".join(style_rules)
        + "".join(column_rules)
        + (
            "@media only screen and (max-width:480px){" + "".join(media_rules) + "}"
            if media_rules
            else ""
        )
        + "</style>"
    )
    return (
        "<!doctype html>"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings>"
        "<o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings>"
        "</xml></noscript><![endif]-->" + style_block + "</head>"
        '<body style="word-spacing:normal;">' + "".join(bodies) + "</body></html>"
    )


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def build_email_content(sections: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Return ``(craft_json, content)`` from ONE id-assignment pass.

    ``sections`` is built from :func:`section`/:func:`row`/:func:`cell`/
    ``*_block()`` above (or straight from an :class:`EmailTemplateDef` via
    ``tools.planners.messages``). Never expose a way to build one output
    without the other — see the module docstring for why.
    """
    craft_json = form_ui.build_content_tree(
        sections,
        root_props=EMAIL_ROOT_PROPS,
        cell_props=_cell_props,
        block_assembler=_assemble_email_block,
    )
    content = _compile_html(craft_json)
    return craft_json, content


def assemble_sections(resolved_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the plain-dict tree :func:`resolve_spec_images` (or
    :func:`offline_resolve_spec_images`) returns into the
    :func:`section`/:func:`row`/:func:`cell`/``*_block()`` spec
    :func:`build_email_content` consumes.

    This is where a row's cell count is checked against its layout preset —
    :func:`row` raises ``ValueError`` on a mismatch. Callers under
    ``tools/planners/`` catch that and re-raise as a ``PlanError`` (a
    validation error at plan time, not a silent reshape); `craft-config`
    lets it surface as a plain CLI error since it isn't planning anything.
    """
    sections: list[dict[str, Any]] = []
    for s in resolved_sections:
        rows: list[dict[str, Any]] = []
        for r in s["rows"]:
            cells: list[dict[str, Any]] = []
            for c in r["cells"]:
                blocks: list[dict[str, Any]] = []
                for b in c["blocks"]:
                    kind = b["kind"]
                    if kind == "text":
                        blocks.append(text_block(b["html"]))
                    elif kind == "image":
                        blocks.append(
                            image_block(
                                file_id=b["file_id"],
                                src=b["src"],
                                name=b["name"],
                                alt=b.get("alt", ""),
                                link=b.get("link", ""),
                                width=b.get("width"),
                                natural_width=b.get("natural_width"),
                                natural_height=b.get("natural_height"),
                            )
                        )
                    elif kind == "button":
                        blocks.append(
                            button_block(b["label"], b["url"], color=b.get("color"))
                        )
                    elif kind == "divider":
                        blocks.append(divider_block(b.get("color")))
                    else:
                        raise ValueError(
                            f"unsupported block kind: {kind!r}. Supported: "
                            f"{', '.join(known_block_kinds())}"
                        )
                cells.append(cell(blocks))
            rows.append(row(cells, layout=r["layout"]))
        sections.append(
            section(rows, background_color=s.get("background_color", "#FFFFFF"))
        )
    return sections


# ---------------------------------------------------------------------------
# Spec resolution — walks an EmailTemplateDef into the plain-dict shape
# assemble_sections() turns into row()/cell()/*_block() calls. Only the
# image leg differs between the live and offline (craft-config) callers, so
# both share this walk.
# ---------------------------------------------------------------------------

# `craft-config`'s offline output has no live upload behind it — these tokens
# make that obvious rather than looking like a real id a spec could paste in.
OFFLINE_FILE_PLACEHOLDER = "<FILE_UUID>"
OFFLINE_HOST_PLACEHOLDER = "<HOST>"


def _walk_blocks(
    spec: EmailTemplateDef,
    resolve_image: Callable[[ImageBlockDef], dict[str, Any]],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for s in spec.sections:
        rows: list[dict[str, Any]] = []
        for r in s.rows:
            cells: list[dict[str, Any]] = []
            for c in r.cells:
                blocks: list[dict[str, Any]] = []
                for b in c.blocks:
                    if isinstance(b, TextBlockDef):
                        blocks.append({"kind": "text", "html": b.html})
                    elif isinstance(b, ButtonBlockDef):
                        blocks.append(
                            {
                                "kind": "button",
                                "label": b.label,
                                "url": b.url,
                                "color": b.color,
                            }
                        )
                    elif isinstance(b, DividerBlockDef):
                        blocks.append({"kind": "divider", "color": b.color})
                    elif isinstance(b, ImageBlockDef):
                        blocks.append({"kind": "image", **resolve_image(b)})
                    else:  # pragma: no cover - the discriminated union rejects this
                        raise ValueError(f"unsupported block: {b!r}")
                cells.append({"blocks": blocks})
            rows.append({"layout": r.layout, "cells": cells})
        sections.append({"rows": rows, "background_color": s.background_color})
    return sections


def resolve_spec_images(spec: EmailTemplateDef) -> list[dict[str, Any]]:
    """Upload every local file an ``EmailTemplateDef``'s Image blocks
    reference and return ``spec.sections`` as the plain nested dicts
    ``tools.planners.messages`` turns into a plan.

    A real write (see :func:`upload_email_image`) — call this from the CLI
    layer for a real apply, never from ``tools/planners/``. Under
    ``--dry-run`` the CLI calls :func:`offline_resolve_spec_images` instead,
    so a dry run uploads nothing — see that function.
    """
    config = load_env_config()
    with KizenClient(config) as client:

        def resolve_image(b: ImageBlockDef) -> dict[str, Any]:
            info = upload_email_image(client, config.base_url, b.file)
            return {**info, "alt": b.alt, "link": b.link, "width": b.width}

        return _walk_blocks(spec, resolve_image)


def offline_resolve_spec_images(spec: EmailTemplateDef) -> list[dict[str, Any]]:
    """Offline counterpart for `messages templates craft-config`: no
    network call of any kind. Reads each local image's header bytes for
    ``naturalWidth``/``naturalHeight`` (that part needs no upload) but
    stands in obvious placeholder tokens for ``fileId``/``src`` — this
    output previews the compiled HTML, it is not meant to be pasted into a
    create/update spec.
    """

    def resolve_image(b: ImageBlockDef) -> dict[str, Any]:
        path = Path(b.file)
        width, height, _ct = read_image_dimensions(path.read_bytes())
        return {
            "file_id": OFFLINE_FILE_PLACEHOLDER,
            "src": f"https://{OFFLINE_HOST_PLACEHOLDER}/api/files/{OFFLINE_FILE_PLACEHOLDER}/download",
            "name": path.name,
            "natural_width": width,
            "natural_height": height,
            "alt": b.alt,
            "link": b.link,
            "width": b.width,
        }

    return _walk_blocks(spec, resolve_image)

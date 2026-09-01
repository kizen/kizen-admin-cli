"""Compile an email template's craft_json tree into the Outlook-safe
`content` HTML.

Split out of `tools/email_craft.py` — see that module's docstring for the
full "why `craft_json` and `content` must come from one pass over one tree"
reasoning; this module is the compile side of that pass. Every function here
takes an existing `node_id`/`craft_json` and never mints one — id minting
stays entirely in `email_craft.py` (`form_ui.build_content_tree` +
`_assemble_email_block`). `email_craft.build_email_content()` calls
`_compile_html` below once, on the exact tree `form_ui.build_content_tree`
just returned, and that's the only entry point into this module's compile
path.

`ColumnLayout`/`COLUMN_LAYOUTS` also live here even though `email_craft.py`'s
`row()` reads `.columns` off them too (`email_craft.py` imports
`COLUMN_LAYOUTS` back by name) — they can't live in `email_craft.py` instead,
since `email_craft.py` already needs `_compile_html` from this module, and a
module can't import back from a module that imports it.
"""

from __future__ import annotations

import math
import re
from html import escape
from typing import Any

_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)")


def _rgba_to_hex(value: str) -> str:
    """Convert an `rgba(r,g,b,a)`/`rgb(r,g,b)` colour string to the lowercase
    `#rrggbb` hex Kizen's own compiler emits for the same value — confirmed
    against the reference template's compiled `content`: `Root.props.color`
    (`rgba(74,86,96,1)`) compiles to `#4a5660`, `Root.props.linkColor`
    (`rgba(82,142,249,1)`) compiles to `#528ef9`. Alpha is dropped, matching
    both observed conversions (both alpha `1`) — this surface has no
    confirmed case of a translucent text/link colour reaching `content`.
    Anything that isn't `rgba(...)`/`rgb(...)` passes through unchanged
    (most `container*` colour props on this surface are already hex or a
    literal like `"transparent"`, not every colour prop here uses the
    rgba wire format)."""
    m = _RGBA_RE.fullmatch(value.strip())
    if not m:
        return value
    r, g, b = (int(x) for x in m.groups())
    return f"#{r:02x}{g:02x}{b:02x}"


# The MJML boilerplate reset block — Outlook/webkit/Gecko normalization with
# no per-template data, confirmed byte-exact against the reference template's
# compiled `content` (read-only `GET`, 2026-08-26). Kept as one literal
# constant rather than built up piecewise, since every byte here is fixed.
_MJML_RESET_STYLE = (
    '<style type="text/css">\n'
    "#outlook a { padding: 0; }\n"
    "body { margin: 0; padding: 0; -webkit-text-size-adjust: 100%; "
    "-ms-text-size-adjust: 100%; }\n"
    "table, td { border-collapse: collapse; mso-table-lspace: 0pt; "
    "mso-table-rspace: 0pt; }\n"
    "img { border: 0; height: auto; line-height: 100%; outline: none; "
    "text-decoration: none; -ms-interpolation-mode: bicubic; }\n"
    "p { display: block; margin: 13px 0; }\n"
    "</style>"
)

# `.kizen-text-styles` — the class Kizen's own compiler uses to scope text
# typography and rich-text element styling (links, paragraphs, code/pre).
# The rule text itself carries no per-template data except `linkColor`
# (interpolated below), so it's kept as one structural block, confirmed
# against the reference template's compiled `content`. BCLI-023's text model
# (paragraph/list/code rendering) is untouched by this — these rules only
# apply Kizen's own styling to whatever HTML a `Text` block already embeds.
_KIZEN_TEXT_STYLES_TEMPLATE = (
    ".kizen-text-styles a {{ color: {link_color}; text-decoration: none; }}"
    ".kizen-text-styles a *, .kizen-text-styles span * "
    "{{ color: inherit; font-size: inherit; }}"
    ".kizen-text-styles a:hover, .kizen-text-styles a:focus, "
    ".kizen-text-styles a:hover *, .kizen-text-styles a:focus * "
    "{{ text-decoration: underline; }}"
    ".kizen-text-styles a:hover s, .kizen-text-styles a:focus s "
    "{{ text-decoration: underline line-through; }}"
    ".kizen-text-styles p {{ margin: 0; line-height: 1.5em; min-height: 1em; }}"
    ".kizen-text-styles p * "
    "{{ font-family: inherit; font-size: inherit; line-height: inherit; }}"
    ".kizen-text-styles ul, .kizen-text-styles ol "
    "{{ margin-top: 0; margin-bottom: 10px; }}"
    ".kizen-text-styles code {{ font-family: 'Courier New', Monospace; "
    "font-size: inherit; font-weight: 400; background-color: #F5F6F7; "
    "padding: 5px; color: #4A5660; border-radius: 4px; }}"
    ".kizen-text-styles pre {{ padding: 10px; background-color: #F5F6F7; "
    "border-radius: 4px; border: 1px solid #D8DDE1; }}"
    ".kizen-text-styles pre code {{ padding: 0; background-color: unset; "
    "border-radius: 0; white-space: pre-wrap; word-break: break-all; }}"
)


def _section_class(node_id: str) -> str:
    """`section-<nodeId>` — the existing Section/Row coupling class every
    `_render_section`/`_render_row` call site uses. A tiny shared helper so
    it and `_image_auto_class` below format node ids identically rather than
    as two independently hand-rolled f-strings — see
    `test_image_and_section_class_conventions_share_id_formatting`."""
    return f"section-{node_id}"


def _image_auto_class(node_id: str) -> str:
    """`image-<nodeId>-auto` — the auto-mode image sizing class, same
    `<prefix>-<nodeId>[-suffix]` shape as `_section_class` above. Confirmed
    against the reference template's compiled `content`
    (`.image-<nodeId>-auto > table td { ... }`, one real occurrence
    inspected structurally, node id not reproduced here)."""
    return f"image-{node_id}-auto"


class ColumnLayout:
    __slots__ = ("preset", "columns", "classes", "media_widths")

    def __init__(
        self,
        preset: str,
        columns: tuple[float, ...],
        classes: tuple[str, ...],
        media_widths: tuple[str, ...],
    ) -> None:
        self.preset = preset
        self.columns = columns
        self.classes = classes
        self.media_widths = media_widths


# 880px was the content width in every case observed pre-BCLI-024 (900
# Section maxWidth - 20px padding, both hardcoded at the time). Now that
# `Section.max_width`/`Row.container_width`/padding are spec-settable
# (BCLI-024), the actual per-row pixel width is computed by
# `_row_content_width_px` below, not held as one constant — see that
# function's docstring for the fallback formula, which reduces to exactly
# 880.0 when nothing is overridden.

COLUMN_LAYOUTS: dict[str, ColumnLayout] = {
    "1 Column": ColumnLayout(
        "1 Column",
        (1,),
        ("mj-column-per-100",),
        ("100%",),
    ),
    "2 Columns": ColumnLayout(
        "2 Columns",
        (0.5, 0.5),
        ("mj-column-per-50", "mj-column-per-50"),
        ("50%", "50%"),
    ),
    "2 Columns (1/3 and 2/3)": ColumnLayout(
        "2 Columns (1/3 and 2/3)",
        (0.3333333333333333, 0.6666666666666666),
        ("mj-column-per-33-333332", "mj-column-per-66-666664"),
        ("33.333332%", "66.666664%"),
    ),
    "2 Columns (2/3 and 1/3)": ColumnLayout(
        "2 Columns (2/3 and 1/3)",
        (0.6666666666666666, 0.3333333333333333),
        ("mj-column-per-66-666664", "mj-column-per-33-333332"),
        ("66.666664%", "33.333332%"),
    ),
}


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
        '<table role="presentation" '
        f'align="{p["alignment"]}" '
        'border="0" cellpadding="0" cellspacing="0" '
        'style="border-collapse:separate;line-height:100%;">'
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


def _ancestor_section_props(node_id: str, craft_json: dict[str, Any]) -> dict[str, Any]:
    """Walk a leaf block's fixed 3-hop ancestry — block -> Cell -> Row ->
    Section (`_assemble_cell` always sets a block's own `parent` to the
    enclosing Cell's id, per `tools.form_ui`) — and return the enclosing
    `Section`'s own `props`. Used by auto-mode image sizing below to read
    the Section's `containerWidth`."""
    cell_id = craft_json[node_id]["parent"]
    row_id = craft_json[cell_id]["parent"]
    section_id = craft_json[row_id]["parent"]
    return craft_json[section_id]["props"]


def _render_image(node_id: str, craft_json: dict[str, Any]) -> tuple[str, list[str]]:
    """Return `(html, extra_style_rules)`. Kizen wraps every Image block in
    the same two-level table Kizen's own compiler uses (confirmed against
    the reference's compiled `content` for both a fixed-width and an
    auto-mode image) — an outer `<td>` carrying block padding and, in auto
    mode, the `.image-<nodeId>-auto` coupling class, then a nested
    `<table><td style="width:...">` around the `<img>` itself. That inner
    `<td>` is what the auto-mode CSS rule's `> table td` selector actually
    targets — the rule is meaningless without this wrapper, so the two are
    implemented together, not the rule alone.

    Auto mode (`Image.props.width` absent — see `_assemble_email_block`):
    the `<img>`'s `width` attribute becomes the parent Section's own
    `containerWidth`, falling back to `Root.props.maxWidth` when the
    Section doesn't set one (inferred, not observed live — see the work
    item's Open questions), and a `.image-<nodeId>-auto > table td` rule
    caps it at the image's own `naturalWidth`.
    """
    node = craft_json[node_id]
    p = node["props"]
    style_rules: list[str] = []
    explicit_width = p.get("width")
    if explicit_width is not None:
        img_width: Any = explicit_width
        td_class = ""
    else:
        section_props = _ancestor_section_props(node_id, craft_json)
        container_width = section_props.get("containerWidth")
        if container_width is None:
            container_width = craft_json["ROOT"]["props"]["maxWidth"]
        img_width = container_width
        td_class = _image_auto_class(node_id)
        natural_width = p.get("naturalWidth")
        if natural_width is not None:
            style_rules.append(
                f".{td_class} > table td {{ width: 100% !important; "
                f"max-width: {natural_width}px; }}"
            )

    img = (
        f'<img alt="{escape(p.get("alt") or "")}" height="auto" '
        f'src="{escape(p["src"], quote=True)}" width="{img_width}" '
        'style="border:0;display:block;outline:none;text-decoration:none;'
        'height:auto;width:100%;font-size:13px;" />'
    )
    wrapped = (
        '<table border="0" cellpadding="0" cellspacing="0" role="presentation" '
        'width="100%" style="vertical-align:top;"><tr>'
        f'<td align="center" class="{td_class}" style="background:rgba(0,0,0,0);'
        "font-size:0px;padding:10px 25px;"
        f"padding-top:{p.get('containerPaddingTop', '10')}px;"
        f"padding-right:{p.get('containerPaddingRight', '10')}px;"
        f"padding-bottom:{p.get('containerPaddingBottom', '10')}px;"
        f"padding-left:{p.get('containerPaddingLeft', '10')}px;"
        'word-break:break-word;">'
        '<table border="0" cellpadding="0" cellspacing="0" role="presentation" '
        'style="border-collapse:collapse;border-spacing:0px;"><tr>'
        f'<td style="width:{img_width}px;">{img}</td>'
        "</tr></table>"
        "</td></tr></table>"
    )
    link = p.get("link")
    if link:
        wrapped = f'<a href="{escape(link, quote=True)}" target="_blank">{wrapped}</a>'
    return wrapped, style_rules


def _render_text(node: dict[str, Any], craft_json: dict[str, Any]) -> str:
    """The wrapper Kizen's own compiler puts around every Text block's copy
    — the `kizen-text-styles` class plus its typography, sourced from
    `Root.props` (confirmed against the reference's compiled `content`):
    `font-family`/`font-size`/`color` (rgba->hex converted) come from
    `Root.props`; `line-height:1`/`text-align:left` are fixed literals with
    no controlling Root prop in the reference. `custom.text` is still
    embedded verbatim inside it — this never touches BCLI-023's text model,
    only the wrapper *around* it."""
    root_props = craft_json["ROOT"]["props"]
    font_family = root_props.get("fontFamily", "Arial")
    font_size = root_props.get("fontSize", "14")
    color = _rgba_to_hex(root_props.get("color", "rgba(74,86,96,1)"))
    return (
        '<div class="kizen-text-styles" style="'
        f"font-family:{font_family};font-size:{font_size}px;line-height:1;"
        f'text-align:left;color:{color};">{node["custom"]["text"]}</div>'
    )


def _render_block(node_id: str, craft_json: dict[str, Any]) -> tuple[str, list[str]]:
    node = craft_json[node_id]
    name = _resolved_name(node)
    if name == "Text":
        # Embedded verbatim, not stripped — see craft_summary()'s _plain_text,
        # which tag-strips both sides before comparing.
        return _render_text(node, craft_json), []
    if name == "Image":
        return _render_image(node_id, craft_json)
    if name == "Button":
        return _render_button(node), []
    if name == "Divider":
        return _render_divider(node), []
    raise ValueError(f"cannot compile HTML for unsupported node type {name!r}")


def _render_cell(cell_id: str, craft_json: dict[str, Any]) -> tuple[str, list[str]]:
    node = craft_json[cell_id]
    html_parts: list[str] = []
    style_rules: list[str] = []
    for block_id in node["nodes"]:
        html, rules = _render_block(block_id, craft_json)
        html_parts.append(html)
        style_rules.extend(rules)
    return "".join(html_parts), style_rules


def _truncate4(value: float) -> float:
    """Truncate (not round) to 4 decimal places — matches the live-confirmed
    column-width convention (`293.3333`, not `293.3333333333333` or the
    rounded `293.3334`; `586.6666`, not the rounded `586.6667`)."""
    return math.floor(value * 10000) / 10000


def _row_content_width_px(row_id: str, craft_json: dict[str, Any]) -> float:
    """The row's actual compiled pixel width — the value the mso table and
    the `.section-<rowId> { max-width:...px; }` rule must use.

    Trusts an explicit `Row.props.containerWidth` when the spec set one
    directly (BCLI-024's independent, spec-settable field — the same
    "explicit, not derived" trust model this surface already uses for
    `Row.props.columns`/`Cell.props.__width`). Otherwise derives it from the
    parent `Section`'s own `maxWidth` and padding: content width = maxWidth
    - (paddingLeft + paddingRight) — the formula this module's `content
    width` comment already stated, now actually applied instead of frozen
    as one hardcoded constant. With every prop at its pre-BCLI-024 default
    (`Section.maxWidth: "900"`, padding `"10"` each side), this reduces to
    exactly `900 - 10 - 10 = 880.0`, the old hardcoded value — so unmodified
    specs compile identically to before.

    Either way, the result is then scaled by `Row.props.width` (percent,
    default `"100"`) — Kizen's real compiler applies this as a genuine
    multiplier on top of `containerWidth`/the derived width, confirmed
    against the reference template (`width: '75'` on a `containerWidth:
    '580'` row compiles to `435px`, not `580px`). A default `"100"` makes
    this a no-op.
    """
    row_props = craft_json[row_id]["props"]
    if "containerWidth" in row_props:
        base_width = float(row_props["containerWidth"])
    else:
        section_props = craft_json[craft_json[row_id]["parent"]]["props"]
        max_width = float(section_props["maxWidth"])
        pad_left = float(section_props.get("containerPaddingLeft", 0))
        pad_right = float(section_props.get("containerPaddingRight", 0))
        base_width = max_width - pad_left - pad_right
    return base_width * float(row_props.get("width", 100)) / 100


def _padding_css(props: dict[str, Any]) -> str:
    """CSS `padding` shorthand (top/right/bottom/left, no unit suffix on the
    value) from a node's own `containerPadding{Top,Right,Bottom,Left}`
    props — the same order `_render_button` already uses for its own
    padding. Reads whatever `Section`/`Row` actually carries in
    `craft_json`, default `"10"` or an explicit spec override alike, so
    `content` cannot silently disagree with `craft_json` the way it did
    before this fix (Section/Row padding never reached the compiled output
    at all)."""
    return (
        f"{props.get('containerPaddingTop', '0')}px "
        f"{props.get('containerPaddingRight', '0')}px "
        f"{props.get('containerPaddingBottom', '0')}px "
        f"{props.get('containerPaddingLeft', '0')}px"
    )


def _fmt_px(value: float) -> str:
    """Format a computed pixel length for the compiled CSS without a
    spurious trailing `.0` (`880.0px` -> `880px`) while leaving a genuine
    fractional value untouched (`293.3333px` stays `293.3333px`). Used at
    `_render_row`'s width call sites: `_row_content_width_px`'s two sites
    and the per-column `mso_widths_px` (`_truncate4`'s output)."""
    if value == int(value):
        return str(int(value))
    return str(value)


def _render_row(row_id: str, craft_json: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (body_html, style_rules) for one Row — `style_rules` is the
    row's own `max-width` rule followed by any rules its cells' blocks
    contributed (currently only an auto-mode Image's `.image-<id>-auto`
    rule, see `_render_image`)."""
    node = craft_json[row_id]
    columns = node["props"]["columns"]
    layout = _layout_for_columns(columns)
    cell_ids = [node["linkedNodes"][f"column-{i + 1}"] for i in range(len(columns))]
    content_width_px = _row_content_width_px(row_id, craft_json)
    content_width_str = _fmt_px(content_width_px)
    mso_widths_px = [
        _fmt_px(_truncate4(content_width_px * frac)) for frac in layout.columns
    ]

    parts = [
        f'<div class="{_section_class(row_id)}" '
        f'style="padding:{_padding_css(node["props"])};">'
    ]
    parts.append(
        '<!--[if mso | IE]><table align="center" border="0" cellpadding="0" '
        'cellspacing="0" role="presentation" '
        f'style="width:{content_width_str}px;"><tr>'
    )
    extra_style_rules: list[str] = []
    columns_data = zip(cell_ids, layout.classes, mso_widths_px, strict=True)
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
        cell_html, cell_style_rules = _render_cell(cid, craft_json)
        parts.append(cell_html)
        extra_style_rules.extend(cell_style_rules)
        parts.append("</div>")
    parts.append("<!--[if mso | IE]></td></tr></table><![endif]-->")
    parts.append("</div>")

    style_rule = f".{_section_class(row_id)} {{ max-width:{content_width_str}px; }}"
    return "".join(parts), [style_rule, *extra_style_rules]


def _render_section(
    section_id: str, craft_json: dict[str, Any]
) -> tuple[str, list[str]]:
    node = craft_json[section_id]
    props = node["props"]
    bg = props.get("containerBackgroundColor", "#FFFFFF")
    rows_html: list[str] = []
    style_rules = [f".{_section_class(section_id)} {{ background-color:{bg}; }}"]
    for row_id in node["nodes"]:
        row_html, row_rules = _render_row(row_id, craft_json)
        rows_html.append(row_html)
        style_rules.extend(row_rules)
    section_padding = _padding_css(props)
    body = (
        f'<div class="{_section_class(section_id)}" style="padding:{section_padding};">'
        + "".join(rows_html)
        + "</div>"
    )

    # The outer background-table wrapper `Section.props.containerWidth`
    # needs, deferred here from BCLI-024 (see that item's Outcome). Only
    # added when the spec set an explicit container_width — matching this
    # surface's existing "None means no override, byte-identical to
    # pre-this-prop output" convention (see `_section_props`), and the only
    # case this module has real reference evidence for. Simplified relative
    # to Kizen's own VML/background-image fallback markup (this emitter has
    # no background-image concept at all, only `background_color`) — see the
    # work item's report for the scoping call.
    container_width = props.get("containerWidth")
    if container_width is not None:
        style_rules.append(
            f".{_section_class(section_id)} {{ max-width:{container_width}px; }}"
        )
        body = (
            '<!--[if mso | IE]><table border="0" cellpadding="0" cellspacing="0" '
            f'role="presentation" align="center" width="{container_width}" '
            f'style="width:{container_width}px;"><tr><td>'
            "<![endif]-->" + body + "<!--[if mso | IE]></td></tr></table><![endif]-->"
        )
    return body, style_rules


def _distinct_column_widths(craft_json: dict[str, Any]) -> dict[str, str]:
    """Every distinct `mj-column-per-N` class in use across this template's
    `Row` nodes, mapped to its media-query width percentage. Shared by
    `_column_base_width_rules`, `_media_query_rules`, and
    `_moz_text_html_style_block` so the three rule sets can't independently
    drift on which classes exist."""
    seen: dict[str, str] = {}
    for node in craft_json.values():
        if _resolved_name(node) != "Row":
            continue
        columns = node["props"]["columns"]
        layout = _layout_for_columns(columns)
        for cls, media_w in zip(layout.classes, layout.media_widths, strict=True):
            seen[cls] = media_w
    return seen


def _column_base_width_rules(craft_json: dict[str, Any]) -> list[str]:
    """Unconditional `.mj-column-per-N` width rules — MJML's own convention.

    Each column `<div>` also carries a hardcoded inline `width:100%` (see
    `_render_row`), which is the fallback for clients that ignore `<style>`
    entirely. This unconditional rule is what overrides that fallback for
    every CSS-aware client at normal viewport widths, so multi-column rows
    render side by side by default; `_media_query_rules` below is what
    collapses them back to full width on narrow viewports.
    """
    return [
        f".{cls} {{ width:{w} !important; max-width:{w}; }}"
        for cls, w in sorted(_distinct_column_widths(craft_json).items())
    ]


def _media_query_rules(craft_json: dict[str, Any]) -> list[str]:
    """`max-width:<mobileBreak>px` rules that collapse every column to full
    width, so a narrow viewport stacks instead of staying multi-column."""
    return [
        f".{cls} {{ width:100% !important; max-width:100%; }}"
        for cls in sorted(_distinct_column_widths(craft_json))
    ]


def _moz_text_html_style_block(craft_json: dict[str, Any], mobile_break: str) -> str:
    """Gecko-based mail clients (Thunderbird and others) key column-stacking
    behaviour off a `.moz-text-html`-prefixed selector rather than the plain
    `.mj-column-per-N` rule `_column_base_width_rules` emits — its absence
    is real and recipient-visible, scoped to that client family. Same
    class/width pairs, wrapped in Kizen's own `min-width` media-attribute
    convention (confirmed against the reference template's compiled
    `content`) so it only applies above the same `mobileBreak` breakpoint."""
    widths = _distinct_column_widths(craft_json)
    if not widths:
        return ""
    rules = "".join(
        f".moz-text-html .{cls} {{ width:{w} !important; max-width:{w}; }} "
        for cls, w in sorted(widths.items())
    )
    return f'<style media="screen and (min-width:{mobile_break}px)">{rules}</style>'


def _compile_html(craft_json: dict[str, Any]) -> str:
    root = craft_json["ROOT"]
    root_props = root["props"]
    bodies: list[str] = []
    style_rules: list[str] = []
    for section_id in root["nodes"]:
        body, rules = _render_section(section_id, craft_json)
        bodies.append(body)
        style_rules.extend(rules)

    # Confirmed live 2026-08-26: `Root.props.mobileBreak` (`"414"` by
    # default), not the hardcoded `480` this module used before. The `480`
    # fallback below only applies if a caller's `craft_json` predates this
    # prop entirely — every tree this module itself builds always has it.
    mobile_break = str(root_props.get("mobileBreak", "480"))
    column_rules = _column_base_width_rules(craft_json)
    media_rules = _media_query_rules(craft_json)
    style_block = (
        '<style type="text/css">'
        ".mj-outlook-group-fix{width:100% !important;}"
        + "".join(style_rules)
        + "".join(column_rules)
        + (
            f"@media only screen and (max-width:{mobile_break}px){{"
            + "".join(media_rules)
            + "}"
            if media_rules
            else ""
        )
        + "</style>"
        + _moz_text_html_style_block(craft_json, mobile_break)
    )
    link_color = _rgba_to_hex(root_props.get("linkColor", "rgba(82,142,249,1)"))
    kizen_text_styles_block = (
        "<style>"
        + _KIZEN_TEXT_STYLES_TEMPLATE.format(link_color=link_color)
        + "</style>"
    )
    body_bg = root_props.get("backgroundColor", "#F8FAFF")
    return (
        "<!doctype html>"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        + _MJML_RESET_STYLE
        + "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings>"
        "<o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings>"
        "</xml></noscript><![endif]-->"
        + style_block
        + kizen_text_styles_block
        + "</head>"
        f'<body style="word-spacing:normal;background-color:{body_bg};">'
        f'<div style="background-color:{body_bg};">'
        + "".join(bodies)
        + "</div></body></html>"
    )

"""Tests for `tools.email_craft` — the email-template emitter.

Pins the load-bearing facts from the work item's live probe (2026-08-25):
  * the 4 v1 column presets' `columns`/`__width` fractions and compiled-HTML
    markup (`mj-column-per-N` classes, `@media` widths, `<!--[if mso]-->`
    `<td>` widths) are byte-exact, not recomputed;
  * `craft_json` and `content` are coupled by node id — every `Section`/`Row`
    id has a matching `section-<id>` class in the compiled HTML and vice
    versa, checked here the same way `tools.messages.craft_summary()` does;
  * PNG and JPEG header-byte pixel-dimension parsing, with GIF/WebP/SVG
    failing loudly;
  * an unsupported block kind or out-of-v1-scope layout is a clear error,
    never a silent reshape.

All fixtures here are synthetic (hand-built minimal PNG/JPEG bytes, no
captured personal data), per the item's "no real customer data" rule.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from kizen_builder.models.spec.email_templates import (
    COLUMN_FRACTIONS,
    EmailTemplateDef,
    ParagraphDef,
)
from kizen_builder.tools import email_craft as ec
from kizen_builder.tools import email_html as eh
from kizen_builder.tools import form_ui
from kizen_builder.tools.messages import craft_summary

_SECTION_CLASS = re.compile(r"section-([0-9a-f]{6,})")


def _split_style_block(content: str) -> tuple[str, str]:
    """(base_css, media_css) — split this module's own main `<style>` block
    (the one carrying `.mj-outlook-group-fix{width:100% !important;}` as its
    first rule — the head also carries the static MJML reset block, and, if
    `Row`s exist, the `.moz-text-html` style block, both `<style>` tags of
    their own) at its `@media` query, so a test can tell a base
    (unconditional) rule from a mobile-collapse rule instead of merely
    checking presence anywhere in the document. That distinction is the
    whole point: a column-width rule that only exists inside `@media
    (max-width:...px)` renders stacked at desktop width, which is the
    inverted-CSS bug this module fixes."""
    marker = ".mj-outlook-group-fix{width:100% !important;}"
    start = content.index(marker)
    style = content[start : content.index("</style>", start)]
    if "@media" not in style:
        return style, ""
    base, media = style.split("@media only screen and (max-width:", 1)
    _breakpoint, media = media.split("px){", 1)
    # Strip exactly the one closing brace that ends the @media block itself
    # (not a rule's own closing brace).
    return base, media[:-1] if media.endswith("}") else media


def _resolved_name(node: dict) -> str:
    t = node.get("type")
    return t.get("resolvedName") if isinstance(t, dict) else t


def _ids_of(craft_json: dict, *kinds: str) -> set[str]:
    return {nid for nid, node in craft_json.items() if _resolved_name(node) in kinds}


# ---------------------------------------------------------------------------
# Synthetic PNG/JPEG builders (no external dependency, no captured data)
# ---------------------------------------------------------------------------


def _make_png(width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = (b"\x00" + b"\xff\x00\x00" * width) * height
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _make_jpeg(width: int, height: int) -> bytes:
    soi = b"\xff\xd8"
    sof0_payload = (
        struct.pack(">B", 8)
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + struct.pack(">B", 1)
        + struct.pack(">BBB", 1, 0x11, 0)
    )
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    return soi + sof0


# ---------------------------------------------------------------------------
# Column-layout table — byte-exact, confirmed live 2026-08-25
# ---------------------------------------------------------------------------


def test_v1_layouts_are_exactly_the_four_confirmed_live():
    assert ec.known_layouts() == [
        "1 Column",
        "2 Columns",
        "2 Columns (1/3 and 2/3)",
        "2 Columns (2/3 and 1/3)",
    ]


def test_column_fractions_are_byte_exact_not_rounded():
    assert ec.COLUMN_LAYOUTS["1 Column"].columns == (1,)
    assert ec.COLUMN_LAYOUTS["2 Columns"].columns == (0.5, 0.5)
    assert ec.COLUMN_LAYOUTS["2 Columns (1/3 and 2/3)"].columns == (
        0.3333333333333333,
        0.6666666666666666,
    )
    assert ec.COLUMN_LAYOUTS["2 Columns (2/3 and 1/3)"].columns == (
        0.6666666666666666,
        0.3333333333333333,
    )
    # The spec model's own table must agree with the emitter's.
    for name, layout in ec.COLUMN_LAYOUTS.items():
        assert COLUMN_FRACTIONS[name] == layout.columns


def test_two_thirds_one_third_compiled_markup_matches_live_probe():
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell([ec.text_block("<p>a</p>")]),
                        ec.cell([ec.text_block("<p>b</p>")]),
                    ],
                    layout="2 Columns (1/3 and 2/3)",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    base_css, media_css = _split_style_block(content)
    assert "mj-column-per-33-333332" in content
    assert "mj-column-per-66-666664" in content
    # The column widths are BASE (unconditional) rules — this is what makes
    # the row render side by side at desktop width, not just under 480px.
    assert "width:33.333332% !important; max-width:33.333332%;" in base_css
    assert "width:66.666664% !important; max-width:66.666664%;" in base_css
    assert "33.333332%" not in media_css
    assert "66.666664%" not in media_css
    # The media query's job is to COLLAPSE both columns to full width.
    assert (
        ".mj-column-per-33-333332 { width:100% !important; max-width:100%; }"
        in media_css
    )
    assert (
        ".mj-column-per-66-666664 { width:100% !important; max-width:100%; }"
        in media_css
    )
    assert "width:293.3333px;" in content
    assert "width:586.6666px;" in content


def test_one_column_and_two_column_compiled_markup_matches_live_probe():
    sections = [
        ec.section(
            [
                ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column"),
                ec.row(
                    [
                        ec.cell([ec.text_block("<p>a</p>")]),
                        ec.cell([ec.text_block("<p>b</p>")]),
                    ],
                    layout="2 Columns",
                ),
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    base_css, media_css = _split_style_block(content)
    assert "mj-column-per-100" in content
    # 880px, not 880.0px, at every width call site in `_render_row` —
    # `_row_content_width_px`'s two sites and the per-column `mso_widths_px`
    # (`_truncate4`'s output) alike. See the float-formatting fix (BCLI-025
    # item 7).
    assert 'role="presentation" style="width:880px;"><tr>' in content
    assert " { max-width:880px; }" in content
    assert 'width:880px;">' in content  # the 1-Column row's own mso <td>
    assert "width:880.0px;" not in content
    # 2 Columns: one base rule + one media-collapse rule + one
    # `.moz-text-html`-prefixed rule (BCLI-025 item 2) + one div per column
    # = 5 occurrences of the class name.
    assert content.count("mj-column-per-50") == 5
    assert ".mj-column-per-50 { width:50% !important; max-width:50%; }" in base_css
    assert ".mj-column-per-50 { width:100% !important; max-width:100%; }" in media_css
    assert "width:50%" not in media_css
    # The 2-Columns row's own two `content_width_px` call sites are unchanged
    # from the 1-Column row above (same Section, so still 880/880px, not
    # split). "440px" here is the per-column `mso_widths_px` (`_truncate4`,
    # 0.5 * 880), once per column.
    assert content.count("width:440px;") == 2
    assert "width:440.0px;" not in content
    assert not re.search(r"\.0px", content)


def test_button_anchor_declarations_match_a_real_captured_template():
    """Re-verified 2026-08-26, read-only, against a fresh capture of the
    reference template on `cli-testing`: the `<a>` tag's own `style`
    declarations (`color` as `_rgba_to_hex`-converted hex, and the five
    declarations — `font-weight:normal`, `line-height:120%`, `margin:0`,
    `text-transform:none`, `mso-padding-alt:0px` — in this exact position
    and order) are byte-exact against Kizen's own compiler, independently
    confirmed across all five Button nodes in that template. The real
    template's own copy/URL/color never enter this repo (personal-data
    rule) — the values below are synthetic, chosen to exercise the same
    code path.

    **This assertion is scoped to the `<a>` tag only.** The enclosing
    `<table>`/`<td>` markup below is NOT verified against that capture and
    is known to also diverge from it (no `align` attribute on the real
    `<table>` at all; the real `<td>` carries different attributes and
    style) — out of this item's scope, see
    `00-inbox/button-table-and-divider-markup-diverge-further.md`. Do not
    read the `<table>`/`<td>` portion of this assertion as a live-verified
    claim; it pins the emitter's current (known-diverging) output only, so
    an unrelated future change doesn't silently reshape it unnoticed.

    **Previously corrected 2026-08-24 in BCLI-024 review**: the original
    assertion was missing the button table's own `align="center"` and
    `line-height:100%;` — this correction attributed `align="center"` to the
    `<table>`, which the fresh capture above shows is wrong (it is the
    `<td>` that carries `align="center"` in the real markup); left as-is
    here since fixing it is part of the follow-on note above, not this
    item's scope."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.button_block(
                                    "Click Here", "https://example.com", color="#1B64F2"
                                )
                            ]
                        ),
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert (
        '<table role="presentation" align="center" border="0" cellpadding="0" '
        'cellspacing="0" style="border-collapse:separate;line-height:100%;">'
        "<tr><td "
        'style="border-radius:8px;background:#1B64F2;text-align:center;" '
        'bgcolor="#1B64F2"><a href="https://example.com" target="_blank" '
        'style="display:inline-block;background:#1B64F2;color:#ffffff;'
        "font-family:Arial;font-size:16px;font-weight:normal;line-height:120%;"
        "margin:0;text-decoration:none;text-transform:none;"
        "padding:10px 20px 10px 20px;mso-padding-alt:0px;"
        'border-radius:8px;">Click Here</a></td></tr></table>'
    ) in content


def test_divider_markup_pins_current_shape_not_checked_against_a_capture():
    """`_render_divider`'s output — pins the emitter's current shape, not
    checked against a real capture. A fresh read-only capture of the
    reference template's two Divider nodes (2026-08-26) shows Kizen's real
    compiled Divider diverges substantially from this — it is wrapped in a
    two-level `<table><td>` (this emitter emits a bare `<p>`), the `<p>`'s
    own style differs (`border-top:solid 1px #color` — style before size,
    not size before style; `margin:0px auto`, not `0 auto`; genuinely empty
    `<p></p>`, not `<p> </p>`), and Kizen adds an MSO-conditional `<table>`
    fallback this emitter has none of. Fixing this is out of this item's
    scope (only `_render_button`'s `<a>` tag was in scope) — see
    `00-inbox/button-table-and-divider-markup-diverge-further.md`."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.divider_block("#E5E7EB")])],
                    layout="1 Column",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert (
        '<p style="border-top:3px solid #E5E7EB;font-size:1px;margin:0 auto;'
        'width:100%;"> </p>'
    ) in content


def test_render_image_fixed_width_compiled_markup_matches_the_reference_shape():
    """`_render_image`'s output shape — re-derived from the reference
    template's real compiled `content` (read-only `GET`, 2026-08-25) and
    **independently re-verified against a fresh capture of the same
    template, 2026-08-26**, as part of BCLI-023's audit of this file's other
    byte-exact claims: the fresh capture's fixed-width `<img>` tag matches
    this test's asserted attribute/style set exactly (only the surrounding
    `<table>`/`<td>` differs by an explicit `<tbody>` the real markup emits
    and this emitter doesn't — not asserted here, so it doesn't affect this
    claim). Kizen wraps every Image in a two-level table (`<td class="...">`
    carrying block padding, a nested `<table><td style="width:...">` around
    the `<img>`), not a bare `<img>` tag as this emitter produced before
    BCLI-025 — the `<img>`'s own attributes/style are byte-exact against the
    reference; the wrapper is asserted structurally (tag/attribute
    placement), not as one giant pinned string, so a future formatting-only
    tweak to the wrapper doesn't make this test as brittle as re-deriving
    the whole thing by hand."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://example.com/logo.png",
                                    name="logo.png",
                                    alt="Logo",
                                    natural_width=200,
                                    natural_height=100,
                                    width=150,
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    assert (
        '<img alt="Logo" height="auto" src="https://example.com/logo.png" '
        'width="150" style="border:0;display:block;outline:none;'
        'text-decoration:none;height:auto;width:100%;font-size:13px;" />'
    ) in content
    assert "data-natural-width" not in content
    assert "data-natural-height" not in content

    image_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Image"
    )
    # Fixed mode: no `-auto` class, and no auto-mode CSS rule at all.
    assert f'class="{eh._image_auto_class(image_id)}"' not in content
    assert f".{eh._image_auto_class(image_id)} > table td" not in content
    # The outer block-level <td> carries the image's own containerPadding.
    outer_td = re.search(
        r'<td align="center" class="" style="background:rgba\(0,0,0,0\);'
        r"font-size:0px;padding:10px 25px;padding-top:(\d+)px;"
        r"padding-right:(\d+)px;padding-bottom:(\d+)px;padding-left:(\d+)px;"
        r'word-break:break-word;">',
        content,
    )
    assert outer_td, "no block-level <td> wrapper found around the Image"
    assert outer_td.groups() == ("10", "10", "10", "10")
    # The inner nested table's own <td> carries the img's pixel width.
    assert '<td style="width:150px;">' in content


def test_render_image_with_link_wraps_the_whole_table_in_an_anchor():
    """Pins current output shape; not verified against a real captured
    template. The reference template's one worked Image example (the hero)
    has no `link`, so this case has never been checked against Kizen's own
    compiled `content` — this test only guards against `_render_image`'s
    `<a>`-wrapping silently changing shape, e.g. reverting to wrapping just
    the bare `<img>` instead of the whole two-level table."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://example.com/logo.png",
                                    name="logo.png",
                                    alt="Logo",
                                    link="https://example.com/landing",
                                    natural_width=200,
                                    natural_height=100,
                                    width=150,
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    anchor_open = '<a href="https://example.com/landing" target="_blank">'
    assert anchor_open in content
    start = content.index(anchor_open) + len(anchor_open)
    # The anchor wraps the ENTIRE image table, not just the <img> tag —
    # everything between the anchor open and its matching close is the
    # two-level table this fix is pinning, ending right where the <img>'s
    # own markup does.
    assert content[start : start + len('<table border="0"')] == '<table border="0"'
    img_end = content.index("/>", start) + len("/>")
    assert content[
        img_end : img_end + len("</td></tr></table></td></tr></table></a>")
    ] == ("</td></tr></table></td></tr></table></a>")


def test_render_image_auto_mode_uses_section_container_width_and_natural_width_rule():
    """Auto mode (`width` omitted in the spec): the `<img>`'s `width`
    attribute becomes the parent Section's own `containerWidth`, and a
    `.image-<nodeId>-auto > table td` rule caps it at the image's own
    `naturalWidth` — both confirmed against the reference template's one
    real auto-mode Image node (`containerWidth: 600`, `naturalWidth: 1200`
    -> `width="600"` and `max-width: 1200px` in that exact rule shape)."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://example.com/hero.png",
                                    name="hero.png",
                                    alt="Hero",
                                    natural_width=1200,
                                    natural_height=630,
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                    container_width="600",
                )
            ],
            container_width="900",
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    image_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Image"
    )
    assert "width" not in craft_json[image_id]["props"]
    assert craft_json[image_id]["props"]["size"] == "auto"

    auto_class = eh._image_auto_class(image_id)
    assert f'class="{auto_class}"' in content
    assert (
        f".{auto_class} > table td {{ width: 100% !important; max-width: 1200px; }}"
        in content
    )
    # Auto width comes from the Row's parent SECTION's own containerWidth
    # (900), not the Row's own containerWidth (600) and not the image's own
    # naturalWidth (1200) — the fallback chain's first, most-specific rung.
    assert (
        '<img alt="Hero" height="auto" src="https://example.com/hero.png" '
        'width="900"' in content
    )
    assert '<td style="width:900px;">' in content


def test_render_image_auto_mode_falls_back_to_root_max_width_when_section_unset():
    """When the parent Section has no explicit `container_width` at all
    (the common case for an unmodified spec), auto mode falls back to
    `Root.props.maxWidth`. Inferred, not observed live — the work item's
    Open questions flags this as the fallback with the weakest evidence in
    this item, since the reference template's one auto-mode image had an
    explicit `Section.containerWidth` set."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://example.com/hero.png",
                                    name="hero.png",
                                    natural_width=1200,
                                    natural_height=630,
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    assert "containerWidth" not in craft_json[section_id]["props"]
    # EMAIL_ROOT_PROPS["maxWidth"] == "900".
    assert 'width="900"' in content
    assert '<td style="width:900px;">' in content


def test_image_and_section_class_conventions_share_id_formatting():
    """The `.image-<nodeId>-auto` and `.section-<nodeId>` conventions must
    not drift apart from each other — pinned here, together, per the work
    item's explicit constraint, rather than trusting two independently
    hand-rolled f-strings to stay in sync. Both wrap the SAME node id in
    the SAME `<prefix>-<nodeId>[-suffix]` shape."""
    for node_id in ["abc123", "0" * 24, "a-node-with-dashes"]:
        assert eh._section_class(node_id) == f"section-{node_id}"
        assert eh._image_auto_class(node_id) == f"image-{node_id}-auto"


def test_exactly_one_tr_per_row_regardless_of_column_count():
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([]), ec.cell([])],
                    layout="2 Columns",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    # One opening mso <tr> for the row's own mso table, not one per column.
    assert content.count("<tr>") == 1


# ---------------------------------------------------------------------------
# The coupling rule: Section/Row ids <-> section-<id> classes, both ways
# ---------------------------------------------------------------------------


def test_every_section_and_row_id_has_a_matching_class_and_vice_versa():
    sections = [
        ec.section(
            [
                ec.row([ec.cell([ec.text_block("<p>one</p>")])], layout="1 Column"),
                ec.row(
                    [
                        ec.cell([ec.button_block("Go", "https://example.com")]),
                        ec.cell([ec.divider_block()]),
                    ],
                    layout="2 Columns",
                ),
            ]
        ),
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>two</p>")])], layout="1 Column")]
        ),
    ]
    craft_json, content = ec.build_email_content(sections)
    node_ids = _ids_of(craft_json, "Section", "Row")
    html_classes = set(_SECTION_CLASS.findall(content))
    assert node_ids == html_classes
    # 2 sections + 3 rows (2 in the first section, 1 in the second).
    assert len(node_ids) == 5


def test_craft_summary_reports_coupled_and_text_in_sync_for_emitted_output():
    """The real end-to-end check: run the drift detector this item reuses
    (`craft_summary`, unmodified) against this emitter's own output."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>Hello <strong>World</strong></p>")])],
                    layout="1 Column",
                ),
                ec.row(
                    [
                        ec.cell([ec.button_block("Go", "https://example.com")]),
                        ec.cell([ec.divider_block()]),
                    ],
                    layout="2 Columns",
                ),
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    summary = craft_summary({"craft_json": craft_json, "content": content})
    assert summary["structure_coupled"] is True
    assert summary["text_in_sync"] is True
    assert summary["coupled"] is True


def test_text_block_html_is_embedded_verbatim_in_content():
    html = '<p><span style="font-size: 18px">Hi <em>there</em></span></p>'
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block(html)])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert html in content


# ---------------------------------------------------------------------------
# Cell.props.__width — the additive form_ui hook this item adds
# ---------------------------------------------------------------------------


def test_cell_props_carries_double_underscore_width_matching_row_columns():
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell([ec.text_block("<p>a</p>")]),
                        ec.cell([ec.text_block("<p>b</p>")]),
                    ],
                    layout="2 Columns (2/3 and 1/3)",
                )
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    cells = [n for n in craft_json.values() if _resolved_name(n) == "Cell"]
    widths = sorted(c["props"]["__width"] for c in cells)
    assert widths == sorted([0.6666666666666666, 0.3333333333333333])


def test_form_ui_cell_props_hook_defaults_to_empty_dict_props():
    """Regression: the hook this module relies on must default to today's
    forms/layouts behaviour when not passed — see
    tests/test_form_ui_payloads.py and tests/test_layout_custom_content.py
    for the full guard."""
    tree = form_ui.build_content_tree(
        [form_ui.section([form_ui.row([form_ui.cell([form_ui.text_block("x")])])])]
    )
    cell = next(
        n for n in tree.values() if n.get("type", {}).get("resolvedName") == "Cell"
    )
    assert cell["props"] == {}


# ---------------------------------------------------------------------------
# Validation: row/layout, unsupported block kind — never a silent reshape
# ---------------------------------------------------------------------------


def test_row_rejects_cell_count_mismatch_for_its_layout():
    with pytest.raises(ValueError, match="needs 2 cell"):
        ec.row([ec.cell([])], layout="2 Columns")


def test_row_rejects_unknown_layout_name():
    with pytest.raises(ValueError, match="unknown row layout"):
        ec.row([ec.cell([])], layout="Fancy Layout")


@pytest.mark.parametrize(
    "layout",
    ["3 Columns", "3 Columns (gutters)", "4 Columns", "5 Columns", "6 Columns"],
)
def test_row_rejects_out_of_v1_scope_layouts_by_name(layout):
    with pytest.raises(ValueError, match="out of v1 scope"):
        ec.row([ec.cell([])], layout=layout)


def test_unsupported_block_kind_fails_loudly():
    with pytest.raises(ValueError, match="unsupported email block kind"):
        ec.build_email_content(
            [
                ec.section(
                    [ec.row([ec.cell([{"kind": "attachments"}])], layout="1 Column")]
                )
            ]
        )


# ---------------------------------------------------------------------------
# Image header-byte dimension parsing — PNG and JPEG, GIF/WebP/SVG rejected
# ---------------------------------------------------------------------------


def test_png_dimensions_read_from_header_bytes():
    png = _make_png(37, 21)
    assert ec.read_image_dimensions(png) == (37, 21, "image/png")


def test_jpeg_dimensions_read_from_sof0_segment():
    jpg = _make_jpeg(64, 48)
    assert ec.read_image_dimensions(jpg) == (64, 48, "image/jpeg")


@pytest.mark.parametrize(
    "label,data",
    [
        ("gif", b"GIF89a" + b"\x00" * 20),
        ("webp", b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20),
        ("svg", b'<?xml version="1.0"?><svg></svg>'),
        ("bogus", b"not an image at all, just text"),
    ],
)
def test_unsupported_image_formats_fail_loudly(label, data):
    with pytest.raises(ValueError):
        ec.read_image_dimensions(data)


def test_offline_resolve_spec_images_reads_local_dims_without_uploading(tmp_path: Path):
    png_path = tmp_path / "logo.png"
    png_path.write_bytes(_make_png(64, 32))
    spec = EmailTemplateDef.model_validate(
        {
            "name": "Synthetic",
            "sections": [
                {
                    "rows": [
                        {
                            "layout": "1 Column",
                            "cells": [
                                {
                                    "blocks": [
                                        {
                                            "kind": "image",
                                            "file": str(png_path),
                                            "alt": "logo",
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    craft_json, _content = ec.build_email_content(sections)
    image_node = next(n for n in craft_json.values() if _resolved_name(n) == "Image")
    assert image_node["props"]["naturalWidth"] == 64
    assert image_node["props"]["naturalHeight"] == 32
    assert image_node["props"]["fileId"] == ec.OFFLINE_FILE_PLACEHOLDER
    assert image_node["props"]["alt"] == "logo"


# ---------------------------------------------------------------------------
# Golden fixture — deterministic ids, byte-exact output for a small synthetic
# template. Regression net for accidental format drift in the emitter.
# ---------------------------------------------------------------------------


def _props_of(craft_json: dict, kind: str) -> dict:
    return next(n["props"] for n in craft_json.values() if _resolved_name(n) == kind)


def _all_props_of(craft_json: dict, kind: str) -> list[dict]:
    return [n["props"] for n in craft_json.values() if _resolved_name(n) == kind]


# ---------------------------------------------------------------------------
# Layout props (BCLI-024) — actual emitted prop values and placement, not
# merely "the key exists somewhere". The regression test pins the
# all-defaults case byte-identical to the pre-this-item emitter's exact
# key set — see the model's own defaults for why (SectionDef.max_width is
# "900", not the reference's "600").
# ---------------------------------------------------------------------------


def test_all_defaults_spec_is_byte_identical_to_the_pre_item_section_and_row_props():
    """Pins BCLI-024's own regression-safety acceptance criterion: a spec
    that sets none of the new layout fields must reproduce the *exact* key
    set `form_ui._assemble_section`/`_assemble_row` produced before this
    item — in particular, no `containerWidth` key at all (it was never
    written before this item added the field)."""
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    craft_json, _content = ec.build_email_content(sections)

    section_props = _props_of(craft_json, "Section")
    assert section_props["maxWidth"] == "900"
    assert section_props["width"] == "100"
    assert "containerWidth" not in section_props
    assert section_props["containerPaddingTop"] == "10"
    assert section_props["containerPaddingRight"] == "10"
    assert section_props["containerPaddingBottom"] == "10"
    assert section_props["containerPaddingLeft"] == "10"

    row_props = _props_of(craft_json, "Row")
    assert row_props["maxWidth"] == "900"
    assert row_props["width"] == "100"
    assert "containerWidth" not in row_props
    assert row_props["containerPaddingTop"] == "10"
    assert row_props["containerPaddingRight"] == "10"
    assert row_props["containerPaddingBottom"] == "10"
    assert row_props["containerPaddingLeft"] == "10"


def test_section_layout_props_are_emitted_when_set_matching_the_reference_pattern():
    """Values chosen to match the pattern independently confirmed live
    against the reference template's `Section` nodes (BCLI-024 Context):
    `maxWidth: '600'`, `containerWidth: '900'`, uniform padding `10` (or
    `0` on the one full-bleed section observed)."""
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")],
            max_width="600",
            container_width="900",
            padding={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Section")
    assert props["maxWidth"] == "600"
    assert props["containerWidth"] == "900"
    assert props["containerPaddingTop"] == "0"
    assert props["containerPaddingRight"] == "0"
    assert props["containerPaddingBottom"] == "0"
    assert props["containerPaddingLeft"] == "0"


def test_row_layout_props_are_emitted_when_set_including_asymmetric_padding():
    """Values chosen to match the reference's non-uniform rows (BCLI-024
    Context): one row at `width: '75'` (not `'100'`), one row with
    asymmetric `containerPaddingLeft/Right: '40'` against `'10'`
    top/bottom — `Row` props are independent, not derived from `Section`."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>x</p>")])],
                    layout="1 Column",
                    width="75",
                    container_width="600",
                    padding={"top": "10", "right": "40", "bottom": "10", "left": "40"},
                )
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Row")
    assert props["width"] == "75"
    assert props["containerWidth"] == "600"
    assert props["containerPaddingTop"] == "10"
    assert props["containerPaddingRight"] == "40"
    assert props["containerPaddingBottom"] == "10"
    assert props["containerPaddingLeft"] == "40"


def test_row_layout_props_are_independent_per_row_not_uniform_across_a_section():
    """The load-bearing design fact this item's Context established: `Row`
    layout props don't follow a clean formula from the parent `Section`, so
    two rows in the same section can carry different values."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>a</p>")])],
                    layout="1 Column",
                    container_width="580",
                ),
                ec.row(
                    [ec.cell([ec.text_block("<p>b</p>")])],
                    layout="1 Column",
                    container_width="600",
                ),
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    widths = sorted(p["containerWidth"] for p in _all_props_of(craft_json, "Row"))
    assert widths == ["580", "600"]


# ---------------------------------------------------------------------------
# Compiled `content` must track the SAME width `craft_json` carries — a
# blocking defect found in review: `content`'s mso table/media widths were
# still pinned to a module-level 880px constant, never reacting to
# `Section.max_width`/`Row.container_width`/padding, so a spec with
# `max_width: "600"` produced a `craft_json` that renders at 600px in
# Kizen's builder and a `content` that still renders at 880px — the exact
# two-fields-disagree failure this whole surface exists to prevent
# (`craft_summary()` can't see it: node ids and text both still match).
# ---------------------------------------------------------------------------


def _row_style_max_width_px(content: str, row_id: str) -> str:
    m = re.search(rf"\.section-{row_id} \{{ max-width:([0-9.]+)px; \}}", content)
    assert m, f"no max-width rule found for row {row_id}"
    return m.group(1)


def _mso_table_width_px(content: str, row_id: str) -> str:
    # The row's own opening <div class="section-{row_id}" ...> is
    # immediately followed by its mso table; scope the search to that row's
    # own fragment so a multi-row template can't match a sibling row's
    # table. Matches on the class prefix only (not a full tag), since the
    # div also carries a `style="padding:...;"` attribute.
    start = content.index(f'<div class="section-{row_id}"')
    fragment = content[start : start + 400]
    m = re.search(r'role="presentation" style="width:([0-9.]+)px;"', fragment)
    assert m, f"no mso table width found for row {row_id}"
    return m.group(1)


def _row_padding_css(content: str, row_id: str) -> str:
    m = re.search(rf'<div class="section-{row_id}" style="padding:([^;"]+);"', content)
    assert m, f"no padding style found on row {row_id}'s own wrapper div"
    return m.group(1)


def _section_padding_css(content: str, section_id: str) -> str:
    m = re.search(
        rf'<div class="section-{section_id}" style="padding:([^;"]+);"', content
    )
    assert m, f"no padding style found on section {section_id}'s own wrapper div"
    return m.group(1)


def test_compiled_content_row_width_tracks_section_max_width_with_default_padding():
    """The coordinator's own repro: `max_width: '600'` on a section with the
    default (uniform `'10'`) padding must compile to 580px in `content`,
    not the old hardcoded 880px."""
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")],
            max_width="600",
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _row_style_max_width_px(content, row_id) == "580"
    assert _mso_table_width_px(content, row_id) == "580"


def test_compiled_content_row_width_tracks_section_max_width_with_zero_padding():
    """Same section `max_width`, but full-bleed (`padding: 0/0/0/0`) — must
    compile to 600px, not 580px and not 880px."""
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")],
            max_width="600",
            padding={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _row_style_max_width_px(content, row_id) == "600"
    assert _mso_table_width_px(content, row_id) == "600"


def test_compiled_content_row_width_defaults_to_880_matching_pre_bcli_024_output():
    """No overrides at all: must still compile to 880px — today's exact
    pre-existing hardcoded value, now *derived* (900 Section maxWidth - 10 -
    10 padding) rather than frozen as a constant."""
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _row_style_max_width_px(content, row_id) == "880"
    assert _mso_table_width_px(content, row_id) == "880"


def test_compiled_content_row_width_prefers_explicit_row_container_width_over_section():
    """An explicit `Row.container_width` is trusted directly, even when it
    disagrees with what the Section-derived formula would produce —
    matching BCLI-024's "independent, explicit fields" design, the same
    trust model as `Row.props.columns`/`Cell.props.__width`."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>x</p>")])],
                    layout="1 Column",
                    container_width="450",
                )
            ],
            max_width="600",
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert craft_json[row_id]["props"]["containerWidth"] == "450"
    assert _row_style_max_width_px(content, row_id) == "450"
    assert _mso_table_width_px(content, row_id) == "450"


def test_compiled_content_two_rows_in_one_template_get_independent_widths():
    """Two sections with different `max_width`/padding in the SAME
    template compile to two DIFFERENT row widths in `content` — proving
    the width is computed per row, not held as one module-level value that
    the last section computed would silently apply to every row."""
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>a</p>")])], layout="1 Column")],
            max_width="600",
        ),
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>b</p>")])], layout="1 Column")],
            max_width="600",
            padding={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        ),
    ]
    craft_json, content = ec.build_email_content(sections)
    row_ids = [nid for nid, n in craft_json.items() if _resolved_name(n) == "Row"]
    assert len(row_ids) == 2
    widths = sorted(_row_style_max_width_px(content, rid) for rid in row_ids)
    assert widths == ["580", "600"]


def test_compiled_content_column_split_truncates_to_four_decimals_at_a_non_default_width():
    """The mso per-column pixel split must use the SAME truncate-to-4-
    decimals convention the live-confirmed 880px defaults already used
    (`293.3333`, not the rounded `293.3334` or the full-precision
    `293.3333333333333`), generalized to a non-880 row width."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell([ec.text_block("<p>a</p>")]),
                        ec.cell([ec.text_block("<p>b</p>")]),
                    ],
                    layout="2 Columns (1/3 and 2/3)",
                    container_width="580",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert "width:193.3333px;" in content
    assert "width:386.6666px;" in content


# ---------------------------------------------------------------------------
# Compiled `content` must also carry `Section`/`Row` padding — a second
# blocking defect found in review, same root cause as the width bug:
# `_render_row`/`_render_section` built their markup without ever reading
# `containerPadding{Top,Right,Bottom,Left}`, so `content` had NO padding
# declaration for `Section`/`Row` at all (text rendered flush against the
# canvas edge), regardless of what `craft_json` said.
# ---------------------------------------------------------------------------


def test_compiled_content_section_padding_matches_craft_json_when_set():
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")],
            padding={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    assert _section_padding_css(content, section_id) == "0px 0px 0px 0px"


def test_compiled_content_row_padding_matches_craft_json_when_set():
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>x</p>")])],
                    layout="1 Column",
                    padding={"top": "20", "right": "20", "bottom": "20", "left": "20"},
                )
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _row_padding_css(content, row_id) == "20px 20px 20px 20px"


def test_compiled_content_padding_defaults_to_uniform_10_matching_craft_json():
    """No overrides at all: `content` must still carry the SAME uniform
    `'10'` padding `craft_json` has always defaulted to — this is not a new
    field's default, it's a pre-existing prop that `content` simply never
    rendered before this fix, on every section and row, not just ones that
    explicitly set the new `padding` field."""
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _section_padding_css(content, section_id) == "10px 10px 10px 10px"
    assert _row_padding_css(content, row_id) == "10px 10px 10px 10px"


def test_compiled_content_padding_matches_craft_json_for_the_asymmetric_case():
    """The coordinator's own repro shape: left/right differ from top/bottom
    (`20/30/20/30`, not a uniform value) — a fix that only handles the
    uniform default would pass the two tests above and still fail this
    one."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>x</p>")])],
                    layout="1 Column",
                    padding={"top": "20", "right": "30", "bottom": "20", "left": "30"},
                )
            ],
            padding={"top": "10", "right": "40", "bottom": "10", "left": "40"},
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _section_padding_css(content, section_id) == "10px 40px 10px 40px"
    assert _row_padding_css(content, row_id) == "20px 30px 20px 30px"


def test_compiled_content_padding_is_independent_per_row_and_section():
    """Two sections, each with its own row, all four carrying different
    padding — proving padding is read per node, not one value leaking
    across the template (the same class of bug the width fix already
    guarded against)."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>a</p>")])],
                    layout="1 Column",
                    padding={"top": "20", "right": "20", "bottom": "20", "left": "20"},
                )
            ],
            padding={"top": "10", "right": "10", "bottom": "10", "left": "10"},
        ),
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>b</p>")])],
                    layout="1 Column",
                    padding={"top": "30", "right": "30", "bottom": "30", "left": "30"},
                )
            ],
            padding={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        ),
    ]
    craft_json, content = ec.build_email_content(sections)
    section_ids = [
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    ]
    row_ids = [nid for nid, n in craft_json.items() if _resolved_name(n) == "Row"]
    section_paddings = sorted(_section_padding_css(content, sid) for sid in section_ids)
    row_paddings = sorted(_row_padding_css(content, rid) for rid in row_ids)
    assert section_paddings == ["0px 0px 0px 0px", "10px 10px 10px 10px"]
    assert row_paddings == ["20px 20px 20px 20px", "30px 30px 30px 30px"]


def test_divider_size_is_emitted_when_set():
    sections = [
        ec.section([ec.row([ec.cell([ec.divider_block(size="1")])], layout="1 Column")])
    ]
    craft_json, content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Divider")
    assert props["size"] == "1"
    assert "border-top:1px solid" in content


def test_divider_size_defaults_to_3_when_unset():
    sections = [
        ec.section([ec.row([ec.cell([ec.divider_block()])], layout="1 Column")])
    ]
    craft_json, _content = ec.build_email_content(sections)
    assert _props_of(craft_json, "Divider")["size"] == "3"


def test_button_layout_props_are_emitted_when_set_matching_the_reference_pattern():
    """Values chosen to match the pattern independently confirmed live
    against the reference template's `Button` node (BCLI-024 Context):
    `borderRadius: '20'`, `padding{Left,Right}: '30'`, `alignment: 'left'`."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.button_block(
                                    "Go",
                                    "https://example.com",
                                    border_radius="20",
                                    padding_left="30",
                                    padding_right="30",
                                    alignment="left",
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Button")
    assert props["borderRadius"] == "20"
    assert props["paddingLeft"] == "30"
    assert props["paddingRight"] == "30"
    assert props["alignment"] == "left"
    assert "border-radius:20px;" in content
    assert "padding:10px 30px 10px 30px;" in content


def test_button_layout_props_default_to_todays_hardcoded_values_when_unset():
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.button_block("Go", "https://example.com")])],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Button")
    assert props["borderRadius"] == "8"
    assert props["paddingLeft"] == "20"
    assert props["paddingRight"] == "20"
    assert props["alignment"] == "center"


def test_image_layout_props_are_emitted_only_when_set():
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://host/api/files/f1/download",
                                    name="logo.png",
                                    container_width="580",
                                    max_width="300",
                                    max_height="200",
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Image")
    assert props["containerWidth"] == "580"
    assert props["maxWidth"] == "300"
    assert props["maxHeight"] == "200"


def test_image_layout_props_are_absent_by_default_matching_todays_output():
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.image_block(
                                    file_id="f1",
                                    src="https://host/api/files/f1/download",
                                    name="logo.png",
                                )
                            ]
                        )
                    ],
                    layout="1 Column",
                )
            ]
        )
    ]
    craft_json, _content = ec.build_email_content(sections)
    props = _props_of(craft_json, "Image")
    assert "containerWidth" not in props
    assert "maxWidth" not in props
    assert "maxHeight" not in props


def test_spec_driven_layout_props_flow_end_to_end_from_email_template_def():
    """The full path this item wires: `EmailTemplateDef` -> `_walk_blocks`
    -> `assemble_sections` -> `build_email_content`, for one section/row
    carrying every new field at once."""
    spec = EmailTemplateDef.model_validate(
        {
            "name": "Newsletter",
            "sections": [
                {
                    "max_width": "600",
                    "container_width": "900",
                    "padding": {"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    "rows": [
                        {
                            "layout": "1 Column",
                            "width": "75",
                            "container_width": "580",
                            "padding": {
                                "top": "10",
                                "right": "40",
                                "bottom": "10",
                                "left": "40",
                            },
                            "cells": [
                                {
                                    "blocks": [
                                        {
                                            "kind": "button",
                                            "label": "Go",
                                            "url": "https://x",
                                            "border_radius": "20",
                                            "padding_left": "30",
                                            "padding_right": "30",
                                            "alignment": "left",
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    craft_json, _content = ec.build_email_content(sections)

    section_props = _props_of(craft_json, "Section")
    assert section_props["maxWidth"] == "600"
    assert section_props["containerWidth"] == "900"

    row_props = _props_of(craft_json, "Row")
    assert row_props["width"] == "75"
    assert row_props["containerWidth"] == "580"
    assert row_props["containerPaddingRight"] == "40"

    button_props = _props_of(craft_json, "Button")
    assert button_props["borderRadius"] == "20"
    assert button_props["alignment"] == "left"


# ---------------------------------------------------------------------------
# Systematic content-coverage test — added in review after THREE separate
# craft_json-vs-content divergences were found by hand (width, then padding,
# then Button.alignment/Image.position): a new field landing correctly in
# craft_json and silently never reaching content is this item's recurring
# failure mode, and it was invisible to every acceptance test up to this
# point because the original reference-diff only ever compared craft_json.
# This test walks every field BCLI-024 added and asserts its value reaches
# content ON THE RIGHT NODE — not merely somewhere in the document — with a
# named, commented exemption for any field confirmed to be craft_json-only
# by design, never a silent pass.
# ---------------------------------------------------------------------------


def test_content_reflects_every_layout_prop_this_item_added():
    """One fixture, every new field set to a distinctive, individually
    identifiable value, so a bug that reads the wrong node's prop (not just
    "no prop at all") would also be caught."""

    def resolved(n: dict) -> str:
        t = n.get("type")
        return t.get("resolvedName") if isinstance(t, dict) else t

    sections = [
        ec.section(
            [
                # Row A: no explicit container_width, so Section.max_width's
                # effect on the compiled row width is directly observable
                # (isolated from Row B's own explicit override below).
                ec.row(
                    [
                        ec.cell(
                            [
                                ec.divider_block(size="7"),
                                ec.button_block(
                                    "Go",
                                    "https://example.com",
                                    border_radius="31",
                                    padding_left="33",
                                    padding_right="37",
                                    alignment="right",
                                ),
                                ec.image_block(
                                    file_id="f1",
                                    src="https://host/x",
                                    name="x.png",
                                    container_width="401",
                                    max_width="403",
                                    max_height="407",
                                ),
                            ]
                        )
                    ],
                    layout="1 Column",
                    width="77",  # exempt, see below
                    padding={"top": "21", "right": "23", "bottom": "27", "left": "29"},
                ),
                # Row B: explicit container_width, proving it's trusted
                # directly rather than only ever derived from the Section.
                ec.row(
                    [ec.cell([ec.text_block("<p>b</p>")])],
                    layout="1 Column",
                    container_width="271",
                ),
            ],
            max_width="543",
            container_width="919",  # exempt, see below
            padding={"top": "11", "right": "13", "bottom": "17", "left": "19"},
        )
    ]
    craft_json, content = ec.build_email_content(sections)

    section_id = next(nid for nid, n in craft_json.items() if resolved(n) == "Section")
    row_ids = [nid for nid, n in craft_json.items() if resolved(n) == "Row"]
    row_a = next(
        rid for rid in row_ids if craft_json[rid]["props"].get("width") == "77"
    )
    row_b = next(
        rid
        for rid in row_ids
        if craft_json[rid]["props"].get("containerWidth") == "271"
    )
    image_id = next(nid for nid, n in craft_json.items() if resolved(n) == "Image")

    # --- SectionDef.max_width: INDIRECT — with no Row.container_width
    # override, Row A's derived width is 543 - 19 - 13 = 511.0 (Section's
    # own left/right padding, not Row A's).
    # --- RowDef.width: DIRECT — a genuine multiplier on top of that derived
    # width, confirmed against the reference template (a `width: '75'` row
    # there compiles narrower than its `containerWidth`, not equal to it).
    # 511.0 * 0.77 = 393.47.
    assert _row_style_max_width_px(content, row_a) == "393.47"

    # --- SectionDef.padding: DIRECT, on the Section's own wrapper div.
    assert _section_padding_css(content, section_id) == "11px 13px 17px 19px"

    # --- RowDef.container_width (Row B): DIRECT, trusted over the
    # Section-derived formula Row A exercises above.
    assert _row_style_max_width_px(content, row_b) == "271"

    # --- RowDef.padding (Row A): DIRECT, on the Row's own wrapper div —
    # independent of Section's own padding, asserted above as a different
    # value (21/23/27/29 vs. 11/13/17/19).
    assert _row_padding_css(content, row_a) == "21px 23px 27px 29px"

    # --- DividerBlockDef.size: DIRECT, in the compiled border-top rule.
    assert "border-top:7px" in content

    # --- ButtonBlockDef.border_radius/padding_left/padding_right/alignment:
    # DIRECT, all on the Button's own compiled table/anchor markup. Scoped
    # to this button's own fragment (not "somewhere in content") by
    # locating its distinctive align="right".
    button_start = content.index('<table role="presentation" align="right"')
    button_fragment = content[button_start : button_start + 700]
    assert "border-radius:31px" in button_fragment
    # Top/bottom stay this item's pre-existing hardcoded "10" — only
    # left/right were added by ButtonBlockDef; order is top/right/bottom/left.
    assert "padding:10px 37px 10px 33px;" in button_fragment

    # --- SectionDef.container_width: DIRECT as of BCLI-025 (was
    # craft_json-only through BCLI-024 — see that item's Outcome). Kizen's
    # real `content` applies it to an outer background-table wrapper;
    # `_render_section` now builds a simplified version of that wrapper
    # (mso-conditional table only, no VML background-image fallback, since
    # this emitter has no background-image concept — see the work item's
    # report for that scoping call).
    assert 'width="919" style="width:919px;"' in content
    assert f".{eh._section_class(section_id)} {{ max-width:919px; }}" in content

    # --- Fields confirmed craft_json-only by checking Kizen's own compiled
    # output for the reference template (not merely "no consumer in
    # `_render_*`" — see the process note in BCLI-024's work item for why
    # that alone isn't sufficient):
    #   * ImageBlockDef.container_width/max_width/max_height (content's
    #     `<img>` always uses the emitter's own `width`/fixed
    #     `max-width:100%` pair — these three new props have no consumer in
    #     `_render_image`, and this one *is* confirmed absent from the
    #     reference's compiled content too).
    assert craft_json[row_a]["props"]["width"] == "77"
    assert craft_json[image_id]["props"]["containerWidth"] == "401"
    assert craft_json[image_id]["props"]["maxWidth"] == "403"
    assert craft_json[image_id]["props"]["maxHeight"] == "407"


# ---------------------------------------------------------------------------
# BCLI-025 — compiled `content` fidelity against Kizen's own compiler.
# Every fix below was checked against the reference template's REAL compiled
# `content` (read-only `GET`, `cli-testing`, 2026-08-26), not inferred from
# `craft_json` or from "no consumer exists in `_render_*`" — see that item's
# report for the trace. In binding priority order.
# ---------------------------------------------------------------------------


def test_font_family_reaches_every_text_blocks_wrapper(monkeypatch: pytest.MonkeyPatch):
    """Divergence 1 (highest priority): compiled `content` carried NO
    `font-family` for text at all before this fix — every recipient
    rendered in the client's serif fallback. Fixed via the
    `kizen-text-styles` wrapper div Kizen's own compiler uses (confirmed
    structurally against the reference), sourced from `Root.props`, never
    `TextBlockDef`/`_render_paragraphs` (BCLI-023's scope — untouched)."""
    monkeypatch.setitem(ec.EMAIL_ROOT_PROPS, "fontFamily", "Georgia")
    monkeypatch.setitem(ec.EMAIL_ROOT_PROPS, "color", "rgba(10,20,30,1)")
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert (
        '<div class="kizen-text-styles" style="font-family:Georgia;'
        'font-size:14px;line-height:1;text-align:left;color:#0a141e;">'
        "<p>x</p></div>"
    ) in content


def test_font_family_default_matches_todays_arial_value():
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert "font-family:Arial;font-size:14px;line-height:1;" in content


def test_rgba_to_hex_matches_the_two_conversions_confirmed_live():
    """`Root.props.color: rgba(74,86,96,1)` -> `#4a5660` and
    `Root.props.linkColor: rgba(82,142,249,1)` -> `#528ef9` — both read
    directly off the reference template's compiled `content`."""
    assert eh._rgba_to_hex("rgba(74,86,96,1)") == "#4a5660"
    assert eh._rgba_to_hex("rgba(82,142,249,1)") == "#528ef9"
    assert eh._rgba_to_hex("#FFFFFF") == "#FFFFFF"  # passes through non-rgba unchanged


def test_moz_text_html_rule_exists_for_every_column_class_in_a_multi_column_layout():
    """Divergence 2: Gecko-based mail clients (Thunderbird and others) key
    column-stacking behaviour off `.moz-text-html`-prefixed rules — real,
    recipient-visible, but scoped to that client family. Parses the
    compiled `<style>` block and asserts the rule exists for each
    `mj-column-per-*` class a multi-column layout actually uses."""
    sections = [
        ec.section(
            [
                ec.row(
                    [
                        ec.cell([ec.text_block("<p>a</p>")]),
                        ec.cell([ec.text_block("<p>b</p>")]),
                    ],
                    layout="2 Columns (1/3 and 2/3)",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    moz_style = re.search(
        r'<style media="screen and \(min-width:\d+px\)">(.*?)</style>', content
    )
    assert moz_style, "no .moz-text-html style block found"
    moz_css = moz_style.group(1)
    assert (
        ".moz-text-html .mj-column-per-33-333332 "
        "{ width:33.333332% !important; max-width:33.333332%; }"
    ) in moz_css
    assert (
        ".moz-text-html .mj-column-per-66-666664 "
        "{ width:66.666664% !important; max-width:66.666664%; }"
    ) in moz_css


def test_no_moz_text_html_style_block_when_template_has_no_rows():
    """`_moz_text_html_style_block` returns nothing when there are no `Row`
    nodes to derive column classes from, rather than an empty/broken rule."""
    assert eh._moz_text_html_style_block({}, "414") == ""


def test_mjml_reset_block_is_present_and_byte_exact():
    """Divergence 3: the static MJML reset block (`#outlook a`,
    `body{margin:0}`, `table,td{border-collapse}`, `img{...}`,
    `p{display:block;margin:13px 0}`) — confirmed byte-exact against the
    reference template's compiled `content` on 2026-08-25, and
    **independently re-verified against a fresh read-only capture of the
    same template, 2026-08-26**, as part of BCLI-023's audit of this file's
    byte-exact claims: the block has no per-template data (it's a fixed
    constant, `_MJML_RESET_STYLE`), and the fresh capture's own reset block
    matches this assertion's literal exactly, byte for byte. No per-template
    data, so byte-exact is the right bar here (same allowance the work item
    gives the `.moz-text-html` rule's structural shape)."""
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert (
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
    ) in content


def test_mobile_break_breakpoint_reads_root_props_not_hardcoded_480(
    monkeypatch: pytest.MonkeyPatch,
):
    """Divergence 4: the mobile-collapse media query's breakpoint must come
    from `craft_json["ROOT"]["props"]["mobileBreak"]`, not a hardcoded
    `480`. `mobileBreak` isn't spec-settable today (no `EmailTemplateDef`
    field for it), so this monkeypatches `EMAIL_ROOT_PROPS` directly, per
    the work item's own suggested approach for this test."""
    monkeypatch.setitem(ec.EMAIL_ROOT_PROPS, "mobileBreak", "600")
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([]), ec.cell([])],
                    layout="2 Columns",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert "@media only screen and (max-width:600px){" in content
    assert "@media only screen and (max-width:480px)" not in content
    # The .moz-text-html block's min-width also tracks the same breakpoint.
    assert 'media="screen and (min-width:600px)"' in content


def test_mobile_break_default_is_414_not_the_old_hardcoded_480():
    sections = [ec.section([ec.row([ec.cell([]), ec.cell([])], layout="2 Columns")])]
    _craft_json, content = ec.build_email_content(sections)
    assert "@media only screen and (max-width:414px){" in content
    assert "@media only screen and (max-width:480px)" not in content


def test_body_background_color_reads_root_props(monkeypatch: pytest.MonkeyPatch):
    """Divergence 5 (body-background half): `Root.props.backgroundColor`
    must reach `<body>`'s inline style — confirmed against the reference
    that it also reaches an outer wrapping `<div>`, not just `<body>`."""
    monkeypatch.setitem(ec.EMAIL_ROOT_PROPS, "backgroundColor", "#112233")
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert '<body style="word-spacing:normal;background-color:#112233;">' in content
    assert '<div style="background-color:#112233;">' in content


def test_body_background_color_default_matches_todays_value():
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert '<body style="word-spacing:normal;background-color:#F8FAFF;">' in content


def test_section_container_width_gets_no_outer_wrapper_when_unset():
    """Byte-identical-defaults guarantee: a Section that doesn't set
    `container_width` gets no outer mso wrapper table at all — matching
    this surface's existing "None means no override" convention for every
    other layout prop `_section_props` handles."""
    sections = [
        ec.section([ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")])
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    assert f'class="{eh._section_class(section_id)}"' in content
    assert 'role="presentation" align="center" width=' not in content


def test_section_container_width_wrapper_carries_the_containerWidth_attribute():
    """Divergence 5 (outer wrapper half): a Section with an explicit
    `container_width` gets an mso-conditional table hosting that value as
    both a `width` attribute and an inline `width:...px` style — matching
    Kizen's own reference-confirmed pattern of applying `containerWidth` to
    an outer background-table wrapper (`_render_section`'s simplified
    version of it — see the work item's report for the scoping call on how
    far this emitter reproduces Kizen's full VML markup)."""
    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>x</p>")])], layout="1 Column")],
            container_width="900",
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    section_id = next(
        nid for nid, n in craft_json.items() if _resolved_name(n) == "Section"
    )
    assert (
        '<!--[if mso | IE]><table border="0" cellpadding="0" cellspacing="0" '
        'role="presentation" align="center" width="900" style="width:900px;">'
        "<tr><td><![endif]-->"
        f'<div class="{eh._section_class(section_id)}"'
    ) in content
    assert f".{eh._section_class(section_id)} {{ max-width:900px; }}" in content


def test_float_formatted_content_width_never_prints_a_trailing_dot_zero_at_non_default_width():
    """Divergence 7: the float-formatting artifact is general to any
    computed row width, not just the literal 880 case (`_row_content_width_px`
    always returns a Python `float`, per BCLI-024). A `container_width` that
    still lands on a whole number (here 700) must print `700px`, never
    `700.0px`, at every width call site in `_render_row` — the row's own two
    `content_width_px` sites and the per-column mso `<td>` width alike."""
    sections = [
        ec.section(
            [
                ec.row(
                    [ec.cell([ec.text_block("<p>x</p>")])],
                    layout="1 Column",
                    container_width="700",
                )
            ]
        )
    ]
    craft_json, content = ec.build_email_content(sections)
    row_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Row")
    assert _row_style_max_width_px(content, row_id) == "700"
    assert _mso_table_width_px(content, row_id) == "700"
    assert not re.search(r"\.0px", content)


def test_golden_output_for_a_small_synthetic_template(monkeypatch: pytest.MonkeyPatch):
    counter = iter(f"{i:024x}" for i in range(1, 50))
    monkeypatch.setattr(form_ui, "_new_id", lambda: next(counter))

    sections = [
        ec.section(
            [ec.row([ec.cell([ec.text_block("<p>Hello</p>")])], layout="1 Column")],
            background_color="#EEEEEE",
        )
    ]
    craft_json, content = ec.build_email_content(sections)

    root_id = "ROOT"
    section_id = "000000000000000000000001"
    row_id = "000000000000000000000002"
    cell_id = "000000000000000000000003"
    text_id = "000000000000000000000004"

    assert set(craft_json) == {root_id, section_id, row_id, cell_id, text_id}
    assert craft_json[root_id]["nodes"] == [section_id]
    assert craft_json[section_id]["nodes"] == [row_id]
    assert craft_json[row_id]["linkedNodes"] == {"column-1": cell_id}
    assert craft_json[cell_id]["props"] == {"__width": 1}
    assert craft_json[text_id]["custom"]["text"] == "<p>Hello</p>"

    assert f'class="section-{section_id}"' in content
    assert f'class="section-{row_id}"' in content
    assert f".section-{section_id} {{ background-color:#EEEEEE; }}" in content
    assert "<p>Hello</p>" in content
    assert "mj-column-per-100" in content


# ---------------------------------------------------------------------------
# BCLI-023 — structured `paragraphs` text model + inline merge fields.
# `TextBlockDef.html` is gone; `_paragraphs_to_html` renders the canonical
# markup confirmed live against the reference template's touched `Text`
# nodes (2026-08-26). Every test that builds a full spec asserts BOTH
# `craft_json`'s `custom.text` and the compiled `content` — the two are
# independent stored fields the server never reconciles, and eight defects
# have already shipped on this surface from a check that inspected only one.
# ---------------------------------------------------------------------------


def test_text_block_html_key_is_rejected_loudly_not_silently():
    """`TextBlockDef.html` is removed. A spec that still carries the old
    `html` key on a `text` block fails validation (`extra="forbid"`),
    naming the offending `html` key — the same treatment BCLI-022 already
    gives every other unrepresentable key on this surface (`craft_json`,
    `content`, `sender_type`)."""
    with pytest.raises(ValidationError) as exc_info:
        EmailTemplateDef.model_validate(
            {
                "name": "Newsletter",
                "sections": [
                    {
                        "rows": [
                            {
                                "cells": [
                                    {"blocks": [{"kind": "text", "html": "<p>Hi</p>"}]}
                                ]
                            }
                        ]
                    }
                ],
            }
        )
    errors = exc_info.value.errors()
    assert any(
        e["type"] == "extra_forbidden" and e["loc"][-1] == "html" for e in errors
    )


def test_paragraphs_to_html_bold_heading_with_explicit_size():
    html = ec._paragraphs_to_html([ParagraphDef(text="Heading", bold=True, size=20)])
    assert html == (
        '<p data-line-height="default" style="line-height: 1.25;">'
        '<span style="font-size: 20px;"><strong>Heading</strong></span></p>'
    )


def test_paragraphs_to_html_empty_spacer_paragraph_has_no_span():
    html = ec._paragraphs_to_html([ParagraphDef(text="")])
    assert html == '<p data-line-height="default" style="line-height: 1.25;"></p>'
    assert "<span" not in html


def test_paragraphs_to_html_plain_body_defaults_size_to_email_root_font_size():
    html = ec._paragraphs_to_html([ParagraphDef(text="Body copy")])
    assert f"font-size: {ec.EMAIL_ROOT_PROPS['fontSize']}px;" in html
    assert html == (
        '<p data-line-height="default" style="line-height: 1.25;">'
        '<span style="font-size: 14px;">Body copy</span></p>'
    )


def test_paragraphs_to_html_color_is_appended_to_span_style_after_size():
    html = ec._paragraphs_to_html([ParagraphDef(text="Body copy", color="#1B64F2")])
    assert '<span style="font-size: 14px; color: #1B64F2;">' in html


def test_paragraphs_to_html_align_appends_text_align_to_the_p_style():
    html = ec._paragraphs_to_html([ParagraphDef(text="Body copy", align="center")])
    assert (
        '<p data-line-height="default" style="line-height: 1.25; text-align: center;">'
        in html
    )
    # No align set: no text-align at all, not even the default `left`.
    unaligned = ec._paragraphs_to_html([ParagraphDef(text="Body copy")])
    assert "text-align" not in unaligned


def test_paragraphs_to_html_link_wraps_the_whole_span_not_the_bare_text():
    html = ec._paragraphs_to_html(
        [ParagraphDef(text="Read more", link="https://example.com")]
    )
    assert html == (
        '<p data-line-height="default" style="line-height: 1.25;">'
        '<a rel="noopener noreferrer nofollow" href="https://example.com">'
        '<span style="font-size: 14px;">Read more</span></a></p>'
    )


def test_paragraphs_to_html_reserved_namespace_merge_field_needs_no_resolver():
    """`{{ business.city }}` resolves via `merge_fields`'s own captured
    fallback table with no `resolve_label`/`resolve_objectname` at all —
    exercises the exact `craft-config`/`--dry-run` offline path."""
    html = ec._paragraphs_to_html([ParagraphDef(text="Hi {{ business.city }}")])
    assert (
        '<span class="kzn-merge-field" data-merge-field-fallback-label="Business City" '
        'data-merge-field-relationship="business.city">{{ business.city }}</span>'
    ) in html
    assert "data-merge-field-objectname" not in html


def test_paragraphs_to_html_custom_object_merge_field_with_fake_resolvers():
    """A non-reserved namespace with a resolver pair answering it gets
    `data-merge-field-objectname` — synthetic namespace/labels, never the
    reference template's real object/field names."""

    def resolve_label(namespace: str, field_path: str) -> str | None:
        return "Stage" if (namespace, field_path) == ("some_object", "stage") else None

    def resolve_objectname(namespace: str) -> str | None:
        return "Some Object" if namespace == "some_object" else None

    html = ec._paragraphs_to_html(
        [ParagraphDef(text="{{ some_object.stage }}")],
        resolve_label=resolve_label,
        resolve_objectname=resolve_objectname,
    )
    assert (
        '<span class="kzn-merge-field" data-merge-field-fallback-label="Stage" '
        'data-merge-field-relationship="some_object.stage" '
        'data-merge-field-objectname="Some Object">{{ some_object.stage }}</span>'
    ) in html


def test_paragraph_text_rejects_automation_variable_tokens():
    """Library email templates aren't scoped to one automation, so an
    `automation_variable.*` token (only meaningful inside an
    automation-scoped message) is a spec-validation error, not silently
    rendered as a token nobody can resolve."""
    with pytest.raises(ValidationError) as exc_info:
        ParagraphDef(text="Code: {{ automation_variable.discount_code }}")
    assert "automation_variable" in str(exc_info.value)


def test_paragraph_text_rejects_automation_variable_token_via_full_spec():
    with pytest.raises(ValidationError):
        EmailTemplateDef.model_validate(
            {
                "name": "Newsletter",
                "sections": [
                    {
                        "rows": [
                            {
                                "cells": [
                                    {
                                        "blocks": [
                                            {
                                                "kind": "text",
                                                "paragraphs": [
                                                    {
                                                        "text": "{{ automation_variable.x }}"
                                                    }
                                                ],
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        )


def _text_block_spec(paragraphs: list[dict]) -> dict:
    return {
        "name": "Newsletter",
        "sections": [
            {
                "rows": [
                    {
                        "cells": [
                            {"blocks": [{"kind": "text", "paragraphs": paragraphs}]}
                        ]
                    }
                ]
            }
        ],
    }


def test_spec_driven_paragraphs_flow_end_to_end_from_email_template_def():
    """The full path this item wires: `EmailTemplateDef` -> `_walk_blocks`
    (renders `paragraphs` to HTML right there) -> `assemble_sections` ->
    `build_email_content`. Asserts BOTH stored fields carry the rendered
    paragraph markup, per the item's non-negotiable — `craft_json` and
    `content` are independent and never reconciled by the server."""
    spec = EmailTemplateDef.model_validate(
        _text_block_spec([{"text": "Heading", "bold": True, "size": 20}])
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    craft_json, content = ec.build_email_content(sections)

    expected = (
        '<p data-line-height="default" style="line-height: 1.25;">'
        '<span style="font-size: 20px;"><strong>Heading</strong></span></p>'
    )
    text_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Text")
    assert craft_json[text_id]["custom"]["text"] == expected
    assert expected in content


def test_merge_field_span_appears_identically_in_craft_json_and_content():
    """`merge-field-markup-captured-live.md` confirmed this identity holds
    for a real captured template; this item's one-pass design (both fields
    built from the same rendered HTML string) should preserve it by
    construction — tested here, not assumed."""
    spec = EmailTemplateDef.model_validate(
        _text_block_spec([{"text": "Hi {{ business.city }}"}])
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    craft_json, content = ec.build_email_content(sections)

    text_id = next(nid for nid, n in craft_json.items() if _resolved_name(n) == "Text")
    span = (
        '<span class="kzn-merge-field" data-merge-field-fallback-label="Business City" '
        'data-merge-field-relationship="business.city">{{ business.city }}</span>'
    )
    assert span in craft_json[text_id]["custom"]["text"]
    assert span in content


def test_offline_resolver_omits_objectname_for_every_namespace_including_custom_object():
    """`craft-config`/`--dry-run` never make a live call
    (`offline_resolve_spec_images` passes `resolve_label=None,
    resolve_objectname=None` to `_walk_blocks`) — so a custom-object
    namespace's `data-merge-field-objectname` is omitted entirely offline,
    a real, documented divergence from what `create`/`update` would
    produce. `data-merge-field-fallback-label` is still populated, from
    `merge_fields`'s own title-case fallback."""
    spec = EmailTemplateDef.model_validate(
        _text_block_spec([{"text": "{{ some_custom_object.stage }}"}])
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    _craft_json, content = ec.build_email_content(sections)

    assert 'data-merge-field-relationship="some_custom_object.stage"' in content
    assert "data-merge-field-objectname" not in content
    # No namespace prefix here: `_fallback_label` only prefixes namespaces in
    # `merge_fields._LABEL_PREFIXES` (`business`/`team_member`) — anything
    # else, including a real custom-object api_name with no live resolver,
    # falls back to a bare title-cased field name.
    assert 'data-merge-field-fallback-label="Stage"' in content


def test_craft_summary_text_in_sync_for_paragraphs_based_text_with_merge_field():
    """`craft_summary()`'s `text_in_sync` (`tools/messages.py`, frozen per
    BCLI-022's Constraints, not modified here) still reports `true` for a
    template built from `paragraphs`, including a merge-field span — a
    regression test, not an assumption. Both sides tag-strip to the same
    plain text (`{{ business.city }}` survives tag-stripping identically on
    both the `craft_json` and `content` side), which is exactly why this is
    expected to hold, not merely hoped."""
    spec = EmailTemplateDef.model_validate(
        _text_block_spec(
            [
                {"text": "Hi {{ business.city }}, welcome"},
                {"text": ""},
                {"text": "Second paragraph", "bold": True},
            ]
        )
    )
    resolved = ec.offline_resolve_spec_images(spec)
    sections = ec.assemble_sections(resolved)
    craft_json, content = ec.build_email_content(sections)

    summary = craft_summary({"craft_json": craft_json, "content": content})
    assert summary["structure_coupled"] is True
    assert summary["text_in_sync"] is True
    assert summary["coupled"] is True


def test_email_merge_field_resolvers_are_live_and_cached_per_call(
    monkeypatch: pytest.MonkeyPatch,
):
    """`_email_merge_field_resolvers()` is backed by `tools.objects.get_object`
    — no `AutomationDef`/`LiveContext` import — and caches each looked-up
    object by api_name for the life of one resolver pair, since a template
    can reference the same custom object many times across paragraphs and
    `get_object` opens its own `KizenClient` per call."""
    calls: list[str] = []

    def fake_get_object(api_name: str) -> dict:
        calls.append(api_name)
        return {
            "display_name": "Some Object",
            "fields": [{"api_name": "stage", "display_name": "Stage"}],
        }

    monkeypatch.setattr(ec.objects, "get_object", fake_get_object)
    resolve_label, resolve_objectname = ec._email_merge_field_resolvers()

    assert resolve_label("some_object", "stage") == "Stage"
    assert resolve_label("some_object", "missing_field") is None
    assert resolve_objectname("some_object") == "Some Object"
    # Reserved namespaces never reach `get_object` at all.
    assert resolve_label("business", "city") is None

    assert calls == ["some_object"]  # one live call, reused by every lookup after


def test_email_merge_field_resolvers_return_none_on_unknown_object(
    monkeypatch: pytest.MonkeyPatch,
):
    from kizen_builder.api.client import KizenAPIError

    def fake_get_object(api_name: str) -> dict:
        raise KizenAPIError(404, "not found")

    monkeypatch.setattr(ec.objects, "get_object", fake_get_object)
    resolve_label, resolve_objectname = ec._email_merge_field_resolvers()

    assert resolve_label("nonexistent", "field") is None
    assert resolve_objectname("nonexistent") is None

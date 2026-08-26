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

from kizen_builder.models.spec.email_templates import COLUMN_FRACTIONS, EmailTemplateDef
from kizen_builder.tools import email_craft as ec
from kizen_builder.tools import form_ui
from kizen_builder.tools.messages import craft_summary

_SECTION_CLASS = re.compile(r"section-([0-9a-f]{6,})")


def _split_style_block(content: str) -> tuple[str, str]:
    """(base_css, media_css) — split the compiled `<style>` block at its
    `@media` query, so a test can tell a base (unconditional) rule from a
    mobile-collapse rule instead of merely checking presence anywhere in the
    document. That distinction is the whole point: a column-width rule that
    only exists inside `@media (max-width:480px)` renders stacked at desktop
    width, which is the inverted-CSS bug this module fixes."""
    style = content.split("<style", 1)[1].split("</style>", 1)[0]
    if "@media" not in style:
        return style, ""
    base, media = style.split("@media only screen and (max-width:480px){", 1)
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
    assert "width:880.0px;" in content
    # 2 Columns: one base rule + one media-collapse rule + one div per
    # column = 4 occurrences of the class name.
    assert content.count("mj-column-per-50") == 4
    assert ".mj-column-per-50 { width:50% !important; max-width:50%; }" in base_css
    assert ".mj-column-per-50 { width:100% !important; max-width:100%; }" in media_css
    assert "width:50%" not in media_css
    assert content.count("width:440.0px;") == 2


def test_button_and_divider_compiled_markup_matches_a_real_captured_template():
    """Verified 2026-08-26, read-only, against a real Kizen-authored
    template on `cli-testing`: `_render_button`/`_render_divider`'s output
    is byte-exact against that template's actual compiled `content` for a
    Button/Divider node carrying the same props. The real template's own
    copy/URL/color never enter this repo (personal-data rule) — the values
    below are synthetic, chosen to exercise the same code path."""
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
                        ec.cell([ec.divider_block("#E5E7EB")]),
                    ],
                    layout="2 Columns",
                )
            ]
        )
    ]
    _craft_json, content = ec.build_email_content(sections)
    assert (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'style="border-collapse:separate;"><tr><td '
        'style="border-radius:8px;background:#1B64F2;text-align:center;" '
        'bgcolor="#1B64F2"><a href="https://example.com" target="_blank" '
        'style="display:inline-block;background:#1B64F2;color:rgba(255,255,255,1);'
        "font-family:Arial;font-size:16px;padding:10px 20px 10px 20px;"
        'border-radius:8px;text-decoration:none;">Click Here</a></td></tr></table>'
    ) in content
    assert (
        '<p style="border-top:3px solid #E5E7EB;font-size:1px;margin:0 auto;'
        'width:100%;"> </p>'
    ) in content


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

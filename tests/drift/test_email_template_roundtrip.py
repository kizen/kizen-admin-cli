"""Drift test: `messages templates create --spec-file`'s payload round-trips
against a real environment — the same code path the CLI uses end to end:
spec -> `email_craft.resolve_spec_images()` (a real image upload,
`source="public_image"`) -> `plan_create_template_from_spec()` -> `apply_plan()`.

Everything created is registered with `scratch` immediately after it's
created, per the module-level convention in `tests/drift/conftest.py`.
"""

from __future__ import annotations

import struct
import zlib

import httpx
import pytest

from kizen_builder.api import files as files_api
from kizen_builder.api import messages as messages_api
from kizen_builder.models.spec.email_templates import EmailTemplateDef, ParagraphDef
from kizen_builder.tools import email_craft as ec
from kizen_builder.tools.messages import craft_summary
from kizen_builder.tools.planners import messages as message_planners
from kizen_builder.tools.plans import apply_plan
from tests.drift.conftest import debris_name

pytestmark = pytest.mark.drift


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


def test_create_template_from_spec_roundtrips_live(
    drift_client, drift_config, scratch, tmp_path
):
    png_path = tmp_path / "logo.png"
    png_path.write_bytes(_make_png(9, 7))

    spec = EmailTemplateDef.model_validate(
        {
            "name": debris_name("email-template"),
            "subject": "Drift check — safe to delete",
            "sections": [
                {
                    "background_color": "#FFFFFF",
                    "rows": [
                        {
                            "layout": "1 Column",
                            "cells": [
                                {
                                    "blocks": [
                                        {
                                            "kind": "text",
                                            "paragraphs": [
                                                {
                                                    "text": "Hello {{ business.city }}",
                                                    "bold": True,
                                                    "size": 18,
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ],
                        },
                        {
                            "layout": "2 Columns (1/3 and 2/3)",
                            "cells": [
                                {
                                    "blocks": [
                                        {
                                            "kind": "image",
                                            "file": str(png_path),
                                            "alt": "logo",
                                        }
                                    ]
                                },
                                {
                                    "blocks": [
                                        {
                                            "kind": "button",
                                            "label": "Go",
                                            "url": "https://example.com",
                                        }
                                    ]
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    )

    # Real write #1: the image upload (source="public_image", is_public=True).
    resolved_sections = ec.resolve_spec_images(spec)
    image_block = resolved_sections[0]["rows"][1]["cells"][0]["blocks"][0]
    file_id = image_block["file_id"]
    assert file_id, "upload_email_image did not return a file id"
    scratch.track("file", file_id, lambda: files_api.delete_file(drift_client, file_id))
    assert image_block["natural_width"] == 9
    assert image_block["natural_height"] == 7

    # The emitted Image.src must be reachable by a real recipient — no Kizen
    # auth headers on this request at all. Without is_public=True on the
    # upload, this 404s (the finding this test exists to catch).
    unauth = httpx.get(image_block["src"])
    assert unauth.status_code == 200, (
        f"uploaded image is not publicly readable: {unauth.status_code} "
        f"for {image_block['src']}"
    )

    # Real write #2: the template itself, via the exact planner + apply_plan
    # the CLI's `messages templates create` command uses.
    plan = message_planners.plan_create_template_from_spec(spec, resolved_sections)
    result = apply_plan(plan)
    assert result.all_ok, [r.message for r in result.results if r.status != "ok"]
    template_id = result.results[0].server_uuid
    assert template_id
    scratch.track(
        "email template",
        template_id,
        lambda: messages_api.delete_template(drift_client, template_id),
    )

    # Read it back and run the same drift check `messages templates get` does.
    live = messages_api.get_template(drift_client, template_id)
    summary = craft_summary(live)
    assert summary["structure_coupled"] is True, summary
    assert summary["text_in_sync"] is True, summary
    assert summary["coupled"] is True

    # BCLI-023: `craft_summary` being green is not enough on its own — it
    # only tag-strips and compares plain text, which would not catch a
    # canonical-shape regression (the exact "green but wrong" gap BCLI-022's
    # inverted-media-query defect shipped through undetected). Assert the
    # stored `custom.text` structurally matches what `_paragraphs_to_html`
    # emits for these exact paragraphs — tag names and style keys, not the
    # server's byte-for-byte response, since whitespace/attribute-ordering
    # is not something this test should be brittle against. `business.*` is
    # a reserved namespace `merge_fields.render()` resolves identically with
    # or without a live resolver (reserved namespaces short-circuit before
    # any resolver call), so the offline-computed shape is a valid oracle
    # for this live-created template's `text_in_sync`-passing text.
    text_nodes = [
        n
        for n in live["craft_json"].values()
        if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == "Text"
    ]
    assert len(text_nodes) == 1
    stored_text = text_nodes[0]["custom"]["text"]
    expected_text = ec._paragraphs_to_html(
        [ParagraphDef(text="Hello {{ business.city }}", bold=True, size=18)]
    )
    assert stored_text == expected_text
    assert stored_text in live["content"]
    assert 'data-merge-field-relationship="business.city"' in stored_text, (
        "merge-field span missing from the stored craft_json text"
    )
    assert 'data-merge-field-relationship="business.city"' in live["content"]

    row_nodes = [
        n
        for n in live["craft_json"].values()
        if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == "Row"
    ]
    fractions_seen = {tuple(n["props"]["columns"]) for n in row_nodes}
    assert (1,) in fractions_seen
    assert (0.3333333333333333, 0.6666666666666666) in fractions_seen

    # The stored content's column-width rule must be a BASE rule, not only
    # inside the mobile @media query — confirms the fix against the real
    # stored payload, not just the offline emitter. The breakpoint itself
    # tracks `EMAIL_ROOT_PROPS["mobileBreak"]` ("414"), not the pre-BCLI-025
    # hardcoded 480 (see `tools.email_craft._compile_html`).
    # `.index('<style type="text/css">')` would match the MJML reset block's
    # own `<style>` tag first (both open with the identical literal), not
    # the main style block this assertion means to isolate — anchor on the
    # `.mj-outlook-group-fix` marker instead, the way `test_email_craft.py`'s
    # `_split_style_block` does.
    style_start = live["content"].index(".mj-outlook-group-fix{width:100% !important;}")
    media_start = live["content"].index("@media only screen and (max-width:414px)")
    base_css = live["content"][style_start:media_start]
    assert "width:33.333332% !important; max-width:33.333332%;" in base_css

    cell_nodes = [
        n
        for n in live["craft_json"].values()
        if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == "Cell"
    ]
    widths_seen = {n["props"].get("__width") for n in cell_nodes}
    assert 0.3333333333333333 in widths_seen
    assert 0.6666666666666666 in widths_seen

    image_nodes = [
        n
        for n in live["craft_json"].values()
        if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == "Image"
    ]
    assert len(image_nodes) == 1
    assert image_nodes[0]["props"]["fileId"] == file_id
    assert image_nodes[0]["props"]["naturalWidth"] == 9
    assert image_nodes[0]["props"]["naturalHeight"] == 7

"""CLI-level tests for `messages templates create`/`craft-config`.

`craft-config` and a `--dry-run` `create` must make no live calls at all —
asserted the same way test_email_template_planners.py does: an active
`@respx.mock` with no routes registered raises on any httpx call that
escapes the process, so a passing test here is itself the proof.
"""

from __future__ import annotations

import json
import struct
import zlib

import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.tools import email_craft as ec

runner = CliRunner()


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


SPEC = {
    "name": "Newsletter",
    "subject": "Hello",
    "sections": [
        {
            "rows": [
                {
                    "layout": "2 Columns",
                    "cells": [
                        {
                            "blocks": [
                                {
                                    "kind": "text",
                                    "paragraphs": [{"text": "left"}],
                                }
                            ]
                        },
                        {
                            "blocks": [
                                {"kind": "button", "label": "Go", "url": "https://x"}
                            ]
                        },
                    ],
                }
            ]
        }
    ],
}


@respx.mock
def test_craft_config_with_no_spec_lists_block_kinds_and_layouts():
    result = runner.invoke(cli.app, ["messages", "templates", "craft-config"])
    assert result.exit_code == 0
    assert "text" in result.output
    assert "2 Columns (1/3 and 2/3)" in result.output


@respx.mock
def test_craft_config_with_spec_emits_coupled_craft_json_and_content(tmp_path):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    result = runner.invoke(
        cli.app,
        ["messages", "templates", "craft-config", "--spec-file", str(spec_file)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "craft_json" in payload
    assert "content" in payload
    assert "mj-column-per-50" in payload["content"]


@respx.mock
def test_craft_config_out_html_writes_compiled_body(tmp_path):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    out_html = tmp_path / "preview.html"
    result = runner.invoke(
        cli.app,
        [
            "messages",
            "templates",
            "craft-config",
            "--spec-file",
            str(spec_file),
            "--out-html",
            str(out_html),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_html.read_text().startswith("<!doctype html>")


@respx.mock
def test_create_dry_run_shows_plan_with_no_live_calls(tmp_path):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    result = runner.invoke(
        cli.app,
        [
            "messages",
            "templates",
            "create",
            "--spec-file",
            str(spec_file),
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    op = plan["operations"][0]
    assert op["action"] == "create"
    assert op["kind"] == "email_template"
    assert op["payload"]["sender_type"] == "business"
    assert "craft_json" not in SPEC  # the spec file itself never carried it


@respx.mock
def test_create_dry_run_with_an_image_block_uploads_nothing(tmp_path):
    """The regression net for the dry-run-performs-a-real-write finding: an
    active `@respx.mock` with no routes registered fails on ANY live call —
    including the image upload's presign/S3/success dance — so a dry run
    that resolves images for real would error here instead of passing.
    """
    png_path = tmp_path / "logo.png"
    png_path.write_bytes(_make_png(4, 4))
    spec = {
        "name": "Newsletter",
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
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))

    result = runner.invoke(
        cli.app,
        [
            "messages",
            "templates",
            "create",
            "--spec-file",
            str(spec_file),
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    image_node = next(
        n
        for n in plan["operations"][0]["payload"]["craft_json"].values()
        if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == "Image"
    )
    assert image_node["props"]["fileId"] == ec.OFFLINE_FILE_PLACEHOLDER


@respx.mock
def test_craft_config_with_merge_field_text_makes_no_live_calls_and_omits_objectname(
    tmp_path,
):
    """The same no-live-call proof as the image case above, for a
    `paragraphs`-based text block carrying a custom-object merge-field
    token: `craft-config` never resolves it live (an active `@respx.mock`
    with no routes registered would fail on any http call that escaped),
    and its output reflects that — `data-merge-field-objectname` is absent,
    a real, documented divergence from what `create`/`update` would
    produce."""
    spec = {
        "name": "Newsletter",
        "sections": [
            {
                "rows": [
                    {
                        "layout": "1 Column",
                        "cells": [
                            {
                                "blocks": [
                                    {
                                        "kind": "text",
                                        "paragraphs": [
                                            {"text": "{{ some_custom_object.stage }}"}
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ],
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    result = runner.invoke(
        cli.app,
        ["messages", "templates", "craft-config", "--spec-file", str(spec_file)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert (
        'data-merge-field-relationship="some_custom_object.stage"'
        in (payload["content"])
    )
    assert "data-merge-field-objectname" not in payload["content"]


@respx.mock
def test_craft_config_reflects_layout_props_from_the_spec(tmp_path):
    """BCLI-024's acceptance criterion: `craft-config` reflects every new
    prop in its output, exercised through the actual CLI command — not
    just the model accepting the field."""
    spec = {
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
                                    },
                                    {"kind": "divider", "size": "1"},
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    result = runner.invoke(
        cli.app,
        ["messages", "templates", "craft-config", "--spec-file", str(spec_file)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    craft_json = payload["craft_json"]

    def props_of(kind):
        return next(
            n["props"]
            for n in craft_json.values()
            if isinstance(n, dict) and n.get("type", {}).get("resolvedName") == kind
        )

    section_props = props_of("Section")
    assert section_props["maxWidth"] == "600"
    assert section_props["containerWidth"] == "900"

    row_props = props_of("Row")
    assert row_props["width"] == "75"
    assert row_props["containerWidth"] == "580"
    assert row_props["containerPaddingRight"] == "40"

    button_props = props_of("Button")
    assert button_props["borderRadius"] == "20"
    assert button_props["alignment"] == "left"

    divider_props = props_of("Divider")
    assert divider_props["size"] == "1"


def test_create_has_no_craft_json_or_content_flag():
    result = runner.invoke(cli.app, ["messages", "templates", "create", "--help"])
    assert result.exit_code == 0
    assert "--craft-json" not in result.output
    assert "--content" not in result.output

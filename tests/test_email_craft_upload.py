"""Tests for `tools.email_craft`'s live-call surface: image upload (via
`api.files.upload_file`, `source="public_image"`) and `api.files.delete_file`.

Everything here is respx-mocked, same three-legged S3 dance
`test_smart_connectors_authoring.py` already exercises for
`SMART_CONNECTOR_IMPORT` — this pins the email-specific `source` constant
and the resulting `Image.src` shape instead.
"""

from __future__ import annotations

import struct
import zlib

import httpx
import pytest
import respx

from kizen_builder.api import files as files_api
from kizen_builder.api.client import KizenClient
from kizen_builder.models.spec.email_templates import EmailTemplateDef
from kizen_builder.tools import email_craft as ec
from tests.conftest import FAKE_BASE_URL

S3_URL = "https://files.example.test/"


@pytest.fixture
def client(env_config):
    with KizenClient(env_config) as c:
        yield c


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


@respx.mock
def test_upload_email_image_uses_public_image_source_and_returns_natural_dims(
    client, tmp_path
):
    png_path = tmp_path / "logo.png"
    png_path.write_bytes(_make_png(12, 34))

    presign = respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": S3_URL,
                "fields": {"key": "biz/public_image/logo.png"},
                "s3object_id": "s3-obj-9",
            },
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e1"'}))
    success = respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-9", "name": "logo.png"})
    )

    info = ec.upload_email_image(client, FAKE_BASE_URL, png_path)

    assert presign.calls.last.request.url.params["source"] == files_api.PUBLIC_IMAGE
    # is_public=true is what makes the emitted src reachable by an
    # unauthenticated recipient — see api/files.py::upload_file. Without it
    # every uploaded image 404s for anyone reading the email outside an
    # authenticated session.
    assert b"is_public=true" in success.calls.last.request.content
    assert info["file_id"] == "file-9"
    assert info["src"] == f"{FAKE_BASE_URL}/api/files/file-9/download"
    assert info["natural_width"] == 12
    assert info["natural_height"] == 34


@respx.mock
def test_resolve_spec_images_uploads_every_image_block(tmp_path):
    png_path = tmp_path / "pic.png"
    png_path.write_bytes(_make_png(5, 6))
    respx.get(f"{FAKE_BASE_URL}/api/s3/presigned-post").mock(
        return_value=httpx.Response(
            200,
            json={"url": S3_URL, "fields": {"key": "k"}, "s3object_id": "s3-obj-1"},
        )
    )
    respx.post(S3_URL).mock(return_value=httpx.Response(204, headers={"etag": '"e"'}))
    respx.post(f"{FAKE_BASE_URL}/api/s3/success").mock(
        return_value=httpx.Response(200, json={"id": "file-1", "name": "pic.png"})
    )

    spec = EmailTemplateDef.model_validate(
        {
            "name": "t",
            "sections": [
                {
                    "rows": [
                        {
                            "layout": "1 Column",
                            "cells": [
                                {"blocks": [{"kind": "image", "file": str(png_path)}]}
                            ],
                        }
                    ]
                }
            ],
        }
    )
    resolved = ec.resolve_spec_images(spec)
    block = resolved[0]["rows"][0]["cells"][0]["blocks"][0]
    assert block["file_id"] == "file-1"
    assert block["natural_width"] == 5
    assert block["natural_height"] == 6


@respx.mock
def test_delete_file_is_a_real_delete(client):
    route = respx.delete(f"{FAKE_BASE_URL}/api/files/file-1").mock(
        return_value=httpx.Response(204)
    )
    files_api.delete_file(client, "file-1")
    assert route.called


@respx.mock
def test_upload_email_image_rejects_unsupported_format_before_any_network_call(
    client, tmp_path
):
    bogus = tmp_path / "picture.gif"
    bogus.write_bytes(b"GIF89a" + b"\x00" * 20)
    # No routes registered: any network call would 500 via respx's
    # assert_all_mocked default, proving the format check runs first.
    with pytest.raises(ValueError, match="GIF"):
        ec.upload_email_image(client, FAKE_BASE_URL, bogus)

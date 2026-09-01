"""Image upload + header-byte pixel dimensions for email `Image` blocks.

Split out of `tools/email_craft.py` (see that module's docstring for the
craft_json/content coupling invariant this surface exists around) — nothing
here mints a node id or touches `craft_json`/`content`. `email_craft.py`
imports `upload_email_image`/`read_image_dimensions` by name so its own
`resolve_spec_images`/`offline_resolve_spec_images` keep calling them as if
they were still local.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kizen_builder.api import files as files_api
from kizen_builder.api.client import KizenClient


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
    :func:`email_craft.offline_resolve_spec_images` instead, which uploads
    nothing. ``base_url`` is the target env's own base URL
    (``EnvConfig.base_url``) — ``Image.src`` is host-absolute, confirmed
    live, so a template is environment-bound.
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

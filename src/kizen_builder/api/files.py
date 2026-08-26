"""Raw file transfer against Kizen's file store.

Everything else in ``api/`` speaks JSON; this module is the exception. Kizen
stores files in S3 and the browser talks to S3 directly, so a CLI upload has to
walk the same three-legged dance:

1. ``GET /api/s3/presigned-post`` — Kizen mints a signed POST policy and an
   ``s3object_id`` (the uuid the eventual ``File`` record will use).
2. ``POST <returned url>`` — a multipart form straight to **S3**, carrying the
   returned ``fields`` verbatim plus the file bytes last. Kizen's auth headers
   must *not* go on this request; the signature is the authorization. S3 answers
   204 with the object's ``ETag``.
3. ``POST /api/s3/success`` — hands Kizen the uuid/key/name/etag so it registers
   a real ``File`` row. **Form-encoded, not JSON** — and since ``KizenClient``
   pins ``Content-Type: application/json`` at the client level, the form
   content-type has to be passed explicitly per-request to override it.

``source`` labels what the file is for and is baked into both the S3 key and the
signed policy's tags, so it has to match on legs 1 and 3.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import EnvConfig

# The ``source`` a smart connector's reference/sample file is uploaded under.
SMART_CONNECTOR_IMPORT = "smart_connector_import"

# The ``source`` an email-template Image block's file is uploaded under —
# confirmed live 2026-08-25 from the Kizen email builder's own browser
# network trace, then confirmed end-to-end through `upload_file()` itself.
# `source` is a server-validated closed choice (~30 other plausible names
# all rejected live); this is the only one confirmed to work for an image.
# See `kizen docs show email-templates`.
PUBLIC_IMAGE = "public_image"


def download_file(
    config: EnvConfig, file_id: str, timeout: float = 120.0
) -> tuple[bytes, str | None]:
    """Download a stored file as raw bytes.

    Hits ``/api/files/{file_id}/download`` with the env's auth headers and
    returns ``(content, filename)`` where filename is parsed from the
    Content-Disposition header when present. Kept off ``KizenClient``, whose
    verbs only ever return parsed JSON — this is one of two places the CLI needs
    a raw response body.
    """
    headers = {**config.auth_headers(), "Accept": "*/*"}
    try:
        with httpx.Client(
            base_url=config.base_url, headers=headers, timeout=timeout
        ) as c:
            resp = c.get(f"/api/files/{file_id}/download")
    except httpx.HTTPError as exc:
        raise KizenAPIError(
            0, f"network error downloading file {file_id}: {exc}"
        ) from exc
    if not resp.is_success:
        raise KizenAPIError(resp.status_code, f"failed to download file {file_id}")

    filename: str | None = None
    disp = resp.headers.get("content-disposition", "")
    if "filename=" in disp:
        filename = disp.split("filename=", 1)[1].strip().strip('"') or None
    return resp.content, filename


def upload_file(
    client: KizenClient,
    path: str | Path,
    *,
    source: str = SMART_CONNECTOR_IMPORT,
    content_type: str | None = None,
    is_public: bool = False,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Upload a local file and return the registered Kizen ``File`` dict.

    The returned dict's ``id`` is what a ``source_file_id`` (or any file field)
    wants. ``content_type`` is guessed from the extension when omitted; the
    guess is signed into the S3 policy, so a mismatch between what's declared
    here and what S3 receives fails the upload rather than uploading something
    mislabeled. ``is_public`` sets ``POST /api/s3/success``'s own
    ``is_public`` field (confirmed live 2026-08-25 via ``GET
    /api/docs/schema``, "Whether the S3 object is public (default: false)")
    — omitted means false, matching the field's own documented default. A
    caller that needs an unauthenticated recipient to load the file (an
    email's ``Image.src``, say) must pass ``is_public=True`` explicitly.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"{src} is not a file")
    ctype = (
        content_type or mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    )

    # Leg 1: signed policy from Kizen.
    presigned = client.get(
        "/api/s3/presigned-post",
        params={"contenttype": ctype, "filename": src.name, "source": source},
    )
    if not isinstance(presigned, dict) or not presigned.get("url"):
        raise KizenAPIError(0, f"unexpected presigned-post response: {presigned!r}")
    fields = dict(presigned.get("fields") or {})
    key = fields.get("key")
    s3object_id = presigned.get("s3object_id")
    if not key or not s3object_id:
        raise KizenAPIError(
            0, f"presigned-post response missing key/s3object_id: {presigned!r}"
        )

    max_size = presigned.get("max_file_size")
    size = src.stat().st_size
    if isinstance(max_size, int) and size > max_size:
        raise KizenAPIError(
            0, f"{src.name} is {size} bytes; the store's limit is {max_size}"
        )

    # Leg 2: straight to S3, unauthenticated (the policy is the credential).
    # The file part must come last — S3 ignores form fields that follow it.
    try:
        with httpx.Client(timeout=timeout) as anon:
            s3_resp = anon.post(
                presigned["url"],
                data=fields,
                files={"file": (src.name, src.read_bytes(), ctype)},
            )
    except httpx.HTTPError as exc:
        raise KizenAPIError(
            0, f"network error uploading to the file store: {exc}"
        ) from exc
    if not s3_resp.is_success:
        raise KizenAPIError(
            s3_resp.status_code,
            f"file store rejected the upload of {src.name}: {s3_resp.text[:500]}",
        )
    etag = (s3_resp.headers.get("etag") or "").strip('"')

    # Leg 3: register the File with Kizen (form-encoded).
    data = {"uuid": s3object_id, "key": key, "name": src.name, "etag": etag}
    if is_public:
        data["is_public"] = "true"
    registered = client.post(
        "/api/s3/success",
        params={"source": source},
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not isinstance(registered, dict) or not registered.get("id"):
        raise KizenAPIError(0, f"unexpected s3/success response: {registered!r}")
    return registered


def delete_file(client: KizenClient, file_id: str) -> Any:
    """DELETE /api/files/{id} — confirmed live 2026-08-25 (a follow-up
    download of the same id 404s afterward, so this is a real delete, not a
    soft no-op). Needed for drift teardown: `GET /api/files` is broken
    (301s to plain HTTP, then 404s), so there is no other way to find or
    remove a file a drift run uploaded.
    """
    return client.delete(f"/api/files/{file_id}")

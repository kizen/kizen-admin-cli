# Surface: files — upload, reference, and download

Kizen files are a generic resource. The same upload flow backs a file-type
field on a record, an image in a form page or record layout, and a smart
connector's reference file — nothing about it is connector-specific.

Not a spec-file surface: there is no `files` command group. This is the wire
reference for the flow other commands use, plus what you need to reference an
already-uploaded file by id.

## Referencing an image that is already uploaded

An image block — in a form/survey page or a record layout `custom_content`
block — can point at a file already in Kizen with no re-upload.
`GET /api/files/{id}` (confirmed live 2026-07-22) returns everything such a
block needs:

| key | use |
|---|---|
| `id` | the file's UUID |
| `url` | the `.../download` URL live examples embed as `src` |
| `thumbnail_url` | preview |
| `content_type` | MIME type |
| `is_public` | public-readable flag |
| `is_common` / `common_key` | a shared/org-wide image is `is_common: true` |

## ⚠️ The file *listing* endpoint is broken

`GET /api/files` — with or without the `search` / `is_common` / `page_size`
params the OpenAPI schema documents — 301s to `http://…/api/files/`. That is a
**scheme downgrade to plain HTTP**, not a trailing-slash normalization, and
following the chain back to `https://…/api/files/` 404s.

Confirmed specific to the collection endpoint: `GET /api/files/{id}` (one
file, no trailing slash) works fine and returns full detail.

**Upshot:** there is no clean way to browse or search existing files over the
API. A caller has to already know the file's UUID — copied from another block
or record that references it, or from the Kizen UI's file manager.

## Uploading a file (the S3 presigned flow)

Files do not upload to Kizen directly; they go to S3 under a signed policy and
are then registered. Three calls:

```
GET  /api/s3/presigned-post?contenttype=<mime>&filename=<name>&source=<source>
POST <the returned `url`>          # multipart form from the returned `fields`
POST /api/s3/success?source=<source>
```

1. **Presign.** The response carries `url`, `fields` (the policy form), and
   `s3object_id`.
2. **POST the form straight to S3.** Include `file` **last**, and send **no
   Kizen auth headers** — the signed policy *is* the credential, and adding
   auth headers breaks the signature.
3. **Register.** `POST /api/s3/success` is **form-encoded, not JSON**, with
   `uuid` / `key` / `name` / `etag`:
   - `uuid` = the presign response's `s3object_id`
   - `key` = out of the presign response's `fields`
   - `etag` = S3's response header, **quotes stripped**

`source` scopes the upload. The confirmed values are `smart_connector_import`
(a connector's reference file), `public_image` (an email image) and
`record_import` (a CSV for `records import`, stored under
`<business_id>/record_import/<id>.csv`; confirmed live 2026-09-28). The presign
`s3object_id` is the registered file's `id`. A record import's failure report
is a file the server creates. `DELETE /api/files/{id}` answers 403 on it, both
before and after its object is deleted (confirmed live 2026-09-29), so it
can't be cleaned up. Downloading is `GET /api/files/{file_id}/download` — raw
bytes plus a `Content-Disposition` filename, and it does need the normal
Kizen auth headers.

## See also

- `kizen docs show smart-connectors` — the connector reference-file flow that
  wraps this (`set-input`), including the file *shape* each connector type
  needs.
- `kizen docs show form` / `kizen docs show layout` — the image blocks that
  consume a file id.

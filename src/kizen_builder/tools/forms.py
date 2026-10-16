"""Read tools for forms and surveys in a Kizen environment.

Forms (``/api/forms``) and surveys (``/api/surveys``) are structurally
identical — every function here takes ``base_path`` as a keyword so one
implementation covers both; the CLI passes ``"/api/forms"`` or
``"/api/surveys"`` per command group.

Submissions, subscribers, page-view, and upload endpoints are out of scope
for this slice.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import forms as forms_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import form_ui

FORMS_BASE_PATH = "/api/forms"
SURVEYS_BASE_PATH = "/api/surveys"


def list_forms(
    *, base_path: str = FORMS_BASE_PATH, search: str | None = None
) -> list[dict[str, Any]]:
    """Return a summary of every form/survey in the configured env."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = forms_api.list_forms(client, base_path, search=search)

    out: list[dict[str, Any]] = []
    for f in raw:
        out.append(
            {
                "env": config.name,
                "id": f.get("id"),
                "name": f.get("name"),
                "api_name": f.get("api_name"),
                "template_type": f.get("template_type"),
                "n_submissions": f.get("number_submissions"),
                "related_object": f.get("related_object"),
                "deleted": f.get("deleted", False),
                "created": f.get("created"),
            }
        )
    return out


def resolve_form_id(
    client: KizenClient, base_path: str, identifier: str
) -> tuple[str, str]:
    """Resolve a form/survey identifier (api_name or UUID) to (id, name).

    The API accepts either in the path, but we resolve so callers get the
    real UUID and a display name. Raises LookupError if not found.
    """
    try:
        detail = forms_api.get_form(client, base_path, identifier)
        return detail["id"], detail.get("name") or identifier
    except KizenAPIError as e:
        if e.status_code != 404:
            raise
        # Fall back to a name/api_name scan of the list endpoint.
        for f in forms_api.list_forms(client, base_path):
            if identifier in (f.get("id"), f.get("api_name"), f.get("name")):
                return f["id"], f.get("name") or identifier
        raise LookupError(f"'{identifier}' not found under {base_path}") from e


def _normalize_field(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f.get("id"),
        "api_name": f.get("name"),
        "display_name": f.get("display_name"),
        "field_type": f.get("field_type"),
        "is_required": f.get("is_required"),
        "is_read_only": f.get("is_read_only"),
        "is_hidden": f.get("is_hidden"),
        "is_deletable": f.get("is_deletable"),
        "order": f.get("order"),
        "options": [
            {"id": o.get("id"), "name": o.get("name"), "code": o.get("code")}
            for o in (f.get("options") or [])
        ]
        or None,
    }


def get_form(
    identifier: str, *, base_path: str = FORMS_BASE_PATH, include_fields: bool = True
) -> dict[str, Any]:
    """Return one form/survey plus its fields.

    ``identifier`` may be the api_name or the UUID.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = forms_api.get_form(client, base_path, identifier)
        fields: list[dict[str, Any]] = []
        if include_fields:
            fields = forms_api.list_form_fields(client, base_path, detail["id"])

    return {
        "env": config.name,
        "id": detail.get("id"),
        "name": detail.get("name"),
        "api_name": detail.get("api_name"),
        "description": detail.get("description"),
        "template_type": detail.get("template_type"),
        "related_object": detail.get("related_object"),
        "submission_action": detail.get("submission_action"),
        "redirect_url": detail.get("redirect_url"),
        "pass_variables_on_redirect": detail.get("pass_variables_on_redirect"),
        "challenge_token_required": detail.get("challenge_token_required"),
        "subscribers": detail.get("subscribers"),
        "business_merge_fields": detail.get("business_merge_fields"),
        "n_submissions": detail.get("number_submissions"),
        "deleted": detail.get("deleted", False),
        "created": detail.get("created"),
        "fields": [_normalize_field(f) for f in fields],
        "raw": detail,
    }


# ---------------------------------------------------------------------------
# form_ui — resolve a friendly page spec (fields referenced by api_name)
# against a live form/survey's fields, and build the full form_ui value.
# ---------------------------------------------------------------------------


def _resolve_block(
    block_spec: dict[str, Any], fields_by_name: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    kind = block_spec.get("kind")
    if kind == "custom_field":
        field_name = block_spec["field"]
        field = fields_by_name.get(field_name)
        if field is None:
            raise LookupError(
                f"no field named '{field_name}' on this form/survey. "
                f"Available: {sorted(fields_by_name)}"
            )
        return form_ui.custom_field_block(field)
    if kind == "text":
        return form_ui.text_block(block_spec["html"])
    if kind == "html":
        return form_ui.html_block(block_spec["html"])
    if kind == "button":
        return form_ui.button_block(
            block_spec.get("label", "Submit"),
            action=block_spec.get("action", "submit"),
            url=block_spec.get("url", ""),
            color=block_spec.get("color"),
        )
    if kind == "divider":
        return form_ui.divider_block(block_spec.get("color"))
    if kind == "image":
        return form_ui.image_block(
            block_spec["file_id"],
            block_spec["src"],
            block_spec["name"],
            width=block_spec.get("width"),
            natural_width=block_spec.get("natural_width"),
            natural_height=block_spec.get("natural_height"),
        )
    raise ValueError(f"unknown block kind {kind!r}")


def _resolve_row(
    row_spec: dict[str, Any], fields_by_name: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    cells = [
        form_ui.cell([_resolve_block(b, fields_by_name) for b in c["blocks"]])
        for c in row_spec["cells"]
    ]
    return form_ui.row(cells, columns=row_spec.get("columns"))


def _resolve_section(
    section_spec: dict[str, Any], fields_by_name: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    rows = [_resolve_row(r, fields_by_name) for r in section_spec["rows"]]
    return form_ui.section(
        rows, background_color=section_spec.get("background_color", "#FFFFFF")
    )


def _resolve_page(
    page_spec: dict[str, Any], fields_by_name: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if page_spec.get("simple"):
        chosen: list[dict[str, Any]] = []
        for name in page_spec["fields"]:
            field = fields_by_name.get(name)
            if field is None:
                raise LookupError(
                    f"no field named '{name}' on this form/survey. "
                    f"Available: {sorted(fields_by_name)}"
                )
            chosen.append(field)
        return form_ui.simple_form_page(
            chosen,
            heading=page_spec.get("heading"),
            subheading=page_spec.get("subheading"),
            submit_label=page_spec.get("submit_label", "Submit"),
            name=page_spec.get("name", "Form Page"),
        )
    sections = [_resolve_section(s, fields_by_name) for s in page_spec["sections"]]
    return form_ui.page(
        page_spec.get("name", "Form Page"),
        sections,
        is_form_page=page_spec.get("is_form_page", True),
        hidden=page_spec.get("hidden", False),
        hideable=page_spec.get("hideable"),
        deletable=page_spec.get("deletable", False),
    )


def _enrich_custom_object_fields(
    raw_fields: list[dict[str, Any]], client: KizenClient, related_object_id: str | None
) -> list[dict[str, Any]]:
    """Replace each form field's skinny ``custom_object_field`` stub — the
    6-key shape (``id, name, display_name, field_type, is_default,
    custom_object``) that ``list_form_fields`` itself returns — with the
    FULL custom-object field record from the related object's own field
    list (``category, canonical_display_name, is_hidden, order, meta,
    properties, access, ...`` — 20+ keys).

    Confirmed live 2026-07-21: a form saved with the skinny stub rendered
    correctly and accepted a real submission, but the Kizen page-builder
    (the drag-and-drop editor) failed to open it for re-editing. A form
    saved with the full record (matching a real UI-built form) opens fine
    in the builder. The public submit-side renderer is more tolerant than
    the builder/editor.
    """
    if not related_object_id:
        return raw_fields
    full_fields = co_api.list_fields(client, related_object_id)
    by_id = {f["id"]: f for f in full_fields if f.get("id")}
    enriched: list[dict[str, Any]] = []
    for f in raw_fields:
        cof = f.get("custom_object_field")
        if cof and cof.get("id") in by_id:
            f = {**f, "custom_object_field": by_id[cof["id"]]}
        enriched.append(f)
    return enriched


def build_form_ui_from_spec(
    identifier: str, spec: dict[str, Any], *, base_path: str = FORMS_BASE_PATH
) -> dict[str, Any]:
    """Resolve a friendly page spec into a full ``form_ui`` value, ready to
    hand to ``planners.forms.plan_update_form`` as ``{"form_ui": ...}``.

    ``spec`` is ``{"pages": [...], "skip_thank_you"?: bool, "business_merge_fields"?: [...]}``.
    Each page is either ``{"simple": true, "fields": [api_name, ...], "heading"?,
    "subheading"?, "submit_label"?, "name"?}`` (one field per row via
    :func:`kizen_builder.tools.form_ui.simple_form_page`) or a full
    ``{"name"?, "is_form_page"?, "sections": [{"rows": [{"cells": [{"blocks":
    [...]}]}]}]}`` tree — each block is ``{"kind": "custom_field", "field":
    <api_name>}``, ``{"kind": "text", "html": ...}`` (rich-text/WYSIWYG),
    ``{"kind": "html", "html": ...}`` (raw HTML block, a different node type),
    ``{"kind": "button", ...}``, ``{"kind": "divider", ...}``, or
    ``{"kind": "image", ...}`` (see ``tools/form_ui.py``). A "Thank You" page
    is appended automatically unless
    the spec already includes a page with ``is_form_page: false``, or sets
    top-level ``"skip_thank_you": true``.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        form_id, _name = resolve_form_id(client, base_path, identifier)
        detail = forms_api.get_form(client, base_path, form_id)
        raw_fields = forms_api.list_form_fields(client, base_path, form_id)
        related_object_id = (detail.get("related_object") or {}).get("id")
        raw_fields = _enrich_custom_object_fields(raw_fields, client, related_object_id)
    fields_by_name = {f["name"]: f for f in raw_fields if f.get("name")}

    page_specs = spec.get("pages") or []
    if not page_specs:
        raise ValueError("spec must include at least one page under 'pages'")
    pages = [_resolve_page(p, fields_by_name) for p in page_specs]

    has_non_form_page = any(not p.get("is_form_page", True) for p in page_specs)
    if not has_non_form_page and not spec.get("skip_thank_you"):
        pages.append(form_ui.thank_you_page())

    return form_ui.build_form_ui(
        pages, business_merge_fields=spec.get("business_merge_fields")
    )

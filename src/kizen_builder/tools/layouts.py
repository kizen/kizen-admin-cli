"""Read and apply record-layout configurations for custom objects.

Layouts in Kizen are attached to custom objects and control how fields are
arranged on the record detail view.  Every custom object gets a default
"Standard View" layout auto-created on creation.

Key API rules (learned the hard way):
- Always PUT to update — POST fails with "names must be unique" because Kizen
  auto-creates the default layout.
- Every level of ``config`` **must** have an ``id`` (UUID).  Without IDs,
  Kizen merges rather than replaces, leaving orphaned blocks from the previous
  layout that corrupt the view.
- ``autoInclude: true`` blocks must list **all other** category UUIDs in
  ``excluded``; any missing category bleeds through.

Reads (``list_layouts`` / ``get_layout``) back the CLI's ``kizen layouts``
commands. Mutations go through the plan gate: ``planners.layouts`` builds a
``Plan`` from a full config and ``kizen layouts update`` previews + applies it
(the PUT lives in ``api.layouts.update_layout``). The ``explicit_block`` /
``auto_block`` helpers below build ``fields`` blocks for authoring a config;
most other block types (``timeline``, ``action_block``, …) are passed
through opaquely — copy them from ``kizen layouts get <object> -o json``.
``custom_content`` blocks (a static-content/HTML block, confirmed live
2026-07-21) can be authored with :func:`custom_content_block` instead —
see its docstring.
"""

from __future__ import annotations

import uuid
from typing import Any

from kizen_builder.api import layouts as layout_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools import form_ui
from kizen_builder.tools.objects import get_object

# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------


def explicit_block(
    internal_name: str,
    cat_ids: list[str],
    chosen_labels: list[str],
    display_name: str = "",
) -> dict[str, Any]:
    """A layout block that shows exactly the listed category UUIDs.

    Use this for most blocks where you want precise control over which
    categories are shown.

    Parameters
    ----------
    internal_name:
        The block's internal label (shown in the layout editor).
    cat_ids:
        Ordered list of category UUIDs to include in this block.
    chosen_labels:
        Display labels corresponding 1-to-1 with ``cat_ids``.
    display_name:
        Optional override for the block's display name (usually left blank).
    """
    if len(cat_ids) != len(chosen_labels):
        raise ValueError(
            f"cat_ids and chosen_labels must have the same length "
            f"(got {len(cat_ids)} and {len(chosen_labels)})"
        )
    return {
        "id": str(uuid.uuid4()),
        "type": "fields",
        "label": "Fields",
        "metadata": {
            "excluded": [],
            "included": list(cat_ids),
            "autoInclude": False,
            "chosenCategories": [
                {"label": label, "value": cat_id}
                for label, cat_id in zip(chosen_labels, cat_ids, strict=True)
            ],
        },
        "displayName": display_name,
        "internalName": internal_name,
    }


def auto_block(
    internal_name: str,
    shown_cat_id: str,
    shown_label: str,
    all_cat_ids: list[str],
    display_name: str = "",
) -> dict[str, Any]:
    """A layout block that auto-includes one category and excludes all others.

    Use this when you want Kizen to automatically pick up new fields added to
    a category (e.g. Supply & Equipment, Post-Treatment in the flow_sheet
    pattern).  ``all_cat_ids`` must be the complete list of every category UUID
    on the object — the excluded list is derived from it by removing
    ``shown_cat_id``.

    Parameters
    ----------
    internal_name:
        The block's internal label.
    shown_cat_id:
        UUID of the one category this block should display.
    shown_label:
        Display label for ``shown_cat_id``.
    all_cat_ids:
        Complete list of every category UUID on the object.  All UUIDs except
        ``shown_cat_id`` go into ``excluded``.
    display_name:
        Optional override for the block's display name.
    """
    excluded = [cid for cid in all_cat_ids if cid != shown_cat_id]
    return {
        "id": str(uuid.uuid4()),
        "type": "fields",
        "label": "Fields",
        "metadata": {
            "excluded": excluded,
            "included": [],
            "autoInclude": True,
            "chosenCategories": [{"label": shown_label, "value": shown_cat_id}],
        },
        "displayName": display_name,
        "internalName": internal_name,
    }


# ---------------------------------------------------------------------------
# Custom content (static-content / HTML) blocks
# ---------------------------------------------------------------------------


def custom_content_block(
    sections: list[dict[str, Any]], *, label: str = "Custom Content"
) -> dict[str, Any]:
    """A "Custom Content" layout block — a craft.js content tree embedded
    directly as ``metadata.blockJson`` (a **nested object**, NOT a
    JSON-encoded string — unlike a form/survey's ``page_data``). Confirmed
    live 2026-07-21 from a real layout authored through the actual Kizen
    layout builder.

    Build ``sections`` with ``tools.form_ui``'s ``section()``/``row()``/
    ``cell()``/``text_block()``/``html_block()``/``button_block()``/
    ``divider_block()``/``image_block()`` helpers — the same camelCase
    craft.js node vocabulary forms/surveys use for ``CustomField``/``Text``/
    ``Button``/``Divider``/``Image``/``HTMLBlock`` leaf blocks (``custom_field``
    blocks don't apply here — a layout isn't tied to one related object the
    way a form is). The one confirmed structural difference: this block's
    ``Root`` node carries **no** ``container*``-prefixed props at all — just
    ``backgroundColor``/``color``/``fontFamily``/``fontSize``/``linkColor``/
    ``lineHeight``/``height``/``maxWidth``/``hasShadow``/``tabletBreak``/
    ``mobileBreak``, matching the dashboard static-content dashlet's Root
    shape (just camelCased) rather than a form page's Root shape.

    Every node starts from the layout builder's own creation defaults
    (``form_ui.STATIC_BLOCK_DEFAULTS``), not a form's.
    """
    tree = form_ui.build_content_tree(sections, defaults=form_ui.STATIC_BLOCK_DEFAULTS)
    return {
        "id": str(uuid.uuid4()),
        "label": label,
        "type": "custom_content",
        "metadata": {"blockJson": tree},
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _fetch_layouts(object_api_name: str) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(object_id, raw_layouts)`` for a custom object.

    Normalizes the list endpoint's envelope (DRF ``results`` or a bare list).
    """
    obj = get_object(object_api_name)
    obj_id = obj["id"]

    config = load_env_config()
    with KizenClient(config) as client:
        layouts = layout_api.list_layouts(client, obj_id)
    return obj_id, layouts


def _layout_block_summary(config: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a layout config to one row per block (for a readable listing)."""
    rows: list[dict[str, Any]] = []
    for gi, group in enumerate(config or []):
        for ci, column in enumerate(group.get("columns", [])):
            for item in column.get("items", []):
                meta = item.get("metadata") or {}
                rows.append(
                    {
                        "group": gi,
                        "column": ci,
                        "width": column.get("width"),
                        "type": item.get("type"),
                        "internalName": item.get("internalName"),
                        "displayName": item.get("displayName"),
                        "autoInclude": meta.get("autoInclude"),
                        "included": meta.get("included"),
                    }
                )
    return rows


def list_layouts(object_api_name: str) -> list[dict[str, Any]]:
    """Return every layout defined on a custom object (summary form)."""
    _obj_id, layouts = _fetch_layouts(object_api_name)
    return [
        {
            "id": layout.get("id"),
            "name": layout.get("name"),
            "active": layout.get("active"),
            "order": layout.get("order"),
            "block_count": len(_layout_block_summary(layout.get("config", []))),
        }
        for layout in layouts
    ]


def get_layout(object_api_name: str, layout_name: str | None = None) -> dict[str, Any]:
    """Return one layout for a custom object.

    With no ``layout_name`` the first layout (the auto-created "Standard View")
    is returned. Otherwise the layout whose name matches is returned. Raises
    ``LookupError`` if the object has no layouts or the named one is absent.
    """
    _obj_id, layouts = _fetch_layouts(object_api_name)
    if not layouts:
        raise LookupError(f"no layouts found for object '{object_api_name}'")

    layout: dict[str, Any] | None
    if layout_name is None:
        layout = layouts[0]
    else:
        layout = next((lo for lo in layouts if lo.get("name") == layout_name), None)
        if layout is None:
            names = ", ".join(repr(lo.get("name")) for lo in layouts)
            raise LookupError(
                f"object '{object_api_name}' has no layout named {layout_name!r} "
                f"(available: {names})"
            )
    return {
        "id": layout["id"],
        "name": layout.get("name", "Standard View"),
        "active": layout.get("active"),
        "order": layout.get("order"),
        "config": layout.get("config", []),
        "blocks": _layout_block_summary(layout.get("config", [])),
        "raw": layout,
    }


# ---------------------------------------------------------------------------
# Apply (inject UUIDs + PUT)
# ---------------------------------------------------------------------------


def inject_layout_ids(config: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk a layout config and ensure every group, column, and item has an id.

    Modifies and returns the config in-place (also returns it for convenience).
    Existing IDs are preserved; missing ones are generated with uuid4.
    """
    for group in config:
        if "id" not in group:
            group["id"] = str(uuid.uuid4())
        for column in group.get("columns", []):
            if "id" not in column:
                column["id"] = str(uuid.uuid4())
            for item in column.get("items", []):
                if "id" not in item:
                    item["id"] = str(uuid.uuid4())
    return config

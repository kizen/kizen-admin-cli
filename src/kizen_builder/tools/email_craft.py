"""Build an email template's `craft_json` and compiled `content` from one spec.

**Why this has to be one function, not two.** `craft_json` (the editable
craft.js tree the builder shows) and `content` (the Outlook-safe HTML that
actually gets sent) are stored as independent fields — the server compiles
neither from the other, confirmed live both for `PATCH` and `POST` (see
`docs/specs/email-templates.md`). The compiled HTML carries a
`section-<nodeId>` class for every `Section` **and** every `Row` node, with
no orphans on either side in every live capture. So the two fields are
coupled by node id, not merely parallel: build the tree once, mint each id
once, and derive both outputs from that single pass. Building the tree and
then compiling HTML in a second pass that mints its *own* ids produces a
template whose builder view and real output silently disagree — the exact
failure this module exists to make impossible.

`build_email_content()` is the one entry point that upholds that invariant:
it calls `tools.form_ui.build_content_tree` once (the single id-minting pass
for `Root`/`Section`/`Row`/`Cell` nodes, threaded through the
`cell_props`/`block_assembler`/`section_props`/`row_props` hooks added there
for this module) and then compiles `content` from that exact tree via
`email_html._compile_html` — never a second tree-walk that could mint ids of
its own. This module owns id minting end-to-end: `form_ui.build_content_tree`
for the container nodes, `_assemble_email_block` for leaf blocks below.
Split across three modules for size — `email_html.py` (compiles `content`
from an existing tree; never mints an id) and `email_images.py` (upload +
header-byte dimension reading) — but the one-pass invariant this docstring
describes is unchanged: neither of those modules imports `form_ui` or calls
`_new_id()`.

This module builds this surface's `Text`/`Image`/`Button`/`Divider` nodes
from the email builder's own creation defaults (`form_ui.EMAIL_DEFAULTS`)
plus the spec's own keys, and owns craft_json assembly and spec
resolution. `upload_email_image`/`read_image_dimensions` are imported back
from `email_images.py` by name, so `email_craft.upload_email_image` etc.
keep resolving with no re-export shim. The v1 column-preset table
(`ColumnLayout`/`COLUMN_LAYOUTS`, byte-exact `columns`/`__width` fractions
and compiled-HTML markup, confirmed live 2026-08-25) lives in `email_html.py`
instead of here — `row()` below imports `COLUMN_LAYOUTS` back from there to
validate cell counts, since `email_html.py` can't import from this module
(this module already needs `_compile_html` from it, and a circular import
isn't an option).

v1 scope only: `Text`, `Image`, `Button`, `Divider` leaf blocks, and the 4
column presets in `COLUMN_LAYOUTS` (`1 Column`, `2 Columns`, `2 Columns
(1/3 and 2/3)`, `2 Columns (2/3 and 1/3)`). `Attachments` and the other 5
presets (`3`/`4`/`5`/`6 Columns`, `3 Columns (gutters)`) are confirmed live
but out of scope — anything using them fails loudly rather than silently
degrading. There is no raw-HTML escape hatch on this surface (no
`HTMLBlock`, confirmed live directly against the builder).
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec.email_templates import (
    ButtonBlockDef,
    DividerBlockDef,
    EmailTemplateDef,
    ImageBlockDef,
    PaddingDef,
    ParagraphDef,
    TextBlockDef,
)
from kizen_builder.tools import form_ui, merge_fields, objects
from kizen_builder.tools.email_html import COLUMN_LAYOUTS, _compile_html
from kizen_builder.tools.email_images import read_image_dimensions, upload_email_image

# Every node starts from the props the Kizen email builder writes when a user
# adds it — `form_ui.EMAIL_DEFAULTS`, where the front-end source is cited —
# and only the spec's own keys replace them.

# ---------------------------------------------------------------------------
# v1 column presets — the byte-exact layout table (`ColumnLayout`,
# `COLUMN_LAYOUTS`) lives in `email_html.py` and is imported above; this
# module keeps the out-of-scope guard and the public enumeration API.
# ---------------------------------------------------------------------------

# The other 5 presets are pre-captured groundwork for a follow-on item, not
# built here. Naming one is a clear, immediate error, not a silent skip.
_OUT_OF_SCOPE_LAYOUTS = (
    "3 Columns",
    "3 Columns (gutters)",
    "4 Columns",
    "5 Columns",
    "6 Columns",
)


def known_layouts() -> list[str]:
    """The v1 closed enum of layout names, in display order."""
    return list(COLUMN_LAYOUTS)


def known_block_kinds() -> list[str]:
    """The v1 closed set of leaf block kinds this emitter supports."""
    return ["text", "image", "button", "divider"]


# ---------------------------------------------------------------------------
# Spec-builder helpers — plain dicts consumed by build_email_content().
# Mirrors tools.form_ui's cell()/row()/section() naming, but this surface's
# own leaf-block shapes (email Button/Divider props differ from forms' —
# see the module docstring) and its own layout validation.
# ---------------------------------------------------------------------------


def text_block(html: str) -> dict[str, Any]:
    return {"kind": "text", "html": html}


def image_block(
    *,
    file_id: str,
    src: str,
    name: str,
    alt: str = "",
    link: str = "",
    width: int | None = None,
    natural_width: int | None = None,
    natural_height: int | None = None,
    container_width: str | None = None,
    max_width: str | None = None,
    max_height: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "image",
        "file_id": file_id,
        "src": src,
        "name": name,
        "alt": alt,
        "link": link,
        "width": width,
        "natural_width": natural_width,
        "natural_height": natural_height,
        "container_width": container_width,
        "max_width": max_width,
        "max_height": max_height,
    }


def button_block(
    label: str,
    url: str,
    *,
    color: str | None = None,
    border_radius: str | None = None,
    padding_left: str | None = None,
    padding_right: str | None = None,
    alignment: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "button",
        "label": label,
        "url": url,
        "color": color,
        "border_radius": border_radius,
        "padding_left": padding_left,
        "padding_right": padding_right,
        "alignment": alignment,
    }


def divider_block(
    color: str | None = None, *, size: str | None = None
) -> dict[str, Any]:
    return {"kind": "divider", "color": color, "size": size}


def cell(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"blocks": blocks}


def row(
    cells: list[dict[str, Any]],
    layout: str = "1 Column",
    *,
    width: str | None = None,
    container_width: str | None = None,
    padding: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """One row using a v1 column preset by name.

    Raises ``ValueError`` — never a silent reshape — for an unknown preset
    name or a cell count that doesn't match it, naming the valid presets or
    the expected count. The planner (``tools.planners.messages``) catches
    this and re-raises as a ``PlanError``.

    ``width``/``container_width``/``padding`` left as ``None`` keep the
    email builder's own Row default — see ``_row_props``.
    """
    if layout in _OUT_OF_SCOPE_LAYOUTS:
        raise ValueError(
            f"layout {layout!r} is confirmed live but out of v1 scope "
            f"(see the work item's pre-captured groundwork). Supported: "
            f"{', '.join(known_layouts())}"
        )
    preset = COLUMN_LAYOUTS.get(layout)
    if preset is None:
        raise ValueError(
            f"unknown row layout {layout!r}. Supported: {', '.join(known_layouts())}"
        )
    if len(cells) != len(preset.columns):
        raise ValueError(
            f"layout {layout!r} needs {len(preset.columns)} cell(s), got {len(cells)}"
        )
    return {
        "cells": cells,
        "columns": list(preset.columns),
        "layout": layout,
        "width": width,
        "container_width": container_width,
        "padding": padding,
    }


def section(
    rows: list[dict[str, Any]],
    *,
    background_color: str | None = None,
    max_width: str | None = None,
    container_width: str | None = None,
    padding: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Each keyword left as ``None`` keeps the email builder's own Section
    default — see ``_section_props``."""
    return {
        "rows": rows,
        "background_color": background_color,
        "max_width": max_width,
        "container_width": container_width,
        "padding": padding,
    }


# ---------------------------------------------------------------------------
# craft_json assembly — reuses tools.form_ui's Root/Section/Row/Cell shell
# via the cell_props/block_assembler hooks added there for this module.
# ---------------------------------------------------------------------------


def _cell_props(width: float | None) -> dict[str, Any]:
    # Kizen's email compile writes each Cell's column fraction onto the tree
    # it saves (packages/mjml/src/parser.ts:249-257, run on the saved object
    # by components/MessageBuilder/utils.js:156-171), redundant with (and
    # agreeing with) the parent Row's own columns entry.
    return {"__width": width}


def _padding_overrides(padding: dict[str, str | None] | None) -> dict[str, Any]:
    """The sides a spec's ``padding`` sets; a side left out keeps the
    node's default."""
    if padding is None:
        return {}
    sides = {
        "containerPaddingTop": padding.get("top"),
        "containerPaddingRight": padding.get("right"),
        "containerPaddingBottom": padding.get("bottom"),
        "containerPaddingLeft": padding.get("left"),
    }
    return {key: value for key, value in sides.items() if value is not None}


def _section_props(section_spec: dict[str, Any]) -> dict[str, Any]:
    """The ``section_props`` hook for ``form_ui.build_content_tree``: the
    spec's own Section keys, merged over ``form_ui.EMAIL_DEFAULTS.section``.
    A key the spec leaves as ``None`` is not overridden; ``containerWidth``,
    which has no creation default, is only sent when the spec sets it."""
    overrides: dict[str, Any] = {}
    if section_spec.get("max_width") is not None:
        overrides["maxWidth"] = section_spec["max_width"]
    if section_spec.get("container_width") is not None:
        overrides["containerWidth"] = section_spec["container_width"]
    overrides.update(_padding_overrides(section_spec.get("padding")))
    return overrides


def _row_props(row_spec: dict[str, Any]) -> dict[str, Any]:
    """The ``row_props`` hook for ``form_ui.build_content_tree`` — see
    ``_section_props``. `Row` layout props are not uniform across the
    reference template (`containerWidth`, padding, and `width` itself vary
    row-to-row with no clean derivation from `Section.max_width`/padding),
    so these are independent overrides, never computed from the parent
    Section."""
    overrides: dict[str, Any] = {}
    if row_spec.get("width") is not None:
        overrides["width"] = row_spec["width"]
    if row_spec.get("container_width") is not None:
        overrides["containerWidth"] = row_spec["container_width"]
    overrides.update(_padding_overrides(row_spec.get("padding")))
    return overrides


def _set_given(
    props: dict[str, Any], block: dict[str, Any], keys: dict[str, str]
) -> None:
    """Copy each spec key the block sets (anything but ``None``, an empty
    string or ``"0"`` included) onto its prop."""
    for spec_key, prop in keys.items():
        if block.get(spec_key) is not None:
            props[prop] = block[spec_key]


def _assemble_text_block(block: dict[str, Any], parent_id: str) -> dict[str, Any]:
    return form_ui._leaf_node(
        "Text",
        form_ui._props(form_ui.EMAIL_DEFAULTS.text),
        parent_id,
        {"text": block["html"]},
    )


def _assemble_image_block(block: dict[str, Any], parent_id: str) -> dict[str, Any]:
    # The email builder's image creation props, with the file's own
    # src/name/fileId/natural size; a spec `width` switches to a fixed size
    # the way the Size setting does. No `width` keeps `size: "auto"` and
    # `width: None`; see `email_html._render_image` for how `content` sizes
    # it.
    props = form_ui._image_props(block, form_ui.EMAIL_DEFAULTS.image)
    _set_given(
        props,
        block,
        {
            "alt": "alt",
            "link": "link",
            "container_width": "containerWidth",
            "max_width": "maxWidth",
            "max_height": "maxHeight",
        },
    )
    return form_ui._leaf_node("Image", props, parent_id)


def _assemble_button_block(block: dict[str, Any], parent_id: str) -> dict[str, Any]:
    props = form_ui._props(form_ui.EMAIL_DEFAULTS.button)
    props["label"] = block["label"]
    props["url"] = block.get("url", "")
    _set_given(
        props,
        block,
        {
            "color": "color",
            "border_radius": "borderRadius",
            "padding_left": "paddingLeft",
            "padding_right": "paddingRight",
            "alignment": "alignment",
        },
    )
    return form_ui._leaf_node("Button", props, parent_id)


def _assemble_divider_block(block: dict[str, Any], parent_id: str) -> dict[str, Any]:
    props = form_ui._props(form_ui.EMAIL_DEFAULTS.divider)
    _set_given(props, block, {"size": "size", "color": "color"})
    return form_ui._leaf_node("Divider", props, parent_id)


def _assemble_email_block(
    block: dict[str, Any], parent_id: str, content: dict[str, Any]
) -> str:
    kind = block["kind"]
    node_id = form_ui._new_id()

    if kind == "text":
        node = _assemble_text_block(block, parent_id)
    elif kind == "image":
        node = _assemble_image_block(block, parent_id)
    elif kind == "button":
        node = _assemble_button_block(block, parent_id)
    elif kind == "divider":
        node = _assemble_divider_block(block, parent_id)
    else:
        raise ValueError(
            f"unsupported email block kind: {kind!r}. Supported: "
            f"{', '.join(known_block_kinds())}"
        )

    content[node_id] = node
    return node_id


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def build_email_content(sections: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Return ``(craft_json, content)`` from ONE id-assignment pass.

    ``sections`` is built from :func:`section`/:func:`row`/:func:`cell`/
    ``*_block()`` above (or straight from an :class:`EmailTemplateDef` via
    ``tools.planners.messages``). Never expose a way to build one output
    without the other — see the module docstring for why.
    """
    craft_json = form_ui.build_content_tree(
        sections,
        defaults=form_ui.EMAIL_DEFAULTS,
        cell_props=_cell_props,
        block_assembler=_assemble_email_block,
        section_props=_section_props,
        row_props=_row_props,
    )
    content = _compile_html(craft_json)
    return craft_json, content


def assemble_sections(resolved_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the plain-dict tree :func:`resolve_spec_images` (or
    :func:`offline_resolve_spec_images`) returns into the
    :func:`section`/:func:`row`/:func:`cell`/``*_block()`` spec
    :func:`build_email_content` consumes.

    This is where a row's cell count is checked against its layout preset —
    :func:`row` raises ``ValueError`` on a mismatch. Callers under
    ``tools/planners/`` catch that and re-raise as a ``PlanError`` (a
    validation error at plan time, not a silent reshape); `craft-config`
    lets it surface as a plain CLI error since it isn't planning anything.
    """
    sections: list[dict[str, Any]] = []
    for s in resolved_sections:
        rows: list[dict[str, Any]] = []
        for r in s["rows"]:
            cells: list[dict[str, Any]] = []
            for c in r["cells"]:
                blocks: list[dict[str, Any]] = []
                for b in c["blocks"]:
                    kind = b["kind"]
                    if kind == "text":
                        blocks.append(text_block(b["html"]))
                    elif kind == "image":
                        blocks.append(
                            image_block(
                                file_id=b["file_id"],
                                src=b["src"],
                                name=b["name"],
                                alt=b.get("alt", ""),
                                link=b.get("link", ""),
                                width=b.get("width"),
                                natural_width=b.get("natural_width"),
                                natural_height=b.get("natural_height"),
                                container_width=b.get("container_width"),
                                max_width=b.get("max_width"),
                                max_height=b.get("max_height"),
                            )
                        )
                    elif kind == "button":
                        blocks.append(
                            button_block(
                                b["label"],
                                b["url"],
                                color=b.get("color"),
                                border_radius=b.get("border_radius"),
                                padding_left=b.get("padding_left"),
                                padding_right=b.get("padding_right"),
                                alignment=b.get("alignment"),
                            )
                        )
                    elif kind == "divider":
                        blocks.append(divider_block(b.get("color"), size=b.get("size")))
                    else:
                        raise ValueError(
                            f"unsupported block kind: {kind!r}. Supported: "
                            f"{', '.join(known_block_kinds())}"
                        )
                cells.append(cell(blocks))
            rows.append(
                row(
                    cells,
                    layout=r["layout"],
                    width=r.get("width"),
                    container_width=r.get("container_width"),
                    padding=r.get("padding"),
                )
            )
        sections.append(
            section(
                rows,
                background_color=s.get("background_color"),
                max_width=s.get("max_width"),
                container_width=s.get("container_width"),
                padding=s.get("padding"),
            )
        )
    return sections


# ---------------------------------------------------------------------------
# Spec resolution — walks an EmailTemplateDef into the plain-dict shape
# assemble_sections() turns into row()/cell()/*_block() calls. Only the
# image leg differs between the live and offline (craft-config) callers, so
# both share this walk.
# ---------------------------------------------------------------------------

# `craft-config`'s offline output has no live upload behind it — these tokens
# make that obvious rather than looking like a real id a spec could paste in.
OFFLINE_FILE_PLACEHOLDER = "<FILE_UUID>"
OFFLINE_HOST_PLACEHOLDER = "<HOST>"


def _padding_dict(padding: PaddingDef | None) -> dict[str, str | None] | None:
    return padding.model_dump() if padding is not None else None


def _paragraphs_to_html(
    paragraphs: list[ParagraphDef],
    *,
    resolve_label: merge_fields.ResolveLabel | None = None,
    resolve_objectname: merge_fields.ResolveObjectName | None = None,
) -> str:
    """Render a `TextBlockDef.paragraphs` list into the exact markup Kizen's
    own rich-text editor normalises **to** on save — confirmed live
    2026-08-26 against the reference template's touched `Text` nodes (see
    the work item's "The canonical vocabulary" section): one `<p
    data-line-height="default" style="line-height: 1.25;">` per paragraph,
    `text-align: <align>;` appended to that same `style` only when `align`
    is set, an empty `<p>...</p>` with no `<span>` child for `{"text": ""}`,
    and non-empty text wrapped in one `<span style="...">` carrying
    `font-size: Npx;` and/or `color: ...;` when `size`/`color` is set
    (`<strong>` nested *inside* the span when `bold`, not the reverse), the
    whole run wrapped in `<a rel="noopener noreferrer nofollow" href="...">`
    when `link` is set. `line-height: 1.25` is what the editor writes on a
    paragraph with no line height picked (apps/react-app/src/components/
    WYSIWYG/extensions/line-height.ts:37-38,69-72). It writes a font size
    only once one is picked (extensions/font-size.ts:32-40), so an omitted
    `size` writes none and the text takes the template's own size; a span
    with no style is not written at all, as in the reference's bare-`<a>`
    paragraphs.

    Named to match its module (`tools.email_craft`), not `_render_paragraphs`
    — every `_render_*` function is a compile-side function in
    `tools.email_html` after the split, and this one mints no node id and
    touches no `craft_json` (settled in BCLI-026's review).

    `text` is rendered via `merge_fields.render()` — the sole owner of
    `{{ namespace.field }}` -> `<span class="kzn-merge-field">` markup — in
    place of a bare `html.escape(text)` call; nothing else about escaping
    changes, since `render()` already HTML-escapes every non-token span
    internally. `resolve_label`/`resolve_objectname` of `None` (the
    `craft-config`/`--dry-run` offline path) is handled entirely by
    `merge_fields.render()` itself: every namespace still gets a best-effort
    label from its own fallback tables, but `data-merge-field-objectname`
    is omitted for every namespace since there is no live object lookup to
    answer it — see `_email_merge_field_resolvers` below.
    """
    parts: list[str] = []
    for para in paragraphs:
        p_style = "line-height: 1.25;"
        if para.align is not None:
            p_style += f" text-align: {para.align};"
        if para.text == "":
            parts.append(f'<p data-line-height="default" style="{p_style}"></p>')
            continue

        rendered = merge_fields.render(
            para.text,
            resolve_label=resolve_label,
            resolve_objectname=resolve_objectname,
        )
        if para.bold:
            rendered = f"<strong>{rendered}</strong>"

        span_styles: list[str] = []
        if para.size is not None:
            span_styles.append(f"font-size: {para.size}px;")
        if para.color:
            span_styles.append(f"color: {para.color};")
        run = rendered
        if span_styles:
            run = f'<span style="{" ".join(span_styles)}">{rendered}</span>'

        if para.link:
            run = (
                f'<a rel="noopener noreferrer nofollow" '
                f'href="{escape(para.link, quote=True)}">{run}</a>'
            )
        parts.append(f'<p data-line-height="default" style="{p_style}">{run}</p>')
    return "".join(parts)


def _email_merge_field_resolvers() -> tuple[
    merge_fields.ResolveLabel, merge_fields.ResolveObjectName
]:
    """The live `resolve_label`/`resolve_objectname` pair `merge_fields.render()`
    needs for one spec-compile call, backed by `tools.objects.get_object` —
    deliberately **no** `AutomationDef`/`LiveContext` import, matching
    `merge_fields.py`'s own no-automation-dependency design.

    Simpler than `tools.planners.automations._merge_field_resolvers`, not
    just a copy of it: that resolver special-cases `entity_record`/
    `custom_objects` against an automation's own `target_object`, because
    those two pseudo-tokens mean "the triggering record"/"this automation's
    own target object." A library email template has no target object at
    all, so this resolver has nothing to resolve those two against — they
    fall through to `merge_fields`'s own reserved-namespace handling like
    any other reserved namespace (a bare title-cased guess, no namespace
    prefix). This is a real, load-bearing behavioral difference from
    automations, not an oversight — the real label for `entity_record`/
    `custom_objects` can only be known once a template is cloned into an
    automation-scoped message with a real target object.

    Caches each looked-up object by api_name for the lifetime of this
    resolver pair (a plain `dict[str, dict]`, the same lazy-cache shape
    `LiveContext._objects_by_api` uses in `tools/planners/automations.py`,
    without importing `LiveContext` itself) — a template can reference the
    same custom object many times across paragraphs, and
    `tools.objects.get_object` opens its own `KizenClient` per call.
    """
    cache: dict[str, dict[str, Any]] = {}

    def _object(api_name: str) -> dict[str, Any] | None:
        if api_name not in cache:
            try:
                cache[api_name] = objects.get_object(api_name)
            except KizenAPIError:
                return None
        return cache[api_name]

    def resolve_label(namespace: str, field_path: str) -> str | None:
        if namespace in merge_fields.RESERVED_NAMESPACES:
            return None
        obj = _object(namespace)
        if obj is None:
            return None
        match = next((f for f in obj["fields"] if f["api_name"] == field_path), None)
        return match["display_name"] if match else None

    def resolve_objectname(namespace: str) -> str | None:
        obj = _object(namespace)
        return obj.get("display_name") if obj else None

    return resolve_label, resolve_objectname


def _walk_blocks(
    spec: EmailTemplateDef,
    resolve_image: Callable[[ImageBlockDef], dict[str, Any]],
    resolve_label: merge_fields.ResolveLabel | None = None,
    resolve_objectname: merge_fields.ResolveObjectName | None = None,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for s in spec.sections:
        rows: list[dict[str, Any]] = []
        for r in s.rows:
            cells: list[dict[str, Any]] = []
            for c in r.cells:
                blocks: list[dict[str, Any]] = []
                for b in c.blocks:
                    if isinstance(b, TextBlockDef):
                        blocks.append(
                            {
                                "kind": "text",
                                "html": _paragraphs_to_html(
                                    b.paragraphs,
                                    resolve_label=resolve_label,
                                    resolve_objectname=resolve_objectname,
                                ),
                            }
                        )
                    elif isinstance(b, ButtonBlockDef):
                        blocks.append(
                            {
                                "kind": "button",
                                "label": b.label,
                                "url": b.url,
                                "color": b.color,
                                "border_radius": b.border_radius,
                                "padding_left": b.padding_left,
                                "padding_right": b.padding_right,
                                "alignment": b.alignment,
                            }
                        )
                    elif isinstance(b, DividerBlockDef):
                        blocks.append(
                            {"kind": "divider", "color": b.color, "size": b.size}
                        )
                    elif isinstance(b, ImageBlockDef):
                        blocks.append(
                            {
                                "kind": "image",
                                **resolve_image(b),
                                "container_width": b.container_width,
                                "max_width": b.max_width,
                                "max_height": b.max_height,
                            }
                        )
                    else:  # pragma: no cover - the discriminated union rejects this
                        raise ValueError(f"unsupported block: {b!r}")
                cells.append({"blocks": blocks})
            rows.append(
                {
                    "layout": r.layout,
                    "cells": cells,
                    "width": r.width,
                    "container_width": r.container_width,
                    "padding": _padding_dict(r.padding),
                }
            )
        sections.append(
            {
                "rows": rows,
                "background_color": s.background_color,
                "max_width": s.max_width,
                "container_width": s.container_width,
                "padding": _padding_dict(s.padding),
            }
        )
    return sections


def resolve_spec_images(spec: EmailTemplateDef) -> list[dict[str, Any]]:
    """Upload every local file an ``EmailTemplateDef``'s Image blocks
    reference and return ``spec.sections`` as the plain nested dicts
    ``tools.planners.messages`` turns into a plan.

    A real write (see :func:`email_images.upload_email_image`) — call this
    from the CLI layer for a real apply, never from ``tools/planners/``.
    Under ``--dry-run`` the CLI calls :func:`offline_resolve_spec_images`
    instead, so a dry run uploads nothing — see that function.

    Also builds the live merge-field resolvers (:func:`_email_merge_field_resolvers`)
    and threads them through :func:`_walk_blocks`, so a ``Text`` block's
    ``paragraphs`` render with real ``data-merge-field-fallback-label``/
    ``data-merge-field-objectname`` values for any custom-object namespace
    referenced.
    """
    config = load_env_config()
    resolve_label, resolve_objectname = _email_merge_field_resolvers()
    with KizenClient(config) as client:

        def resolve_image(b: ImageBlockDef) -> dict[str, Any]:
            info = upload_email_image(client, config.base_url, b.file)
            return {**info, "alt": b.alt, "link": b.link, "width": b.width}

        return _walk_blocks(
            spec,
            resolve_image,
            resolve_label=resolve_label,
            resolve_objectname=resolve_objectname,
        )


def offline_resolve_spec_images(spec: EmailTemplateDef) -> list[dict[str, Any]]:
    """Offline counterpart for `messages templates craft-config`: no
    network call of any kind. Reads each local image's header bytes for
    ``naturalWidth``/``naturalHeight`` (that part needs no upload) but
    stands in obvious placeholder tokens for ``fileId``/``src`` — this
    output previews the compiled HTML, it is not meant to be pasted into a
    create/update spec.

    Merge fields in a ``Text`` block's ``paragraphs`` get the same
    no-network treatment: ``resolve_label=None, resolve_objectname=None``
    passed to :func:`_walk_blocks`. ``merge_fields.render()`` still answers
    every label from its own built-in fallback tables, but
    ``data-merge-field-objectname`` is omitted entirely for **every**
    namespace, including a real custom-object one — there is no live object
    lookup to answer it offline. This is a real, visible divergence between
    this preview output and what `create`/`update` would actually produce
    for a spec referencing a custom-object merge field, the same class of
    divergence this function already documents for Image blocks
    (placeholder ``fileId``/``src`` rather than real ones) — not silently
    produced as if it were full fidelity.
    """

    def resolve_image(b: ImageBlockDef) -> dict[str, Any]:
        path = Path(b.file)
        width, height, _ct = read_image_dimensions(path.read_bytes())
        return {
            "file_id": OFFLINE_FILE_PLACEHOLDER,
            "src": f"https://{OFFLINE_HOST_PLACEHOLDER}/api/files/{OFFLINE_FILE_PLACEHOLDER}/download",
            "name": path.name,
            "natural_width": width,
            "natural_height": height,
            "alt": b.alt,
            "link": b.link,
            "width": b.width,
        }

    return _walk_blocks(
        spec, resolve_image, resolve_label=None, resolve_objectname=None
    )

"""Spec models for `messages templates create/update --spec-file`.

Backs `tools/email_craft.py`'s emitter, which builds `craft_json` and the
compiled `content` HTML from one pass over this tree — see that module's
docstring for why the two can't be authored separately. Wire-format facts
(node shapes, the `section-<id>` coupling rule) live in
`docs/specs/email-templates.md`, not here; this module only encodes what a
spec author is allowed to write.

v1 ships four column presets and four leaf block kinds. Both closed sets are
enforced by construction: `ColumnPreset` is a `Literal`, so a typo'd preset
name is a spec-validation error, not a bad fractions array to catch later,
and `BlockDef` is a discriminated union on `kind`, so an unsupported kind
(`attachments`, anything else) fails with the literal list of valid kinds
rather than a silent skip. `Attachments` and the other five column presets
(`3`/`4`/`5`/`6 Columns`, `3 Columns (gutters)`) are confirmed live but out
of v1 scope — see the work item that shipped this module.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kizen_builder.tools.merge_fields import MERGE_FIELD_RE

# The 4 v1 layouts, confirmed live 2026-08-25 — exact `Row.props.columns` /
# `Cell.props.__width` fractions, not rounded or recomputed. See
# docs/specs/email-templates.md.
ColumnPreset = Literal[
    "1 Column",
    "2 Columns",
    "2 Columns (1/3 and 2/3)",
    "2 Columns (2/3 and 1/3)",
]

COLUMN_FRACTIONS: dict[str, tuple[float, ...]] = {
    "1 Column": (1,),
    "2 Columns": (0.5, 0.5),
    "2 Columns (1/3 and 2/3)": (0.3333333333333333, 0.6666666666666666),
    "2 Columns (2/3 and 1/3)": (0.6666666666666666, 0.3333333333333333),
}


class PaddingDef(BaseModel):
    """Four independent sides, matching the wire format 1:1
    (`containerPaddingTop`/`Right`/`Bottom`/`Left`) rather than a CSS-style
    shorthand — the reference template shows asymmetric padding (e.g. `40`
    left/right with `10` top/bottom on one row), so a shorthand would be a
    lossy abstraction over four independently-set keys."""

    model_config = ConfigDict(extra="forbid")

    top: str = "10"
    right: str = "10"
    bottom: str = "10"
    left: str = "10"


class ParagraphDef(BaseModel):
    """One paragraph of rich-text copy, for the single-run, single-style,
    single-link case: uniform size/bold/color/align and at most one link,
    applied to the whole paragraph. Rendered by
    `tools.email_craft._paragraphs_to_html` into the markup Kizen's own
    rich-text editor normalises **to** on save for that case (`<p
    data-line-height="default" style="line-height: 1.25;">` +
    `<span style="font-size: Npx;">` + `<strong>` + `<a rel="noopener
    noreferrer nofollow">`), confirmed live 2026-08-26 — see the work item
    that added this model for the full trace.

    Deliberately closed vocabulary, and it does not cover every shape the
    live editor can produce: re-measured 2026-08-26 against the same
    reference template, 4 of 31 `<p>` blocks (across 9 `Text` nodes) contain
    a bare `<a>` with no `<span>` anywhere in the paragraph, and 2 spans
    carry a `<strong>`-wrapped run alongside plain text in the same span
    (bold on part of a run, not the whole paragraph). Neither shape is
    expressible here — `link` always wraps a `<span>`, and `bold` is
    all-or-nothing per paragraph — and there is no `html` escape hatch to
    fall back on, so paragraphs shaped like either cannot be authored from
    this CLI. See
    `00-inbox/paragraph-model-cannot-express-partial-run-formatting.md`.

    `text` may contain `{{ namespace.field }}` merge-field tokens (rendered
    via `tools/merge_fields.py`'s `render()`, the shared owner of that
    markup) — see `_paragraphs_to_html`. `automation_variable.*` tokens are
    rejected here: library email templates aren't scoped to any one
    automation, so a variable name only one automation happens to declare
    isn't portable, and rendering it would silently produce the literal
    token text rather than a value everywhere else.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    size: int | None = Field(
        default=None,
        description="px. None renders the builder's own default span size, "
        "not 'no span' — every live canonical span carries an explicit size.",
    )
    bold: bool = False
    color: str | None = Field(
        default=None,
        description="Unvalidated string, matching ButtonBlockDef.color's "
        "existing pattern — both #hex and rgb(...) are confirmed live.",
    )
    link: str | None = None
    align: Literal["left", "center", "right"] | None = None

    @field_validator("text")
    @classmethod
    def _reject_automation_variable_tokens(cls, v: str) -> str:
        for m in MERGE_FIELD_RE.finditer(v):
            namespace = m.group(1).split(".", 1)[0]
            if namespace == "automation_variable":
                raise ValueError(
                    f"merge-field token {m.group(0)!r} uses the "
                    "'automation_variable' namespace, which only exists in "
                    "an automation-scoped message (see 'The "
                    "automation_variable scope boundary' in the work item) "
                    "— not valid on a library email template's paragraph text"
                )
        return v


class TextBlockDef(BaseModel):
    """Rich-text copy as a list of paragraphs — see `ParagraphDef`.
    `_paragraphs_to_html` renders this into the HTML string embedded
    verbatim in both `craft_json` (`custom.text`) and the compiled `content`
    — see the coupling rule in `docs/specs/email-templates.md`. No raw-HTML
    escape hatch on this surface (`extra="forbid"` on every block model), same
    as BCLI-022 established for the rest of this surface."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
    paragraphs: list[ParagraphDef] = Field(
        min_length=1,
        description="At least one paragraph is required — the html field "
        "this replaced was required too. A spacer is a paragraph with "
        'text: "", not an empty list.',
    )


class ImageBlockDef(BaseModel):
    """An image, uploaded from a local file at plan time.

    `file` is a path on disk, not a `file_id` — there is no CLI surface to
    look one up after the fact (`GET /api/files` is broken; see
    docs/specs/email-templates.md), so the spec captures the upload at the
    point it happens. `naturalWidth`/`naturalHeight` are read from the
    file's own header bytes, never guessed.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["image"] = "image"
    file: str
    alt: str = ""
    link: str = ""
    width: int | None = Field(
        default=None,
        description=(
            "Display width in px. Omitting this triggers Kizen's own "
            "auto-sizing mode: the image fills its parent Section's "
            "containerWidth (or Root.maxWidth if the Section doesn't set "
            "one) and is capped at its own natural width by a per-image "
            "CSS rule, instead of rendering at a fixed pixel width."
        ),
    )
    container_width: str | None = None
    max_width: str | None = None
    max_height: str | None = None


class ButtonBlockDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["button"] = "button"
    label: str
    url: str
    color: str | None = None
    border_radius: str = "8"
    padding_left: str = "20"
    padding_right: str = "20"
    alignment: Literal["left", "center", "right"] = "center"


class DividerBlockDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["divider"] = "divider"
    color: str | None = None
    size: str = "3"


BlockDef = Annotated[
    TextBlockDef | ImageBlockDef | ButtonBlockDef | DividerBlockDef,
    Field(discriminator="kind"),
]


class CellDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[BlockDef] = Field(default_factory=list)


class RowDef(BaseModel):
    """One row. `layout` picks a closed-enum column preset; the emitter (not
    this model — see `tools/planners/messages.py`) rejects a row whose cell
    count doesn't match the preset with a `PlanError`, at plan time rather
    than as a silent reshape.

    `width`/`container_width`/`padding` are independently spec-settable, not
    derived from `SectionDef`'s equivalents — the reference template shows
    `Row.containerWidth`/padding/`width` varying row-to-row with no clean
    formula from `Section.max_width`/padding (some rows fit
    `max_width - 2*padding`, others don't). Same "redundant-but-must-agree"
    trust model this surface already uses for `Row.props.columns` vs.
    `Cell.props.__width`.
    """

    model_config = ConfigDict(extra="forbid")

    layout: ColumnPreset = "1 Column"
    cells: list[CellDef]
    width: str = "100"
    container_width: str | None = None
    padding: PaddingDef | None = None


class SectionDef(BaseModel):
    """`max_width`/`container_width`/`padding` default to the emitter's
    pre-existing hardcoded values (`900`/absent/`10` uniform) so a spec that
    sets none of them is byte-identical to the pre-this-item emitter — see
    `tools/email_craft.py::_section_props`. The reference template's own
    common value for `max_width` is `600` (a real newsletter's content
    width); a spec targeting that layout sets it explicitly rather than
    inheriting it as the default, which would break the regression
    guarantee this item's acceptance criteria require.
    """

    model_config = ConfigDict(extra="forbid")

    rows: list[RowDef] = Field(default_factory=list)
    background_color: str = "#FFFFFF"
    max_width: str = "900"
    container_width: str | None = None
    padding: PaddingDef | None = None


class EmailTemplateDef(BaseModel):
    """Top-level spec for `messages templates create/update --spec-file`.

    Deliberately has no `craft_json`/`content` key of any kind — those two
    fields are always derived from `sections` by `tools/email_craft.py`, so
    a hand-authored pair can never be passed in and go out of sync. `subject`
    is optional (kept blank if omitted, matching what the builder allows).
    `sender_type` is not a field here — it is hard-coded to `"business"` by
    the planner; see `docs/specs/email-templates.md`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    subject: str = ""
    sections: list[SectionDef] = Field(default_factory=list)

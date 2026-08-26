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

from pydantic import BaseModel, ConfigDict, Field

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


class TextBlockDef(BaseModel):
    """Rich-text copy. `html` is embedded verbatim in both `craft_json`
    (`custom.text`) and the compiled `content` — see the coupling rule in
    `docs/specs/email-templates.md`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
    html: str


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
        description="Display width in px. Defaults to 150 (form_ui's default).",
    )


class ButtonBlockDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["button"] = "button"
    label: str
    url: str
    color: str | None = None


class DividerBlockDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["divider"] = "divider"
    color: str | None = None


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
    than as a silent reshape."""

    model_config = ConfigDict(extra="forbid")

    layout: ColumnPreset = "1 Column"
    cells: list[CellDef]


class SectionDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[RowDef] = Field(default_factory=list)
    background_color: str = "#FFFFFF"


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

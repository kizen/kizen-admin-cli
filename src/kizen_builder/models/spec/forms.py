"""Forms & surveys (structurally identical: one model, two base paths)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kizen_builder.models.spec._base import ApiName
from kizen_builder.models.spec.field_configs import (
    DecimalConfig,
    MoneyConfig,
    PhoneConfig,
    RatingConfig,
    StatusOption,
    check_type_blocks,
    reject_api_spellings,
)
from kizen_builder.models.spec.objects import _TYPES_REQUIRING_OPTIONS

FormFieldType = Literal[
    "checkbox",
    "checkboxes",
    "date",
    "datetime",
    "decimal",
    "dropdown",
    "dynamictags",
    "email",
    "files",
    "integer",
    "longtext",
    "money",
    "phonenumber",
    "radio",
    "rating",
    "relationship",
    "status",
    "team_selector",
    "text",
    "wysiwyg",
    "yesnomaybe",
]
"""Field types valid on a form/survey field — the same 21 custom-object types
minus `activity_custom_field`. A form field links to its related object's field
through `custom_object_field` instead."""


_FORM_TYPES_REQUIRING_OPTIONS = _TYPES_REQUIRING_OPTIONS | {"yesnomaybe"}
"""Same as `_TYPES_REQUIRING_OPTIONS`, plus `yesnomaybe` — verified live:
unlike custom-object/activity fields (where yesnomaybe needs no
explicit options), the forms/surveys API 400s with "options: This field is
required for yesnomaybe" if it's omitted."""


_LINKED_FIELD_BLOCKS = (
    "options",
    "status_options",
    "money_options",
    "rating",
    "decimal_options",
    "phone_options",
)


class FormFieldDef(BaseModel):
    """A single field on a form or survey.

    Structurally close to :class:`ActivityFieldDef`. ``custom_object_field``
    names a field on the form's ``related_object``; the planner resolves it
    and takes ``field_type`` from it when omitted. Kizen copies a linked
    field's options and type settings from the record's field and drops any
    sent, so a linked field takes none. Fields order within one flat list via
    ``order`` (no categories). ``api_name`` is optional — Kizen derives one
    from the display name when omitted.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human display name, shown in the Kizen UI.",
    )
    api_name: ApiName | None = Field(
        default=None,
        description="Stable identifier (wire `name`). Kizen derives one if omitted.",
    )
    field_type: FormFieldType | None = Field(
        default=None,
        description="Required unless `custom_object_field` is set, which supplies it.",
    )
    description: str | None = Field(default=None, max_length=500)
    required: bool = False
    read_only: bool = False
    hidden: bool = Field(
        default=False,
        description="Sent as `is_hidden`, the record-view hidden flag. "
        "The form still shows the field.",
    )
    order: int | None = Field(default=None, ge=0, le=32767)
    custom_object_field: str | None = Field(
        default=None,
        description="api_name or UUID of a field on the form's `related_object`. "
        "A submission writes this answer to that field of the record it creates.",
    )

    # Type-specific config (same blocks as FieldDef / ActivityFieldDef).
    options: list[str] | None = Field(default=None)
    status_options: list[StatusOption] | None = Field(default=None)
    money_options: MoneyConfig | None = Field(default=None)
    rating: RatingConfig | None = Field(default=None)
    decimal_options: DecimalConfig | None = Field(default=None)
    phone_options: PhoneConfig | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _reject_api_spellings(cls, data: Any) -> Any:
        return reject_api_spellings(data, cls.model_fields)

    @model_validator(mode="after")
    def _validate_type_specific_config(self) -> Self:
        label = f"field '{self.name}'"
        if self.custom_object_field is not None:
            given = [k for k in _LINKED_FIELD_BLOCKS if getattr(self, k) is not None]
            if given:
                raise ValueError(
                    f"{label} is linked to '{self.custom_object_field}', whose "
                    f"{', '.join(given)} Kizen copies: remove them"
                )
            return self
        if self.field_type is None:
            raise ValueError(
                f"{label} needs 'field_type', or 'custom_object_field' to take it "
                "from the record's field"
            )
        check_type_blocks(self, label, _FORM_TYPES_REQUIRING_OPTIONS)
        return self


class FormDef(BaseModel):
    """A form or survey (identical wire shape; only the API base path differs).

    Confirmed against the live ``FormObjectRequest``/``FormObject`` schemas at
    ``/api/docs/schema`` (the "public" schema at ``/api/docs/public/schema``
    doesn't cover forms/surveys at all). Unlike :class:`ActivityDef`, forms
    have no ``is_editable``/``webhook_url``/association-mode concept — instead
    every form/survey is *required* to declare which custom object its
    submissions attach records to (``related_object``) and a UI
    ``template_type``. ``extra="allow"``, so a top-level key the
    CLI doesn't read is ignored. ``kizen forms get`` / ``kizen surveys get``
    output is not a valid spec as printed, and ``fields`` entries take spec
    spellings, not the API's.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="Display name of the form/survey.")
    api_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=255,
        description="Stable identifier. Kizen may rewrite it on create.",
    )
    description: str | None = Field(default=None, max_length=500)
    related_object: str | None = Field(
        default=None,
        description="api_name (or UUID) of the custom object submissions attach "
        "records to. Required by the API on create — resolved to "
        "'related_object_id' at plan time.",
    )
    related_object_id: str | None = Field(
        default=None,
        description="Raw UUID escape hatch for 'related_object' — set directly "
        "to skip the api_name lookup.",
    )
    template_type: Literal["modern", "open", "splash"] = Field(
        default="modern",
        description="UI template. Required by the API; Kizen has no default "
        "so this CLI defaults to 'modern'.",
    )
    submission_action: Literal["go_to_page", "go_to_url"] | None = Field(default=None)
    redirect_url: str | None = Field(
        default=None,
        max_length=200,
        description="Used when submission_action is 'go_to_url'.",
    )
    pass_variables_on_redirect: bool | None = None
    challenge_token_required: bool | None = None
    subscribers: str | None = None
    business_merge_fields: list[str] | None = None
    form_ui: dict[str, Any] | None = None

    fields: list[FormFieldDef] | None = Field(
        default=None,
        description="Optional fields to create on the form/survey in the same plan.",
    )

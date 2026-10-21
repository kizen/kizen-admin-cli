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
    "choices",
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
    "selector",
    "status",
    "team_selector",
    "text",
    "timezone",
    "wysiwyg",
    "yesnomaybe",
]
"""Field types valid on a form/survey field — the same 24 custom-object types
minus `activity_custom_field` (forms are standalone data-capture surfaces, not
tied to a custom object like activities are)."""


_FORM_TYPES_REQUIRING_OPTIONS = _TYPES_REQUIRING_OPTIONS | {"yesnomaybe"}
"""Same as `_TYPES_REQUIRING_OPTIONS`, plus `yesnomaybe` — verified live:
unlike custom-object/activity fields (where yesnomaybe needs no
explicit options), the forms/surveys API 400s with "options: This field is
required for yesnomaybe" if it's omitted."""


class FormFieldDef(BaseModel):
    """A single field on a form or survey.

    Structurally identical to :class:`ActivityFieldDef` minus the
    `activity_custom_field` / `custom_object_field` linked-field concept,
    which doesn't apply here. Fields order within one flat list via `order`
    (no categories). ``api_name`` is optional — Kizen derives one from the
    display name when omitted.
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
    field_type: FormFieldType
    description: str | None = Field(default=None, max_length=500)
    required: bool = False
    read_only: bool = False
    hidden: bool = False
    order: int | None = Field(default=None, ge=0, le=32767)

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
        check_type_blocks(self, f"field '{self.name}'", _FORM_TYPES_REQUIRING_OPTIONS)
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

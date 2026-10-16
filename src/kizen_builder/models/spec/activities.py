"""Activity types (loggable definitions) and their fields."""

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

ActivityFieldType = Literal[
    "activity_custom_field",
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
"""Field types valid on an activity — the 24 custom-object types plus
`activity_custom_field` (a field mirrored from a related custom object)."""


AssociationMode = Literal[
    "all_objects_associated",
    "selected_objects_associated",
    "no_objects_associated",
]
"""How an activity type associates with custom objects: with every object,
only a hand-picked set (`selected_object_ids`), or none."""


class ActivityFieldDef(BaseModel):
    """A single field on an activity type.

    Structurally close to :class:`FieldDef`, but activity fields have no
    category (they order within one flat list via ``order``) and no reserved
    api_name restriction. ``api_name`` is optional — Kizen derives one from
    the display name when omitted.
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
    field_type: ActivityFieldType
    description: str | None = Field(default=None, max_length=500)
    required: bool = False
    read_only: bool = False
    hidden: bool = False
    order: int | None = Field(default=None, ge=0, le=32767)
    custom_object_field: str | None = Field(
        default=None,
        description="UUID of a custom-object field this activity field mirrors "
        "(for `activity_custom_field` type).",
    )

    # Type-specific config (same blocks as FieldDef).
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
        check_type_blocks(
            self, f"activity field '{self.name}'", _TYPES_REQUIRING_OPTIONS
        )
        return self


class ActivityDef(BaseModel):
    """An activity type (loggable definition).

    ``extra="allow"``, so a top-level key the CLI doesn't read is ignored.
    ``kizen activities get`` output is not a valid spec as printed, and
    ``fields`` entries take spec spellings, not the API's.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="Display name of the activity.")
    api_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=255,
        description="Kept as given; omit it and Kizen derives one from `name`.",
    )
    description: str | None = Field(default=None, max_length=500)
    is_editable: bool | None = Field(
        default=None,
        description="Whether logged instances can be edited after submission.",
    )
    association_mode: AssociationMode | None = Field(default=None)
    visibility_rules: list[dict[str, Any]] | None = Field(
        default=None,
        description="Conditional field-visibility rules (opaque rule dicts).",
    )
    submission_action: Literal["redirect", "trigger_webhook"] | None = Field(
        default=None
    )
    webhook_url: str | None = None
    redirect_url: str | None = None
    calendar_sync_enabled: bool | None = None
    selected_object_ids: list[str] | None = Field(
        default=None,
        description="UUIDs of objects for `selected_objects_associated` mode. "
        "Sent as `associated_objects`.",
    )
    associated_objects: list[dict[str, Any]] | None = Field(
        default=None,
        description="The wire/read shape, sent verbatim. Use it to set `is_required`.",
    )
    loggable_sharing_settings: dict[str, Any] | None = None

    fields: list[ActivityFieldDef] | None = Field(
        default=None,
        description="Optional fields to create on the type in the same plan.",
    )

    @model_validator(mode="before")
    @classmethod
    def _check_association_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            check_association_keys(data)
        return data


def check_association_keys(spec: dict[str, Any]) -> None:
    """Reject the association keys an activity create or update can't send."""
    if "custom_object_ids" in spec:
        raise ValueError(
            "'custom_object_ids' is not sent to Kizen; use 'selected_object_ids'"
        )
    if (
        spec.get("associated_objects") is not None
        and spec.get("selected_object_ids") is not None
    ):
        raise ValueError("set 'associated_objects' or 'selected_object_ids', not both")

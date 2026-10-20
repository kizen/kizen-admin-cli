"""Custom object definition: fields, categories, pipelines."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kizen_builder.models.spec._base import ApiName, FieldType
from kizen_builder.models.spec.field_configs import (
    DecimalConfig,
    MoneyConfig,
    PhoneConfig,
    RatingConfig,
    RelationConfig,
    StatusOption,
)
from kizen_builder.utils import derived_object_api_name

_TYPES_REQUIRING_OPTIONS = {"dropdown", "radio", "checkboxes", "choices"}


_RESERVED_FIELD_API_NAMES = frozenset(
    {
        "name",
        "owner",
        "created",
        "updated",
        "id",
        # Observed collisions with Kizen-reserved default field names
        # (not all documented in the OpenAPI spec — learned empirically
        # against a live Contact object):
        "first_name",
        "middle_name",
        "last_name",
        "full_name",
        "email",
        "home_phone",
        "business_phone",
        "mobile_phone",
        "birthday",
    }
)
"""api_names that Kizen auto-creates as undeletable default fields on every custom
object (or collide with reserved names on contact-like objects). Trying to POST
a new field with one of these names triggers `forbidden_field_write` from the
server.

Rule of thumb: prefix field api_names with the object's api_name
(e.g. `patient_first_name` instead of `first_name`) to avoid collisions
entirely."""


class FieldDef(BaseModel):
    """A single field on a custom object.

    Which of the `*_options` / `relation` / `rating` blocks are required is
    determined by `field_type` and enforced by `_validate_type_specific_config`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human display name, shown in the Kizen UI.",
    )
    api_name: ApiName = Field(
        description="Stable identifier. Must be unique within the parent custom object."
    )
    field_type: FieldType
    description: str | None = Field(default=None, max_length=500)
    required: bool = False
    read_only: bool = False
    hidden: bool = False

    # Type-specific config. Only the block matching field_type should be set.
    options: list[str] | None = Field(
        default=None,
        description="For dropdown / radio / checkboxes / choices fields. A list of option labels.",
    )
    status_options: list[StatusOption] | None = Field(
        default=None,
        description="For pipeline status fields.",
    )
    relation: RelationConfig | None = Field(
        default=None,
        description="For relationship fields.",
    )
    money_options: MoneyConfig | None = Field(
        default=None,
        description="For money fields.",
    )
    rating: RatingConfig | None = Field(
        default=None,
        description="For rating fields.",
    )
    decimal_options: DecimalConfig | None = Field(
        default=None,
        description="For decimal fields.",
    )
    phone_options: PhoneConfig | None = Field(
        default=None,
        description="For phonenumber fields.",
    )

    @model_validator(mode="after")
    def _validate_type_specific_config(self) -> Self:
        ft = self.field_type

        if self.api_name in _RESERVED_FIELD_API_NAMES:
            raise ValueError(
                f"field api_name '{self.api_name}' collides with a Kizen-reserved "
                f"name. Prefix it with the parent object's api_name "
                f"(e.g. 'patient_{self.api_name}' for a Patient object)."
            )

        if ft in _TYPES_REQUIRING_OPTIONS:
            if not self.options:
                raise ValueError(
                    f"field '{self.api_name}' of type '{ft}' requires a non-empty 'options' list"
                )
        elif self.options is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'options'"
            )

        if ft == "relationship":
            if self.relation is None:
                raise ValueError(
                    f"field '{self.api_name}' of type 'relationship' requires a 'relation' block"
                )
        elif self.relation is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have a 'relation' block"
            )

        if ft == "money" and self.money_options is None:
            # Default to USD so the user doesn't have to spell it out every time.
            self.money_options = MoneyConfig()
        if ft != "money" and self.money_options is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'money_options'"
            )

        if ft == "rating" and self.rating is None:
            self.rating = RatingConfig()
        if ft != "rating" and self.rating is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'rating'"
            )

        if ft != "decimal" and self.decimal_options is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'decimal_options'"
            )

        if ft != "phonenumber" and self.phone_options is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'phone_options'"
            )

        if ft == "status":
            if not self.status_options:
                raise ValueError(
                    f"field '{self.api_name}' of type 'status' requires 'status_options'"
                )
        elif self.status_options is not None:
            raise ValueError(
                f"field '{self.api_name}' of type '{ft}' cannot have 'status_options'"
            )

        return self


class FieldCategory(BaseModel):
    """A group of fields within a custom object.

    Categories must exist before fields; that's why fields are nested here in
    the spec — the dependency is explicit in the structure.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    api_name: ApiName
    fields: list[FieldDef] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def _unique_field_api_names(cls, v: list[FieldDef]) -> list[FieldDef]:
        seen: set[str] = set()
        for f in v:
            if f.api_name in seen:
                raise ValueError(
                    f"duplicate field api_name '{f.api_name}' within category"
                )
            seen.add(f.api_name)
        return v


ObjectType = Literal["standard", "pipeline"]


class PipelineStageSpec(BaseModel):
    """One initial stage for a `object_type: pipeline` object at creation time."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: Literal["open", "won", "lost", "disqualified"] = "open"
    order: int | None = None
    percentage_chance_to_close: int | None = Field(default=None, ge=0, le=100)


class PipelineDef(BaseModel):
    """Pipeline configuration for `object_type: pipeline` objects.

    The live `CustomObjectRequest` schema doesn't mark this required, but the
    API 400s ("pipeline: This field is required.") without it, and its
    `stages` must be a non-empty list.
    """

    model_config = ConfigDict(extra="forbid")

    stages: list[PipelineStageSpec] = Field(default_factory=list)


class ObjectDef(BaseModel):
    """A custom object (aka model / table) in Kizen."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Display name (e.g. 'Customers'). Usually plural.",
    )
    api_name: ApiName | None = Field(
        default=None,
        description=(
            "Optional. Kizen derives the api_name from `name` and refuses a "
            "supplied one, so if given it must equal the derived value."
        ),
    )
    entity_name: str | None = Field(
        default=None,
        description="Singular form (e.g. 'Customer'). Defaults to `name` if omitted.",
        max_length=200,
    )
    object_type: ObjectType = "standard"
    description: str | None = Field(default=None, max_length=500)
    default_on_activities: bool = True
    default_color: str = "#085BEE"
    default_icon: str = "bars-light"
    pipeline: PipelineDef | None = Field(
        default=None,
        description=(
            "Required stage config for object_type='pipeline'. If omitted, "
            "a single placeholder 'Open' stage is created — layer on the "
            "real stages afterward via `objects stages create`."
        ),
    )
    field_categories: list[FieldCategory] = Field(default_factory=list)

    @field_validator("field_categories")
    @classmethod
    def _unique_category_api_names(cls, v: list[FieldCategory]) -> list[FieldCategory]:
        seen: set[str] = set()
        for c in v:
            if c.api_name in seen:
                raise ValueError(
                    f"duplicate category api_name '{c.api_name}' within object"
                )
            seen.add(c.api_name)
        return v

    @model_validator(mode="after")
    def _unique_field_api_names_within_object(self) -> Self:
        seen: set[str] = set()
        for cat in self.field_categories:
            for f in cat.fields:
                if f.api_name in seen:
                    raise ValueError(
                        f"duplicate field api_name '{f.api_name}' within object '{self.effective_api_name}'"
                    )
                seen.add(f.api_name)
        return self

    @property
    def effective_api_name(self) -> str:
        return self.api_name or derived_object_api_name(self.name)

    @property
    def effective_entity_name(self) -> str:
        return self.entity_name or self.name

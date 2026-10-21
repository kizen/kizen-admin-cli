"""Type-specific config blocks and validators shared by field-like models
(FieldDef, ActivityFieldDef, FormFieldDef)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kizen_builder.models.spec._base import ApiName, RelationCardinality, RelationType

_API_SPELLINGS = {
    "display_name": "name",
    "is_hidden": "hidden",
    "is_read_only": "read_only",
    "is_required": "required",
    "phonenumber_options": "phone_options",
}
"""API field keys (as `get` prints them) -> the spec key that means the same."""


def reject_api_spellings(data: Any, spec_keys: Iterable[str]) -> Any:
    """Refuse a field spec that uses the API's spelling of a spec key.

    `objects get` / `activities get` / `forms get` print fields with the API's
    keys, so a spec copied from that output would otherwise lose them. This
    runs before pydantic's extra-key check, so it names the other unknown keys
    too.
    """
    if not isinstance(data, dict):
        return data
    found = sorted(k for k in data if k in _API_SPELLINGS)
    if not found:
        return data
    clauses = []
    for key in found:
        why = "the API's spelling"
        if key == "display_name":
            why += "; the API's 'name' is 'api_name' in a spec"
        clauses.append(f"write '{_API_SPELLINGS[key]}', not '{key}' ({why})")
    unknown = sorted(k for k in data if k not in spec_keys and k not in _API_SPELLINGS)
    if unknown:
        clauses.append("unknown keys: " + ", ".join(f"'{k}'" for k in unknown))
    label = data.get("name") or data.get("display_name")
    raise ValueError((f"field '{label}': " if label else "") + "; ".join(clauses))


_BLOCK_TYPES = {
    "money_options": "money",
    "rating": "rating",
    "decimal_options": "decimal",
    "phone_options": "phonenumber",
    "status_options": "status",
}


def check_type_blocks(
    field: Any, label: str, types_requiring_options: set[str]
) -> None:
    """Reject `options` or a type block that doesn't match `field_type`, and
    default the money and rating blocks."""
    ft = field.field_type
    if ft in types_requiring_options and not field.options:
        raise ValueError(f"{label} of type '{ft}' requires a non-empty 'options' list")
    if ft not in types_requiring_options and field.options is not None:
        raise ValueError(f"{label} of type '{ft}' cannot have 'options'")
    for block, block_type in _BLOCK_TYPES.items():
        if ft != block_type and getattr(field, block) is not None:
            raise ValueError(f"{label} of type '{ft}' cannot have '{block}'")
    if ft == "money" and field.money_options is None:
        field.money_options = MoneyConfig()
    if ft == "rating" and field.rating is None:
        field.rating = RatingConfig()


class RelationConfig(BaseModel):
    """Config for a `field_type: relationship` field."""

    model_config = ConfigDict(extra="forbid")

    target_object: ApiName = Field(
        description="api_name of the target custom object. Must exist in this spec."
    )
    relation_type: RelationType | RelationCardinality = Field(
        default="many_to_one",
        description=(
            "Cardinality/directionality of the relation. Prefer the clear "
            "cardinality names (one_to_one, many_to_one, one_to_many, "
            "many_to_many) — matches what Kizen's UI shows. The raw wire "
            "values (primary, additional, primary_for, additional_for) are "
            "also accepted for specs authored from live API output."
        ),
    )
    related_name: str | None = Field(
        default=None,
        description="Display label for the inverse relation on the target object.",
    )
    target_category: ApiName | None = Field(
        default=None,
        description=(
            "api_name of the field category on the target object where the inverse "
            "relation field should live. If omitted, Kizen picks a default."
        ),
    )


class MoneyConfig(BaseModel):
    """Config for a `field_type: money` field."""

    model_config = ConfigDict(extra="allow")

    currency: str = Field(
        default="USD",
        description="ISO 4217 currency code (e.g. USD, EUR, GBP).",
        min_length=3,
        max_length=3,
    )


class RatingConfig(BaseModel):
    """Config for a `field_type: rating` field."""

    model_config = ConfigDict(extra="allow")

    min_value: int = Field(default=1, ge=1, le=9)
    max_value: int = Field(default=5, ge=2, le=10)
    min_label: str = Field(default="Low", min_length=1)
    max_label: str = Field(default="High", min_length=1)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.max_value <= self.min_value:
            raise ValueError("rating.max_value must be greater than min_value")
        return self


class DecimalConfig(BaseModel):
    """Config for a `field_type: decimal` field."""

    model_config = ConfigDict(extra="allow")

    min_value: float = 0
    max_value: float = 999999


class PhoneConfig(BaseModel):
    """Config for a `field_type: phonenumber` field."""

    model_config = ConfigDict(extra="allow")

    enable_extension: bool = False


class StatusOption(BaseModel):
    """An option on a pipeline `status` field."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    code: str | None = None
    status: Literal["open", "won", "lost", "disqualified"] = "open"
    percentage_chance_to_close: int | None = Field(default=None, ge=0, le=100)

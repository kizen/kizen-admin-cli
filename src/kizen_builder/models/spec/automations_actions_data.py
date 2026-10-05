"""Automation step config models: record/variable/relationship mutation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kizen_builder.models.spec._base import ApiName
from kizen_builder.models.spec.automations_shared import (
    AutomationVariableConfig,
    VariableSourceConfig,
)


class ActionChangeFieldValueConfig(BaseModel):
    """Config for `step_type: change_field_value`."""

    model_config = ConfigDict(extra="allow")

    update_mode: Literal["update_fields", "clear_fields"] = "update_fields"
    field_ref: str | None = Field(
        default=None,
        description="'object.field' reference to the field to modify. Resolved to UUID at apply time.",
    )
    field_to_modify: str | None = Field(
        default=None, description="Direct UUID fallback for field_ref."
    )
    fields_to_clear: list[str] = Field(
        default_factory=list,
        description="Field UUIDs to clear (for clear_fields mode).",
    )
    change_type: str | None = None
    specific_field_value: Any = Field(
        default=None,
        description=(
            "Literal value to write to the field. Bare scalar (str/int/float/bool) "
            "for primitive fields; UUID string for dropdown/relation fields. "
            "Wire format wraps single-step changes in an `actions: [...]` array; "
            "the planner does that wrapping automatically."
        ),
    )
    field_resolution: str | None = None
    field_value_mappings: list[dict[str, Any]] = Field(default_factory=list)
    variable: str | None = None
    automation_target_relationship_field: str | None = None
    related_object: str | None = None
    related_object_field: str | None = None


class ActionCreateRelatedEntityConfig(BaseModel):
    """Config for `step_type: create_related_entity`."""

    model_config = ConfigDict(extra="allow")

    target_object: ApiName = Field(
        description=(
            "api_name of the target custom object. Resolved to UUID from state "
            "at apply time and sent as `target_custom_object`, which a block "
            "may also set directly (it wins when both are given)."
        )
    )
    field_values: list[dict[str, Any]] = Field(default_factory=list)


class ActionModifyRelatedEntitiesConfig(BaseModel):
    """Config for `step_type: modify_related_entities`."""

    model_config = ConfigDict(extra="allow")

    relationship_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the relationship field. Resolved to UUID at apply time.",
    )
    relationship_field_id: str | None = None


class ActionModifyRelatedEntitiesAutomationConfig(BaseModel):
    """Config for `step_type: modify_related_entities_automation`."""

    model_config = ConfigDict(extra="allow")

    relationship_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the relationship field. Resolved to UUID at apply time.",
    )
    relationship_field_id: str | None = None
    automation_api_name: str | None = Field(
        default=None,
        description="api_name of the automation to act on. Resolved to UUID from state at apply time.",
    )
    automation_id: str | None = None
    action: Literal["start", "cancel", "pause"] | None = None


class ActionSearchRecordsConfig(BaseModel):
    """Config for `step_type: search_records`.

    Searches an object's records and writes the result into an array-type
    automation variable — confirmed live (2026-07-22) against a
    6-step global automation exercising all four `filter_type` variants.
    Unlike most steps, `custom_object` here is independent of the
    automation's own `target_object` (it must be, since global automations
    have none) — `filter_config`/`filter_groups` resolve against it, not
    against the automation's target_object.
    """

    model_config = ConfigDict(extra="allow")

    custom_object: str | dict[str, Any] = Field(
        description="Object being searched — api_name, UUID, or an {id}/{name} dict."
    )
    filter_type: str = Field(
        description=(
            "How records are selected. Confirmed values: 'all_records', "
            "'in_group'/'not_in_group' (filter_groups-driven), 'custom_filter' "
            "(filter_config-driven)."
        )
    )
    filter_config: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Same shape as a condition step's filter_config — either a JSON "
            "filter spec ({'all'|'any': [...]}, field names resolved against "
            "`custom_object`) or a raw filter_config dict. Only meaningful "
            "when filter_type='custom_filter'."
        ),
    )
    filter_groups: list[Any] = Field(
        default_factory=list,
        description=(
            "Saved filter group(s) this step applies — name, UUID, or an "
            "{id}/{name} dict, resolved against `custom_object`. Used with "
            "filter_type 'in_group'/'not_in_group'."
        ),
    )
    destination_variable: str | dict[str, Any] = Field(
        description="Array-type automation variable to write results into — name or {name} dict."
    )
    destination_variable_resolution: str = Field(
        default="overwrite",
        description=(
            "How results combine with the variable's current contents. "
            "Confirmed values: 'overwrite', 'overwrite_except_null', "
            "'add_only', 'remove_only', 'update_if_blank'."
        ),
    )


class ActionInitializeVariableConfig(BaseModel):
    """Config for `step_type: initialize_variable`."""

    model_config = ConfigDict(extra="allow")

    variable: AutomationVariableConfig
    sources: list[VariableSourceConfig] = Field(default_factory=list)
    is_required: bool = False


class ActionUpdateVariableConfig(BaseModel):
    """Config for `step_type: update_variable`."""

    model_config = ConfigDict(extra="allow")

    variable: AutomationVariableConfig
    sources: list[VariableSourceConfig] = Field(default_factory=list)


class ActionMathOperatorConfig(BaseModel):
    """Config for `step_type: math_operator`."""

    model_config = ConfigDict(extra="allow")

    type: Literal["simple_builder"] = "simple_builder"

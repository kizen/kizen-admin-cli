"""Automation step config models: record/variable/relationship mutation."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from kizen_builder.models.spec._base import ApiName
from kizen_builder.models.spec.automations_shared import (
    AutomationVariableConfig,
    FilterConfig,
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
    # Choices: `GET /api/docs/schema`, 2026-11-17 (`ChangeTypeEnum`).
    change_type: (
        Literal[
            "specific_value",
            "related_object_field",
            "this_record_field",
            "variable",
            "entity_variable_field",
            "blank",
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "Where the new value comes from. Left out, specific_value: "
            "`specific_field_value`."
        ),
    )
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


RecordFieldResolution = Literal["overwrite", "update_if_blank", "add_only"]

_UUID_OR_READ = "a UUID, or the {id, ...} a read gives"


class ActionCreateRelatedEntityConfig(BaseModel):
    """Config for `step_type: create_related_entity`. Field values can't be
    set at creation: Kizen accepted a `field_values` key and stored nothing
    (confirmed live 2026-10-05), so set them in a following
    `modify_related_entities` step through the same `context_entity_field`."""

    model_config = ConfigDict(extra="allow")

    # Kizen 400s a Contact here ("contact creation not supported", confirmed
    # live 2026-10-30), and the planner refuses `client_client`.
    target_custom_object: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "Object the new record is created on — api_name, UUID, or an "
            "{id}/{name} dict. Resolved to UUID at apply time. Not "
            "client_client: a Contact is created in a code step."
        ),
    )
    target_object: ApiName | None = Field(
        default=None,
        description=(
            "Alias for `target_custom_object`, which wins when both are given."
        ),
    )
    new_entity_name: str | None = Field(
        default=None,
        description="Required by Kizen: the new record's name, with merge fields.",
    )
    new_entity_name_html: str | None = Field(
        default=None,
        description="Required by Kizen: the same name as HTML, e.g. <p>...</p>.",
    )
    new_entity_owner_type: (
        Literal["assign_from_context_record", "newly_assigned_owner"] | None
    ) = Field(
        default=None,
        description=(
            "Required by Kizen: assign_from_context_record gives it the "
            "triggering record's owner; newly_assigned_owner picks one by "
            "new_entity_owner_sub_type."
        ),
    )
    new_entity_owner_sub_type: (
        Literal[
            "round_robin_any_role",
            "round_robin_specific_role",
            "round_robin_choose_team_members",
            "specific_team_member",
            "assign_from_variable",
        ]
        | None
    ) = Field(default=None, description="How newly_assigned_owner picks the owner.")
    new_entity_owner_role: str | dict[str, Any] | None = Field(
        default=None,
        description=f"The role for round_robin_specific_role: {_UUID_OR_READ}.",
    )
    new_entity_owner_employees: list[str | dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Team members for specific_team_member (one) or "
            f"round_robin_choose_team_members: each {_UUID_OR_READ}."
        ),
    )
    new_entity_owner_variable: str | dict[str, Any] | None = Field(
        default=None,
        description="The variable holding the owner, for assign_from_variable.",
    )
    new_entity_stage: str | dict[str, Any] | None = Field(
        default=None,
        description=f"The starting stage on a pipeline object: {_UUID_OR_READ}.",
    )
    # After the step runs, the field holds the new record's id (confirmed
    # live 2026-08-13).
    context_entity_field: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "A relationship field on the automation's object, set to the new "
            "record: 'object.field' or a bare field name, resolved at plan time."
        ),
    )
    context_entity_field_resolution: RecordFieldResolution | None = Field(
        default=None,
        description=(
            "How context_entity_field takes the new record. Left out: add_only "
            "on a to-many field, where overwrite unlinks the records already "
            "related, else overwrite. An update keeps the live value."
        ),
    )
    target_variable: str | dict[str, Any] | None = Field(
        default=None, description="A variable that receives the new record."
    )
    variable_field_resolution: RecordFieldResolution | None = Field(
        default=None,
        description="How target_variable takes the new record. Builder default: overwrite.",
    )
    automations_to_start: list[str | dict[str, Any]] | None = Field(
        default=None,
        description=f"Automations to start on the new record: each {_UUID_OR_READ}.",
    )
    existing_record_found_action: (
        Literal["create_append_suffix", "error_do_not_create"] | None
    ) = Field(
        default=None,
        description=(
            "When a record with that name exists. Builder default: error_do_not_create."
        ),
    )
    archived_record_found_action: (
        Literal["unarchive", "overwrite", "create_append_suffix", "error_do_not_create"]
        | None
    ) = Field(
        default=None,
        description=(
            "When an archived record with that name exists. Builder default: overwrite."
        ),
    )

    @model_validator(mode="after")
    def _require_target(self) -> Self:
        if not (self.target_custom_object or self.target_object):
            raise ValueError(
                "create_related_entity requires 'target_custom_object' "
                "(or its alias 'target_object')"
            )
        return self


class FieldToModifyConfig(BaseModel):
    """One `fields_to_modify` item of `modify_related_entities`: a field
    change with `value_type` in place of `change_type`."""

    model_config = ConfigDict(extra="allow")

    # Choices: `GET /api/docs/schema`, 2026-11-17 (`ValueTypeEnum`).
    value_type: (
        Literal[
            "specific_value",
            "context_entity_value",
            "variable",
            "entity_variable_field",
            "blank",
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "Where the new value comes from: context_entity_value reads "
            "`context_entity_field`, a field on the automation's own record."
        ),
    )


class ActionModifyRelatedEntitiesConfig(BaseModel):
    """Config for `step_type: modify_related_entities`."""

    model_config = ConfigDict(extra="allow")

    relationship_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the relationship field. Resolved to UUID at apply time.",
    )
    relationship_field_id: str | None = None
    record_source: Literal["related_object_field", "automation_variable"] | None = (
        Field(
            default=None,
            description=(
                "Where the records to modify come from. Omitted, the server uses "
                "'related_object_field', which needs a hop in "
                "`automation_target_relationship_fields`. 'automation_variable' "
                "reads them from `record_source_variables` instead, with "
                "`automation_target_relationship_fields: []`."
            ),
        )
    )
    record_source_variables: list[str] | None = Field(
        default=None,
        description=(
            "Names of the non-array entity variables holding the records, for "
            "`record_source: automation_variable`. An array variable 400s."
        ),
    )
    fields_to_modify: list[FieldToModifyConfig] | None = None

    # Each item goes to the planner with the keys it was given, nulls
    # included: a read's `change_type: null` decides which key it sends.
    @field_serializer("fields_to_modify")
    def _items_as_given(
        self, items: list[FieldToModifyConfig] | None
    ) -> list[dict[str, Any]] | None:
        if items is None:
            return None
        return [item.model_dump(mode="json", exclude_unset=True) for item in items]


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
    filter_config: FilterConfig | None = Field(
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


ArrayAggregationMode = Literal["add_first_non_blank", "add_all"]

_ARRAY_AGGREGATION_MODE_DESCRIPTION = (
    "Array variables: 'add_all' adds every source, 'add_first_non_blank' only "
    "the first non-blank one."
)
_DEDUPLICATE_ARRAY_DESCRIPTION = "Array variables: keep unique values only."


class ActionInitializeVariableConfig(BaseModel):
    """Config for `step_type: initialize_variable`."""

    model_config = ConfigDict(extra="allow")

    variable: AutomationVariableConfig
    sources: list[VariableSourceConfig] = Field(default_factory=list)
    is_required: bool = False
    array_aggregation_mode: ArrayAggregationMode | None = Field(
        default=None, description=_ARRAY_AGGREGATION_MODE_DESCRIPTION
    )
    deduplicate_array: bool | None = Field(
        default=None, description=_DEDUPLICATE_ARRAY_DESCRIPTION
    )


class ActionUpdateVariableConfig(BaseModel):
    """Config for `step_type: update_variable`."""

    model_config = ConfigDict(extra="allow")

    variable: AutomationVariableConfig
    sources: list[VariableSourceConfig] = Field(default_factory=list)
    array_aggregation_mode: ArrayAggregationMode | None = Field(
        default=None, description=_ARRAY_AGGREGATION_MODE_DESCRIPTION
    )
    deduplicate_array: bool | None = Field(
        default=None, description=_DEDUPLICATE_ARRAY_DESCRIPTION
    )
    value_resolution: (
        Literal["add_only", "overwrite", "overwrite_except_null"] | None
    ) = Field(
        default=None,
        description=(
            "Array variables only; the server 400s it on a scalar variable. "
            "How the sources combine with the array's current value: "
            "'add_only' appends (duplicates kept when deduplicate_array is false), "
            "'overwrite' replaces it, even with an empty array, "
            "'overwrite_except_null' replaces it unless the sources are empty. "
            "Null (the default) behaved like 'overwrite'."
        ),
    )


_MATH_VARIABLE = 'A number variable: its name, or {"name": ...} / {"id": ...}.'

# Kizen 400s inverse under addition and negative under multiplication
# (confirmed live 2026-11-16).
_OPERATIONS = {
    "addition": ("regular", "negative"),
    "multiplication": ("regular", "inverse"),
}


class MathArgument(BaseModel):
    """One term of a `math_operator` sum or product."""

    model_config = ConfigDict(extra="allow")

    argument_type: Literal["field", "constant", "variable"] = Field(
        description="Which of field_id, constant or variable holds the term."
    )
    operation_type: Literal["regular", "inverse", "negative"] = Field(
        description=(
            "negative subtracts the term (addition only), inverse divides by "
            "it (multiplication only), regular uses it as it is."
        )
    )
    constant: float | None = Field(default=None, description="A number.")
    field_id: str | None = Field(default=None, description="A number field's UUID.")
    field: str | dict[str, Any] | None = Field(
        default=None, description="Read spelling of field_id."
    )
    variable: str | dict[str, Any] | None = Field(
        default=None, description=_MATH_VARIABLE
    )


class ActionMathOperatorConfig(BaseModel):
    """Config for `step_type: math_operator`: adds or multiplies its
    arguments and stores the result in `field_id` or `variable`."""

    model_config = ConfigDict(extra="allow")

    type: Literal["simple_builder"] = "simple_builder"
    subtype: Literal["addition", "multiplication"] | None = Field(
        default=None,
        description="Add the arguments, or multiply them. Required by Kizen.",
    )
    field_id: str | None = Field(
        default=None,
        description="Where the result goes, on the record: a number field's UUID.",
    )
    field: str | dict[str, Any] | None = Field(
        default=None, description="Read spelling of field_id."
    )
    variable: str | dict[str, Any] | None = Field(
        default=None, description=f"Where the result goes, in the run. {_MATH_VARIABLE}"
    )
    simple_builder_arguments: list[MathArgument] | None = Field(
        default=None, description="The terms, in order."
    )

    @field_validator("simple_builder_arguments")
    @classmethod
    def _operation_fits_subtype(
        cls, args: list[MathArgument] | None, info: ValidationInfo
    ) -> list[MathArgument] | None:
        subtype = info.data.get("subtype")
        allowed = _OPERATIONS.get(subtype or "", ())
        for i, arg in enumerate(args or ()):
            if allowed and arg.operation_type not in allowed:
                raise ValueError(
                    f"{subtype} takes operation_type {' or '.join(allowed)}, "
                    f"not {arg.operation_type} (argument {i})"
                )
        return args

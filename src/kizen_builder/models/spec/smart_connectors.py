"""Smart-connector flow: execution variables + load steps."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExecutionVariableDataType = Literal[
    "boolean",
    "date",
    "datetime",
    "email",
    "number",
    "phone_number",
    "string",
    "uuid",
]
"""Types an execution variable can declare.

Note these are the *connector's* type names, not Kizen field types — a Kizen
`integer` or `decimal` field is fed by a `number` variable. The authoritative
list, with each type's legal `input_format` / `output_format` values, is
`kizen smart-connectors metadata`.
"""


class ExecutionVariableDef(BaseModel):
    """One value the connector reads out of a column of its SQL output.

    `data_source` names an output column of the SQL — what it selects, which
    need not exist in the reference file at all. The catch is that the column
    list Kizen validates against (the connector's `headers`) is only refreshed
    by publishing the SQL, so new output columns need a `push --publish` before
    they can be mapped.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1, description="Referred to by matching / field-mapping rules."
    )
    data_source: str | None = Field(
        default=None,
        description="Output column this reads. Defaults to `name`.",
    )
    data_type: ExecutionVariableDataType = "string"
    scope: str | None = Field(
        default=None,
        description="Output table the column lives in. Defaults to the "
        "connector's only scope when it has exactly one.",
    )
    is_array: bool = Field(
        default=False,
        description="Split the value into several — needed for multi-select "
        "(checkboxes) fields, alongside `array_delimiter`.",
    )
    array_delimiter: str | None = None
    required: bool | None = None
    input_format: str | None = Field(
        default=None,
        description="How to parse the incoming text, e.g. `yes_no` for a "
        "boolean or a strftime pattern for a date.",
    )
    output_format: str | None = None
    value: str | None = Field(
        default=None, description="Literal value, for a variable with no data_source."
    )
    display_order: int | None = Field(
        default=None, description="Defaults to the order listed in the spec."
    )

    @model_validator(mode="after")
    def _array_needs_delimiter(self) -> Self:
        if self.is_array and not self.array_delimiter:
            raise ValueError(
                f"execution variable '{self.name}' sets is_array but no "
                f"array_delimiter — the values can't be split without one"
            )
        return self


NoMatchAction = Literal["create_new", "next_rule_ignore_previous", "do_not_upload"]


SingleMatchAction = Literal[
    "update_current", "next_rule", "next_rule_ignore_previous", "do_not_upload"
]


MultipleMatchAction = Literal["next_rule", "next_rule_ignore_previous", "do_not_upload"]


MatchArchiveAction = Literal[
    "unarchive_and_update",
    "unarchive_only",
    "create_new",
    "next_rule",
    "next_rule_ignore_previous",
    "do_not_upload",
]


ConflictResolution = Literal[
    "overwrite", "only_update_blank", "only_add_options", "overwrite_except_null"
]


class MatchingRuleDef(BaseModel):
    """How a load step decides whether an incoming row is an existing record.

    Rules are tried in order. The defaults are the common upsert: match on the
    field, update the one match, create when there's no match, refuse to guess
    when several match.
    """

    model_config = ConfigDict(extra="forbid")

    field: str | None = Field(
        default=None,
        description="Field api_name (or UUID) on the load's object to match on. "
        "Omit only with is_match_by_kizen_id.",
    )
    variable: str = Field(
        description="Execution-variable name (or UUID) holding the value to "
        "match — including a variable another load step exposes."
    )
    is_match_by_kizen_id: bool = Field(
        default=False,
        description="Match on the literal Kizen record id rather than a field.",
    )
    no_match_action: NoMatchAction = "create_new"
    single_match_action: SingleMatchAction = "update_current"
    multiple_match_action: MultipleMatchAction = "do_not_upload"
    match_archive_action: MatchArchiveAction = "create_new"
    order: int | None = Field(
        default=None, description="Defaults to the order listed in the spec."
    )

    @model_validator(mode="after")
    def _needs_a_field_or_kizen_id(self) -> Self:
        if not self.field and not self.is_match_by_kizen_id:
            raise ValueError(
                "a matching rule needs either 'field' or is_match_by_kizen_id"
            )
        return self


class FieldMappingRuleDef(BaseModel):
    """One execution variable written into one field of the load's object.

    `conflict_resolution` is left unset by default on purpose: Kizen 400s with
    "not valid for this field type" on plain text/email fields, and the server's
    own default (`only_update_blank`) is what you'd pick anyway. Set it only for
    the field types that accept it.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Field api_name (or UUID) on the load's object.")
    variable: str | None = Field(
        default=None,
        description="Execution-variable name (or UUID). Use for the common 1:1 case.",
    )
    variables: list[str] | None = Field(
        default=None,
        description="Several variables concatenated into one field. The wire "
        "format is always this plural list — `variable` is sugar for a list of one.",
    )
    conflict_resolution: ConflictResolution | None = None
    can_create_field_options: bool = Field(
        default=False,
        description="Let an unrecognized value add a new option to a "
        "dropdown/checkboxes field instead of failing the row.",
    )
    display_order: int | None = Field(
        default=None, description="Defaults to the order listed in the spec."
    )

    @model_validator(mode="after")
    def _exactly_one_variable_form(self) -> Self:
        if bool(self.variable) == bool(self.variables):
            raise ValueError(
                f"field mapping for '{self.field}' needs exactly one of "
                f"'variable' (one) or 'variables' (several)"
            )
        return self

    @property
    def variable_refs(self) -> list[str]:
        """The mapping's variables as the wire's plural list, either way it was written."""
        return list(self.variables) if self.variables else [self.variable or ""]


class LoadStepDef(BaseModel):
    """One object the connector writes to.

    Several load steps run in `order`, each writing to its own object. A step
    can expose the Kizen id of the record it just matched or created
    (`exposes_variable`), which a later step references to fill a relationship
    field — that's how one connector builds a linked graph of records.
    """

    model_config = ConfigDict(extra="forbid")

    custom_object: str = Field(description="Object api_name (or UUID) to write to.")
    scope: str | None = Field(
        default=None,
        description="Output table feeding this step. Defaults to the "
        "connector's only scope when it has exactly one.",
    )
    type: Literal["csv_load"] = "csv_load"
    order: int | None = Field(
        default=None, description="Defaults to the order listed in the spec."
    )
    matching_rules: list[MatchingRuleDef] = Field(
        min_length=1,
        description="Dedup keys, tried in order. At least one is required.",
    )
    field_mapping_rules: list[FieldMappingRuleDef] = Field(
        min_length=1,
        description="Column-to-field writes. Kizen requires one for the "
        "object's own `name` field on every load step.",
    )
    exposes_variable: str | None = Field(
        default=None,
        description="Name for the uuid variable carrying this step's "
        "matched/created record id, for a later step to reference.",
    )
    automation_trigger_config: str | None = Field(
        default=None,
        description="Whether automations fire for the records this step writes "
        "(e.g. `fire_all`). Left to the server's default when unset.",
    )

    @model_validator(mode="after")
    def _last_rule_has_nowhere_to_fall_through(self) -> Self:
        last = self.matching_rules[-1]
        if last.multiple_match_action == "next_rule":
            raise ValueError(
                f"the last matching rule on '{self.custom_object}' sets "
                f"multiple_match_action: next_rule, but there is no next rule "
                f"— Kizen rejects this"
            )
        return self


class SmartConnectorFlowDef(BaseModel):
    """Execution variables plus load steps: what a connector does with its output.

    Everything here refers to things by name — object api_names, field
    api_names, variable names — and is resolved to UUIDs against live state at
    plan time.
    """

    model_config = ConfigDict(extra="forbid")

    connector: str | None = Field(
        default=None,
        description="Connector api_name or UUID. The CLI argument wins when both are given.",
    )
    execution_variables: list[ExecutionVariableDef] = Field(
        default_factory=list,
        description="Replaces the connector's data-source variables wholesale. "
        "Start from `smart-connectors suggest-variables`.",
    )
    loads: list[LoadStepDef] = Field(
        min_length=1, description="One entry per object written to, in execution order."
    )

    @field_validator("execution_variables")
    @classmethod
    def _unique_variable_names(
        cls, v: list[ExecutionVariableDef]
    ) -> list[ExecutionVariableDef]:
        seen: set[str] = set()
        for var in v:
            if var.name in seen:
                raise ValueError(f"duplicate execution variable name '{var.name}'")
            seen.add(var.name)
        return v

    @model_validator(mode="after")
    def _unique_exposed_names(self) -> Self:
        declared = {v.name for v in self.execution_variables}
        exposed_by: dict[str, str] = {}
        for index, load in enumerate(self.loads):
            name = load.exposes_variable
            if not name:
                continue
            step = (
                f"load step '{load.custom_object}' "
                f"(order {index if load.order is None else load.order})"
            )
            if name in declared:
                raise ValueError(
                    f"{step} exposes '{name}', which is also the name of an "
                    f"execution variable — rename one of them"
                )
            if name in exposed_by:
                raise ValueError(
                    f"{exposed_by[name]} and {step} both expose '{name}' — "
                    f"rename one of them"
                )
            exposed_by[name] = step
        return self

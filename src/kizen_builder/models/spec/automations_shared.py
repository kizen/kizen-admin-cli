"""Sub-models shared across automation trigger/step config blocks."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self, Union, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

# An on_or_around_date trigger or schedule counts days with these offsets.
DAYS_OFFSETS = ("days_before", "days_after")
DAYS_OFFSET_MESSAGE = (
    "date_offset_days must be at least 1 with days_before or days_after; "
    "use on_day for the day itself"
)


class TeamMemberConfig(BaseModel):
    """Select a team member by type (used in assign/notify/schedule_activity steps).

    Permissive shape: accepts both the spec form (``type`` + ``*_id``
    fields) and the live ``assigned_to`` shape from automation responses
    (``assignment_type`` + nested ``role``/``employee``/``field`` objects).
    The planner normalizes either to the wire form.
    """

    model_config = ConfigDict(extra="allow")

    # Spec-side type discriminator
    type: str | None = None
    # Live-side equivalent (assigned_to.assignment_type)
    assignment_type: str | None = None
    role_id: str | None = None
    employee_id: str | None = None
    employee_ids: list[str] = Field(default_factory=list)
    field_ref: str | None = Field(
        default=None,
        description="'object.field' reference for team_selector_field type. Resolved to UUID at apply time.",
    )


AutomationVariableDataType = Literal[
    "string",
    "number",
    "boolean",
    "date",
    "datetime",
    "phone_number",
    "email",
    "uuid",
    "employee",
    "entity",
]
"""Types an automation variable can declare: the server's
`ExecutionVariableDataTypeEnum`. Not the connector set in `smart_connectors.py`,
which has no `employee` or `entity`. The drift snapshot pins this list
(`AutomationVariableRequest`).
"""


class AutomationVariableConfig(BaseModel):
    """Define an automation variable (name + data type)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    data_type: AutomationVariableDataType
    is_array: bool = Field(
        default=False,
        description=(
            "Array variable. Only string, uuid and entity variables can be arrays, "
            "and an array needs array_aggregation_mode on the step."
        ),
    )
    data_subtype: str | dict[str, Any] | None = None
    input_format: str | None = None
    output_format: str | None = None
    # Carried by live reads (`steps get`); the builder strips or ignores them.
    id: Any = None
    created: Any = None
    deleted: Any = None
    is_array_item_variable: Any = None
    source_array_variable: Any = None


# The write schema has `source_subtype` as a free string, but the server
# checks it per `source_type`. These are the lists its 400s give (confirmed
# live 2026-11-17). A field_value source's 400 lists nothing, and a
# trigger_time source takes any value, so neither is checked here.
SOURCE_SUBTYPES: dict[str, tuple[str, ...]] = {
    "static": get_args(AutomationVariableDataType),
    "logged_activity_trigger": (
        "logged_by",
        "logged_at",
        "due_datetime",
        "activity_field_value",
        "record_association",
        "logged_activity_id",
        "scheduled_activity_id",
        "activity_object_id",
    ),
    "activity_past_due_trigger": (
        "scheduled_at",
        "assignee",
        "assignee_role",
        "due_datetime",
        "record_association",
        "scheduled_activity_id",
        "activity_object_id",
    ),
}


class VariableSourceConfig(BaseModel):
    """Source for a variable value (field, literal, another variable, etc.)."""

    model_config = ConfigDict(extra="allow")

    source_type: str = Field(
        description=(
            "One of: static, field_value, variable, trigger_time, webhook_trigger, "
            "logged_activity_trigger, activity_past_due_trigger."
        )
    )
    field_ref: str | None = Field(
        default=None,
        description=(
            "'object.field' reference, resolved to the source's `field` UUID at "
            "apply time."
        ),
    )
    source_subtype: str | None = Field(
        default=None,
        description=(
            "Required on a static source, where it's the value's type. The "
            "values each source_type takes are in the step-type card's notes."
        ),
    )
    # Choices: `GET /api/docs/schema`, 2026-11-17 (`ArraySeparatorEnum`).
    array_separator: Literal["comma", "newline"] | None = Field(
        default=None,
        description=(
            "A static source with a list value: how its items are split. "
            "Kizen stores comma when it's left out."
        ),
    )
    field_id: str | None = Field(
        default=None, description="The field's UUID, sent as `field`."
    )
    value: Any = None
    variable: str | None = Field(
        default=None,
        description=(
            "The variable this source reads, by name: a `variable` source's "
            "value, or a `record_variable_field` source's record."
        ),
    )

    @field_validator("source_subtype")
    @classmethod
    def _known_subtype(cls, value: str | None, info: ValidationInfo) -> str | None:
        allowed = SOURCE_SUBTYPES.get(info.data.get("source_type", ""))
        if value is None or allowed is None or value in allowed:
            return value
        raise PydanticCustomError(
            "literal_error",
            "Input should be {expected}",
            {"expected": ", ".join(repr(v) for v in allowed)},
        )

    # The server 400s a static source without one: "source_subtype should
    # be one of [...]" (seen live 2026-11-17).
    @model_validator(mode="after")
    def _static_has_subtype(self) -> Self:
        if self.source_type == "static" and not self.source_subtype:
            raise ValueError(
                "a static source needs source_subtype: the type of its value, "
                "such as string, number, boolean or datetime"
            )
        return self


class LlmDestinationConfig(BaseModel):
    """Output destination for an LLM/AI step result (`call_llm`,
    `file_content_extraction`, `audio_transcription`).

    Two shapes, not three mutually-exclusive options:
    - **Same-object write**: set exactly one of `field_ref`/`field` (a field
      on the automation's own `target_object`) or `variable`.
    - **Related-object write**: set `related_object_field` (a field on a
      record directly related to `target_object` — dotted `"object.field"`
      ref, like any other field_ref, or a raw field UUID) *together with* a
      relationship hop naming the field on `target_object` that points at
      that related object. The hop can be given explicitly via
      `relationship_field_ref`/`relationship_field_id` (same convenience
      alias pattern as `modify_related_entities`), or left unset to
      auto-detect it when exactly one such relationship field exists.
      `field`/`field_ref` are also accepted as the hop for this shape (the
      confirmed wire dialect repurposes `field` as the hop whenever
      `related_object_field` is present) but `relationship_field_ref` reads
      clearer — see `kizen automations step-types show call_llm` for the
      destinations a step can write.
    """

    model_config = ConfigDict(extra="allow")

    field_ref: str | None = Field(
        default=None,
        description=(
            "'object.field' reference, resolved to field UUID at apply time. "
            "Same-object destination field, or — when related_object_field "
            "is also set — the relationship hop."
        ),
    )
    field: str | dict[str, Any] | None = Field(
        default=None, description="Direct field UUID fallback for field_ref."
    )
    related_object_field: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "Destination field on a record related to target_object. Accepts "
            "a dotted 'object.field' ref (resolved like field_ref) or a raw "
            "field UUID. Requires a relationship hop — see "
            "relationship_field_ref."
        ),
    )
    relationship_field_ref: str | None = Field(
        default=None,
        description=(
            "'object.field' ref (or bare field UUID) naming the relationship "
            "field on target_object that points at related_object_field's "
            "object — the hop for a related-object write. Alias: "
            "relationship_field_id. Auto-detected when omitted and exactly "
            "one relationship field between target_object and the "
            "destination's object exists."
        ),
    )
    relationship_field_id: str | None = Field(
        default=None,
        description="Alias for relationship_field_ref.",
    )
    variable: str | dict[str, Any] | None = Field(
        default=None,
        description="Automation variable name to write the result into (bare string, or {name: ...}).",
    )
    options: list[str] = Field(
        default_factory=list,
        description=(
            "Option UUIDs for a choice-type destination field; the CLI fills "
            "them from the field's options when omitted."
        ),
    )
    is_required: bool = False
    confidence_threshold: float = 0.7
    conflict_resolution: str | None = None


# ---------------------------------------------------------------------------
# code_step / plugin_code_step inputs and outputs
# ---------------------------------------------------------------------------

_HOP = (
    "The relationship field on the automation's object: 'object.field', "
    "resolved at apply time. It must be single-value (many_to_one or "
    "one_to_one): Kizen 400s a to-many hop."
)
_RELATED = (
    "The field on the related object: a UUID or 'object.field'. "
    "related_field_ref is the same."
)


class _CodeBinding(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(
        min_length=2,
        max_length=255,
        description="The key the script reads it under (inputs.<name>, outputs.<name>).",
    )
    data_type: AutomationVariableDataType | None = Field(
        default=None,
        description="Declares the type, where the bound field or variable doesn't.",
    )
    is_list: bool | None = Field(default=None, description="The value is a list.")


class _FieldSource(_CodeBinding):
    field_ref: str | None = Field(
        default=None,
        description=(
            "'object.field', resolved at apply time; for related_field, the "
            "relationship to hop, which must be single-value (Kizen 400s a "
            "to-many hop)."
        ),
    )
    field_id: str | None = Field(default=None, description="Direct UUID fallback.")
    field: str | dict[str, Any] | None = Field(
        default=None, description="Read spelling: a field UUID or {id, ...}."
    )


class _VariableSource(_CodeBinding):
    variable_name: str | None = Field(default=None, description="The variable's name.")
    variable: str | dict[str, Any] | None = Field(
        default=None, description="Read spelling: its name or {name, ...}."
    )


# A to-many relationship's records are read by binding the relationship as a
# field input with data_type entity and is_list true; the script gets their
# ids (confirmed live 2026-11-16).
class CodeFieldInput(_FieldSource):
    """A field on the automation's record."""

    input_type: Literal["field"] = Field(
        default="field", description="Where the value comes from. Default: field."
    )


class CodeVariableInput(_VariableSource):
    """An automation variable."""

    input_type: Literal["variable"]


# Single-value hops only (confirmed live 2026-11-16).
class CodeRelatedFieldInput(_FieldSource):
    """A field on the record a relationship field points to."""

    input_type: Literal["related_field"]
    field_ref: str | None = Field(default=None, description=_HOP)
    related_field: str | dict[str, Any] | None = Field(
        default=None, description=_RELATED
    )
    related_field_ref: str | None = None


class CodeStaticValueInput(_CodeBinding):
    """A literal value."""

    input_type: Literal["static_value"]
    static_value: Any = Field(
        default=None,
        description=(
            "The value itself, or {value, entity_record, employee} for a "
            "record or team member."
        ),
    )


class PluginConfigInput(_CodeBinding):
    """The plugin's install config, for plugin steps only. No source key."""

    input_type: Literal["business_plugin_config"]


class _Written(BaseModel):
    conflict_resolution: (
        Literal[
            "overwrite",
            "add_only",
            "remove_only",
            "update_if_blank",
            "overwrite_except_null",
        ]
        | None
    ) = Field(default=None, description="How the value meets one already there.")
    create_missing_option: bool | None = Field(
        default=None, description="Add a value the field has no option for."
    )


class CodeFieldOutput(_FieldSource, _Written):
    """Written to a field on the automation's record."""

    output_type: Literal["field"] = Field(
        default="field", description="Where the value goes. Default: field."
    )


class CodeVariableOutput(_VariableSource, _Written):
    """Written to an automation variable."""

    output_type: Literal["variable"]


class CodeRelatedFieldOutput(_FieldSource, _Written):
    """Written to a field on the record a relationship field points to."""

    output_type: Literal["related_field"]
    field_ref: str | None = Field(default=None, description=_HOP)
    related_field: str | dict[str, Any] | None = Field(
        default=None, description=_RELATED
    )
    related_field_ref: str | None = None


# An entry whose type no member takes lands on one of these, so the finding
# lists the types. `entity_variable_field` is the server's too, but the
# planner drops its source, so it isn't offered.
class _UnknownCodeInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    input_type: Literal["field", "variable", "related_field", "static_value"]


class _UnknownPluginInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    input_type: Literal[
        "field", "variable", "related_field", "static_value", "business_plugin_config"
    ]


class _UnknownOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    output_type: Literal["field", "variable", "related_field"]


def _tagged(key: str, members: dict[str, type[BaseModel]], unknown: type) -> Any:
    """A list entry picked by ``key``, which defaults to ``field``. Each tag
    is "<value> <input|output>", which `UNION_TAGS` strips from paths."""
    noun = key.split("_")[0]

    def kind(v: Any) -> str | None:
        if isinstance(v, dict):
            value = v.get(key, "field")
        elif isinstance(v, BaseModel):
            value = getattr(v, key, None)
        else:
            return None
        return f"{value} {noun}" if value in members else f"unknown {noun}"

    return Annotated[
        Union[  # noqa: UP007
            tuple(Annotated[m, Tag(f"{v} {noun}")] for v, m in members.items())
            + (Annotated[unknown, Tag(f"unknown {noun}")],)
        ],
        Discriminator(
            kind,
            custom_error_type="code_binding",
            custom_error_message=f"each entry is an object with a name and an {key}",
        ),
    ]


_CODE_INPUTS: dict[str, type[BaseModel]] = {
    "field": CodeFieldInput,
    "variable": CodeVariableInput,
    "related_field": CodeRelatedFieldInput,
    "static_value": CodeStaticValueInput,
}
_CODE_OUTPUTS: dict[str, type[BaseModel]] = {
    "field": CodeFieldOutput,
    "variable": CodeVariableOutput,
    "related_field": CodeRelatedFieldOutput,
}

CodeStepInputConfig = _tagged("input_type", _CODE_INPUTS, _UnknownCodeInput)
"""A code step input (CodeStepInputRequest), keyed on `input_type`."""
PluginCodeStepInputConfig = _tagged(
    "input_type",
    {**_CODE_INPUTS, "business_plugin_config": PluginConfigInput},
    _UnknownPluginInput,
)
"""A plugin step input (PluginCodeStepInputRequest): a code step input, or
`business_plugin_config`."""
CodeStepOutputConfig = _tagged("output_type", _CODE_OUTPUTS, _UnknownOutput)
"""A code or plugin step output (CodeStepOutputRequest), keyed on
`output_type`."""

_CODE_TAGS = frozenset(
    [f"{v} input" for v in (*_CODE_INPUTS, "business_plugin_config", "unknown")]
    + [f"{v} output" for v in (*_CODE_OUTPUTS, "unknown")]
)


# ---------------------------------------------------------------------------
# filter_config
# ---------------------------------------------------------------------------

# Tags a discriminated union adds to an error's location. They aren't keys,
# so the findings leave them out of the path.
UNION_TAGS = frozenset({"filter clause", "filter group", "raw filter", *_CODE_TAGS})

FilterOp = Literal[
    "!=",
    "<",
    "<=",
    "=",
    ">",
    ">=",
    "between",
    "contains",
    "ends_with",
    "entered_stage",
    "has_all",
    "has_any",
    "has_none",
    "is_any_of",
    "is_blank",
    "is_checked",
    "is_me",
    "left_stage",
    "month_equals",
    "not_any_of",
    "not_blank",
    "not_checked",
    "not_contains",
    "not_ends_with",
    "not_starts_with",
    "starts_with",
    "time_in_stage",
]
"""The filter DSL's ops (`kizen filters ops`). A test pins this to
`filtering.VALID_SPEC_OPS`, which compiles them."""


class FilterClause(BaseModel):
    """One test of a field: `{"field", "op", "value"}`. The planner compiles
    it (`kizen docs show filters`); this only checks the keys."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="The field's api_name on the filtered object.")
    op: FilterOp
    value: Any = Field(
        default=None,
        description=(
            "What the field is compared to. A list for is_any_of, not_any_of, "
            "has_* and between; none for is_blank, not_blank, is_me and the "
            "checkbox ops; the stage for the stage ops."
        ),
    )
    # A dropdown matched on a test run with the array holding option names,
    # and with it holding option UUIDs.
    variable: str | None = Field(
        default=None,
        description=(
            "An automation variable in place of `value`, on a condition or "
            "search_records step: is_any_of or not_any_of with an array "
            "variable, = or contains with a string one."
        ),
    )
    # The stage keys' values are checked when the clause compiles, which
    # names the op they belong to.
    more_than: Any = Field(
        default=None, description="time_in_stage only: a whole number of units."
    )
    less_than: Any = Field(
        default=None, description="time_in_stage only: a whole number of units."
    )
    units: Any = Field(
        default=None,
        description="time_in_stage only: days, hours or weeks; days when left out.",
    )
    on: str | None = Field(default=None, description="entered_stage/left_stage.")
    not_on: str | None = Field(default=None, description="entered_stage/left_stage.")
    earlier_than: str | None = Field(
        default=None, description="entered_stage/left_stage."
    )
    earlier_than_or_on: str | None = Field(
        default=None, description="entered_stage/left_stage."
    )
    later_than: str | None = Field(
        default=None, description="entered_stage/left_stage."
    )
    later_than_or_on: str | None = Field(
        default=None, description="entered_stage/left_stage."
    )


def _filter_node_kind(node: Any) -> str:
    if isinstance(node, dict):
        group = "all" in node or "any" in node
    else:
        group = isinstance(node, FilterGroup)
    return "filter group" if group else "filter clause"


FilterNode = Annotated[
    Annotated[FilterClause, Tag("filter clause")]
    | Annotated["FilterGroup", Tag("filter group")],
    Discriminator(_filter_node_kind),
]


class FilterGroup(BaseModel):
    """`{"all": [...]}` or `{"any": [...]}`: every test, or at least one.
    Each item is a clause or another group."""

    model_config = ConfigDict(extra="forbid")

    all: list[FilterNode] | None = Field(
        default=None,
        min_length=1,
        description="Clauses or groups that must all hold.",
    )
    any: list[FilterNode] | None = Field(
        default=None,
        min_length=1,
        description="Clauses or groups of which one must hold.",
    )

    @model_validator(mode="after")
    def _one_kind(self) -> FilterGroup:
        if (self.all is None) == (self.any is None):
            raise ValueError('a group is exactly {"all": [...]} or {"any": [...]}')
        return self


class RawFilterGroup(BaseModel):
    """One group of the wire form: its clauses, as Kizen's editor writes
    them."""

    model_config = ConfigDict(extra="allow")

    filters: list[dict[str, Any]]


class RawFilterConfig(BaseModel):
    """The wire form, `{"and", "query": [{"id", "and", "filters"}], "invalid"}`,
    as a live read returns it. The planner assigns missing group ids. A
    clause the DSL has no op for, such as a variable compared to a static
    value, is written this way (`kizen docs show filters`)."""

    model_config = ConfigDict(extra="allow")

    query: list[RawFilterGroup]


def _filter_config_kind(node: Any) -> str | None:
    if isinstance(node, FilterGroup):
        return "filter group"
    if isinstance(node, RawFilterConfig):
        return "raw filter"
    if not isinstance(node, dict):
        return None
    if "all" in node or "any" in node:
        return "filter group"
    return "raw filter" if "query" in node else None


def _refuse_bare_clause(value: Any) -> Any:
    """A clause on its own is the commonest wrong shape: name the wrap
    instead of a union error."""
    if isinstance(value, dict) and "field" in value and "query" not in value:
        wrapped = json.dumps({"all": [value]})
        raise ValueError(f"a clause needs a group around it: {wrapped}")
    return value


FilterConfig = Annotated[
    Annotated[FilterGroup, Tag("filter group")]
    | Annotated[RawFilterConfig, Tag("raw filter")],
    Discriminator(
        _filter_config_kind,
        custom_error_type="filter_config",
        custom_error_message=(
            'filter_config is a group, {"all": [...]} or {"any": [...]}, or '
            'the wire form with a "query" list'
        ),
    ),
    BeforeValidator(_refuse_bare_clause),
]
"""A filter in the DSL, compiled by the planner, or in the wire form."""

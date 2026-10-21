"""Sub-models shared across automation trigger/step config blocks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
        description="'object.field' reference, resolved to field UUID at apply time.",
    )
    field_id: str | None = None
    value: Any = None
    variable: str | None = None


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
      clearer — see automation.md's "LLM & extraction destinations" section
      for a worked example.
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
            "Option UUIDs for a choice-type destination field. Auto-populated "
            "from field metadata when omitted and field_ref names a choice field."
        ),
    )
    is_required: bool = False
    confidence_threshold: float = 0.7
    conflict_resolution: str | None = None


class CodeStepInputConfig(BaseModel):
    """An input binding for a code step (CodeStepInputRequest)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=2, max_length=255)
    input_type: Literal["field", "variable", "related_field", "static_value"] = "field"
    field_ref: str | None = Field(
        default=None,
        description="'object.field' reference, resolved to field UUID at apply time.",
    )
    field_id: str | None = Field(default=None, description="Direct UUID fallback.")
    variable_name: str | None = Field(
        default=None, description="Variable name, used when input_type is 'variable'."
    )
    static_value: Any = Field(
        default=None,
        description="Literal value, used when input_type is 'static_value'.",
    )


class PluginCodeStepInputConfig(CodeStepInputConfig):
    """An input binding for a plugin step (PluginCodeStepInputRequest).

    Same as a code step input, plus ``business_plugin_config``, which binds
    the plugin's install config. Run-time delivery is not confirmed live.
    """

    # Widens the base's choices, which mypy reports as an override.
    input_type: Literal[
        "field", "variable", "related_field", "static_value", "business_plugin_config"
    ] = "field"  # type: ignore[assignment]


class CodeStepOutputConfig(BaseModel):
    """An output binding for a code step (CodeStepOutputRequest)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=2, max_length=255)
    output_type: Literal["field", "variable", "related_field"] = "field"
    field_ref: str | None = Field(
        default=None,
        description="'object.field' reference, resolved to field UUID at apply time.",
    )
    field_id: str | None = Field(default=None, description="Direct UUID fallback.")
    variable_name: str | None = Field(
        default=None, description="Variable name, used when output_type is 'variable'."
    )

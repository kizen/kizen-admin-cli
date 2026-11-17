"""Automation step config models: control flow / execution steps."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kizen_builder.models.spec.automations_shared import FilterConfig


class StepConditionConfig(BaseModel):
    """Config for `step_type: condition`.

    NOTE: Branches are NOT specified here. Use `parent_key` + `parent_branch`
    on the child steps to wire YES/NO branches. The Kizen API documents
    `yes_step_ids`/`no_step_ids` here, but those fields trigger an HTTP 500
    when populated. The actual wire mechanism is parent-pointer based.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["in_group", "not_in_group", "custom_filter", "llm_decision"] = (
        "custom_filter"
    )
    filter_config: FilterConfig | None = None
    group_ids: list[str] = Field(
        default_factory=list,
        description="Entity group UUIDs for in_group/not_in_group types.",
    )
    llm_decision: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw dict (not a typed model) for `type: llm_decision`: "
            "'model_name', 'prompt'/'html_prompt', 'business_plugin_app_id' "
            "(optional, filled by the CLI — see ActionLlmCallConfig), and "
            "'merge_field_validation' (see ActionLlmCallConfig) all apply "
            "the same as call_llm."
        ),
    )


class StepDelayConfig(BaseModel):
    """Config for `step_type: delay`."""

    model_config = ConfigDict(extra="allow")

    minutes: int = Field(default=0, ge=0)
    hours: int = Field(default=0, ge=0)
    days: int = Field(default=0, ge=0)
    weeks: int = Field(default=0, ge=0)
    months: int = Field(default=0, ge=0)
    years: int = Field(default=0, ge=0)
    time: str | None = Field(default=None, description="Time of day in HH:MM format.")
    days_specific_time: dict[str, Any] | None = None
    skip_non_working_days: bool = False
    value_origin: Literal["static", "automation_variable"] = "static"
    duration_unit: str | None = None
    variable: str | None = None


class StepLoopConfig(BaseModel):
    """Config for `step_type: loop`. Both variables are named, and each must be
    declared by an `initialize_variable` step: the array, and a single variable
    of the array's type that holds the current item."""

    model_config = ConfigDict(extra="allow")

    loop_type: Literal["for_each_item"] = "for_each_item"
    array_variable: str = Field(description="Name of the array variable to iterate.")
    item_variable: str = Field(
        description="Name of the variable that holds the current item."
    )


class StepGoalConfig(BaseModel):
    """Config for `step_type: goal`."""

    model_config = ConfigDict(extra="allow")

    wait_type: str = Field(description="One of: never, delay, specific_datetime.")
    # Choices: `GET /api/docs/schema`, 2026-11-17 (`DurationUnitEnum`). A read
    # of a goal that doesn't wait on a delay carries "", and saves back as it is.
    delay_type: Literal["minutes", "hours", "days", ""] | None = Field(
        default=None,
        description=(
            "Unit of `delay_amount`, plural (`day` is a 400); a read carries "
            '"" when the goal waits on no delay.'
        ),
    )
    delay_amount: int | None = Field(default=None, ge=0)
    specific_datetime: str | None = Field(
        default=None, description="ISO 8601 datetime string."
    )
    value_origin: str | None = None
    variable: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class ActionGoToAutomationStepConfig(BaseModel):
    """Config for `step_type: go_to_automation_step`.

    Either ``step_key`` (spec key) or ``step`` (live read shape: ``{id, ...}``)
    is acceptable as input — the planner normalizes to the wire form.
    """

    model_config = ConfigDict(extra="allow")

    step_key: str | None = Field(
        default=None,
        description="Key of the target step within this automation. Resolved to step UUID at apply time.",
    )


class ActionStopExecutionConfig(BaseModel):
    """Config for `step_type: stop_execution`.

    Confirmed live (all 5 values captured from a real automation with one
    step per option): the builder UI's "Action Options" dropdown is a
    REQUIRED choice with no default — despite the name, two of the five
    options (`pause`/`pause_and_error`) PAUSE the execution rather than
    stopping it.

    | UI label                              | wire value          |
    |----------------------------------------|----------------------|
    | Stop and mark as Failed                | `stop_and_fail`      |
    | Stop and mark as Successfully Completed| `stop_and_complete`  |
    | Stop and mark as Cancelled             | `stop_and_cancel`    |
    | Pause and Error                        | `pause_and_error`    |
    | Pause                                  | `pause`              |

    Kizen accepts an unset `action` and reads it back `null`, but a run
    that reaches that step never finishes (confirmed live 2026-10-30), so
    the CLI sends `stop_and_complete` for a new step that leaves it out, and
    the live action for an `update` or `steps edit` whose step `id` matches.
    `notify` reads back `false` when unset.
    """

    model_config = ConfigDict(extra="allow")

    action: (
        Literal[
            "stop_and_fail",
            "stop_and_complete",
            "stop_and_cancel",
            "pause_and_error",
            "pause",
        ]
        | None
    ) = None
    notify: bool | None = None


class ActionArchiveRecordConfig(BaseModel):
    """Config for `step_type: archive_record`. `record_source` picks the
    record: the automation's own, the one a relationship field points at, or
    the one an entity variable holds (confirmed live 2026-11-17)."""

    model_config = ConfigDict(extra="allow")

    record_source: Literal[
        "this_record", "related_object_field", "automation_variable"
    ] = "this_record"
    relationship_fields: list[str] | None = Field(
        default=None,
        description=(
            "With related_object_field: the relationship field to follow, as "
            "'object.field' or a UUID. Resolved to UUIDs at apply time."
        ),
    )
    automation_variable_name: str | None = Field(
        default=None,
        description="With automation_variable: the entity variable's name.",
    )

    @model_validator(mode="after")
    def _source_has_its_key(self) -> Self:
        extra = self.model_extra or {}
        if self.record_source == "related_object_field" and not (
            self.relationship_fields or extra.get("relationship_field_ids")
        ):
            raise ValueError(
                "record_source related_object_field needs relationship_fields: "
                "the relationship field that points at the record to archive"
            )
        if self.record_source == "automation_variable" and not (
            self.automation_variable_name or extra.get("automation_variable")
        ):
            raise ValueError(
                "record_source automation_variable needs automation_variable_name: "
                "the entity variable holding the record to archive"
            )
        return self


class ActionModifyAutomationConfig(BaseModel):
    """Config for `step_type: modify_automation`."""

    model_config = ConfigDict(extra="allow")

    automation_api_name: str | None = Field(
        default=None,
        description="api_name of the automation to modify. Resolved to UUID from state at apply time.",
    )
    automation_id: str | None = None
    action: Literal["start", "cancel", "pause"]


class ActionStartAutomationConfig(BaseModel):
    """Config for `step_type: start_automation`."""

    model_config = ConfigDict(extra="allow")

    automation_api_name: str | None = Field(
        default=None,
        description="api_name of the automation to start. Resolved to UUID from state at apply time.",
    )
    automation_id: str | None = Field(
        default=None, description="Direct UUID fallback for automation_api_name."
    )

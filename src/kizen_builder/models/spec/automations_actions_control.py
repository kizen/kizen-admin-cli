"""Automation step config models: control flow / execution steps."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    filter_config: dict[str, Any] | None = None
    group_ids: list[str] = Field(
        default_factory=list,
        description="Entity group UUIDs for in_group/not_in_group types.",
    )
    llm_decision: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw dict (not a typed model) for `type: llm_decision`: "
            "'model_name', 'prompt'/'html_prompt', 'business_plugin_app_id' "
            "(non-native models only — see ActionLlmCallConfig), and "
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


class StepGoalConfig(BaseModel):
    """Config for `step_type: goal`."""

    model_config = ConfigDict(extra="allow")

    wait_type: str = Field(
        description="One of: wait_forever, wait_until_date, wait_duration, etc."
    )
    delay_type: str | None = None
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

    Omitting this block (or leaving `action` unset) is accepted by the API
    and reads back `null` — but every step built through the UI has one of
    the five set, so a spec that cares about the run's final status
    (rather than relying on whatever `null` defaults to) should set one
    explicitly. `notify` has no documented server-side default; it reads
    back `false` when unset.
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
    """Config for `step_type: archive_record` (no required fields)."""

    model_config = ConfigDict(extra="allow")


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


class ActionUpdatePipelineStatusConfig(BaseModel):
    """Config for `step_type: update_pipeline_status`.

    Note: `to_stage_id` takes a Kizen stage UUID directly. Stage name resolution
    (via pipeline field options) is not currently supported by named references.
    """

    model_config = ConfigDict(extra="allow")

    stage_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the pipeline status field. Resolved to UUID at apply time.",
    )
    stage_field_id: str | None = None
    to_stage_id: str = Field(description="UUID of the target pipeline stage.")

"""AutomationStepDef / AutomationDef: the top-level automation graph."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kizen_builder.models.spec._base import ApiName
from kizen_builder.models.spec.automations_actions_code import (
    ActionAudioTranscriptionConfig,
    ActionCodeStepConfig,
    ActionFileContentExtractionConfig,
    ActionHttpRequestConfig,
    ActionLlmCallConfig,
    ActionPluginCodeStepConfig,
)
from kizen_builder.models.spec.automations_actions_control import (
    ActionArchiveRecordConfig,
    ActionGoToAutomationStepConfig,
    ActionModifyAutomationConfig,
    ActionStartAutomationConfig,
    ActionStopExecutionConfig,
    ActionUpdatePipelineStatusConfig,
    StepConditionConfig,
    StepDelayConfig,
    StepGoalConfig,
)
from kizen_builder.models.spec.automations_actions_data import (
    ActionChangeFieldValueConfig,
    ActionCreateRelatedEntityConfig,
    ActionInitializeVariableConfig,
    ActionMathOperatorConfig,
    ActionModifyRelatedEntitiesAutomationConfig,
    ActionModifyRelatedEntitiesConfig,
    ActionSearchRecordsConfig,
    ActionUpdateVariableConfig,
)
from kizen_builder.models.spec.automations_actions_messaging import (
    ActionAssignTeamMemberConfig,
    ActionChangeTagsConfig,
    ActionDeleteScheduledActivityConfig,
    ActionNotifyMemberViaEmailConfig,
    ActionNotifyMemberViaTextConfig,
    ActionRequestInfoViaTextConfig,
    ActionScheduleActivityConfig,
    ActionSendEmailConfig,
    ActionSendRelatedContactEmailConfig,
    ActionSendRelatedContactTextConfig,
    ActionSendTextConfig,
)
from kizen_builder.models.spec.automations_triggers import AutomationTriggerDef

_STEP_TYPE_TO_CONFIG_FIELD: dict[str, str | None] = {
    "assign_team_member": "action_assign_team_member",
    "audio_transcription": "action_audio_transcription",
    "call_llm": "action_call_llm",
    "change_field_value": "action_change_field_value",
    "change_tags": "action_change_tags",
    "code_step": "action_code_step",
    "condition": "step_condition",
    "plugin_code_step": "action_plugin_code_step",
    "create_related_entity": "action_create_related_entity",
    "delay": "step_delay",
    "delete_scheduled_activity": "action_delete_scheduled_activity",
    "file_content_extraction": "action_file_content_extraction",
    "goal": "step_goal",
    "go_to_automation_step": "action_go_to_automation_step",
    "http_request": "action_http_request",
    "initialize_variable": "action_initialize_variable",
    "math_operator": "action_math_operator",
    "modify_automation": "action_modify_automation",
    "modify_related_entities": "action_modify_related_entities",
    "modify_related_entities_automation": "action_modify_related_entities_automation",
    "notify_member_via_email": "action_notify_member_via_email",
    "notify_member_via_text": "action_notify_member_via_text",
    "request_info_via_text": "action_request_info_via_text",
    "schedule_activity": "action_schedule_activity",
    "send_email": "action_send_email",
    "send_related_contact_email": "action_send_related_contact_email",
    "send_text": "action_send_text",
    "send_related_contact_text": "action_send_related_contact_text",
    "start_automation": "action_start_automation",
    "stop_execution": None,  # no config block needed
    "update_pipeline_status": "action_update_pipeline_status",
    "update_variable": "action_update_variable",
    "archive_record": "action_archive_record",
    "search_records": "action_search_records",
    "branch": None,
    "merge_branches": None,
}


_STEPS_REQUIRING_CONFIG = {
    "code_step",
    "call_llm",
    "audio_transcription",
    "file_content_extraction",
    "condition",
    "create_related_entity",
    "go_to_automation_step",
    "http_request",
    "initialize_variable",
    "modify_automation",
    "modify_related_entities_automation",
    "notify_member_via_email",
    "notify_member_via_text",
    "plugin_code_step",
    "start_automation",
    "update_pipeline_status",
    "update_variable",
    "search_records",
}


class AutomationStepDef(BaseModel):
    """A single step in an automation.

    Steps are linked into a graph via `parent_key`. The first step has
    `parent_key: null`. Subsequent steps point to their predecessor's `key`.
    For the FIRST step inside a YES or NO branch of a `condition` (or `goal`)
    step, also set `parent_branch: "yes"` (or `"no"`). Linear successors
    inside a branch have `parent_branch: null`.

    `key` is also used by `go_to_automation_step.step_key` references.
    Exactly one type-specific config block matching `step_type` should be
    present for step types that require configuration.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        description=(
            "Stable identifier for this step within the automation. Used by "
            "parent_key references on other steps and by "
            "go_to_automation_step.step_key. Required and must be unique "
            "within the automation."
        ),
    )
    id: str | None = Field(
        default=None,
        description=(
            "This step's server-assigned UUID, if known (e.g. copied from "
            "`kizen automations show`). When set, it's echoed back on PUT so "
            "the step keeps its identity — and its execution history — "
            "instead of the server assigning a fresh id. Omit for a new "
            "step; the server assigns one."
        ),
    )
    parent_key: str | None = Field(
        default=None,
        description=(
            "Key of the parent step in the graph. `null` for the first step. "
            "For linear chains, points to the previous step. For YES/NO "
            "branch entry steps, points to the parent condition/goal step "
            "and `parent_branch` must also be set."
        ),
    )
    parent_branch: Literal["yes", "no"] | None = Field(
        default=None,
        description=(
            "Set to 'yes' or 'no' only on the FIRST step of a branch under a "
            "condition or goal step. Linear successors inside the branch leave "
            "this null."
        ),
    )
    step_type: str
    order: int = Field(ge=0)
    description: str | None = None
    user_description: str = ""
    action_on_failure: Literal["notify_continue", "notify_pause", "silent_continue"] = (
        "notify_continue"
    )
    should_skip_execution: bool = False
    is_branch_group_initiator: bool = Field(
        default=False,
        description=(
            "Set on a `branch`, `condition` or `goal` step whose lanes rejoin. "
            "The step then needs exactly one `merge_branches` child, and steps "
            "after the merge hang off that merge step."
        ),
    )
    continue_with_branch: Literal["yes", "no"] | None = Field(
        default=None,
        description=(
            "Required on a `condition` or `goal` with "
            "`should_skip_execution: true`: the branch execution takes past it."
        ),
    )
    error_notification_severity_level: (
        Literal["error", "warning", "info", "inherit"] | None
    ) = None

    # Type-specific config blocks (only the one matching step_type should be set)
    step_condition: StepConditionConfig | None = None
    step_delay: StepDelayConfig | None = None
    step_goal: StepGoalConfig | None = None
    action_assign_team_member: ActionAssignTeamMemberConfig | None = None
    action_audio_transcription: ActionAudioTranscriptionConfig | None = None
    action_call_llm: ActionLlmCallConfig | None = None
    action_change_field_value: ActionChangeFieldValueConfig | None = None
    action_change_tags: ActionChangeTagsConfig | None = None
    action_code_step: ActionCodeStepConfig | None = None
    action_plugin_code_step: ActionPluginCodeStepConfig | None = None
    action_create_related_entity: ActionCreateRelatedEntityConfig | None = None
    action_delete_scheduled_activity: ActionDeleteScheduledActivityConfig | None = None
    action_file_content_extraction: ActionFileContentExtractionConfig | None = None
    action_go_to_automation_step: ActionGoToAutomationStepConfig | None = None
    action_http_request: ActionHttpRequestConfig | None = None
    action_initialize_variable: ActionInitializeVariableConfig | None = None
    action_math_operator: ActionMathOperatorConfig | None = None
    action_modify_automation: ActionModifyAutomationConfig | None = None
    action_modify_related_entities: ActionModifyRelatedEntitiesConfig | None = None
    action_modify_related_entities_automation: (
        ActionModifyRelatedEntitiesAutomationConfig | None
    ) = None
    action_notify_member_via_email: ActionNotifyMemberViaEmailConfig | None = None
    action_notify_member_via_text: ActionNotifyMemberViaTextConfig | None = None
    action_request_info_via_text: ActionRequestInfoViaTextConfig | None = None
    action_schedule_activity: ActionScheduleActivityConfig | None = None
    action_stop_execution: ActionStopExecutionConfig | None = None
    action_send_email: ActionSendEmailConfig | None = None
    action_send_related_contact_email: ActionSendRelatedContactEmailConfig | None = None
    action_send_text: ActionSendTextConfig | None = None
    action_send_related_contact_text: ActionSendRelatedContactTextConfig | None = None
    action_start_automation: ActionStartAutomationConfig | None = None
    action_update_pipeline_status: ActionUpdatePipelineStatusConfig | None = None
    action_update_variable: ActionUpdateVariableConfig | None = None
    action_archive_record: ActionArchiveRecordConfig | None = None
    action_search_records: ActionSearchRecordsConfig | None = None

    @model_validator(mode="after")
    def _validate_step_config(self) -> Self:
        valid_types = set(_STEP_TYPE_TO_CONFIG_FIELD.keys())
        if self.step_type not in valid_types:
            raise ValueError(
                f"unknown step_type '{self.step_type}'. "
                f"Valid values: {sorted(valid_types)}"
            )
        config_field = _STEP_TYPE_TO_CONFIG_FIELD[self.step_type]
        if (
            self.step_type in _STEPS_REQUIRING_CONFIG
            and config_field
            and getattr(self, config_field) is None
        ):
            raise ValueError(
                f"step_type '{self.step_type}' requires an '{config_field}' block"
            )
        if self.step_type in ("branch", "merge_branches"):
            given = sorted(
                f
                for f in set(_STEP_TYPE_TO_CONFIG_FIELD.values())
                if f and getattr(self, f) is not None
            )
            if given:
                raise ValueError(
                    f"step_type '{self.step_type}' takes no config block (got {given})"
                )
        return self


class AutomationDef(BaseModel):
    """An automation in Kizen (triggers + steps sent as one atomic POST)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    api_name: ApiName
    type: Literal["record_based", "global"] = "record_based"
    target_object: ApiName | None = Field(
        default=None,
        description=(
            "api_name of the custom object this automation runs against. "
            "Required for record_based automations. Resolved to custom_object_id UUID from state."
        ),
    )
    active: bool | None = None
    user_description: str | None = None
    error_notification_email: str | None = None
    skip_non_working_days: bool = False
    priority_rank: int | None = Field(default=None, ge=0)
    triggers: list[AutomationTriggerDef] = Field(default_factory=list)
    steps: list[AutomationStepDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_automation(self) -> Self:
        if self.type == "record_based" and self.target_object is None:
            raise ValueError(
                "record_based automations require 'target_object' (the api_name of "
                "the custom object this automation runs against)"
            )

        # Step keys must be unique
        keys = [s.key for s in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "duplicate step keys within automation — each 'key' must be unique"
            )
        keys_set = set(keys)
        steps_by_key = {s.key: s for s in self.steps}

        # Validate parent_key references and exactly-one-root rule
        roots = [s for s in self.steps if s.parent_key is None]
        if self.steps and len(roots) == 0:
            raise ValueError(
                "automation has steps but no root step (one step must have parent_key: null)"
            )
        if len(roots) > 1:
            root_keys = [s.key for s in roots]
            raise ValueError(
                f"automation has multiple root steps (parent_key: null): {root_keys}. "
                "Exactly one root is required; chain other steps by parent_key."
            )

        for step in self.steps:
            if step.parent_key is not None and step.parent_key not in keys_set:
                raise ValueError(
                    f"step '{step.key}' has parent_key '{step.parent_key}' which "
                    f"does not match any step key. Available: {sorted(keys_set)}"
                )

            if (
                step.step_type in ("condition", "goal")
                and step.should_skip_execution
                and step.continue_with_branch is None
            ):
                raise ValueError(
                    f"skipped {step.step_type} '{step.key}' needs "
                    "continue_with_branch: 'yes' or 'no' (the branch to run)"
                )

            # parent_branch is only valid when parent is a condition or goal step.
            if step.parent_branch is not None:
                if step.parent_key is None:
                    raise ValueError(
                        f"step '{step.key}' has parent_branch='{step.parent_branch}' "
                        "but no parent_key. parent_branch must accompany parent_key."
                    )
                parent = steps_by_key[step.parent_key]
                if parent.step_type not in ("condition", "goal"):
                    raise ValueError(
                        f"step '{step.key}' has parent_branch='{step.parent_branch}' "
                        f"but its parent '{parent.key}' is a '{parent.step_type}' step, "
                        "not a condition or goal. parent_branch is only valid under "
                        "condition or goal parents."
                    )

        # Cycle detection: every step must reach the root via parent_key chain.
        for step in self.steps:
            seen: set[str] = set()
            cur: str | None = step.key
            while cur is not None:
                if cur in seen:
                    raise ValueError(
                        f"cycle detected in step graph involving '{step.key}'"
                    )
                seen.add(cur)
                cur = steps_by_key[cur].parent_key

        # Validate go_to_automation_step references
        for step in self.steps:
            if step.action_go_to_automation_step:
                ref = step.action_go_to_automation_step.step_key
                if ref not in keys_set:
                    raise ValueError(
                        f"go_to_automation_step '{step.key}' references unknown "
                        f"step key '{ref}'. Available: {sorted(keys_set)}"
                    )

        return self

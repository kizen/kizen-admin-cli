"""Pydantic models defining the Kizen Builder spec format.

This package is the single source of truth for what a valid spec looks like.
The exported JSON Schema (in schema/solution.schema.json) is generated from
these models. Split from a single 2,853-line module into one file per
cluster (naming/enums, field configs, custom objects, automations —
sub-split by trigger/step/action group, dashboards, activities, forms,
smart connectors) — see the individual modules for their own docstrings.

Principles:
  * References use human `api_name`, never UUIDs.
  * `api_name` is the stable identifier used for idempotent re-runs.
  * Field-type-specific config is validated by a model_validator so unsupported
    combinations (e.g. dropdown without options) fail before any API call.

This `__init__` re-exports every public name from every submodule, so
`from kizen_builder.models.spec import X` keeps working unchanged for all
existing importers regardless of which submodule `X` now lives in.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# isort: off
from kizen_builder.models.spec._base import (
    ApiName,
    FieldType,
    RelationType,
    RelationCardinality,
)
from kizen_builder.models.spec.field_configs import (
    RelationConfig,
    MoneyConfig,
    RatingConfig,
    DecimalConfig,
    PhoneConfig,
    StatusOption,
)
from kizen_builder.models.spec.objects import (
    FieldDef,
    FieldCategory,
    ObjectType,
    PipelineStageSpec,
    PipelineDef,
    ObjectDef,
)
from kizen_builder.models.spec.automations_shared import (
    TeamMemberConfig,
    AutomationVariableConfig,
    VariableSourceConfig,
    LlmDestinationConfig,
    CodeStepInputConfig,
    CodeStepOutputConfig,
)
from kizen_builder.models.spec.automations_triggers import (
    TriggerFieldUpdatedConfig,
    TriggerContactTagAddedRemovedConfig,
    TriggerActivityLoggedConfig,
    TriggerFormSubmittedConfig,
    TriggerSurveySubmittedConfig,
    TriggerEmailDeliveredConfig,
    TriggerOnOrAroundDateConfig,
    TriggerWebsiteVisitedConfig,
    TriggerEmailInteractionConfig,
    TriggerEmailReceivedFromContactConfig,
    TriggerStageUpdatedConfig,
    TriggerEmailLinkClickedConfig,
    TriggerScheduleConfig,
    TriggerScheduledActivityOverdueConfig,
    TriggerNewEntityCreatedConfig,
    TriggerWebhookConfig,
    TriggerManualConfig,
    AutomationTriggerDef,
)
from kizen_builder.models.spec.automations_actions_control import (
    StepConditionConfig,
    StepDelayConfig,
    StepGoalConfig,
    ActionGoToAutomationStepConfig,
    ActionStopExecutionConfig,
    ActionArchiveRecordConfig,
    ActionModifyAutomationConfig,
    ActionStartAutomationConfig,
    ActionUpdatePipelineStatusConfig,
)
from kizen_builder.models.spec.automations_actions_messaging import (
    ActionSendEmailConfig,
    ActionSendRelatedContactEmailConfig,
    ActionNotifyMemberViaEmailConfig,
    ActionNotifyMemberViaTextConfig,
    ActionSendTextConfig,
    ActionSendRelatedContactTextConfig,
    ActionRequestInfoViaTextConfig,
    ActionAssignTeamMemberConfig,
    TagToAddConfig,
    ActionChangeTagsConfig,
    ActionScheduleActivityConfig,
    ActionDeleteScheduledActivityConfig,
)
from kizen_builder.models.spec.automations_actions_data import (
    ActionChangeFieldValueConfig,
    ActionCreateRelatedEntityConfig,
    ActionModifyRelatedEntitiesConfig,
    ActionModifyRelatedEntitiesAutomationConfig,
    ActionSearchRecordsConfig,
    ActionInitializeVariableConfig,
    ActionUpdateVariableConfig,
    ActionMathOperatorConfig,
)
from kizen_builder.models.spec.automations_actions_code import (
    ActionHttpRequestConfig,
    ActionCodeStepConfig,
    ActionPluginCodeStepConfig,
    ActionLlmCallConfig,
    ActionAudioTranscriptionConfig,
    ActionFileContentExtractionConfig,
)
from kizen_builder.models.spec.automations import (
    AutomationStepDef,
    AutomationDef,
)
from kizen_builder.models.spec.dashboards import (
    DashletDef,
    DashboardDef,
    FilterGroupDef,
    QuickFilterDef,
    ColumnTemplateDef,
    LayoutDef,
)
from kizen_builder.models.spec.email_templates import (
    ColumnPreset,
    COLUMN_FRACTIONS,
    TextBlockDef,
    ImageBlockDef,
    ButtonBlockDef,
    DividerBlockDef,
    BlockDef,
    CellDef,
    RowDef,
    SectionDef,
    EmailTemplateDef,
)
from kizen_builder.models.spec.activities import (
    ActivityFieldType,
    AssociationMode,
    ActivityFieldDef,
    ActivityDef,
)
from kizen_builder.models.spec.forms import (
    FormFieldType,
    FormFieldDef,
    FormDef,
)
from kizen_builder.models.spec.smart_connectors import (
    ExecutionVariableDataType,
    ExecutionVariableDef,
    NoMatchAction,
    SingleMatchAction,
    MultipleMatchAction,
    MatchArchiveAction,
    ConflictResolution,
    MatchingRuleDef,
    FieldMappingRuleDef,
    LoadStepDef,
    SmartConnectorFlowDef,
)

# isort: on


class SolutionSpec(BaseModel):
    """Top-level document. A single spec file deserializes into one of these."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = Field(
        default="1",
        description="Spec format version. Currently only '1' is supported.",
    )
    name: str = Field(min_length=1, description="Human label for this solution.")
    description: str | None = None
    custom_objects: list[ObjectDef] = Field(default_factory=list)
    automations: list[AutomationDef] = Field(default_factory=list)

    @field_validator("custom_objects")
    @classmethod
    def _unique_object_api_names(cls, v: list[ObjectDef]) -> list[ObjectDef]:
        seen: set[str] = set()
        for o in v:
            if o.api_name in seen:
                raise ValueError(f"duplicate object api_name '{o.api_name}'")
            seen.add(o.api_name)
        return v

    @field_validator("automations")
    @classmethod
    def _unique_automation_api_names(
        cls, v: list[AutomationDef]
    ) -> list[AutomationDef]:
        seen: set[str] = set()
        for a in v:
            if a.api_name in seen:
                raise ValueError(f"duplicate automation api_name '{a.api_name}'")
            seen.add(a.api_name)
        return v

    def iter_fields(self) -> list[tuple[ObjectDef, FieldCategory, FieldDef]]:
        """Flatten all (object, category, field) triples for downstream processing."""
        out: list[tuple[ObjectDef, FieldCategory, FieldDef]] = []
        for obj in self.custom_objects:
            for cat in obj.field_categories:
                for f in cat.fields:
                    out.append((obj, cat, f))
        return out


def build_payload_meta(model: BaseModel) -> dict[str, Any]:
    """Dump a Pydantic model to a plain dict suitable for API payload building."""
    return model.model_dump(mode="json", exclude_none=True)


__all__ = [
    "ApiName",
    "FieldType",
    "RelationType",
    "RelationCardinality",
    "RelationConfig",
    "MoneyConfig",
    "RatingConfig",
    "DecimalConfig",
    "PhoneConfig",
    "StatusOption",
    "FieldDef",
    "FieldCategory",
    "ObjectType",
    "PipelineStageSpec",
    "PipelineDef",
    "ObjectDef",
    "TeamMemberConfig",
    "AutomationVariableConfig",
    "VariableSourceConfig",
    "LlmDestinationConfig",
    "CodeStepInputConfig",
    "CodeStepOutputConfig",
    "TriggerFieldUpdatedConfig",
    "TriggerContactTagAddedRemovedConfig",
    "TriggerActivityLoggedConfig",
    "TriggerFormSubmittedConfig",
    "TriggerSurveySubmittedConfig",
    "TriggerEmailDeliveredConfig",
    "TriggerOnOrAroundDateConfig",
    "TriggerWebsiteVisitedConfig",
    "TriggerEmailInteractionConfig",
    "TriggerEmailReceivedFromContactConfig",
    "TriggerStageUpdatedConfig",
    "TriggerEmailLinkClickedConfig",
    "TriggerScheduleConfig",
    "TriggerScheduledActivityOverdueConfig",
    "TriggerNewEntityCreatedConfig",
    "TriggerWebhookConfig",
    "TriggerManualConfig",
    "AutomationTriggerDef",
    "StepConditionConfig",
    "StepDelayConfig",
    "StepGoalConfig",
    "ActionChangeFieldValueConfig",
    "ActionSendEmailConfig",
    "ActionSendRelatedContactEmailConfig",
    "ActionNotifyMemberViaEmailConfig",
    "ActionNotifyMemberViaTextConfig",
    "ActionSendTextConfig",
    "ActionSendRelatedContactTextConfig",
    "ActionHttpRequestConfig",
    "ActionCodeStepConfig",
    "ActionPluginCodeStepConfig",
    "ActionLlmCallConfig",
    "ActionAudioTranscriptionConfig",
    "ActionFileContentExtractionConfig",
    "ActionAssignTeamMemberConfig",
    "TagToAddConfig",
    "ActionChangeTagsConfig",
    "ActionScheduleActivityConfig",
    "ActionDeleteScheduledActivityConfig",
    "ActionSearchRecordsConfig",
    "ActionCreateRelatedEntityConfig",
    "ActionStartAutomationConfig",
    "ActionModifyAutomationConfig",
    "ActionModifyRelatedEntitiesConfig",
    "ActionModifyRelatedEntitiesAutomationConfig",
    "ActionUpdatePipelineStatusConfig",
    "ActionGoToAutomationStepConfig",
    "ActionInitializeVariableConfig",
    "ActionUpdateVariableConfig",
    "ActionMathOperatorConfig",
    "ActionArchiveRecordConfig",
    "ActionStopExecutionConfig",
    "ActionRequestInfoViaTextConfig",
    "AutomationStepDef",
    "AutomationDef",
    "SolutionSpec",
    "build_payload_meta",
    "DashletDef",
    "DashboardDef",
    "FilterGroupDef",
    "QuickFilterDef",
    "ColumnTemplateDef",
    "LayoutDef",
    "ColumnPreset",
    "COLUMN_FRACTIONS",
    "TextBlockDef",
    "ImageBlockDef",
    "ButtonBlockDef",
    "DividerBlockDef",
    "BlockDef",
    "CellDef",
    "RowDef",
    "SectionDef",
    "EmailTemplateDef",
    "ActivityFieldType",
    "AssociationMode",
    "ActivityFieldDef",
    "ActivityDef",
    "FormFieldType",
    "FormFieldDef",
    "FormDef",
    "ExecutionVariableDataType",
    "ExecutionVariableDef",
    "NoMatchAction",
    "SingleMatchAction",
    "MultipleMatchAction",
    "MatchArchiveAction",
    "ConflictResolution",
    "MatchingRuleDef",
    "FieldMappingRuleDef",
    "LoadStepDef",
    "SmartConnectorFlowDef",
]

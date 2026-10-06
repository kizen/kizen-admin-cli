"""Automation trigger config models + AutomationTriggerDef."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TriggerFieldUpdatedConfig(BaseModel):
    """Config for `trigger_type: field_updated`."""

    model_config = ConfigDict(extra="allow")

    field_ref: str = Field(
        description="'object.field' reference, resolved to field UUID at apply time."
    )
    fire_on_create: bool = True
    from_match_mode: str | None = None
    to_match_mode: str | None = None
    from_value: dict[str, Any] | None = None
    to_value: dict[str, Any] | None = None


class TriggerContactTagAddedRemovedConfig(BaseModel):
    """Config for `trigger_type: contact_tag_added_removed`."""

    model_config = ConfigDict(extra="allow")

    tag_name: str = Field(
        description=(
            "Tag display name. NOT resolved by the builder — this trigger "
            "type isn't wired into _TRIGGER_BUILDERS yet, and there is no "
            "`kizen lookup` command (never built). No CLI-driven way to "
            "list tags today either."
        )
    )
    tag_operation: Literal["added", "removed"]


class TriggerActivityLoggedConfig(BaseModel):
    """Config for `trigger_type: activity_logged`."""

    model_config = ConfigDict(extra="allow")

    activity_type_id: str | None = Field(
        default=None,
        description=(
            "Activity type UUID — find it with `kizen activities list`. "
            "There is no name lookup."
        ),
    )


class TriggerFormSubmittedConfig(BaseModel):
    """Config for `trigger_type: form_submitted`.

    WIRE SHAPE UNVERIFIED — see the caveat on `_trigger_form_submitted` in
    tools/planners/automations.py. `form_name` is resolved live (api_name,
    UUID, or display name, via `kizen forms list`'s matching) rather than
    through a `kizen lookup` command, which doesn't exist.
    """

    model_config = ConfigDict(extra="allow")

    form_name: str | None = Field(
        default=None,
        description="Form api_name/name/UUID, resolved live at apply time.",
    )
    form_id: str | None = Field(
        default=None, description="Direct UUID fallback for form_name."
    )


class TriggerSurveySubmittedConfig(BaseModel):
    """Config for `trigger_type: survey_submitted`.

    WIRE SHAPE UNVERIFIED — see the caveat on `_trigger_survey_submitted` in
    tools/planners/automations.py. `survey_name` is resolved live (api_name,
    UUID, or display name, via `kizen surveys list`'s matching) rather than
    through a `kizen lookup` command, which doesn't exist.
    """

    model_config = ConfigDict(extra="allow")

    survey_name: str | None = Field(
        default=None,
        description="Survey api_name/name/UUID, resolved live at apply time.",
    )
    survey_id: str | None = Field(
        default=None, description="Direct UUID fallback for survey_name."
    )


class TriggerEmailDeliveredConfig(BaseModel):
    """Config for `trigger_type: email_delivered`.

    NOT YET WIRED — this trigger_type isn't registered in _TRIGGER_BUILDERS
    (tools/planners/automations.py), so authoring one hits PlanError today.
    """

    model_config = ConfigDict(extra="allow")

    email_template_name: str = Field(
        description=(
            "Email template name. There is no `kizen lookup` command (never "
            "built); use `kizen messages templates list` to find the "
            "template, once this trigger type is wired."
        )
    )


class TriggerOnOrAroundDateConfig(BaseModel):
    """Config for `trigger_type: on_or_around_date`.

    The date field is identified either by ``field_ref`` ("object.field"
    portable form) or ``field_id`` (raw UUID, env-bound). Round-tripped live
    payloads keep the bare field UUID under ``field_id``.
    """

    model_config = ConfigDict(extra="allow")

    field_ref: str | None = Field(
        default=None,
        description="'object.field' reference. Resolved to UUID at apply time.",
    )
    field_id: str | None = Field(
        default=None,
        description="Raw field UUID. Used when round-tripping live data.",
    )
    date_offset: str | None = Field(
        default=None,
        description="DateOffsetEnum value (e.g. 'on_day_and_time', 'days_before', 'days_after').",
    )
    time: str | None = Field(default=None, description="Time of day in HH:MM format.")
    period: Literal["am", "pm", "AM", "PM"] | None = Field(
        default=None,
        description=(
            "Either case is accepted here; the builder lowercases it before "
            "sending, since the live API rejects uppercase 'AM'/'PM'."
        ),
    )
    date_offset_days: int | None = Field(default=None, ge=0)
    # Live data sometimes has null instead of a bool here.
    skip_non_working_days: bool | None = None
    every_year: bool = False


class TriggerWebsiteVisitedConfig(BaseModel):
    """Config for `trigger_type: website_visited`."""

    model_config = ConfigDict(extra="allow")

    url: str


class TriggerEmailInteractionConfig(BaseModel):
    """Config for `trigger_type: email_interaction`.

    NOT YET WIRED — this trigger_type isn't registered in _TRIGGER_BUILDERS
    (tools/planners/automations.py), so authoring one hits PlanError today.
    """

    model_config = ConfigDict(extra="allow")

    email_template_name: str = Field(
        description=(
            "Email template name. There is no `kizen lookup` command (never "
            "built); use `kizen messages templates list` to find the "
            "template, once this trigger type is wired."
        )
    )
    name: str = Field(
        description="Interaction type (e.g. 'opened', 'clicked', 'unsubscribed')."
    )


class TriggerEmailReceivedFromContactConfig(BaseModel):
    """Config for `trigger_type: email_received_from_contact` (no required fields)."""

    model_config = ConfigDict(extra="allow")


class TriggerStageUpdatedConfig(BaseModel):
    """Config for `trigger_type: stage_updated`.

    Note: `from_stage_id` and `to_stage_id` take Kizen UUIDs directly.
    Stage name resolution requires knowing the pipeline object and is not
    currently supported via named references.
    """

    model_config = ConfigDict(extra="allow")

    stage_field_ref: str | None = Field(
        default=None,
        description="'object.field' reference to the pipeline status field. Resolved to UUID at apply time.",
    )
    stage_field_id: str | None = Field(
        default=None, description="Direct UUID fallback for the stage field."
    )
    from_stage_id: str | None = Field(
        default=None, description="UUID of the 'from' pipeline stage."
    )
    to_stage_id: str | None = Field(
        default=None, description="UUID of the 'to' pipeline stage."
    )
    fire_on_create: bool = False


class TriggerEmailLinkClickedConfig(BaseModel):
    """Config for `trigger_type: email_link_clicked`.

    NOT YET WIRED — this trigger_type isn't registered in _TRIGGER_BUILDERS
    (tools/planners/automations.py), so authoring one hits PlanError today.
    """

    model_config = ConfigDict(extra="allow")

    email_template_name: str = Field(
        description=(
            "Email template name. There is no `kizen lookup` command (never "
            "built); use `kizen messages templates list` to find the "
            "template, once this trigger type is wired."
        )
    )


class TriggerScheduleConfig(BaseModel):
    """Config for `trigger_type: schedule`."""

    model_config = ConfigDict(extra="allow")

    rrule: str = Field(
        min_length=1,
        description="RFC 5545 RRULE string (e.g. 'FREQ=DAILY;BYHOUR=9;BYMINUTE=0').",
    )
    is_advanced: bool = False


class TriggerScheduledActivityOverdueConfig(BaseModel):
    """Config for `trigger_type: scheduled_activity_overdue`."""

    model_config = ConfigDict(extra="allow")

    filter_config: dict[str, Any] | None = None


class TriggerNewEntityCreatedConfig(BaseModel):
    """Config for `trigger_type: new_entity_created`."""

    model_config = ConfigDict(extra="allow")

    action: Literal["create_only", "create_and_unarchive", "unarchive_only"] = (
        "create_only"
    )


class TriggerWebhookConfig(BaseModel):
    """Config for `trigger_type: webhook`."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="Webhook display name.")
    http_method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "POST"
    content_type: str | None = None
    sample_post_body: str | None = None
    extract_raw_body_content: bool = False
    extract_url_query_string: bool = True
    extractors: list[dict[str, Any]] = Field(default_factory=list)


class TriggerManualConfig(BaseModel):
    """Config for `trigger_type: manual` — fires when a user manually starts
    the automation against a record from the UI. The wire format is an empty
    object (`trigger_manual: {}`); the Kizen UI auto-includes a Manual trigger
    on every automation by convention. Spec authors should add it explicitly
    if they want a manual run option alongside other triggers.
    """

    model_config = ConfigDict(extra="allow")


_TRIGGER_TYPE_TO_CONFIG_FIELD: dict[str, str | None] = {
    "field_updated": "trigger_field_updated",
    "contact_tag_added_removed": "trigger_contact_tag_added_removed",
    "activity_logged": "trigger_activity_logged",
    "form_submitted": "trigger_form_submitted",
    "survey_submitted": "trigger_survey_submitted",
    "email_delivered": "trigger_email_delivered",
    "on_or_around_date": "trigger_on_or_around_date",
    "website_visited": "trigger_website_visited",
    "email_interaction": "trigger_email_interaction",
    "email_received_from_contact": "trigger_email_received_from_contact",
    "stage_updated": "trigger_stage_updated",
    "email_link_clicked": "trigger_email_link_clicked",
    "schedule": "trigger_schedule",
    "scheduled_activity_overdue": "trigger_scheduled_activity_overdue",
    "new_entity_created": "trigger_new_entity_created",
    "webhook": "trigger_webhook",
    "manual": "trigger_manual",
}


_TRIGGERS_REQUIRING_CONFIG = {
    "field_updated",
    "contact_tag_added_removed",
    "activity_logged",
    "form_submitted",
    "survey_submitted",
    "email_delivered",
    "on_or_around_date",
    "website_visited",
    "email_interaction",
    "stage_updated",
    "email_link_clicked",
    "schedule",
    "webhook",
    "new_entity_created",
}


class AutomationTriggerDef(BaseModel):
    """A trigger on an automation. Exactly one type-specific config block
    matching `trigger_type` must be present for triggers that require it."""

    model_config = ConfigDict(extra="forbid")

    trigger_type: str
    id: str | None = Field(
        default=None,
        description=(
            "This trigger's server-assigned UUID, if known (e.g. copied "
            "from `kizen automations show`). When set, it's echoed back on "
            "PUT so the trigger keeps its identity instead of the server "
            "assigning a fresh id. Omit for a new trigger; the server "
            "assigns one."
        ),
    )
    order: int | None = None
    description: str | None = None
    user_description: str = ""
    skip_non_working_days: bool | None = None
    should_skip_execution: bool = False

    # Type-specific configs (only the one matching trigger_type should be set)
    trigger_field_updated: TriggerFieldUpdatedConfig | None = None
    trigger_contact_tag_added_removed: TriggerContactTagAddedRemovedConfig | None = None
    trigger_activity_logged: TriggerActivityLoggedConfig | None = None
    trigger_form_submitted: TriggerFormSubmittedConfig | None = None
    trigger_survey_submitted: TriggerSurveySubmittedConfig | None = None
    trigger_email_delivered: TriggerEmailDeliveredConfig | None = None
    trigger_on_or_around_date: TriggerOnOrAroundDateConfig | None = None
    trigger_website_visited: TriggerWebsiteVisitedConfig | None = None
    trigger_email_interaction: TriggerEmailInteractionConfig | None = None
    trigger_email_received_from_contact: (
        TriggerEmailReceivedFromContactConfig | None
    ) = None
    trigger_stage_updated: TriggerStageUpdatedConfig | None = None
    trigger_email_link_clicked: TriggerEmailLinkClickedConfig | None = None
    trigger_schedule: TriggerScheduleConfig | None = None
    trigger_scheduled_activity_overdue: TriggerScheduledActivityOverdueConfig | None = (
        None
    )
    trigger_new_entity_created: TriggerNewEntityCreatedConfig | None = None
    trigger_webhook: TriggerWebhookConfig | None = None
    trigger_manual: TriggerManualConfig | None = None

    @model_validator(mode="after")
    def _validate_trigger_config(self) -> Self:
        valid_types = set(_TRIGGER_TYPE_TO_CONFIG_FIELD.keys())
        if self.trigger_type not in valid_types:
            raise ValueError(
                f"unknown trigger_type '{self.trigger_type}'. "
                f"Valid values: {sorted(valid_types)}"
            )
        config_field = _TRIGGER_TYPE_TO_CONFIG_FIELD[self.trigger_type]
        if (
            config_field
            and self.trigger_type in _TRIGGERS_REQUIRING_CONFIG
            and getattr(self, config_field) is None
        ):
            raise ValueError(
                f"trigger_type '{self.trigger_type}' requires a '{config_field}' block"
            )
        return self

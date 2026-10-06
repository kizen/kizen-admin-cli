"""Automation step config models: messaging, tagging, team-member actions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kizen_builder.models.spec.automations_shared import TeamMemberConfig


class ActionSendEmailConfig(BaseModel):
    """Config for `step_type: send_email`.

    NOT YET WIRED — this step_type isn't registered in _STEP_BUILDERS
    (tools/planners/automations.py); see send_related_contact_email for the
    supported way to send an email from an automation today.
    """

    model_config = ConfigDict(extra="allow")

    email_template_name: str | None = Field(
        default=None,
        description=(
            "Email template name. There is no `kizen lookup` command (never "
            "built); use `kizen messages templates list` to find the "
            "template, once this step type is wired."
        ),
    )
    email_template_id: str | None = Field(
        default=None, description="Direct UUID fallback for email_template_name."
    )
    cc_team_member: TeamMemberConfig | None = None


class ActionSendRelatedContactEmailConfig(BaseModel):
    """Config for `step_type: send_related_contact_email`."""

    model_config = ConfigDict(extra="allow")

    email: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "AutomationMessage id (bare UUID or {id: uuid}) — an "
            "AutomationMessage, not an email template. Use `kizen messages "
            "create <automation> --template <name>` to create one from a "
            "template, then pass its UUID here."
        ),
    )


class ActionNotifyMemberViaEmailConfig(BaseModel):
    """Config for `step_type: notify_member_via_email`.

    Confirmed against the live OpenAPI schema
    (`WriteActionNotifyMemberViaEmailRequest`): unlike
    `send_related_contact_email`, this step has NO inline subject/body —
    the email content lives entirely on a referenced message resource
    (`kizen messages templates` UUID), pointed to by the bare `id` field.
    """

    model_config = ConfigDict(extra="allow")

    team_member: TeamMemberConfig
    id: str | None = Field(
        default=None,
        description="UUID of the email message/template to send (see `kizen messages templates`).",
    )
    email_template_id: str | None = Field(default=None, description="Alias for `id`.")
    cc_team_member: TeamMemberConfig | None = None


class ActionNotifyMemberViaTextConfig(BaseModel):
    """Config for `step_type: notify_member_via_text`.

    Confirmed against the live OpenAPI schema
    (`ActionNotifyMemberViaTextRequest`): content is inline (`content` /
    `html_content`); `base_message_id` optionally bases it on an existing
    message resource.
    """

    model_config = ConfigDict(extra="allow")

    team_member: TeamMemberConfig
    content: str | None = None
    html_content: str | None = None
    base_message_id: str | None = None
    message_template_id: str | None = Field(
        default=None, description="Alias for base_message_id."
    )


class ActionSendTextConfig(BaseModel):
    """Config for `step_type: send_text`."""

    model_config = ConfigDict(extra="allow")

    field_ref: str | None = Field(
        default=None,
        description="'object.field' for the phone number field. Resolved to UUID at apply time.",
    )
    phone_number_field_id: str | None = None
    message: str | None = None
    html_message: str | None = None


class ActionSendRelatedContactTextConfig(BaseModel):
    """Config for `step_type: send_related_contact_text`."""

    model_config = ConfigDict(extra="allow")

    relationship_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the relationship field pointing to the contact.",
    )
    relationship_field_id: str | None = None
    message: str | None = None
    html_message: str | None = None


class ActionRequestInfoViaTextConfig(BaseModel):
    """Config for `step_type: request_info_via_text`."""

    model_config = ConfigDict(extra="allow")

    field_ref: str | None = Field(
        default=None,
        description="'object.field' for the phone number field. Resolved to UUID at apply time.",
    )
    phone_number_field_id: str | None = None
    message: str | None = None
    html_message: str | None = None
    response_field_ref: str | None = Field(
        default=None,
        description="'object.field' to store the text response. Resolved to UUID at apply time.",
    )
    response_field_id: str | None = None
    timeout_minutes: int | None = Field(default=None, ge=1)


class ActionAssignTeamMemberConfig(BaseModel):
    """Config for `step_type: assign_team_member`.

    ``type`` is its own, smaller enum — NOT the one the `team_member`
    selector on notify/send steps uses, and not the 11-value one
    `schedule_activity.assigned_to` uses. Confirmed live 2026-08-06 against
    `AssignTeamMemberWriteTypeEnum` and a create per value: `owner`,
    `last_active`, `last_active_role` and `employees` all 400 with
    *"… is not a valid choice"* on this step.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal[
        "team_member",
        "round_robin_all",
        "round_robin_role",
        "round_robin_team_members",
        "team_selector_field",
        "related_team_selector_field",
    ]
    role_id: str | None = Field(
        default=None, description="Required for `round_robin_role`."
    )
    employee_id: str | None = Field(
        default=None,
        description=(
            "Required for `team_member` — the singular key, not "
            "`employee_ids` (which 400s with 'employee_id … is required')."
        ),
    )
    employee_ids: list[str] = Field(
        default_factory=list, description="The pool for `round_robin_team_members`."
    )


class TagToAddConfig(BaseModel):
    """A tag to apply (referenced by name for portability)."""

    model_config = ConfigDict(extra="allow")

    tag_name: str = Field(
        description=(
            "Tag display name. NOT resolved by the builder — `change_tags` "
            "isn't registered in _STEP_BUILDERS yet (unsupported step type), "
            "and there is no `kizen lookup` command or tags-listing command."
        )
    )


class ActionChangeTagsConfig(BaseModel):
    """Config for `step_type: change_tags`.

    NOT YET WIRED — this step_type isn't registered in _STEP_BUILDERS
    (tools/planners/automations.py), so authoring one hits PlanError today.
    """

    model_config = ConfigDict(extra="allow")

    tags_to_add: list[TagToAddConfig] = Field(default_factory=list)
    tags_to_remove: list[str] = Field(
        default_factory=list,
        description=(
            "Tag names to remove. NOT resolved by the builder — see "
            "TagToAddConfig.tag_name."
        ),
    )


class ActionScheduleActivityConfig(BaseModel):
    """Config for `step_type: schedule_activity`.

    Field names here match what the builder (`_step_schedule_activity` in
    `tools/planners/automations.py`) actually reads — confirmed against a
    live step's wire format, since the API's documented shape doesn't match.
    """

    model_config = ConfigDict(extra="allow")

    activity_type: dict[str, Any] | None = Field(
        default=None,
        description="Expanded {id, ...} activity type, as returned by a live read.",
    )
    activity_type_id: str | None = Field(
        default=None,
        description="Activity type UUID. Wire key for authoring from scratch.",
    )
    schedule: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Schedule detail block, e.g. {'type': 'on_or_around_date', "
            "'on_or_around_date': {'date_offset': ..., 'date_offset_days': ...}}. "
            "NOTE: date_offset_days must be >= 1 live even under date_offset "
            "'on_day' — 0 is rejected ('Ensure this value is greater than or "
            "equal to 1'), despite 'on_day' reading like same-day/zero-offset."
        ),
    )
    notifications: list[Any] | dict[str, Any] | None = Field(
        default=None,
        description=(
            "A live read returns a list (commonly `[]`); accept a dict too "
            "in case a non-empty shape ever surfaces, since the exact "
            "non-empty wire shape is unconfirmed."
        ),
    )
    assigned_to: TeamMemberConfig | None = Field(
        default=None,
        description=(
            "Who the activity is assigned to. Unlike assign_team_member/"
            "notify_member_via_email (which use 'type'), this step's wire "
            "shape uses 'assignment_type' for the same selector — either "
            "spelling is accepted here and normalized at apply time."
        ),
    )
    association_configs: list[Any] = Field(
        default_factory=list,
        description=(
            "Per-object record association for activity types tied to more "
            "than one custom object — one entry per ADDITIONALLY associated "
            "object (not the automation's own target_object — the server "
            "auto-adds a context_record entry for that one; don't send it "
            "yourself, and don't count it when sizing this list). Author "
            "shape: {'object': api_name, 'source': 'none'|'context_record'|"
            "'related_field'|'record_variable'|'variable_related_field', "
            "'relationship_field_ref': 'object.field' (for source="
            "'related_field'), 'automation_variable': name (for source="
            "'record_variable'/'variable_related_field')}. Confirmed live "
            "(2026-07-22) against the public `/api/docs/schema` "
            "`ScheduleActivityAssociationConfigRequest` — 'context_record' "
            "is only valid when 'object' IS the automation's target_object "
            '(400s otherwise: "context_record custom_object_id must match '
            "the... workflow's custom object\"); 'related_field' additionally "
            "requires a relationship field that exists ON THE ASSOCIATED "
            "OBJECT itself, not on the automation's target_object — exact "
            "resolution rules unconfirmed, see `kizen docs show reference` "
            "'schedule_activity association_configs'. A live read's raw wire "
            "dicts ({'custom_object': {id,...}, 'association_source', "
            "'relationship_field', 'automation_variable'}) are also accepted "
            "as-is — note those READ key names differ from the WRITE keys "
            "(`custom_object_id`/`relationship_field_id`/"
            "`automation_variable_name`), which this field's builder "
            "translates automatically. Replaces the non-functional "
            "`additional_association_relationship_fields` key from an "
            "earlier, unconfirmed guess at this step's wire shape — that key "
            "silently no-op'd."
        ),
    )
    note: str | None = None


class ActionDeleteScheduledActivityConfig(BaseModel):
    """Config for `step_type: delete_scheduled_activity`."""

    model_config = ConfigDict(extra="allow")

    filter_config: dict[str, Any] | None = None

"""Automation step config models: HTTP, code execution, LLM/AI steps."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kizen_builder.models.spec.automations_shared import (
    CodeStepInputConfig,
    CodeStepOutputConfig,
    LlmDestinationConfig,
    PluginCodeStepInputConfig,
)


class ActionHttpRequestConfig(BaseModel):
    """Config for `step_type: http_request`."""

    model_config = ConfigDict(extra="allow")

    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"]
    url: str
    html_url: str | None = None
    content_type: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    html_body: str | None = None
    encode_merge_fields_in_body: bool = False


class ActionCodeStepConfig(BaseModel):
    """Config for `step_type: code_step`.

    Note: the wire field for the code is `script`, not `code`.
    This model uses `script` to match the API.
    """

    model_config = ConfigDict(extra="allow")

    script: str = Field(
        description="Python code to execute. Use `inputs` dict for input values and `outputs` dict to return results."
    )
    runtime: Literal["python-3-13", "python-3-12"] = "python-3-13"
    inputs: list[CodeStepInputConfig] = Field(default_factory=list)
    outputs: list[CodeStepOutputConfig] = Field(default_factory=list)
    # Secret NAMES (env-specific bindings; the target env must have a secret
    # configured under the same name). Accepts either bare strings or
    # ``{"name": "..."}`` dicts; the planner normalizes to the wire form.
    secrets: list[Any] = Field(default_factory=list)


class ActionPluginCodeStepConfig(BaseModel):
    """Config for `step_type: plugin_code_step`: run a step an installed
    plugin ships.

    The step is bound by api_name, not by a plugin-app id. Inputs and outputs
    are shaped as for `code_step`; inputs can also be
    ``business_plugin_config``.
    """

    model_config = ConfigDict(extra="allow")

    plugin_app_api_name: str = Field(
        min_length=1,
        description=(
            "The installed plugin's api_name, as `business-plugin-apps` lists "
            "it. A preview install is `<api_name>_preview_<branch>`."
        ),
    )
    action_step_api_name: str = Field(
        min_length=1,
        description="The step's `action_step_api_name` in the plugin's manifest.",
    )
    notify_plugin_developer_on_failure: bool = False
    preview_user_id: str | None = Field(
        default=None,
        description=(
            "The user whose preview of a business app the step runs. Null runs "
            "the installed version. On update, a step whose id matches keeps "
            "its live value when the spec leaves this out."
        ),
    )
    inputs: list[PluginCodeStepInputConfig] = Field(default_factory=list)
    outputs: list[CodeStepOutputConfig] = Field(default_factory=list)
    # Secret names, as for code_step: bare strings or {"name": ...}.
    secrets: list[Any] = Field(default_factory=list)


def _check_destinations(block: Any) -> Any:
    """The server 400s more than one destination on a step that isn't
    advanced: "Multiple destinations are not allowed for non-advanced LLM
    call actions" (confirmed live 2026-11-17 on all three LLM steps)."""
    if len(block.destinations) > 1 and not block.is_advanced:
        raise ValueError("more than one destination needs is_advanced: true")
    return block


_IS_ADVANCED_DESCRIPTION = "Must be true for more than one destination."
_HTML_PROMPT_DESCRIPTION = "Leave it out: the CLI derives it from prompt."

# The data_type Kizen's builder saves on each step; it has no control for it.
# An extraction saved as 'doc' fails when it runs on a PDF (confirmed live
# 2026-11-25), so the CLI always sends these and refuses any other value.
FIXED_DATA_TYPES = {"file_content_extraction": "image", "audio_transcription": "audio"}


def _check_data_type(value: str | None, step_type: str, why: str = "") -> str | None:
    fixed = FIXED_DATA_TYPES[step_type]
    if value is not None and value != fixed:
        raise ValueError(
            f"data_type '{value}' isn't one the builder uses: {step_type} always "
            f"sends '{fixed}'{why}. Leave data_type out; the CLI sends '{fixed}'."
        )
    return value


class ActionLlmCallConfig(BaseModel):
    """Config for `step_type: call_llm`."""

    model_config = ConfigDict(extra="allow")

    model_name: str = Field(
        description=(
            "A model_value from `kizen automations llm-models`, the live "
            "catalog of what this business has enabled, where each row is "
            "paired with the business_plugin_app_id that runs it. With "
            "business_plugin_app_id omitted, the plan refuses a model the "
            "catalog doesn't list. 'gemini/...' and "
            "'openai/...' models need the "
            "provider prefix; Claude models are bare, e.g. "
            "'claude-3-7-sonnet-20250219' (no 'claude/' prefix)."
        )
    )
    prompt: str | None = None
    html_prompt: str | None = Field(default=None, description=_HTML_PROMPT_DESCRIPTION)
    destinations: list[LlmDestinationConfig] = Field(default_factory=list)
    is_advanced: bool = Field(default=False, description=_IS_ADVANCED_DESCRIPTION)
    business_plugin_app_id: str | None = Field(
        default=None,
        description=(
            "Optional: the CLI fills it from `kizen automations llm-models` "
            "for model_name, Kizen AI models included. It is the business's "
            "installed instance of that provider's plugin app, not the "
            "plugin catalog's app-definition id from GET "
            "/api/external-integrations/bootstrap (identical across every "
            "business and rejected with 'Business plugin app not found'). "
            "An id that belongs to a different provider than model_name's "
            "is refused."
        ),
    )
    merge_field_validation: (
        Literal["error_if_required", "default_to_unknown", "blank"] | None
    ) = Field(
        default=None,
        description=(
            "How to handle a blank merge field in prompt/html_prompt. UI "
            "labels: 'error_if_required' = \"Error if any merge field is "
            "blank\" (the default for a new step; 'update' keeps the live "
            "value when unset), 'default_to_unknown' = "
            "\"Default to 'Unknown'\" (fixed literal, not configurable), "
            "'blank' = \"Leave Blank\". Same enum on file_content_extraction, "
            "audio_transcription, and a condition step's llm_decision block."
        ),
    )
    mcp_server: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "The MCP server whose tools the model may call: its api_name or "
            "UUID, from the Call LLM step's own server picker "
            "(GET /api/mcp-servers/step-options). Needs run_tools_as. Omitted "
            "on a new step, no server. On update, a step whose id matches "
            "keeps its live server and account unless the spec sets either; "
            "null removes both."
        ),
    )
    mcp_server_id: str | None = Field(
        default=None,
        description="The MCP server's UUID (the wire spelling of mcp_server).",
    )
    run_tools_as: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "The service account the MCP server's tools run as: its name or "
            "UUID, from the step's run-as picker "
            "(GET /api/automation2/automations/run-tools-as-options), which "
            "leaves out a plugin's own service account. Needs mcp_server."
        ),
    )
    run_tools_as_id: str | None = Field(
        default=None,
        description="The service account's UUID (the wire spelling of run_tools_as).",
    )
    max_sequential_turns: int | None = Field(
        default=None,
        ge=1,
        le=32767,
        description=(
            "How many model turns the step may take in a row. Default 25 on "
            "a new step; update keeps the live value when omitted."
        ),
    )
    tool_failure_policy: Literal["never_fail"] | None = Field(
        default=None,
        description=(
            "What a failed tool call does to the step. 'never_fail' (the "
            "default) is the only value Kizen accepts."
        ),
    )

    @model_validator(mode="after")
    def _many_destinations_are_advanced(self) -> Self:
        return _check_destinations(self)


class ActionAudioTranscriptionConfig(BaseModel):
    """Config for `step_type: audio_transcription`. See
    ActionLlmCallConfig.model_name for confirmed model_name values."""

    model_config = ConfigDict(extra="allow")

    model_name: str
    prompt: str | None = None
    html_prompt: str | None = Field(default=None, description=_HTML_PROMPT_DESCRIPTION)
    input_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the audio file field. Resolved to UUID at apply time.",
    )
    input_field_id: str | None = None
    input_value_source: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Read the file from a related record or a variable instead: "
            "{type: related_object_field, relationship_field (single-valued), "
            "field}, {type: automation_variable, variable: <name>} or "
            "{type: context_entity_field, field}. Fields take a UUID or "
            "'object.field'. Set this or input_field_ref, never both."
        ),
    )
    destinations: list[LlmDestinationConfig] = Field(default_factory=list)
    is_advanced: bool = Field(default=False, description=_IS_ADVANCED_DESCRIPTION)
    data_type: str | None = Field(
        default=None,
        description="Leave it out: the CLI sends 'audio', as the builder does.",
    )
    business_plugin_app_id: str | None = Field(
        default=None,
        description="See ActionLlmCallConfig.business_plugin_app_id.",
    )
    merge_field_validation: (
        Literal["error_if_required", "default_to_unknown", "blank"] | None
    ) = Field(
        default=None,
        description="See ActionLlmCallConfig.merge_field_validation.",
    )

    @field_validator("data_type")
    @classmethod
    def _data_type_is_audio(cls, value: str | None) -> str | None:
        return _check_data_type(value, "audio_transcription")

    @model_validator(mode="after")
    def _many_destinations_are_advanced(self) -> Self:
        return _check_destinations(self)


class ActionFileContentExtractionConfig(BaseModel):
    """Config for `step_type: file_content_extraction`. See
    ActionLlmCallConfig.model_name for confirmed model_name values."""

    model_config = ConfigDict(extra="allow")

    model_name: str
    prompt: str | None = None
    html_prompt: str | None = Field(default=None, description=_HTML_PROMPT_DESCRIPTION)
    input_field_ref: str | None = Field(
        default=None,
        description="'object.field' for the file field. Resolved to UUID at apply time.",
    )
    input_field_id: str | None = None
    input_value_source: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Read the file from a related record or a variable instead: "
            "{type: related_object_field, relationship_field (single-valued), "
            "field}, {type: automation_variable, variable: <name>} or "
            "{type: context_entity_field, field}. Fields take a UUID or "
            "'object.field'. Set this or input_field_ref, never both."
        ),
    )
    destinations: list[LlmDestinationConfig] = Field(default_factory=list)
    is_advanced: bool = Field(default=False, description=_IS_ADVANCED_DESCRIPTION)
    data_type: str | None = Field(
        default=None,
        description=(
            "Leave it out: the CLI sends 'image', as the builder does; it "
            "reads PDF, JPG, JPEG and PNG."
        ),
    )
    business_plugin_app_id: str | None = Field(
        default=None,
        description="See ActionLlmCallConfig.business_plugin_app_id.",
    )
    merge_field_validation: (
        Literal["error_if_required", "default_to_unknown", "blank"] | None
    ) = Field(
        default=None,
        description="See ActionLlmCallConfig.merge_field_validation.",
    )

    @field_validator("data_type")
    @classmethod
    def _data_type_is_image(cls, value: str | None) -> str | None:
        return _check_data_type(
            value,
            "file_content_extraction",
            ", for PDF, JPG, JPEG and PNG files alike, and a step saved as 'doc' "
            "fails when it runs",
        )

    @model_validator(mode="after")
    def _many_destinations_are_advanced(self) -> Self:
        return _check_destinations(self)

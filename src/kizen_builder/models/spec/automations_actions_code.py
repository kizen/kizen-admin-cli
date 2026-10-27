"""Automation step config models: HTTP, code execution, LLM/AI steps."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    inputs: list[PluginCodeStepInputConfig] = Field(default_factory=list)
    outputs: list[CodeStepOutputConfig] = Field(default_factory=list)
    # Secret names, as for code_step: bare strings or {"name": ...}.
    secrets: list[Any] = Field(default_factory=list)


class ActionLlmCallConfig(BaseModel):
    """Config for `step_type: call_llm`."""

    model_config = ConfigDict(extra="allow")

    model_name: str = Field(
        description=(
            "A free string, unvalidated client-side. Run `kizen automations "
            "llm-models` for the live catalog of what this business has "
            "enabled — each row's model_value is this field's value, paired "
            "with the business_plugin_app_id it needs (None for kizen/* "
            "native models). 'gemini/...' and 'openai/...' models need the "
            "provider prefix; Claude models are bare, e.g. "
            "'claude-3-7-sonnet-20250219' (no 'claude/' prefix)."
        )
    )
    prompt: str | None = None
    html_prompt: str | None = None
    destinations: list[LlmDestinationConfig] = Field(default_factory=list)
    is_advanced: bool = False
    business_plugin_app_id: str | None = Field(
        default=None,
        description=(
            "Required for any non-native model_name (anything not "
            "'kizen/...') — the business's installed instance of that "
            "provider's plugin app. Get it from `kizen automations "
            "llm-models`, NOT from GET /api/external-integrations/bootstrap "
            "(that returns the plugin catalog's app-definition id, which is "
            "identical across every business and is rejected with "
            "'Business plugin app not found')."
        ),
    )
    merge_field_validation: (
        Literal["error_if_required", "default_to_unknown", "blank"] | None
    ) = Field(
        default=None,
        description=(
            "How to handle a blank merge field in prompt/html_prompt. UI "
            "labels: 'error_if_required' = \"Error if any merge field is "
            "blank\" (server default when unset), 'default_to_unknown' = "
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
            "means no server, so an update detaches one set in the UI."
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
        description="How many model turns the step may take in a row. Default 25.",
    )
    tool_failure_policy: Literal["never_fail"] | None = Field(
        default=None,
        description=(
            "What a failed tool call does to the step. 'never_fail' (the "
            "default) is the only value Kizen accepts."
        ),
    )


class ActionAudioTranscriptionConfig(BaseModel):
    """Config for `step_type: audio_transcription`. See
    ActionLlmCallConfig.model_name for confirmed model_name values."""

    model_config = ConfigDict(extra="allow")

    model_name: str
    prompt: str | None = None
    html_prompt: str | None = None
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
    is_advanced: bool = False
    data_type: str | None = None
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


class ActionFileContentExtractionConfig(BaseModel):
    """Config for `step_type: file_content_extraction`. See
    ActionLlmCallConfig.model_name for confirmed model_name values."""

    model_config = ConfigDict(extra="allow")

    model_name: str
    prompt: str | None = None
    html_prompt: str | None = None
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
    is_advanced: bool = False
    data_type: str | None = None
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

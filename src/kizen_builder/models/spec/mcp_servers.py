"""MCP server spec: the tools a server serves and who can use it.

Every key is optional so an update spec can carry only what changes. A create
needs ``name`` and ``api_name``; the planner checks that. ``kizen mcp-servers
get --raw`` output is a valid spec once the planner drops its read-only keys.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

MCPServerApiName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z_][a-z0-9_]*$", min_length=2, max_length=64),
]
"""Kizen's own rule: lowercase letters, digits and underscores, not starting
with a digit. Unique per business and fixed once the server exists."""

_SHARING_KEYS = ("private", "all_team_members", "roles", "team_members")
_SHARING_KEYS_MSG = (
    "sharing_settings takes private, all_team_members, roles and team_members. "
    "Service accounts are shared as team members: put the account's id in "
    "team_members."
)


class GrantRef(BaseModel):
    """The read shape of a grant, `{id, display_name}`, as `get --raw` prints it.

    `id` is required: a grant without one would be dropped from the PATCH,
    revoking it without a word.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)


# A grant is a bare UUID (the write shape) or the read shape.
Grant = str | GrantRef


class SharingBuckets(BaseModel):
    """Grants per access level. A level left out grants nobody."""

    model_config = ConfigDict(extra="forbid")

    view: list[Grant] = Field(default_factory=list)
    edit: list[Grant] = Field(default_factory=list)
    admin: list[Grant] = Field(default_factory=list)


class MCPServerSharing(BaseModel):
    """The whole sharing block. Kizen rejects a partial one, so all four keys
    are required."""

    model_config = ConfigDict(extra="forbid")

    private: bool
    all_team_members: Literal[0, 1, 2, 3]
    roles: SharingBuckets
    team_members: SharingBuckets

    @model_validator(mode="before")
    @classmethod
    def _known_keys_only(cls, data: Any) -> Any:
        unknown = (
            sorted(set(data) - set(_SHARING_KEYS)) if isinstance(data, dict) else []
        )
        if unknown:
            raise PydanticCustomError(
                "sharing_settings_keys",
                "unknown key(s) {keys}. " + _SHARING_KEYS_MSG,
                {"keys": ", ".join(unknown)},
            )
        return data


class MCPServerDef(BaseModel):
    """One MCP server. ``tool_names`` come from ``kizen mcp-servers tools``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    api_name: MCPServerApiName | None = None
    description: str | None = None
    tool_names: list[str] | None = None
    is_enabled: bool | None = None
    is_default_for_business: bool | None = None
    sharing_settings: MCPServerSharing | None = None

    @model_validator(mode="before")
    @classmethod
    def _null_only_clears_description(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None and key != "description" and key in cls.model_fields:
                    raise PydanticCustomError(
                        "null_value",
                        "{key} can't be null; leave it out to keep the current value.",
                        {"key": key},
                    )
        return data

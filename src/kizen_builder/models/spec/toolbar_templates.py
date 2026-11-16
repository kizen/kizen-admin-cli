"""Toolbar templates: a named, ordered toolbar (the bar across the top of the page)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ENTRY_KINDS = ("object", "plugin", "url", "builtin", "category", "raw")


class ToolbarEntryDef(BaseModel):
    """One toolbar entry. Exactly one kind key is set:

    - ``object``: an object api_name (``client_client``/``contacts`` is the
      built-in Contacts entry).
    - ``plugin`` + ``page``: an installed plugin's (or business app's) toolbar
      page or toolbar item. Always built from the installed plugin.
    - ``url`` + ``label``: a custom link.
    - ``builtin``: a Kizen item such as ``dashboard`` (``default__dashboard``).
    - ``category`` + ``entries``: a top-level group. A built-in category id
      (``data``, ``platform``, ``help``, ``recent_records``, ``my_account``)
      is that category; any other name is a new one.
    - ``raw``: one wire entry, passed through (needs ``id`` and ``type``).
    """

    model_config = ConfigDict(extra="forbid")

    object: str | None = None
    plugin: str | None = None
    page: str | None = None
    url: str | None = None
    builtin: str | None = None
    category: str | None = None
    entries: list[ToolbarEntryDef] | None = None
    raw: dict[str, Any] | None = None

    label: str | None = None
    icon: str | dict[str, Any] | None = Field(
        default=None,
        description="An icon name, or the UI's {label, value, className} dict.",
    )
    color: str | None = None
    width: Literal[50, 100] | None = Field(
        default=None, description="widthPercent: 50 (half row) or 100."
    )

    @model_validator(mode="after")
    def _one_kind(self) -> ToolbarEntryDef:
        kinds = [k for k in ENTRY_KINDS if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(
                f"an entry needs exactly one of {', '.join(ENTRY_KINDS)}; "
                f"got {kinds or 'none'}"
            )
        kind = kinds[0]
        if (self.plugin is None) != (self.page is None):
            raise ValueError("plugin and page go together")
        if self.entries is not None and kind != "category":
            raise ValueError("only a category takes entries")
        if kind == "url" and not self.label:
            raise ValueError("a url entry needs a label")
        if kind == "raw":
            raw = self.raw or {}
            if not raw.get("id") or not raw.get("type"):
                raise ValueError("a raw entry needs 'id' and 'type'")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in ENTRY_KINDS if getattr(self, k) is not None)


class ToolbarTemplateDef(BaseModel):
    """A toolbar template. ``entries`` or ``configuration_json``, not both.

    ``name`` is required on create. On update, a key left out keeps its live
    value; ``entries``/``configuration_json`` replace the whole list.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None, description="Ignored. `get --json` carries it."
    )
    name: str | None = None
    entries: list[ToolbarEntryDef] | None = None
    configuration_json: Any = Field(
        default=None,
        description="Raw wire entries (a list of {id, type, ...}), instead of "
        "entries. Checked at plan time.",
    )
    sharing_settings: dict[str, Any] | None = None
    owner: str | None = None

    @model_validator(mode="after")
    def _entries_or_config(self) -> ToolbarTemplateDef:
        if self.entries is not None and self.configuration_json is not None:
            raise ValueError("pass entries or configuration_json, not both")
        return self

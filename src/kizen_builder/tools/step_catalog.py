"""What a spec author needs to write one automation step or trigger, with no
live environment: the config block, its fields and choices, an entry to copy,
and a whole automation to start from.

Everything here derives from the planner registries and the spec models, so
it can't describe a type the CLI doesn't wire or a field the model doesn't
have. A type with choices that change the block's shape adds a
:class:`Guide`: hand-written blocks, one per shape, and the keys each choice
needs. `tests/test_step_catalog.py` runs every block through the model.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from kizen_builder.models.spec.automations import AutomationStepDef
from kizen_builder.models.spec.automations_triggers import (
    AutomationTriggerDef,
    trigger_config_field,
)
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    KNOWN_ENUM_CHOICES,
    KNOWN_ENUM_CHOICES_TRIGGERS,
    STEP_PURPOSES,
    TRIGGER_PURPOSES,
    KnownChoices,
    _block_field_for,
)

# Step types that take no config block are wired in `_build_step_payload`, not
# in `_STEP_BUILDERS`.
STEP_TYPES: tuple[str, ...] = tuple(sorted({*_STEP_BUILDERS, *_CONFIGLESS_STEP_TYPES}))
TRIGGER_TYPES: tuple[str, ...] = tuple(sorted(_TRIGGER_BUILDERS))

# Names agents reach for that aren't wired, and the wired types they mean.
# `step-types show` takes a name with one target as that type.
_EMAIL = ("notify_member_via_email", "send_related_contact_email")
_TEXT = ("notify_member_via_text", "send_related_contact_text")
ALIASES: dict[str, tuple[str, ...]] = {
    "ai": ("call_llm",),
    "ai_step": ("call_llm",),
    "llm": ("call_llm",),
    "set_field": ("change_field_value",),
    "update_field": ("change_field_value",),
    "update_record": ("change_field_value",),
    "wait": ("delay",),
    "create_activity": ("schedule_activity",),
    "archive_entity": ("archive_record",),
    "merge": ("merge_branches",),
    "ai_decision": ("condition",),
    "llm_decision": ("condition",),
    "send_email": _EMAIL,
    "send_sms": _TEXT,
    "send_text": _TEXT,
    "send_text_message": _TEXT,
    "send_team_member_text": ("notify_member_via_text",),
    "field_change": ("field_updated",),
    "field_changed": ("field_updated",),
    "date_field": ("on_or_around_date",),
}


class UnknownType(LookupError):
    """Not a step or trigger type this CLI wires."""


# ---------------------------------------------------------------------------
# Shared concepts
# ---------------------------------------------------------------------------

CONCEPTS: dict[str, str] = {
    "who": (
        "A team member selector: a `type` (or `assignment_type`) plus the id "
        "key that option needs. The option list differs by step; each step's "
        "own list is in its `step-types show`."
    ),
    "value-source": (
        "Where a value is read from or written to: a field on the record, a "
        "field on a related record, an automation variable, or a static "
        "value. Steps name the selector differently (`source_type`, "
        "`input_type`, `output_type`, `record_source`, `association_source`)."
    ),
    "variable": (
        "An automation variable, referenced by name. It must be declared by "
        "an `initialize_variable` step at the front of the automation. Most "
        'blocks take the bare name; a few take `{"name": ...}`, noted on the '
        "field."
    ),
    "field": (
        "A field on a record. Action blocks take `field_ref` as "
        "`<object>.<field>` and the CLI resolves it; a filter takes the bare "
        "field api_name. Where a field says UUID, names aren't resolved."
    ),
    "when": (
        "A duration or a time of day. `delay` takes unit counts (`days: 2`); "
        "`schedule_activity` takes `delay_type` + `delay_value` and 24-hour "
        "`HH:MM`; the date trigger takes a 12-hour `time` with `period`."
    ),
    "filter": (
        "A `filter_config` in the filter DSL, the same one `records list "
        "--filter` takes. See `kizen docs show filters` and `kizen filters "
        "ops`."
    ),
    "if-already-set": (
        "What a write does when the target already holds a value, e.g. "
        "`overwrite` or `update_if_blank`. The key is named per step "
        "(`field_resolution`, `value_resolution`, "
        "`destination_variable_resolution`, `variable_field_resolution`, "
        "`conflict_resolution`)."
    ),
    "merge-fields": (
        "`{{ ... }}` tokens in text a step sends or stores. The namespace "
        "depends on the step type: see `kizen docs show automation`, "
        '"Merge-field namespace tokens by step type".'
    ),
}


# ---------------------------------------------------------------------------
# Guides: the hand-written part
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    """One value of a selector and the sibling keys it is written with.
    ``needs`` are key names beside the selector, in the same dict."""

    value: str
    needs: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Selector:
    """A key whose value decides which other keys the block carries.
    ``path`` is dotted from the config block's root."""

    path: str
    options: tuple[Option, ...]
    default: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Variant:
    """A complete config block for one common shape."""

    name: str
    summary: str
    block: dict[str, Any]


@dataclass(frozen=True)
class Lookup:
    """A value that has to come from the environment, and the command that
    lists the candidates."""

    path: str
    command: str
    note: str = ""


@dataclass(frozen=True)
class Guide:
    variants: tuple[Variant, ...]
    selectors: tuple[Selector, ...] = ()
    lookups: tuple[Lookup, ...] = ()
    notes: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    doc: str = "kizen docs show automation"


_RELATIVE_TIME = tuple(
    Option(v, ("time_offset_value",))
    for v in ("hours_before", "hours_after", "minutes_before", "minutes_after")
)

_SCHEDULE_ACTIVITY = Guide(
    variants=(
        Variant(
            "immediately",
            "Due when the step runs, assigned to the record's owner.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {"type": "immediately"},
                "assigned_to": {"assignment_type": "owner"},
            },
        ),
        Variant(
            "with_delay",
            "Due two working days after the step runs, at the run's time of day.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {
                    "type": "with_delay",
                    "with_delay": {
                        "delay_type": "day",
                        "delay_value_type": "static",
                        "delay_value": 2,
                    },
                    "skip_non_working_days": True,
                },
                "assigned_to": {"assignment_type": "owner"},
            },
        ),
        Variant(
            "with_delay_at_time",
            "Due the next day at 09:00.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {
                    "type": "with_delay",
                    "with_delay": {
                        "delay_type": "day",
                        "delay_value_type": "static",
                        "delay_value": 1,
                        "time_offset": "specific_time",
                        "time": "09:00",
                    },
                },
                "assigned_to": {"assignment_type": "owner"},
            },
        ),
        Variant(
            "delay_from_variable",
            "The number of days comes from a number variable.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {
                    "type": "with_delay",
                    "with_delay": {
                        "delay_type": "day",
                        "delay_value_type": "automation_variable",
                        "delay_variable": {"name": "<number variable>"},
                    },
                },
                "assigned_to": {"assignment_type": "owner"},
            },
        ),
        Variant(
            "on_date",
            "Due at 10:00 the day after a date field on the record.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {
                    "type": "on_or_around_date",
                    "on_or_around_date": {
                        "field_ref": "<object>.<date field>",
                        "date_offset": "days_after",
                        "date_offset_days": 1,
                        "time_offset": "specific_time",
                        "time": "10:00",
                    },
                },
                "assigned_to": {"assignment_type": "owner"},
            },
        ),
        Variant(
            "assigned_to_team_member",
            "Assigned to one named team member, with a note and the new "
            "activity's id kept in a variable.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {"type": "immediately"},
                "assigned_to": {
                    "assignment_type": "team_member",
                    "employee_id": "<team member uuid>",
                },
                "note": "<plain text>",
                "html_note": "<p>the same note as HTML</p>",
                "target_variable": "<uuid or string variable>",
            },
        ),
        Variant(
            "round_robin",
            "Assigned to each team member in turn.",
            {
                "activity_type": "<activity api_name>",
                "schedule": {"type": "immediately"},
                "assigned_to": {"assignment_type": "round_robin_all"},
            },
        ),
    ),
    selectors=(
        Selector(
            "schedule.type",
            (
                Option("immediately"),
                Option("with_delay", ("with_delay",)),
                Option("on_or_around_date", ("on_or_around_date",)),
            ),
        ),
        Selector(
            "schedule.with_delay.delay_value_type",
            (
                Option("static", ("delay_value",)),
                Option(
                    "automation_variable",
                    ("delay_variable",),
                    'delay_variable is {"name": <number variable>}',
                ),
            ),
            default="static",
        ),
        Selector(
            "schedule.with_delay.time_offset",
            (
                Option("automation_time", note="the run's time of day"),
                Option("specific_time", ("time",), "time is HH:MM, 24-hour"),
                *_RELATIVE_TIME,
            ),
            default="automation_time",
        ),
        Selector(
            "schedule.on_or_around_date.date_offset",
            tuple(
                Option(v, ("date_offset_days",))
                for v in ("on_day", "on_day_and_time", "days_before", "days_after")
            ),
            note="date_offset_days is 1 or more under every option, on_day included",
        ),
        Selector(
            "schedule.on_or_around_date.time_offset",
            (
                Option("specific_time", ("time",), "time is HH:MM, 24-hour"),
                Option("from_field"),
                Option("from_variable"),
                *_RELATIVE_TIME,
            ),
            default="specific_time",
        ),
        Selector(
            "assigned_to.assignment_type",
            (
                Option("owner", note="the record's owner; not on Contacts"),
                Option("team_member", ("employee_id",)),
                Option("specific_role", ("role_id",)),
                Option("round_robin_all"),
                Option("round_robin_role", ("role_id",)),
                Option("round_robin_team_members", ("employee_ids",)),
                Option("last_active"),
                Option("last_active_role", ("role_id",)),
                Option("team_selector_field", ("field_ref",)),
                Option("team_member_from_variable", ("variable",)),
                Option("role_from_variable", ("variable",)),
            ),
        ),
    ),
    lookups=(
        Lookup("activity_type", "kizen activities list", "api_name, name or UUID"),
        Lookup("assigned_to.employee_id", "kizen team list", "UUID"),
        Lookup("assigned_to.role_id", "kizen roles list", "UUID"),
        Lookup(
            "schedule.on_or_around_date.field_ref",
            "kizen objects get <object>",
            "a date or datetime field, as <object>.<field>",
        ),
        Lookup(
            "assigned_to.field_ref",
            "kizen objects get <object>",
            "a team-selector field, as <object>.<field>",
        ),
    ),
    notes=(
        "on_or_around_date takes its date from `field_ref` or from `variable` "
        '({"name": <date variable>}); set one.',
        "Set `html_note` with `note` when you want both: a write without "
        "`html_note` clears it.",
        "`association_configs` is only for an activity type tied to more than "
        "one object; the server fills the default entries.",
        'On a Contact automation `owner` is refused: Kizen 400s "owner '
        'assignment must be set only for custom object" (confirmed live '
        "2026-11-16). Pick another assignment_type there.",
    ),
    concepts=("who", "when", "variable", "field", "if-already-set", "merge-fields"),
)

# The model requires a target object without marking a field required, so the
# required-keys block derived from its schema wouldn't validate.
_RESOLUTIONS = (
    Option("overwrite"),
    Option("update_if_blank"),
    Option("add_only"),
)

_NEW_RECORD = {
    "target_custom_object": "<object api_name>",
    "new_entity_name": "Follow-up for {{ entity_record.name }}",
    "new_entity_name_html": "<p>Follow-up for {{ entity_record.name }}</p>",
}

_CREATE_RELATED_ENTITY = Guide(
    variants=(
        Variant(
            "linked",
            "A record on another object, named from the triggering record, "
            "owned by its owner and linked back to it through a relationship "
            "field.",
            {
                **_NEW_RECORD,
                "new_entity_owner_type": "assign_from_context_record",
                "context_entity_field": "<object>.<relationship field>",
            },
        ),
        Variant(
            "specific_owner",
            "Owned by one team member.",
            {
                **_NEW_RECORD,
                "new_entity_owner_type": "newly_assigned_owner",
                "new_entity_owner_sub_type": "specific_team_member",
                "new_entity_owner_employees": ["<employee UUID>"],
                "context_entity_field": "<object>.<relationship field>",
            },
        ),
        Variant(
            "round_robin_role",
            "Owners taken in turn from one role, starting at a stage.",
            {
                **_NEW_RECORD,
                "new_entity_owner_type": "newly_assigned_owner",
                "new_entity_owner_sub_type": "round_robin_specific_role",
                "new_entity_owner_role": "<role UUID>",
                "new_entity_stage": "<stage UUID>",
            },
        ),
        Variant(
            "into_variable",
            "Kept in a variable for later steps, with no relationship field. "
            "An existing record of the same name gets a suffix.",
            {
                **_NEW_RECORD,
                "new_entity_owner_type": "assign_from_context_record",
                "target_variable": "<variable name>",
                "existing_record_found_action": "create_append_suffix",
            },
        ),
    ),
    selectors=(
        Selector(
            "new_entity_owner_type",
            (
                Option(
                    "assign_from_context_record", note="the triggering record's owner"
                ),
                Option("newly_assigned_owner", ("new_entity_owner_sub_type",)),
            ),
        ),
        Selector(
            "new_entity_owner_sub_type",
            (
                Option("round_robin_any_role", note="anyone, in turn"),
                Option("round_robin_specific_role", ("new_entity_owner_role",)),
                Option(
                    "round_robin_choose_team_members", ("new_entity_owner_employees",)
                ),
                Option("specific_team_member", ("new_entity_owner_employees",)),
                Option("assign_from_variable", ("new_entity_owner_variable",)),
            ),
            note="only with newly_assigned_owner",
        ),
        Selector(
            "context_entity_field_resolution",
            _RESOLUTIONS,
            "overwrite",
        ),
        Selector("variable_field_resolution", _RESOLUTIONS, "overwrite"),
        Selector(
            "existing_record_found_action",
            (Option("error_do_not_create"), Option("create_append_suffix")),
            "error_do_not_create",
        ),
        Selector(
            "archived_record_found_action",
            (
                Option("overwrite"),
                Option("unarchive"),
                Option("create_append_suffix"),
                Option("error_do_not_create"),
            ),
            "overwrite",
        ),
    ),
    lookups=(
        Lookup("target_custom_object", "kizen objects list", "api_name or UUID"),
        Lookup(
            "context_entity_field",
            "kizen objects get <object>",
            "a relationship field on the automation's object, as <object>.<field>",
        ),
        Lookup("new_entity_owner_employees", "kizen team search <name>"),
        Lookup("new_entity_owner_role", "kizen roles list"),
        Lookup(
            "new_entity_stage",
            "kizen objects get <object>",
            "the stage field's options",
        ),
    ),
    notes=(
        "Field values can't be set at creation: set them in a following "
        "`modify_related_entities` step through the same `context_entity_field`.",
        "It can't create a Contact (client_client): Kizen refuses it. Use a code step.",
    ),
    concepts=("field", "variable", "merge-fields"),
)

_FILTER_NOTES = (
    "A clause on its own needs a group around it: "
    '{"all": [{"field": ..., "op": ..., "value": ...}]}.',
    "`field` is the bare api_name on the filtered object, not <object>.<field>. "
    "`kizen filters ops` lists each op and the value it takes.",
    "A filter value takes no merge fields. To compare with a value from the "
    'record, copy it into a variable and put `"variable"` in place of `"value"`.',
)

_CONDITION = Guide(
    variants=(
        # The llm_decision block is the call_llm keys, untyped in the model,
        # so the derived fields can't show it.
        Variant(
            "llm_decision",
            "An LLM answers a yes/no question about the record.",
            {
                "type": "llm_decision",
                "llm_decision": {
                    "model_name": "<model_name>",
                    "prompt": "<a yes/no question; fields as {{ custom_objects.<field> }}>",
                },
            },
        ),
        Variant(
            "field_test",
            "Yes when a field on the record holds a value.",
            {
                "type": "custom_filter",
                "filter_config": {
                    "all": [
                        {"field": "<field api_name>", "op": "=", "value": "<value>"}
                    ]
                },
            },
        ),
        Variant(
            "any_of",
            "Yes when either test holds; groups nest.",
            {
                "type": "custom_filter",
                "filter_config": {
                    "any": [
                        {"field": "<field api_name>", "op": "is_blank"},
                        {
                            "all": [
                                {
                                    "field": "<dropdown api_name>",
                                    "op": "is_any_of",
                                    "value": ["<option name>"],
                                },
                                {"field": "<number api_name>", "op": ">", "value": 5},
                            ]
                        },
                    ]
                },
            },
        ),
        Variant(
            "field_vs_variable",
            "Yes when a dropdown holds one of the options in an array variable.",
            {
                "type": "custom_filter",
                "filter_config": {
                    "all": [
                        {
                            "field": "<dropdown api_name>",
                            "op": "is_any_of",
                            "variable": "<string array variable>",
                        }
                    ]
                },
            },
        ),
        Variant(
            "variable_vs_static",
            "Yes when a number variable is 5.5 or more. The DSL has no op for "
            "this, so it is written in the wire form.",
            {
                "type": "custom_filter",
                "filter_config": {
                    "and": True,
                    "query": [
                        {
                            "id": "query-0",
                            "and": True,
                            "filters": [
                                {
                                    "type": "variable",
                                    "subtype": "automation_variable",
                                    "lhs_variable_name": "<number variable>",
                                    "condition": ">=",
                                    "rhs_value": "5.5",
                                    "rhs_value_type": "static",
                                    "view_model": [
                                        [
                                            "filter_type",
                                            {
                                                "vars": [
                                                    ["fields_settings_search", True],
                                                    [
                                                        "custom_object_id",
                                                        "<object uuid>",
                                                    ],
                                                    ["object_type", "standard"],
                                                    [
                                                        "client_tag_field_id",
                                                        "<client tag field uuid>",
                                                    ],
                                                ],
                                                "filter_type": "variable",
                                            },
                                        ],
                                        [
                                            "lhs_variable",
                                            {
                                                "name": "<number variable>",
                                                "data_type": "number",
                                            },
                                        ],
                                        ["condition", "more_than_or_equal_to"],
                                        ["value_type", "static"],
                                        ["value", "5.5"],
                                    ],
                                }
                            ],
                        }
                    ],
                    "invalid": False,
                },
            },
        ),
        Variant(
            "in_group",
            "Yes when the record is in a saved filter group.",
            {"type": "in_group", "group_ids": ["<filter group uuid>"]},
        ),
    ),
    selectors=(
        Selector(
            "type",
            (
                Option("custom_filter", ("filter_config",)),
                Option("in_group", ("group_ids",)),
                Option("not_in_group", ("group_ids",)),
                Option(
                    "llm_decision",
                    ("llm_decision",),
                    "model_name and prompt, as on call_llm",
                ),
            ),
            default="custom_filter",
        ),
    ),
    lookups=(
        Lookup(
            "filter_config.all[].field",
            "kizen objects get <object>",
            "a field api_name on the automation's object",
        ),
        Lookup("group_ids", "kizen filter-groups list <object>", "UUID"),
        Lookup(
            "llm_decision.model_name",
            "kizen automations llm-models",
            "a model with a mark under decision",
        ),
    ),
    notes=(
        *_FILTER_NOTES,
        "A variable in a clause must be declared by an initialize_variable "
        "step. A text or email field takes a single string variable, with = "
        "or contains; the other field types take an array (a team-member "
        "field also takes a single employee variable). For a dropdown the "
        "array holds option names or option UUIDs.",
        "variable_vs_static: the view_model, operator names and blank checks "
        "are in `kizen docs show filters`, 'Variable-comparison clauses'.",
    ),
    concepts=("filter", "variable", "field", "merge-fields"),
    doc="kizen docs show filters",
)

_SEARCH_RECORDS = Guide(
    variants=(
        Variant(
            "custom_filter",
            "The records on another object that match a filter, into an "
            "array variable.",
            {
                "custom_object": "<object api_name>",
                "filter_type": "custom_filter",
                "filter_config": {
                    "all": [
                        {"field": "<field api_name>", "op": "=", "value": "<value>"}
                    ]
                },
                "destination_variable": "<entity array variable>",
            },
        ),
        Variant(
            "in_group",
            "The records in a saved filter group.",
            {
                "custom_object": "<object api_name>",
                "filter_type": "in_group",
                "filter_groups": ["<filter group name or uuid>"],
                "destination_variable": "<entity array variable>",
            },
        ),
    ),
    selectors=(
        Selector(
            "filter_type",
            (
                Option("all_records"),
                Option("custom_filter", ("filter_config",)),
                Option("in_group", ("filter_groups",)),
                Option("not_in_group", ("filter_groups",)),
            ),
        ),
        Selector(
            "destination_variable_resolution",
            tuple(
                Option(v)
                for v in (
                    "overwrite",
                    "add_only",
                    "remove_only",
                    "update_if_blank",
                    "overwrite_except_null",
                )
            ),
            default="overwrite",
        ),
    ),
    lookups=(
        Lookup("custom_object", "kizen objects list", "api_name or UUID"),
        Lookup(
            "filter_groups",
            "kizen filter-groups list <object>",
            "groups saved on custom_object, by name or UUID",
        ),
    ),
    notes=(
        "`custom_object` is the object searched, independent of the "
        "automation's own target_object (a global automation has none); "
        "filter fields and filter groups resolve against it.",
        "`destination_variable` is an array variable declared by an "
        "initialize_variable step, named bare.",
        *_FILTER_NOTES,
    ),
    concepts=("filter", "variable"),
)


def _match_modes(value_key: str) -> tuple[Option, ...]:
    return (
        Option("any_including_blank", note="any value, blank too"),
        Option("any_excluding_blank", note="any value but blank"),
        Option("blank"),
        Option("specific_value", (value_key,), "an option, team member or record"),
        Option("date_between", (value_key,), "a date field"),
        Option("values_added"),
        Option("values_removed"),
        Option("contains_any_of"),
        Option("contains_none_of"),
    )


_FIELD_UPDATED = Guide(
    variants=(
        Variant(
            "changed_to",
            "A dropdown, radio or status field changes to one option, from anything.",
            {
                "field_ref": "<object>.<field>",
                "fire_on_create": False,
                "to_match_mode": "specific_value",
                "to_value": "<option name>",
            },
        ),
        Variant(
            "changed_from_to",
            "A field moves from one option to another.",
            {
                "field_ref": "<object>.<field>",
                "fire_on_create": False,
                "from_match_mode": "specific_value",
                "from_value": "<option name>",
                "to_match_mode": "specific_value",
                "to_value": "<option name>",
            },
        ),
        Variant(
            "any_change",
            "Any change to the field, on create too.",
            {"field_ref": "<object>.<field>", "fire_on_create": True},
        ),
        Variant(
            "filled_in",
            "A blank field gets a value.",
            {
                "field_ref": "<object>.<field>",
                "fire_on_create": False,
                "from_match_mode": "blank",
                "to_match_mode": "any_excluding_blank",
            },
        ),
        Variant(
            "date_between",
            "A date field is set to a date in a range.",
            {
                "field_ref": "<object>.<date field>",
                "to_match_mode": "date_between",
                "to_value": {
                    "date_start": "2026-07-05T06:15:00.000Z",
                    "date_end": "2026-07-19T04:00:00.000Z",
                    "type": "date_range",
                },
            },
        ),
        Variant(
            "owner_changed_to",
            "A team selector field (owner here) changes to one team member.",
            {
                "field_ref": "<object>.owner",
                "to_match_mode": "specific_value",
                "to_value": "<employee UUID>",
            },
        ),
    ),
    selectors=(
        Selector(
            "from_match_mode",
            _match_modes("from_value"),
            "any_including_blank",
            "specific_value when from_value is set",
        ),
        Selector(
            "to_match_mode",
            _match_modes("to_value"),
            "any_including_blank",
            "specific_value when to_value is set",
        ),
    ),
    lookups=(
        Lookup(
            "field_ref",
            "kizen objects get <object>",
            "<object>.<field>, or a bare field name on the automation's object",
        ),
        Lookup(
            "to_value",
            "kizen objects get <object>",
            "an option name as the field lists it; a team member's UUID from "
            "`kizen team search <name>`",
        ),
    ),
    notes=(
        "A match mode left out is planned as specific_value when its value "
        "is set, and stored as any_including_blank when it isn't. An update "
        "keeps a live trigger's mode.",
        "values_added, values_removed, contains_any_of and contains_none_of "
        "are the server's enum but haven't been built from the CLI; check the "
        "value shape on a read of a UI-built trigger first.",
    ),
    concepts=("field",),
)

_MATH_OPERATOR = Guide(
    variants=(
        Variant(
            "increment_field",
            "Add 1 to a number field on the record.",
            {
                "type": "simple_builder",
                "subtype": "addition",
                "field_id": "<number field UUID>",
                "simple_builder_arguments": [
                    {
                        "argument_type": "field",
                        "operation_type": "regular",
                        "field_id": "<number field UUID>",
                    },
                    {
                        "argument_type": "constant",
                        "operation_type": "regular",
                        "constant": 1,
                    },
                ],
            },
        ),
        Variant(
            "difference_into_variable",
            "One field minus another, kept in a number variable.",
            {
                "type": "simple_builder",
                "subtype": "addition",
                "variable": "<variable name>",
                "simple_builder_arguments": [
                    {
                        "argument_type": "field",
                        "operation_type": "regular",
                        "field_id": "<number field UUID>",
                    },
                    {
                        "argument_type": "field",
                        "operation_type": "negative",
                        "field_id": "<number field UUID>",
                    },
                ],
            },
        ),
        Variant(
            "ratio_into_field",
            "A variable divided by a field, written to a field.",
            {
                "type": "simple_builder",
                "subtype": "multiplication",
                "field_id": "<number field UUID>",
                "simple_builder_arguments": [
                    {
                        "argument_type": "variable",
                        "operation_type": "regular",
                        "variable": "<variable name>",
                    },
                    {
                        "argument_type": "field",
                        "operation_type": "inverse",
                        "field_id": "<number field UUID>",
                    },
                ],
            },
        ),
    ),
    selectors=(
        Selector(
            "subtype",
            (
                Option("addition", note="arguments summed; negative subtracts"),
                Option("multiplication", note="arguments multiplied; inverse divides"),
            ),
        ),
        Selector(
            "simple_builder_arguments[].argument_type",
            (
                Option("field", ("field_id",)),
                Option("constant", ("constant",)),
                Option("variable", ("variable",)),
            ),
        ),
        Selector(
            "simple_builder_arguments[].operation_type",
            (
                Option("regular"),
                Option("negative", note="addition only"),
                Option("inverse", note="multiplication only"),
            ),
        ),
    ),
    lookups=(
        Lookup(
            "field_id",
            "kizen objects get <object>",
            "a number field's id; the result goes in it",
        ),
        Lookup(
            "simple_builder_arguments[].field_id",
            "kizen objects get <object>",
            "a number field's id",
        ),
    ),
    notes=(
        "The result goes in `field_id` or in `variable`: give one.",
        "Field ids are UUIDs here; a field name isn't resolved.",
        "A variable named here is a number variable the spec declares.",
    ),
    concepts=("field", "variable"),
)


def _input_types(*extra: Option) -> Selector:
    return Selector(
        "inputs[].input_type",
        (
            Option("field", ("field_ref",), "a field on the automation's record"),
            Option("variable", ("variable_name",)),
            Option(
                "related_field",
                ("field_ref", "related_field"),
                "field_ref is the single-value relationship hop",
            ),
            Option("static_value", ("static_value",)),
            *extra,
        ),
        "field",
    )


_OUTPUT_TYPES = Selector(
    "outputs[].output_type",
    (
        Option("field", ("field_ref",)),
        Option("variable", ("variable_name",)),
        Option("related_field", ("field_ref", "related_field")),
    ),
    "field",
)

_CODE_OUTPUT_RESOLUTION = Selector(
    "outputs[].conflict_resolution",
    (
        Option("overwrite"),
        Option("add_only"),
        Option("remove_only"),
        Option("update_if_blank"),
        Option("overwrite_except_null"),
    ),
)

_CODE_STEP = Guide(
    variants=(
        Variant(
            "field_to_field",
            "Read a field, write a field on the same record.",
            {
                "script": "outputs.summary = (inputs.notes or '').strip()[:200]",
                "inputs": [{"name": "notes", "field_ref": "<object>.<field>"}],
                "outputs": [{"name": "summary", "field_ref": "<object>.<field>"}],
            },
        ),
        Variant(
            "variable_out",
            "A constant and a variable in, a variable out for later steps.",
            {
                "script": "outputs.total = inputs.base + inputs.extra",
                "inputs": [
                    {
                        "name": "base",
                        "input_type": "static_value",
                        "static_value": 10,
                        "data_type": "number",
                    },
                    {
                        "name": "extra",
                        "input_type": "variable",
                        "variable_name": "<variable name>",
                    },
                ],
                "outputs": [
                    {
                        "name": "total",
                        "output_type": "variable",
                        "variable_name": "<variable name>",
                        "data_type": "number",
                    }
                ],
            },
        ),
        Variant(
            "related_field_in",
            "Read a field on the record a single-value relationship points to.",
            {
                "script": "outputs.copy = inputs.parent_name",
                "inputs": [
                    {
                        "name": "parent_name",
                        "input_type": "related_field",
                        "field_ref": "<object>.<relationship field>",
                        "related_field": "<related object>.<field>",
                    }
                ],
                "outputs": [{"name": "copy", "field_ref": "<object>.<field>"}],
            },
        ),
        Variant(
            "to_many_ids",
            "The ids of the records on a to-many relationship.",
            {
                "script": "outputs.count = len(inputs.related or [])",
                "inputs": [
                    {
                        "name": "related",
                        "field_ref": "<object>.<relationship field>",
                        "data_type": "entity",
                        "is_list": True,
                    }
                ],
                "outputs": [
                    {
                        "name": "count",
                        "field_ref": "<object>.<field>",
                        "data_type": "number",
                    }
                ],
            },
        ),
    ),
    selectors=(_input_types(), _OUTPUT_TYPES, _CODE_OUTPUT_RESOLUTION),
    lookups=(
        Lookup("inputs[].field_ref", "kizen objects get <object>", "<object>.<field>"),
        Lookup("outputs[].field_ref", "kizen objects get <object>", "<object>.<field>"),
    ),
    notes=(
        "related_field reads one hop through a single-value relationship "
        "(many_to_one or one_to_one); a to-many hop is a 400. For a to-many "
        "relationship, bind it as a field input with data_type entity and "
        "is_list true: the script gets the related records' ids.",
        "To write to records on a to-many relationship, use a "
        "modify_related_entities step.",
        "The script reads inputs.<name> and sets outputs.<name>: "
        "`kizen docs show code-steps`.",
    ),
    concepts=("field", "variable"),
    doc="kizen docs show code-steps",
)

_PLUGIN_CODE_STEP = Guide(
    variants=(
        Variant(
            "with_install_config",
            "A plugin step given a field and the plugin's install config.",
            {
                "plugin_app_api_name": "<installed plugin api_name>",
                "action_step_api_name": "<step api_name>",
                "inputs": [
                    {"name": "heading", "field_ref": "<object>.<field>"},
                    {"name": "settings", "input_type": "business_plugin_config"},
                ],
                "outputs": [
                    {
                        "name": "result",
                        "output_type": "variable",
                        "variable_name": "<variable name>",
                    }
                ],
            },
        ),
    ),
    selectors=(
        _input_types(
            Option("business_plugin_config", note="the install config; no source key")
        ),
        _OUTPUT_TYPES,
        _CODE_OUTPUT_RESOLUTION,
    ),
    notes=(
        "Inputs and outputs are code_step's: `kizen automations step-types "
        "show code_step`. Kizen checks them against the plugin's manifest.",
    ),
    concepts=("field", "variable"),
)

_ASSIGN_TEAM_MEMBER = Guide(
    variants=(
        Variant(
            "one_member",
            "Make one team member the record's owner.",
            {"type": "team_member", "employee_id": "<employee UUID>"},
        ),
        Variant(
            "round_robin_role",
            "Owners taken in turn from one role.",
            {"type": "round_robin_role", "role_id": "<role UUID>"},
        ),
        Variant(
            "round_robin_members",
            "Owners taken in turn from a list of team members.",
            {
                "type": "round_robin_team_members",
                "employee_ids": ["<employee UUID>", "<employee UUID>"],
            },
        ),
        Variant(
            "from_field",
            "The team member in a team selector field on the record.",
            {
                "type": "team_selector_field",
                "field_ref": "<object>.<team selector field>",
            },
        ),
        Variant(
            "from_related_record",
            "The team member in a team selector field on a related record.",
            {
                "type": "related_team_selector_field",
                "related_field_id": "<relationship field UUID>",
                "field_ref": "<related object>.<team selector field>",
            },
        ),
    ),
    selectors=(
        Selector(
            "type",
            (
                Option("team_member", ("employee_id",)),
                Option("round_robin_all", note="anyone, in turn"),
                Option("round_robin_role", ("role_id",)),
                Option("round_robin_team_members", ("employee_ids",)),
                Option("team_selector_field", ("field_ref",)),
                Option(
                    "related_team_selector_field",
                    ("related_field_id", "field_ref"),
                    "related_field_id is a single-value hop on this object",
                ),
            ),
        ),
    ),
    lookups=(
        Lookup("employee_id", "kizen team search <name>"),
        Lookup("employee_ids", "kizen team search <name>"),
        Lookup("role_id", "kizen roles list"),
        Lookup("field_ref", "kizen objects get <object>", "a team selector field"),
        Lookup(
            "related_field_id",
            "kizen objects get <object>",
            "a many_to_one or one_to_one relationship field's id",
        ),
    ),
    notes=(
        "owner, last_active, last_active_role and employees are the notify "
        "steps' choices and are refused here.",
    ),
)

_STEP_GUIDES: dict[str, Guide] = {
    "assign_team_member": _ASSIGN_TEAM_MEMBER,
    "code_step": _CODE_STEP,
    "condition": _CONDITION,
    "create_related_entity": _CREATE_RELATED_ENTITY,
    "math_operator": _MATH_OPERATOR,
    "plugin_code_step": _PLUGIN_CODE_STEP,
    "schedule_activity": _SCHEDULE_ACTIVITY,
    "search_records": _SEARCH_RECORDS,
}
_TRIGGER_GUIDES: dict[str, Guide] = {"field_updated": _FIELD_UPDATED}


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _step_block_field(type_name: str) -> str | None:
    if type_name in _CONFIGLESS_STEP_TYPES:
        return None
    return _block_field_for(type_name)


def _step_entry(type_name: str) -> dict[str, Any]:
    return {"key": type_name, "parent_key": None, "step_type": type_name}


def _trigger_entry(type_name: str) -> dict[str, Any]:
    return {"trigger_type": type_name}


@dataclass(frozen=True)
class _Kind:
    name: str  # "step" | "trigger"
    types: tuple[str, ...]
    envelope: type[BaseModel]
    block_field: Callable[[str], str | None]
    entry: Callable[[str], dict[str, Any]]
    purposes: dict[str, str]
    guides: dict[str, Guide]
    known: dict[str, dict[str, KnownChoices]]
    notes: tuple[str, ...] = ()


_STEP = _Kind(
    "step",
    STEP_TYPES,
    AutomationStepDef,
    _step_block_field,
    _step_entry,
    STEP_PURPOSES,
    _STEP_GUIDES,
    KNOWN_ENUM_CHOICES,
)
_TRIGGER = _Kind(
    "trigger",
    TRIGGER_TYPES,
    AutomationTriggerDef,
    trigger_config_field,
    _trigger_entry,
    TRIGGER_PURPOSES,
    _TRIGGER_GUIDES,
    KNOWN_ENUM_CHOICES_TRIGGERS,
    notes=("A trigger has no key; steps refer to triggers by position.",),
)
# No step and trigger share a name; a test keeps it that way, so a name alone
# finds its kind.
_KINDS = (_STEP, _TRIGGER)


def suggest(
    name: str, wired: Sequence[str] = (*STEP_TYPES, *TRIGGER_TYPES), n: int = 1
) -> list[str]:
    """The types in ``wired`` that ``name`` most likely means, best first.
    Near names count among the aliases too, and an alias gives its types.
    Only aliases whose types are all in ``wired`` count, so a trigger typo
    isn't offered a step."""
    aliases = {a: t for a, t in ALIASES.items() if set(t) <= set(wired)}
    close = (
        [name]
        if name in aliases
        else difflib.get_close_matches(name, [*wired, *aliases], n=n)
    )
    return list(dict.fromkeys(t for m in close for t in aliases.get(m, (m,))))


def _kind_of(type_name: str) -> _Kind:
    for kind in _KINDS:
        if type_name in kind.types:
            return kind
    targets = ALIASES.get(type_name, ())
    if len(targets) > 1:
        lines = "\n".join(f"  {t}: {_kind_of(t).purposes[t]}" for t in targets)
        raise UnknownType(f"'{type_name}' isn't a type name. It could be:\n{lines}")
    meant = suggest(type_name, n=3)
    hint = (
        f"Did you mean {' or '.join(repr(m) for m in meant)}?"
        if meant
        else "`kizen automations step-types list` lists every type."
    )
    raise UnknownType(f"'{type_name}' is not a wired step or trigger type. {hint}")


def resolve(name: str) -> str:
    """The wired type a name means: the name itself, or the one type an
    alias stands for. Raises :class:`UnknownType` for anything else, an
    alias with several types included."""
    targets = ALIASES.get(name, ())
    if len(targets) == 1:
        return targets[0]
    _kind_of(name)
    return name


def _block_model(kind: _Kind, type_name: str) -> type[BaseModel] | None:
    """The spec model of a type's config block, or None when the block is
    untyped or the type has none."""
    field = kind.block_field(type_name)
    info = kind.envelope.model_fields.get(field) if field else None
    if info is None:
        return None
    for candidate in (info.annotation, *getattr(info.annotation, "__args__", ())):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if not ref:
        return node
    return dict(schema.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {}))


def _ref_name(node: dict[str, Any]) -> str | None:
    ref = node.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _members(
    schema: dict[str, Any], prop: dict[str, Any]
) -> list[tuple[str | None, dict[str, Any]]]:
    """Each type a property can take, through nested unions, with the name
    of the model it is, if any."""
    resolved = _resolve(schema, prop)
    alts = resolved.get("anyOf") or resolved.get("oneOf")
    if not alts:
        return [(_ref_name(prop), resolved)]
    return [m for a in alts for m in _members(schema, a)]


def _alternatives(schema: dict[str, Any], prop: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for _, node in _members(schema, prop)]


def _type_label(schema: dict[str, Any], prop: dict[str, Any]) -> str:
    labels: list[str] = []
    for alt in _alternatives(schema, prop):
        if "enum" in alt or "const" in alt:
            label = "choice"
        elif alt.get("type") == "array":
            label = "list"
        elif alt.get("type") == "null":
            continue
        else:
            label = str(alt.get("type") or "any")
        if label not in labels:
            labels.append(label)
    return " | ".join(labels) or "any"


def _choices(schema: dict[str, Any], prop: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for alt in _alternatives(schema, prop):
        values = alt.get("enum") or ([alt["const"]] if "const" in alt else [])
        out.extend(str(v) for v in values if str(v) not in out)
    return out


def _nested(
    schema: dict[str, Any], prop: dict[str, Any], path: str
) -> list[tuple[str | None, dict[str, Any], str]]:
    """The blocks under a property, a list's items included (as ``path[]``),
    each with its model name and the prefix of its rows."""
    out: list[tuple[str | None, dict[str, Any], str]] = []
    for name, alt in _members(schema, prop):
        if alt.get("type") == "object" and alt.get("properties"):
            out.append((name, alt, f"{path}."))
        elif alt.get("type") == "array" and isinstance(alt.get("items"), dict):
            out.extend(
                (item_name, item, f"{path}[].")
                for item_name, item in _members(schema, alt["items"])
                if item.get("type") == "object" and item.get("properties")
            )
    return out


def _add_row(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    """Add ``row``, or fold it into the row already at its path: union
    members share keys, and a discriminator's choices are the members'."""
    same = next((r for r in rows if r["path"] == row["path"]), None)
    if same is None:
        rows.append(row)
        return
    if "choices" in row:
        merged = same.get("choices", [])
        same["choices"] = merged + [c for c in row["choices"] if c not in merged]
    same["required"] = same["required"] and row["required"]
    if "description" in row and "description" not in same:
        same["description"] = row["description"]


def _fields(
    schema: dict[str, Any],
    node: dict[str, Any],
    known: dict[str, KnownChoices],
    prefix: str = "",
    within: frozenset[str] = frozenset(),
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One row per key of a block, nested blocks and list items included, as
    dotted paths. A model nested in itself (a filter group) is listed once:
    ``within`` holds the models above this one. A key holding the same
    models as a sibling before it says so instead of listing them again."""
    rows = [] if rows is None else rows
    node = _resolve(schema, node)
    required = set(node.get("required") or [])
    listed: dict[tuple[str, ...], str] = {}
    for name, prop in (node.get("properties") or {}).items():
        path = f"{prefix}{name}"
        row: dict[str, Any] = {
            "path": path,
            "type": _type_label(schema, prop),
            "required": name in required,
        }
        description = _resolve(schema, prop).get("description") or prop.get(
            "description"
        )
        if description:
            row["description"] = description
        choices = _choices(schema, prop)
        if choices:
            row["choices"] = choices
        elif path in known:
            # A sample, not the server's full set: see `KnownChoices`.
            row["known_choices"] = list(known[path].values)
        nested = _nested(schema, prop, path)
        models = tuple(sorted({m for m, _, _ in nested if m}))
        if models and models in listed:
            row["same_as"] = listed[models]
            _add_row(rows, row)
            continue
        if models:
            listed[models] = path
        _add_row(rows, row)
        for model, sub, sub_prefix in nested:
            if model in within:
                continue
            inner = within | {model} if model else within
            _fields(schema, sub, known, sub_prefix, inner, rows)
    return rows


def _placeholder_block(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """A block holding only the model's required keys: the first choice for a
    choice (the rest are listed under fields), a nested block for a key that
    only takes an object, and a `<name>` placeholder for anything else."""
    node = _resolve(schema, node)
    out: dict[str, Any] = {}
    for name in node.get("required") or []:
        prop = node["properties"][name]
        types = {a.get("type") for a in _alternatives(schema, prop)} - {"null"}
        choices = _choices(schema, prop)
        if choices:
            out[name] = choices[0]
        elif types == {"object"}:
            out[name] = _placeholder_block(schema, _resolve(schema, prop))
        else:
            out[name] = f"<{name}>"
    return out


def _as_entry(
    kind: _Kind, type_name: str, block: dict[str, Any] | None
) -> dict[str, Any]:
    """A whole step or trigger entry, as it sits in a spec's `steps` or
    `triggers` list."""
    entry = kind.entry(type_name)
    field = kind.block_field(type_name)
    if field and block is not None:
        entry[field] = block
    return entry


def list_types() -> list[dict[str, Any]]:
    """Every wired step and trigger type, for `step-types list`."""
    return [
        {
            "type": t,
            "kind": kind.name,
            "block": kind.block_field(t),
            "purpose": kind.purposes[t],
            "variants": (
                [v.name for v in kind.guides[t].variants] if t in kind.guides else []
            ),
        }
        for kind in _KINDS
        for t in kind.types
    ]


def describe(type_name: str) -> dict[str, Any]:
    """All the catalog holds on one type. Raises :class:`UnknownType`."""
    kind = _kind_of(type_name)
    model = _block_model(kind, type_name)
    schema = model.model_json_schema() if model else None
    guide = kind.guides.get(type_name)
    block_field = kind.block_field(type_name)

    if guide:
        variants = [
            {
                "name": v.name,
                "summary": v.summary,
                "spec": _as_entry(kind, type_name, v.block),
            }
            for v in guide.variants
        ]
    else:
        block = (
            _placeholder_block(schema, schema)
            if schema
            else ({} if block_field else None)
        )
        variants = [
            {
                "name": "minimal",
                "summary": "Required keys only." if block else "No required keys.",
                "spec": _as_entry(kind, type_name, block),
            }
        ]

    out: dict[str, Any] = {
        "type": type_name,
        "kind": kind.name,
        "purpose": kind.purposes[type_name],
        "block": block_field,
        "variants": variants,
        "fields": (
            _fields(schema, schema, kind.known.get(type_name, {})) if schema else []
        ),
        "doc": guide.doc if guide else "kizen docs show automation",
    }
    if guide:
        out["selectors"] = [
            {
                "path": s.path,
                **({"default": s.default} if s.default else {}),
                **({"note": s.note} if s.note else {}),
                "options": [
                    {
                        "value": o.value,
                        "needs": list(o.needs),
                        **({"note": o.note} if o.note else {}),
                    }
                    for o in s.options
                ],
            }
            for s in guide.selectors
        ]
        out["lookups"] = [
            {
                "path": lk.path,
                "command": lk.command,
                **({"note": lk.note} if lk.note else {}),
            }
            for lk in guide.lookups
        ]
        out["notes"] = list(guide.notes)
        out["concepts"] = {name: CONCEPTS[name] for name in guide.concepts}
    if kind.notes:
        out["notes"] = [*kind.notes, *out.get("notes", [])]
    if schema:
        out["schema"] = schema
    return out


def variant_spec(type_name: str, variant: str) -> dict[str, Any]:
    """One variant's entry, ready for a spec file (or `steps add`, for a step)."""
    info = describe(type_name)
    for v in info["variants"]:
        if v["name"] == variant:
            return dict(v["spec"])
    names = ", ".join(v["name"] for v in info["variants"])
    raise UnknownType(f"'{type_name}' has no variant '{variant}'. Variants: {names}")


def skeleton(target_object: str | None = None) -> dict[str, Any]:
    """A minimal automation: the envelope, a manual trigger and one step."""
    obj = target_object or "my_object"
    return {
        "name": "<automation name>",
        "api_name": "my_automation",
        "type": "record_based",
        "target_object": obj,
        "active": False,
        "triggers": [_as_entry(_TRIGGER, "manual", {})],
        "steps": [
            {
                **_step_entry("change_field_value"),
                "key": "set_field",
                "action_change_field_value": {
                    "field_ref": f"{obj}.<field api_name>",
                    "specific_field_value": "<value>",
                },
            }
        ],
    }


def lookup_command(type_name: str, path: str) -> str | None:
    """The command that lists candidates for a block path, if the type's
    guide names one."""
    guide = _STEP_GUIDES.get(type_name)
    for lk in guide.lookups if guide else ():
        if lk.path.replace("[]", "") == path:
            return lk.command
    return None


def _has(node: dict[str, Any], key: str) -> bool:
    """Whether a block carries ``key``, under that name or the one a live
    read gives it (`employee_id` reads back as `employee`)."""
    base = re.sub(r"_(id|ref)(s?)$", r"\2", key)
    names = {key, base, f"{base}_id", f"{base}_ref"}
    return any(node.get(k) not in (None, [], "") for k in names)


def selector_findings(type_name: str, block: dict[str, Any]) -> list[dict[str, Any]]:
    """Where a step block picks a selector option but leaves out a key that
    option is written with, or names an option's keys without picking one.
    Advisory: the guide records how an option is normally written, not what
    the server refuses."""
    guide = _STEP_GUIDES.get(type_name)
    findings: list[dict[str, Any]] = []
    for selector in guide.selectors if guide else ():
        *parents, leaf = selector.path.split(".")
        node: Any = block
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            continue
        parent = ".".join(parents)
        value = node.get(leaf)
        if value is None and leaf == "assignment_type":
            value = node.get("type")
        if value is None and selector.default is None:
            fits = [
                o.value
                for o in selector.options
                if o.needs and all(_has(node, n) for n in o.needs)
            ]
            if fits:
                named = sorted(
                    {n for o in selector.options if o.value in fits for n in o.needs}
                )
                findings.append(
                    {
                        "path": selector.path,
                        "message": (
                            f"{', '.join(repr(n) for n in named)} is given but "
                            f"{selector.path} isn't; set it to "
                            + " or ".join(repr(v) for v in fits)
                        ),
                    }
                )
            continue
        value = value if value is not None else selector.default
        option = next((o for o in selector.options if o.value == value), None)
        if option is None:
            continue
        missing = [n for n in option.needs if not _has(node, n)]
        if missing:
            findings.append(
                {
                    "path": ".".join([*parents, missing[0]]),
                    "message": (
                        f"{selector.path} '{value}' is written with "
                        f"{', '.join(repr(m) for m in missing)}"
                        + (f" in '{parent}'" if parent else "")
                        + (f" ({option.note})" if option.note else "")
                    ),
                }
            )
    return findings

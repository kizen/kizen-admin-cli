"""What a spec author needs to write one automation step, with no live
environment: the config block, its fields and choices, and a step to copy.

Everything here derives from the planner registries and the spec models, so
it can't describe a type the CLI doesn't wire or a field the model doesn't
have. A type with choices that change the block's shape adds a
:class:`Guide`: hand-written blocks, one per shape, and the keys each choice
needs. `tests/test_step_catalog.py` runs every block through the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from kizen_builder.models.spec.automations import AutomationStepDef
from kizen_builder.tools.planners.automations import (
    _CONFIGLESS_STEP_TYPES,
    _STEP_BUILDERS,
    KNOWN_ENUM_CHOICES,
    STEP_PURPOSES,
    KnownChoices,
    _block_field_for,
)

# Step types that take no config block are wired in `_build_step_payload`, not
# in `_STEP_BUILDERS`.
STEP_TYPES: tuple[str, ...] = tuple(sorted({*_STEP_BUILDERS, *_CONFIGLESS_STEP_TYPES}))


class UnknownType(LookupError):
    """Not a step type this CLI wires."""


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
_CREATE_RELATED_ENTITY = Guide(
    variants=(
        Variant(
            "linked",
            "A record on another object, named from the triggering record and "
            "linked back to it through a relationship field.",
            {
                "target_custom_object": "<object api_name>",
                "new_entity_name": "Follow-up for {{ entity_record.name }}",
                "new_entity_name_html": "<p>Follow-up for {{ entity_record.name }}</p>",
                "new_entity_owner_type": "assign_from_context_record",
                "context_entity_field": "<object>.<relationship field>",
            },
        ),
    ),
    lookups=(
        Lookup("target_custom_object", "kizen objects list", "api_name or UUID"),
        Lookup(
            "context_entity_field",
            "kizen objects get <object>",
            "a relationship field on the automation's object, as <object>.<field>",
        ),
    ),
    notes=(
        "Field values can't be set at creation: set them in a following "
        "`modify_related_entities` step through the same `context_entity_field`.",
    ),
    concepts=("field", "variable", "merge-fields"),
)

_GUIDES: dict[str, Guide] = {
    "create_related_entity": _CREATE_RELATED_ENTITY,
    "schedule_activity": _SCHEDULE_ACTIVITY,
}


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _block_field(type_name: str) -> str | None:
    if type_name in _CONFIGLESS_STEP_TYPES:
        return None
    return _block_field_for(type_name)


def _block_model(type_name: str) -> type[BaseModel] | None:
    """The spec model of a type's config block, or None when the block is
    untyped or the type has none."""
    field = _block_field(type_name)
    info = AutomationStepDef.model_fields.get(field) if field else None
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


def _alternatives(schema: dict[str, Any], prop: dict[str, Any]) -> list[dict[str, Any]]:
    prop = _resolve(schema, prop)
    alts = prop.get("anyOf") or prop.get("oneOf") or [prop]
    return [_resolve(schema, a) for a in alts]


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


def _fields(
    schema: dict[str, Any],
    node: dict[str, Any],
    known: dict[str, KnownChoices],
    prefix: str = "",
) -> list[dict[str, Any]]:
    """One row per key of a block, nested blocks included, as dotted paths."""
    rows: list[dict[str, Any]] = []
    node = _resolve(schema, node)
    required = set(node.get("required") or [])
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
        rows.append(row)
        for alt in _alternatives(schema, prop):
            if alt.get("type") == "object" and alt.get("properties"):
                rows.extend(_fields(schema, alt, known, f"{path}."))
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


def _as_step(type_name: str, block: dict[str, Any] | None) -> dict[str, Any]:
    """A whole step entry, as it sits in a spec's `steps` list."""
    step: dict[str, Any] = {
        "key": type_name,
        "parent_key": None,
        "step_type": type_name,
        "order": 0,
    }
    field = _block_field(type_name)
    if field and block is not None:
        step[field] = block
    return step


def list_types() -> list[dict[str, Any]]:
    """Every wired type with its config block, for `step-types list`."""
    return [
        {
            "type": t,
            "block": _block_field(t),
            "purpose": STEP_PURPOSES[t],
            "variants": [v.name for v in _GUIDES[t].variants] if t in _GUIDES else [],
        }
        for t in STEP_TYPES
    ]


def describe(type_name: str) -> dict[str, Any]:
    """All the catalog holds on one type. Raises :class:`UnknownType`."""
    if type_name not in STEP_TYPES:
        raise UnknownType(
            f"'{type_name}' is not a wired step type. Wired: {', '.join(STEP_TYPES)}"
        )
    model = _block_model(type_name)
    schema = model.model_json_schema() if model else None
    guide = _GUIDES.get(type_name)
    block_field = _block_field(type_name)

    if guide:
        variants = [
            {"name": v.name, "summary": v.summary, "spec": _as_step(type_name, v.block)}
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
                "spec": _as_step(type_name, block),
            }
        ]

    out: dict[str, Any] = {
        "type": type_name,
        "purpose": STEP_PURPOSES[type_name],
        "block": block_field,
        "variants": variants,
        "fields": (
            _fields(schema, schema, KNOWN_ENUM_CHOICES.get(type_name, {}))
            if schema
            else []
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
    if schema:
        out["schema"] = schema
    return out


def variant_spec(type_name: str, variant: str) -> dict[str, Any]:
    """One variant's step entry, ready for a spec file or `steps add`."""
    info = describe(type_name)
    for v in info["variants"]:
        if v["name"] == variant:
            return dict(v["spec"])
    names = ", ".join(v["name"] for v in info["variants"])
    raise UnknownType(f"'{type_name}' has no variant '{variant}'. Variants: {names}")

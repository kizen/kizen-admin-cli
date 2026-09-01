"""Enumerate, build, and mutate the full permission-group structure.

Creating a permission group requires POSTing the *entire* structure — every
custom object (with its fields), every section, and the contacts block — not
just a name. The wire shape of a single permission is inconsistent: some
serialize as a bare bool, others as ``{"view", "edit", "remove"}`` dicts. So we
never synthesize shapes; we read an existing group as a shape template and only
change leaf *values*.

Everything funnels through :func:`enumerate_leaves`, which walks a group's wire
structure alongside ``/api/permissions/meta-data`` and yields one :class:`Leaf`
per permission — carrying its label, affordance, ``allowed_access`` range, and
current level. The default builder, the randomizer, and the CLI table renderer
are all thin consumers of that one enumerator.

Levels are ordered none < view < edit < remove and map to the UI columns
None / View / Create·Edit / Delete·All.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

ORDER = ["none", "view", "edit", "remove"]
UI_LABEL = {
    "none": "None",
    "view": "View",
    "edit": "Create/Edit",
    "remove": "Delete/All",
}

_SERVER_KEYS = ("id", "summary", "user_count", "role_count", "created", "updated")


def level_index(level: Any) -> int:
    if level is True:
        return ORDER.index("view")
    if level is False or level is None:
        return 0
    try:
        return ORDER.index(str(level))
    except ValueError:
        return 0


@dataclass
class Leaf:
    """One permission control within a group, bound to its live wire slot."""

    area: str  # "section" | "object" | "contacts"
    block_key: str  # section key / custom_object_id / "contacts_section"
    block_label: str
    category: str | None
    row_key: str
    row_label: str
    affordance: str  # "range" | "switch" | "checkbox"
    allowed_access: list[str]
    _container: dict[str, Any] = field(repr=False)
    _wire_key: str | None = field(repr=False)

    @property
    def current_level(self) -> str:
        # ``_wire_key is None`` means the container dict itself is the value
        # (a per-field slot like {"id", "view", "edit"}).
        value = (
            self._container
            if self._wire_key is None
            else self._container[self._wire_key]
        )
        return value_to_level(value, self.allowed_access)

    def set_level(self, level: str) -> None:
        if self._wire_key is None:
            self._container.update(_level_to_shape(self._container, level))
        else:
            self._container[self._wire_key] = _level_to_shape(
                self._container[self._wire_key], level
            )


def value_to_level(value: Any, allowed: list[str]) -> str:
    """Read a wire value back to a level label."""
    if isinstance(value, bool):
        if not value:
            return "none"
        # A 2-state toggle's "on" is the highest non-none allowed level.
        non_none = [a for a in allowed if a != "none"]
        return non_none[-1] if non_none else "view"
    if isinstance(value, dict):
        best = "none"
        for key in ORDER:
            if key in value and value[key]:
                best = key
        return best
    return "none"


def substitute_object_label(label: str, entity: str) -> str:
    """Fill an object-area control label's ``{0}`` placeholder with the
    object's display name (e.g. ``"All {0} Records"`` -> ``"All Companies
    Records"``). Meta only templates object labels this way — field and
    section labels never carry ``{0}`` — so a label without it is returned
    unchanged.
    """
    if "{0}" not in label:
        return label
    return label.replace("{0}", entity).replace("  ", " ").strip()


def _level_to_shape(shape: Any, level: str) -> Any:
    """Return a value matching ``shape``'s wire form set to ``level``."""
    idx = level_index(level)
    if isinstance(shape, bool):
        return idx >= level_index("view")
    if isinstance(shape, dict):
        out: dict[str, Any] = {}
        for key, val in shape.items():
            out[key] = (idx >= ORDER.index(key)) if key in ORDER else val
        return out
    return level


def _field_allowed(field_val: dict[str, Any]) -> list[str]:
    """Allowed levels for a per-field control, inferred from present keys."""
    allowed = ["none"]
    for key in ("view", "edit"):
        if key in field_val:
            allowed.append(key)
    return allowed


# ---------------------------------------------------------------------------
# The one enumerator
# ---------------------------------------------------------------------------


def enumerate_leaves(
    group: dict[str, Any], meta: dict[str, Any], include_fields: bool = True
) -> Iterator[Leaf]:
    """Yield one :class:`Leaf` per permission control in ``group``."""
    yield from _section_leaves(group, meta)
    yield from _contacts_leaves(group, meta, include_fields)
    yield from _object_leaves(group, meta, include_fields)


def _section_leaves(group: dict[str, Any], meta: dict[str, Any]) -> Iterator[Leaf]:
    descs = {s["key"]: s for s in meta.get("sections", [])}
    for key, sec in group.items():
        if (
            not key.endswith("_section")
            or key == "contacts_section"
            or not isinstance(sec, dict)
        ):
            continue
        desc = descs.get(key)
        if desc is None:
            continue
        label = desc.get("label", key)
        if "enabled" in sec:
            yield Leaf(
                "section",
                key,
                label,
                None,
                "enabled",
                "Enabled",
                desc.get("affordance", "switch"),
                desc.get("allowed_access", ["none", "view"]),
                sec,
                "enabled",
            )
        for p in desc.get("permissions", []):
            pk = p["key"]
            if pk in sec:
                yield Leaf(
                    "section",
                    key,
                    label,
                    p.get("category"),
                    pk,
                    p.get("label", pk),
                    p.get("affordance", "range"),
                    p.get("allowed_access", ["none", "view"]),
                    sec,
                    pk,
                )


def _contacts_leaves(
    group: dict[str, Any], meta: dict[str, Any], include_fields: bool
) -> Iterator[Leaf]:
    cs = group.get("contacts_section")
    if not isinstance(cs, dict):
        return
    for desc in meta.get("contacts", []):  # meta order == UI order
        key = desc["key"]
        if key not in cs:
            continue
        yield Leaf(
            "contacts",
            "contacts_section",
            "Contacts",
            desc.get("category"),
            key,
            desc.get("label", key),
            desc.get("affordance", "range"),
            desc.get("allowed_access", ["none", "view", "edit", "remove"]),
            cs,
            key,
        )
    if include_fields:
        yield from _default_field_leaves(
            cs.get("default_fields"),
            meta.get("default_contact_fields", {}),
            "contacts_section",
            "Contacts",
        )
        yield from _custom_field_leaves(
            cs.get("custom_fields"), "contacts_section", "Contacts"
        )


def _object_leaves(
    group: dict[str, Any], meta: dict[str, Any], include_fields: bool
) -> Iterator[Leaf]:
    caps = meta.get("custom_objects", [])
    for obj in group.get("custom_objects", []):
        oid = obj.get("custom_object_id")
        label = f"object:{oid}"
        for desc in caps:  # meta order == UI order
            key = desc["key"]
            if key not in obj:
                continue
            yield Leaf(
                "object",
                oid,
                label,
                desc.get("category"),
                key,
                desc.get("label", key),
                desc.get("affordance", "range"),
                desc.get("allowed_access", ["none", "view", "edit", "remove"]),
                obj,
                key,
            )
        if include_fields:
            yield from _custom_field_leaves(obj.get("fields"), oid, label)


def _default_field_leaves(block, defaults, block_key, block_label) -> Iterator[Leaf]:
    if not isinstance(block, dict):
        return
    for fname, fval in block.items():
        allowed = defaults.get(fname, {}).get("allowed_access") or _field_allowed(fval)
        yield Leaf(
            "field",
            block_key,
            block_label,
            "default_fields",
            fname,
            fname,
            "range",
            allowed,
            block,
            fname,
        )


def _custom_field_leaves(fields, block_key, block_label) -> Iterator[Leaf]:
    if not isinstance(fields, list):
        return
    for f in fields:
        fid = f.get("id")
        yield Leaf(
            "field",
            block_key,
            block_label,
            "custom_fields",
            fid,
            fid,
            "range",
            _field_allowed(f),
            f,
            None,
        )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _strip(group: dict[str, Any], name: str) -> dict[str, Any]:
    payload = copy.deepcopy(group)
    for k in _SERVER_KEYS:
        payload.pop(k, None)
    payload["name"] = name
    return payload


def _default_level(leaf: Leaf) -> str:
    # For fields there is no descriptor default here; fall back to "view".
    return "view"


def apply_levels(
    payload: dict[str, Any], meta: dict[str, Any], chooser: Callable[[Leaf], str]
) -> dict[str, Any]:
    """Mutate ``payload`` in place, setting each leaf to ``chooser(leaf)``."""
    for leaf in enumerate_leaves(payload, meta):
        level = chooser(leaf)
        if level not in leaf.allowed_access:
            # snap to nearest allowed at-or-below, else lowest allowed
            below = [
                a for a in leaf.allowed_access if level_index(a) <= level_index(level)
            ]
            level = below[-1] if below else leaf.allowed_access[0]
        leaf.set_level(level)
    return payload


def build_default_group_payload(
    name: str, template: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """Clone ``template``'s shape and reset every leaf to its meta default."""
    payload = _strip(template, name)
    section_defaults = {}
    for s in meta.get("sections", []):
        section_defaults[(s["key"], "enabled")] = s.get("default")
        for p in s.get("permissions", []):
            section_defaults[(s["key"], p["key"])] = p.get("default")
    co_defaults = {c["key"]: c.get("default") for c in meta.get("custom_objects", [])}
    contact_defaults = {c["key"]: c.get("default") for c in meta.get("contacts", [])}
    dcf = {
        k: v.get("default") for k, v in meta.get("default_contact_fields", {}).items()
    }
    dof = {
        k: v.get("default")
        for k, v in meta.get("default_custom_object_fields", {}).items()
    }

    def chooser(leaf: Leaf) -> str:
        if leaf.area == "section":
            raw = section_defaults.get((leaf.block_key, leaf.row_key))
        elif leaf.area == "contacts":
            raw = contact_defaults.get(leaf.row_key)
        elif leaf.area == "object":
            raw = co_defaults.get(leaf.row_key)
        elif leaf.area == "field" and leaf.category == "default_fields":
            raw = dcf.get(leaf.row_key, dof.get(leaf.row_key, "view"))
        else:
            raw = "view"
        return _coerce_default(raw, leaf.allowed_access)

    return apply_levels(payload, meta, chooser)


def _coerce_default(raw: Any, allowed: list[str]) -> str:
    if isinstance(raw, bool):
        if raw:
            non_none = [a for a in allowed if a != "none"]
            return non_none[-1] if non_none else "view"
        return "none"
    if raw in ORDER:
        return raw
    return allowed[0] if allowed else "none"

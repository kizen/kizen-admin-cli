"""Live→wire translation for automations (GET response → PUT payload).

The automations API reads and writes different dialects:

* GET returns steps with server ``id``s, ``parent_step_id`` linkage, and
  ``parent_condition`` ("yes"/"no"/"") — and returns ``key: null`` (the
  client-supplied keys sent on write are NOT stored), so a GET→edit→PUT
  cycle must synthesize fresh keys and rewrite all id-based
  cross-references (``parent_step_id``, ``go_to_automation_step.step``) to
  those keys. Step/trigger ``id``s are a *separate* matter from ``key``:
  echoing a step's or trigger's real ``id`` back on PUT preserves that
  step/trigger's identity — and its execution history — across the write;
  omitting ``id`` gets a fresh server-assigned one every time, which
  orphans prior executions' history against the old id. This module always
  echoes the ``id`` it already has from the GET, since every step/trigger
  it touches is by definition one that already exists.
* PUT takes ``key`` / ``parent_key`` / ``parent_yes_no`` linkage, bare
  UUIDs where reads return expanded ``{id, name, …}`` objects, and requires
  sequential trigger ``order`` values (reads return gaps/nulls).
* The server accepts disconnected step graphs without complaint (observed
  live: an automation with three roots and no linkage), so
  :func:`validate_payload` gates every PUT on our side.

Per-type wire knowledge lives in the planner builders
(:mod:`kizen_builder.tools.planners.automations`); this module reuses them
where they exist and falls back to a mechanical strip for types that don't
have a builder yet. The empirical contract is round-trip fidelity:
``PUT(live_to_payload(GET(x)))`` followed by a fresh GET must be a semantic
no-op (see :func:`semantic_diff`), verified by
``kizen automations roundtrip``.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.tools.planners.automations import (
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    _block_field_for,
    _merge_server_state,
    _prefix_for,
    _strip,
)
from kizen_builder.tools.plans import PlanError


class _NoLookupContext:
    """Live data is already fully resolved; any lookup means a translation bug."""

    def __getattr__(self, name: str) -> Any:
        def _fail(*args: Any, **kwargs: Any) -> Any:
            raise PlanError(
                f"live→wire translation unexpectedly needed a live lookup "
                f"({name} {args}); the GET response should already contain UUIDs"
            )

        return _fail


class _NoTargetAuto:
    """Stand-in AutomationDef: builders only touch ``target_object``."""

    target_object = None


_SHIM_CTX = _NoLookupContext()
_SHIM_AUTO = _NoTargetAuto()


# ---------------------------------------------------------------------------
# Key synthesis
# ---------------------------------------------------------------------------


def _sorted_steps(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(raw.get("steps") or [], key=lambda s: s.get("order") or 0)


def _sorted_triggers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        raw.get("triggers") or [],
        key=lambda t: t.get("order") if t.get("order") is not None else 0,
    )


def synthesize_step_keys(raw: dict[str, Any]) -> dict[str, str]:
    """Map server step UUID → a fresh, readable, unique key."""
    return {
        s["id"]: f"s{i:02d}_{s['step_type']}" for i, s in enumerate(_sorted_steps(raw))
    }


def synthesize_trigger_keys(raw: dict[str, Any]) -> dict[str, str]:
    return {
        t["id"]: f"t{i}_{t['trigger_type']}"
        for i, t in enumerate(_sorted_triggers(raw))
    }


# ---------------------------------------------------------------------------
# live → PUT payload
# ---------------------------------------------------------------------------


def live_to_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw GET automation response into a full PUT payload.

    The payload is a semantic no-op: applying it unchanged must leave the
    automation identical (modulo rotated step/trigger UUIDs and revision).
    Callers that patch a step mutate the returned payload before PUT.
    """
    step_keys = synthesize_step_keys(raw)
    trigger_keys = synthesize_trigger_keys(raw)

    payload: dict[str, Any] = {
        "name": raw["name"],
        "api_name": raw["api_name"],
        "type": raw["type"],
        "active": raw.get("active", False),
        "skip_non_working_days": raw.get("skip_non_working_days", False),
        "return_all_steps_errors": True,
    }
    if raw.get("custom_object"):
        payload["custom_object_id"] = raw["custom_object"]["id"]
    if raw.get("user_description"):
        payload["user_description"] = raw["user_description"]
    if raw.get("error_notification_email") is not None:
        payload["error_notification_email"] = raw["error_notification_email"]

    payload["triggers"] = [
        _live_trigger_to_wire(t, key=trigger_keys[t["id"]], order=i)
        for i, t in enumerate(_sorted_triggers(raw))
    ]
    payload["steps"] = [
        _live_step_to_wire(s, step_keys, trigger_keys) for s in _sorted_steps(raw)
    ]
    # Carries folder/variables/throttles, injects variable ids into
    # initialize_variable steps, stamps last_revision.
    return _merge_server_state(payload, raw)


def _live_step_to_wire(
    step: dict[str, Any],
    step_keys: dict[str, str],
    trigger_keys: dict[str, str],
) -> dict[str, Any]:
    step_type = step["step_type"]
    branch = step.get("parent_condition") or ""
    parent_id = step.get("parent_step_id")

    action_on_failure = step.get("action_on_failure") or "notify_continue"
    if step_type == "condition" and action_on_failure == "notify_continue":
        action_on_failure = "notify_pause"

    p: dict[str, Any] = {
        "id": step["id"],
        "key": step_keys[step["id"]],
        "parent_key": step_keys.get(parent_id) if parent_id else None,
        "parent_yes_no": branch,
        "parent_condition": branch,
        "type": step_type,
        "prefix": _prefix_for(step_type),
        "order": step.get("order") or 0,
        "user_description": step.get("user_description") or "",
        "action_on_failure": action_on_failure,
        "should_skip_execution": step.get("should_skip_execution", False),
        "goal_type": step_type == "goal",
    }
    if step.get("description"):
        p["description"] = step["description"]

    cfg_key = _block_field_for(step_type)
    block = dict(step.get(cfg_key) or {})

    if step_type == "go_to_automation_step":
        block = _rewire_go_to(block, step_keys, trigger_keys)

    builder = _STEP_BUILDERS.get(step_type)
    if builder is not None:
        p[cfg_key] = builder(block, _SHIM_AUTO, _SHIM_CTX)
    else:
        p[cfg_key] = _generic_block(block)
    return p


def _rewire_go_to(
    block: dict[str, Any],
    step_keys: dict[str, str],
    trigger_keys: dict[str, str],
) -> dict[str, Any]:
    """Rewrite id-based go_to references to synthesized keys.

    Read shape is ``{step: {id, …}, trigger: {id, …}|null}``; the ids belong
    to the current revision and rotate on PUT, so they must become keys that
    resolve within the payload being sent.
    """
    out = dict(block)
    target = out.pop("step", None)
    if isinstance(target, dict) and target.get("id"):
        target_id = target["id"]
        if target_id not in step_keys:
            raise PlanError(
                f"go_to_automation_step points at step id {target_id} which is "
                "not part of this automation"
            )
        out["step_key"] = step_keys[target_id]
    trig = out.pop("trigger", None)
    if isinstance(trig, dict) and trig.get("id"):
        trig_id = trig["id"]
        if trig_id not in trigger_keys:
            raise PlanError(
                f"go_to_automation_step points at trigger id {trig_id} which is "
                "not part of this automation"
            )
        out["trigger_key"] = trigger_keys[trig_id]
    return out


def _live_trigger_to_wire(
    trigger: dict[str, Any], key: str, order: int
) -> dict[str, Any]:
    trigger_type = trigger["trigger_type"]
    p: dict[str, Any] = {
        "id": trigger["id"],
        "key": key,
        "type": trigger_type,
        "prefix": "trigger",
        "user_description": trigger.get("user_description") or "",
        "should_skip_execution": trigger.get("should_skip_execution", False),
        "order": order,
    }
    if trigger.get("description"):
        p["description"] = trigger["description"]
    if trigger.get("skip_non_working_days") is not None:
        p["skip_non_working_days"] = trigger["skip_non_working_days"]

    cfg_key = f"trigger_{trigger_type}"
    block = dict(trigger.get(cfg_key) or {})
    builder = _TRIGGER_BUILDERS.get(trigger_type)
    if builder is not None:
        p[cfg_key] = builder(block, _SHIM_CTX)
    else:
        p[cfg_key] = _generic_block(block)
    return p


def _generic_block(block: dict[str, Any]) -> dict[str, Any]:
    """Fallback for types without a builder: mechanical read-only strip.

    Deliberately conservative — keeps nested ids and expanded objects.
    Round-trip failures against live are the signal to promote a type to a
    real builder in the planner module.
    """
    return _strip(block)


# ---------------------------------------------------------------------------
# Payload validation (the server does NOT do this)
# ---------------------------------------------------------------------------


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Structural checks on a PUT payload. Returns a list of problems.

    The live API silently accepts disconnected graphs, duplicate keys, and
    dangling parents — each of which renders as a corrupt automation in the
    UI — so every write path must pass this first.
    """
    problems: list[str] = []
    steps = payload.get("steps") or []
    keys = [s.get("key") for s in steps]
    key_set = set(keys)

    if len(keys) != len(key_set):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        problems.append(f"duplicate step keys: {dupes}")

    ids = [s["id"] for s in steps if s.get("id")]
    for s in steps:
        if s.get("type") == "goal":
            ids.extend(
                t["id"]
                for t in (s.get("step_goal") or {}).get("triggers") or []
                if t.get("id")
            )
    ids.extend(t["id"] for t in (payload.get("triggers") or []) if t.get("id"))
    if len(ids) != len(set(ids)):
        dupe_ids = sorted({i for i in ids if ids.count(i) > 1})
        problems.append(
            f"duplicate step/trigger ids (would merge separate steps' "
            f"execution history on write): {dupe_ids}"
        )

    by_key = {s.get("key"): s for s in steps}
    roots = [s for s in steps if not s.get("parent_key")]
    if steps and len(roots) != 1:
        problems.append(
            f"expected exactly 1 root step, found {len(roots)} "
            f"({[s.get('key') for s in roots]})"
        )

    for s in steps:
        pk = s.get("parent_key")
        if pk and pk not in key_set:
            problems.append(f"step '{s.get('key')}' has dangling parent_key '{pk}'")
        branch = s.get("parent_yes_no") or ""
        if branch:
            parent = by_key.get(pk)
            if parent is not None and parent.get("type") not in ("condition", "goal"):
                problems.append(
                    f"step '{s.get('key')}' sets parent_yes_no='{branch}' but its "
                    f"parent '{pk}' is a '{parent.get('type')}', not condition/goal"
                )
        goto = s.get("action_go_to_automation_step")
        if goto and goto.get("step_key"):
            target = by_key.get(goto["step_key"])
            if target is None:
                problems.append(
                    f"step '{s.get('key')}' go_to target '{goto['step_key']}' "
                    "does not exist"
                )
            elif target.get("type") == "initialize_variable":
                problems.append(
                    f"step '{s.get('key')}' go_to targets initialize_variable "
                    f"'{goto['step_key']}' — the server rejects this (400)"
                )

    # Cycle check via parent chain
    for s in steps:
        seen: set[str] = set()
        cur = s.get("key")
        while cur:
            if cur in seen:
                problems.append(f"cycle in parent chain involving '{s.get('key')}'")
                break
            seen.add(cur)
            nxt = by_key.get(cur)
            cur = nxt.get("parent_key") if nxt else None

    # Server rule: initialize_variable steps must all come before any other
    # step ("All Initialize Variable steps should be at the beginning of the
    # automation" — HTTP 400 otherwise).
    init_orders = [
        s.get("order") or 0 for s in steps if s.get("type") == "initialize_variable"
    ]
    other_orders = [
        s.get("order") or 0 for s in steps if s.get("type") != "initialize_variable"
    ]
    if init_orders and other_orders and max(init_orders) > min(other_orders):
        problems.append(
            "initialize_variable steps must all be at the front of the "
            "automation, before every other step (server 400s otherwise)"
        )

    orders = [t.get("order") for t in payload.get("triggers") or []]
    if sorted(orders) != list(range(len(orders))):
        problems.append(f"trigger orders must be sequential from 0: got {orders}")
    return problems


# ---------------------------------------------------------------------------
# Semantic diff (GET-before vs GET-after, modulo volatile fields)
# ---------------------------------------------------------------------------

_VOLATILE_TOP_KEYS = {
    "id",
    "created",
    "updated",
    "revision",
    "number_active",
    "number_paused",
    "number_completed",
    "steps",
    "triggers",
}
# `created` appears on nested definition objects (automation variables,
# webhook extractors, …) that the server recreates wholesale on every PUT.
_VOLATILE_NESTED_KEYS = {"stats", "has_error", "step_error", "created"}
# Derived mirrors of the parent linkage; stale copies corrupt UI rendering
# and their ids rotate, so they are excluded from comparison.
_DERIVED_CONDITION_KEYS = {"yes_steps", "no_steps", "groups"}


def canonicalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce a GET response to a form stable across no-op PUTs.

    Step/trigger UUIDs rotate on every PUT, so steps are identified by
    position in order-sorted sequence and id-based cross-references become
    ``<step:N>`` markers. Nested object ids are dropped (compared by their
    remaining keys) except singleton ``{id: …}`` dicts, which have nothing
    else to compare by.
    """
    steps = _sorted_steps(raw)
    idx = {s["id"]: n for n, s in enumerate(steps)}

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out = {}
            for k, val in v.items():
                if k in _VOLATILE_NESTED_KEYS:
                    continue
                if k == "id":
                    if isinstance(val, str) and val in idx:
                        out[k] = f"<step:{idx[val]}>"
                    elif len(v) == 1:
                        out[k] = val
                    continue
                out[k] = walk(val)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and v in idx:
            return f"<step:{idx[v]}>"
        return v

    canon_steps = []
    for s in steps:
        c = {
            k: walk(v)
            for k, v in s.items()
            if k not in ("id", "parent_step_id") and k not in _VOLATILE_NESTED_KEYS
        }
        parent_id = s.get("parent_step_id")
        c["parent"] = f"<step:{idx[parent_id]}>" if parent_id in idx else None
        cond = c.get("step_condition")
        if isinstance(cond, dict):
            for derived in _DERIVED_CONDITION_KEYS:
                cond.pop(derived, None)
        canon_steps.append(c)

    canon_triggers = [
        {
            k: walk(v)
            for k, v in t.items()
            if k != "id" and k not in _VOLATILE_NESTED_KEYS
        }
        for t in _sorted_triggers(raw)
    ]

    top = {
        k: walk(v)
        for k, v in raw.items()
        if k not in _VOLATILE_TOP_KEYS and k not in _VOLATILE_NESTED_KEYS
    }
    return {"top": top, "triggers": canon_triggers, "steps": canon_steps}


def semantic_diff(
    before_raw: dict[str, Any], after_raw: dict[str, Any]
) -> list[tuple[str, Any, Any]]:
    """Differences between two GET responses, ignoring volatile fields.

    Returns ``[]`` when the automations are semantically identical —
    the pass condition for a round-trip.
    """
    return _diff(canonicalize(before_raw), canonicalize(after_raw), "")


def _diff(a: Any, b: Any, path: str) -> list[tuple[str, Any, Any]]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[tuple[str, Any, Any]] = []
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append((sub, "<absent>", b[k]))
            elif k not in b:
                out.append((sub, a[k], "<absent>"))
            else:
                out.extend(_diff(a[k], b[k], sub))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            sub = f"{path}[{i}]"
            if i >= len(a):
                out.append((sub, "<absent>", b[i]))
            elif i >= len(b):
                out.append((sub, a[i], "<absent>"))
            else:
                out.extend(_diff(a[i], b[i], sub))
        return out
    if a != b:
        return [(path, a, b)]
    return []


# ---------------------------------------------------------------------------
# Wire diff (live payload vs. spec-as-applied payload)
# ---------------------------------------------------------------------------

# Per-side synthetic naming, not automation content — excluded from
# comparison so a spec that changes nothing produces an empty diff even
# though `key` is resynthesized from live order on one side and authored by
# hand on the other (see `automation.md`'s "GET and PUT are different
# dialects").
_WIRE_DIFF_EXCLUDED = {"key", "parent_key", "prefix"}


def _wire_pairs(
    live_items: list[dict[str, Any]], spec_items: list[dict[str, Any]]
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    """Match live and spec steps/triggers: by ``id`` when both sides have
    one, then by position among what's left.

    Position fallback covers a spec authored from scratch with no ``id`` at
    all — the same trick :func:`canonicalize` uses for round-trip identity,
    adapted to match across two different payloads instead of one payload
    before/after a write. A spec item that *does* carry an ``id``, but one
    that matches no live item, is not a position-fallback candidate: a real
    ``id`` naming a step that doesn't exist live cannot be that step under
    any interpretation, so it is always an addition (and whatever live item
    is left over is a removal), never merged into an unrelated step by
    position.
    """
    spec_by_id = {s["id"]: s for s in spec_items if s.get("id")}
    consumed: set[int] = set()
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    leftover_live: list[dict[str, Any]] = []
    for live_item in live_items:
        match = spec_by_id.get(live_item.get("id"))
        if match is not None and id(match) not in consumed:
            pairs.append((live_item, match))
            consumed.add(id(match))
        else:
            leftover_live.append(live_item)

    leftover_spec = [s for s in spec_items if id(s) not in consumed]
    dangling_spec = [s for s in leftover_spec if s.get("id")]
    unidentified_spec = [s for s in leftover_spec if not s.get("id")]

    pairs.extend((None, s) for s in dangling_spec)
    for i in range(max(len(leftover_live), len(unidentified_spec))):
        pairs.append(
            (
                leftover_live[i] if i < len(leftover_live) else None,
                unidentified_spec[i] if i < len(unidentified_spec) else None,
            )
        )
    return pairs


def _wire_pair_id(
    pair: tuple[dict[str, Any] | None, dict[str, Any] | None],
) -> str | None:
    """The real id this matched pair shares — live's if it has one (it
    always does; ``live_to_payload`` echoes every step/trigger's id),
    otherwise spec's, otherwise ``None`` for a brand-new step/trigger the
    spec hasn't assigned one to yet."""
    live_item, spec_item = pair
    if live_item and live_item.get("id"):
        return live_item["id"]
    if spec_item and spec_item.get("id"):
        return spec_item["id"]
    return None


def _wire_pair_label(
    pair: tuple[dict[str, Any] | None, dict[str, Any] | None],
) -> str:
    """The path label for one matched pair: the first octet of its id — the
    same ``[:8]`` convention as `tools/planners/dashboards.py`'s plan preview
    and `tools/plans.py`'s plan id — so a diff line can be correlated with
    what's visible in the UI. Falls back to the spec-authored `key` only for
    a brand-new step/trigger with no id anywhere yet.
    """
    pair_id = _wire_pair_id(pair)
    if pair_id:
        return pair_id[:8]
    live_item, spec_item = pair
    key = (spec_item or live_item or {}).get("key") or "?"
    return f"new:{key}"


def _wire_key_labels(
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]], side: int
) -> dict[str, str]:
    """Map one side's raw ``key`` to its pair's label, for resolving
    ``parent_key`` into matched identity (see `_normalize_wire_steps`)."""
    labels: dict[str, str] = {}
    for pair in pairs:
        item = pair[side]
        if item is not None and item.get("key") is not None:
            labels[item["key"]] = _wire_pair_label(pair)
    return labels


def _normalize_wire_triggers(
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]], side: int
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        item = pair[side]
        if item is None:
            continue
        norm = {k: v for k, v in item.items() if k not in _WIRE_DIFF_EXCLUDED | {"id"}}
        # `id` is carried as a normal field, but pinned to the pair's shared
        # canonical value on both sides so a matched pair never diffs on it
        # (position-fallback matches can have an id on only one side) while
        # still surfacing the real, untruncated id for `--json` on a bare
        # addition/removal.
        norm["id"] = _wire_pair_id(pair)
        out[_wire_pair_label(pair)] = norm
    return out


_GO_TO_BLOCK_FIELD = _block_field_for("go_to_automation_step")


def _normalize_wire_steps(
    step_pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]],
    trigger_pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]],
    side: int,
) -> dict[str, Any]:
    step_key_labels = _wire_key_labels(step_pairs, side)
    trigger_key_labels = _wire_key_labels(trigger_pairs, side)
    out: dict[str, Any] = {}
    for pair in step_pairs:
        item = pair[side]
        if item is None:
            continue
        norm = {k: v for k, v in item.items() if k not in _WIRE_DIFF_EXCLUDED | {"id"}}
        norm["id"] = _wire_pair_id(pair)
        # Reparenting must still be visible: resolve `parent_key` to the
        # *matched identity* of the parent (mirroring `canonicalize`'s
        # `<step:N>` markers) instead of dropping it outright — a spec that
        # genuinely moves a step under a different parent shows a real
        # `parent` change even though raw `parent_key` strings are excluded.
        parent_key = item.get("parent_key")
        norm["parent"] = step_key_labels.get(parent_key) if parent_key else None
        # A `go_to_automation_step` reference is the same situation as
        # `parent_key`: it names its target by this side's own `key`, which
        # is separately synthesized (live) or authored (spec). Resolve it to
        # the target's matched-pair identity so pointing at the *same* step
        # compares equal across a cosmetic rekey; an unresolvable key (no
        # matching pair) is left as-is, so a genuine retarget or a dangling
        # reference still surfaces as a real change.
        go_to = norm.get(_GO_TO_BLOCK_FIELD)
        if isinstance(go_to, dict):
            resolved = dict(go_to)
            if resolved.get("step_key") is not None:
                resolved["step_key"] = step_key_labels.get(
                    resolved["step_key"], resolved["step_key"]
                )
            if resolved.get("trigger_key") is not None:
                resolved["trigger_key"] = trigger_key_labels.get(
                    resolved["trigger_key"], resolved["trigger_key"]
                )
            norm[_GO_TO_BLOCK_FIELD] = resolved
        out[_wire_pair_label(pair)] = norm
    return out


def diff_wire_payloads(
    live: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compare two PUT-dialect payloads for the *same* automation — the live
    automation (via :func:`live_to_payload`) against the payload a spec's
    `automations update` would send — and return what would actually change.

    Unlike :func:`semantic_diff` (GET-dialect, same automation before/after a
    write, steps identified by position because the structure can't change),
    the two sides here can legitimately differ in structure: a step can be
    added, removed, or reparented. Steps/triggers are matched by `id` first
    (regardless of `key`/order); position among the remainder is a fallback
    only for spec items with no `id` at all — see :func:`_wire_pairs`.
    `key`/`parent_key`/`prefix` are excluded from the field-by-field
    comparison as per-side synthetic naming, not automation content; a
    `go_to_automation_step` reference is resolved to its target's matched
    identity the same way `parent_key` is, so it survives key resynthesis too.

    Returns ``[{"path", "before", "after"}, ...]`` — the same shape
    `roundtrip_automation`'s `drift` field already uses. A step/trigger only
    on one side (`before` or `after` is the literal `"<absent>"` sentinel) is
    an addition or removal; anything else is a changed field. `path` embeds
    each step/trigger's first-id-octet label so a line can be matched to the
    UI; the full id still travels in the leaf `before`/`after` values for
    additions/removals (an addition/removal's value is the whole normalized
    step/trigger dict, `id` included) since those are consumed by programs.
    """
    trigger_pairs = _wire_pairs(live.get("triggers") or [], spec.get("triggers") or [])
    step_pairs = _wire_pairs(live.get("steps") or [], spec.get("steps") or [])

    norm_live = {k: v for k, v in live.items() if k not in ("triggers", "steps")}
    norm_spec = {k: v for k, v in spec.items() if k not in ("triggers", "steps")}
    norm_live["triggers"] = _normalize_wire_triggers(trigger_pairs, 0)
    norm_spec["triggers"] = _normalize_wire_triggers(trigger_pairs, 1)
    norm_live["steps"] = _normalize_wire_steps(step_pairs, trigger_pairs, 0)
    norm_spec["steps"] = _normalize_wire_steps(step_pairs, trigger_pairs, 1)

    return [
        {"path": p, "before": a, "after": b}
        for p, a, b in _diff(norm_live, norm_spec, "")
    ]

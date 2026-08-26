"""Plan creation/update for automations.

Wire format (reverse-engineered from captured UI payloads):

* Steps are linked via ``parent_key``. Branches under a ``condition``/``goal``
  step set ``parent_yes_no: "yes"|"no"`` on the FIRST step of each branch.
  Linear successors leave ``parent_yes_no`` empty.
* The condition step's ``yes_step_ids`` field is NEVER used — that field
  returns HTTP 500 in the live API.
* Step and trigger ``key``s are client-supplied strings, scoped to one PUT —
  they only need to be unique within that payload. Identity that persists
  *across* PUTs (and with it, execution history) is carried by ``id``, not
  ``key``: a step/trigger whose spec sets ``id`` gets that same id echoed
  back and keeps it; one that doesn't gets a fresh server-assigned id every
  time. This is a deliberate no-state-file model: we don't track step UUIDs
  across sessions ourselves, but a spec authored from a live read (e.g.
  seeded from ``kizen automations show``, which round-trips ``id``) can opt
  in.
* Read responses expand reference fields (``field_to_modify``,
  ``target_custom_object``, ``activity_type``, etc.) into full ``{id, name,
  …}`` objects. Writes take just the bare UUID for those fields.

The action/trigger config Pydantic models are configured with
``extra="allow"`` (see :mod:`kizen_builder.models.spec`), so they accept
both human-authored specs (``api_name`` refs, ``field_refs``) and the
richer shapes returned by the live API (full reference objects with
``id``/``name``/etc.). The dispatch normalizes either input to wire form.

Each step type's payload-builder accepts the union of those two shapes and
applies known wire-format transformations:

* fields with ``_ref: "object.field"`` get resolved to UUIDs via the
  :class:`LiveContext`;
* nested reference objects (``{id, name, …}``) get reduced to ``{id: uuid}``
  or to bare UUID strings, depending on the wire field;
* read-only fields (``id`` on action items, ``stats``, ``has_error``,
  ``deleted``, ``related_*`` metadata) get stripped.

Adding a new step type means: write a builder function, add it to
:data:`_STEP_BUILDERS`. Same for triggers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import (
    AutomationDef,
    AutomationStepDef,
    AutomationTriggerDef,
)
from kizen_builder.tools import merge_fields
from kizen_builder.tools.automations import get_automation, list_automations
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation
from kizen_builder.utils import is_uuid

# ---------------------------------------------------------------------------
# Live-context cache (fetched once per plan call)
# ---------------------------------------------------------------------------


class LiveContext:
    """All the live lookups one plan call needs.

    Lazy: only the lookups that are actually referenced hit the API. Keeps
    typical plan calls down to one ``list_automations`` (collision check)
    plus one ``get_object`` for the target.
    """

    def __init__(self) -> None:
        self.env = load_env_config().name
        # Namespaced by key, not just by object api_name: plain api_name keys
        # hold the object dict, `__fields__{api_name}` keys hold that
        # object's fields list (see `_fields_with_options`) — hence `Any`
        # rather than a single value shape.
        self._objects_by_api: dict[str, Any] = {}
        self._automations_by_api: dict[str, dict[str, Any]] | None = None

    def object_uuid(self, api_name: str) -> str:
        return self.object_data(api_name)["id"]

    def field_uuid(self, object_api_name: str, field_api_name: str) -> str:
        return self._field(object_api_name, field_api_name)["id"]

    def field_data(self, object_api_name: str, field_api_name: str) -> dict[str, Any]:
        """Return the full live field record (id, field_type, options, …) with options populated."""
        # The top-level get_object response often omits options; use the /fields endpoint.
        fields = self._fields_with_options(object_api_name)
        match = next(
            (
                f
                for f in fields
                if f.get("name") == field_api_name
                or f.get("api_name") == field_api_name
            ),
            None,
        )
        if match:
            return match
        # Fall back to the object-level data (id/type without options)
        return self._field(object_api_name, field_api_name)

    def _fields_with_options(self, object_api_name: str) -> list[dict[str, Any]]:
        cache_key = f"__fields__{object_api_name}"
        if cache_key not in self._objects_by_api:
            obj = self.object_data(object_api_name)
            from kizen_builder.api import custom_objects as co_api
            from kizen_builder.api.client import KizenClient

            cfg = load_env_config()
            with KizenClient(cfg) as client:
                self._objects_by_api[cache_key] = co_api.list_fields(client, obj["id"])
        return self._objects_by_api[cache_key]

    def _field(self, object_api_name: str, field_api_name: str) -> dict[str, Any]:
        obj = self.object_data(object_api_name)
        match = next(
            (f for f in obj["fields"] if f["api_name"] == field_api_name), None
        )
        if match is None:
            available = [f["api_name"] for f in obj["fields"]]
            raise PlanError(
                f"field '{field_api_name}' not found on '{object_api_name}' in "
                f"'{self.env}'. Available: {available}"
            )
        return match

    def find_relationship_field(
        self, from_object_api: str, to_object_api: str
    ) -> str | None:
        """UUID of the single relationship field on ``from_object_api`` that
        points at ``to_object_api``, for auto-detecting an LLM destination's
        relationship hop. Returns None if there are zero or more than one
        candidate — ambiguous cases must name the hop explicitly.
        """
        obj = self.object_data(from_object_api)
        matches = [
            f for f in obj["fields"] if f.get("relation_target") == to_object_api
        ]
        return matches[0]["id"] if len(matches) == 1 else None

    def automation_uuid(self, api_name: str) -> str | None:
        if self._automations_by_api is None:
            self._automations_by_api = {a["api_name"]: a for a in list_automations()}
        rec = self._automations_by_api.get(api_name)
        return rec["id"] if rec else None

    def filter_group_uuid(self, object_api_name: str, identifier: str) -> str:
        """UUID of a saved filter group on ``object_api_name`` by name or UUID."""
        from kizen_builder.api.saved_views import FILTER_GROUPS_BASE
        from kizen_builder.tools import saved_views

        try:
            return saved_views.find_saved_view(
                object_api_name, FILTER_GROUPS_BASE, identifier
            )["id"]
        except LookupError as e:
            raise PlanError(str(e)) from e

    def form_uuid(self, identifier: str) -> str:
        return self._form_survey_uuid(identifier, base_path="/api/forms")

    def survey_uuid(self, identifier: str) -> str:
        return self._form_survey_uuid(identifier, base_path="/api/surveys")

    def _form_survey_uuid(self, identifier: str, *, base_path: str) -> str:
        from kizen_builder.api.client import KizenClient
        from kizen_builder.tools.forms import resolve_form_id

        cfg = load_env_config()
        with KizenClient(cfg) as client:
            try:
                form_id, _name = resolve_form_id(client, base_path, identifier)
            except LookupError as e:
                raise PlanError(str(e)) from e
        return form_id

    def object_data(self, api_name: str) -> dict[str, Any]:
        """The full live object record (id, display_name, fields, …).

        Not private: `merge_fields.py`'s objectname resolver needs it from
        outside this class (an object's `display_name` is the `objectname`
        merge-field spans carry for a real custom-object namespace).
        """
        if api_name not in self._objects_by_api:
            try:
                self._objects_by_api[api_name] = get_object(api_name)
            except LookupError as e:
                raise PlanError(f"object '{api_name}' not found: {e}") from e
        return self._objects_by_api[api_name]


# ---------------------------------------------------------------------------
# Public planners
# ---------------------------------------------------------------------------


def plan_create_automation(automation: dict[str, Any] | AutomationDef) -> Plan:
    """Plan the creation of one automation.

    Pulls live state to verify the target_object exists, resolves field
    references, and refuses if an automation with the same api_name is
    already present.
    """
    auto = (
        automation
        if isinstance(automation, AutomationDef)
        else AutomationDef.model_validate(automation)
    )
    ctx = LiveContext()
    env = ctx.env

    existing = next(
        (a for a in list_automations() if a["api_name"] == auto.api_name),
        None,
    )
    if existing is not None:
        raise PlanError(
            f"automation '{auto.api_name}' already exists "
            f"(uuid {existing['id']}). Use plan_update_automation."
        )

    # A create spec has no live state to preserve against, so an omitted
    # `active` resolves to False — the documented "author inactive, then
    # activate" default (docs/specs/automation.md).
    if auto.active is None:
        auto = auto.model_copy(update={"active": False})

    payload = _build_automation_payload(auto, ctx)
    return Plan.build(
        env=env,
        summary=f"Create automation '{auto.api_name}'",
        operations=[
            PlanOperation(
                action="create",
                kind="automation",
                key=auto.api_name,
                preview=_automation_preview(env, auto),
                payload=payload,
            )
        ],
    )


def plan_update_automation(automation: dict[str, Any] | AutomationDef) -> Plan:
    """Plan an update to an existing automation."""
    auto = (
        automation
        if isinstance(automation, AutomationDef)
        else AutomationDef.model_validate(automation)
    )
    ctx = LiveContext()
    env = ctx.env

    existing = next(
        (a for a in list_automations() if a["api_name"] == auto.api_name),
        None,
    )
    if existing is None:
        raise PlanError(
            f"no automation with api_name '{auto.api_name}'. "
            "Use plan_create_automation."
        )

    # Pull the full live automation: PUT replaces the whole entity, so the
    # payload must carry server-managed state, and the revision must be known
    # at plan time so the previewed payload is exactly what apply sends.
    current = get_automation(auto.api_name)["raw"]

    # An omitted `active` preserves whatever the live automation already is,
    # rather than defaulting to False — the bug this item fixes. An explicit
    # value in the spec still wins either direction.
    live_active = bool(current.get("active", False))
    resolved_active = auto.active if auto.active is not None else live_active
    auto = auto.model_copy(update={"active": resolved_active})

    payload = _merge_server_state(_build_automation_payload(auto, ctx), current)
    preview = _automation_preview(env, auto, live_active=live_active)
    preview["current_revision"] = current.get("revision")
    return Plan.build(
        env=env,
        summary=f"Update automation '{auto.api_name}'",
        operations=[
            PlanOperation(
                action="update",
                kind="automation",
                key=auto.api_name,
                preview=preview,
                payload=payload,
                existing_uuid=existing["id"],
            )
        ],
    )


def _merge_server_state(
    payload: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Fold live server-managed state into an update payload.

    PUT replaces the whole automation, so fields the spec doesn't model
    (folder, variables, throttles, …) must be carried over from the live
    automation or the API rejects the write / drops them. Spec-provided
    values win over carried-over ones. Also stamps ``last_revision``
    (optimistic concurrency — a concurrent edit between plan and apply
    surfaces as an API error).

    Variable ``id``s are STRIPPED everywhere: the server recreates the
    variable set wholesale on every PUT no matter what (sent ids are not
    preserved), and an ``id`` inside an initialize_variable step's variable
    definition makes the PUT crash with HTTP 500 (observed live on
    update_current_price / daily_trigger / form_submission). Variables are
    matched by name.
    """
    server_fields: dict[str, Any] = {}
    if current.get("folder") is not None:
        server_fields["folder"] = current["folder"]
    if current.get("variables"):
        # Same read→write reduction as step-level variable definitions
        # (expanded data_subtype → bare UUID, ids stripped).
        server_fields["variables"] = [
            _normalize_variable(v) for v in current["variables"]
        ]
    for field in (
        "priority_rank",
        "error_notification_severity_level",
        "trigger_throttle_seconds",
        "debug_next_n_executions",
    ):
        if current.get(field) is not None:
            server_fields[field] = current[field]
    merged = {**server_fields, **payload}
    merged["last_revision"] = current.get("revision")
    return merged


# ---------------------------------------------------------------------------
# Lifecycle: delete / duplicate
# ---------------------------------------------------------------------------


def plan_delete_automation(api_name: str) -> Plan:
    """Plan deletion of one automation."""
    env = load_env_config().name
    existing = next((a for a in list_automations() if a["api_name"] == api_name), None)
    if existing is None:
        raise PlanError(f"no automation with api_name '{api_name}'")
    op = PlanOperation(
        action="delete",
        kind="automation",
        key=api_name,
        preview={"env": env, "api_name": api_name, "name": existing.get("name")},
        existing_uuid=existing["id"],
    )
    return Plan.build(
        env=env, summary=f"Delete automation '{api_name}'", operations=[op]
    )


def plan_duplicate_automation(api_name: str, *, name: str | None = None) -> Plan:
    """Plan duplication of one automation.

    Confirmed live — see api/automations.py's duplicate_automation docstring
    for the discovered quirk: the server ignores a custom "name" and always
    auto-names the copy "<original> (copy #N)" itself.
    """
    env = load_env_config().name
    existing = next((a for a in list_automations() if a["api_name"] == api_name), None)
    if existing is None:
        raise PlanError(f"no automation with api_name '{api_name}'")
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    op = PlanOperation(
        action="duplicate",
        kind="automation",
        key=f"{api_name}:duplicate",
        preview={
            "env": env,
            "source": api_name,
            "requested_name": name
            or "(none — server auto-names it '<original> (copy #N)')",
        },
        payload=payload,
        existing_uuid=existing["id"],
    )
    return Plan.build(
        env=env, summary=f"Duplicate automation '{api_name}'", operations=[op]
    )


# ---------------------------------------------------------------------------
# Folders (org/navigation) — confirmed live (create/update/delete round
# tripped against a throwaway folder); see api/automations.py's folders
# section for the one wire-format surprise (update is PATCH, not PUT).
# ---------------------------------------------------------------------------


def _live_folders() -> list[dict[str, Any]]:
    from kizen_builder.api import automations as auto_api
    from kizen_builder.api.client import KizenClient

    cfg = load_env_config()
    with KizenClient(cfg) as client:
        return auto_api.list_folders(client)


def _find_folder(identifier: str, folders: list[dict[str, Any]]) -> dict[str, Any]:
    match = next(
        (f for f in folders if identifier in (f.get("id"), f.get("name"))), None
    )
    if match is None:
        available = [f.get("name") for f in folders]
        raise PlanError(f"folder '{identifier}' not found. Available: {available}")
    return match


def plan_create_folder(name: str, *, parent: str | None = None) -> Plan:
    """Plan creation of one automation folder."""
    env = load_env_config().name
    payload: dict[str, Any] = {"name": name}
    if parent:
        payload["parent_folder_id"] = _find_folder(parent, _live_folders())["id"]
    op = PlanOperation(
        action="create",
        kind="automation_folder",
        key=f"folder:{name}",
        preview={"env": env, "name": name, "parent": parent},
        payload=payload,
    )
    return Plan.build(
        env=env, summary=f"Create automation folder '{name}'", operations=[op]
    )


def plan_update_folder(
    identifier: str, *, name: str | None = None, parent: str | None = None
) -> Plan:
    """Plan an update to one automation folder's name and/or parent."""
    env = load_env_config().name
    folders = _live_folders()
    current = _find_folder(identifier, folders)
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if parent is not None:
        payload["parent_folder_id"] = (
            _find_folder(parent, folders)["id"] if parent else None
        )
        # Server-side bug (confirmed live 2026-08-04): PATCH
        # .../folders/{id} 500s whenever parent_folder_id is sent without
        # name in the same body, even though PatchedWriteAutomationFolderRequest
        # marks both optional. Echoing the current name alongside a
        # parent-only change routes around it.
        payload.setdefault("name", current["name"])
    if not payload:
        raise PlanError("nothing to update — pass --name and/or --parent")
    op = PlanOperation(
        action="update",
        kind="automation_folder",
        key=f"folder:{current.get('name') or identifier}",
        preview={"env": env, **payload},
        payload=payload,
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env, summary=f"Update automation folder '{identifier}'", operations=[op]
    )


def plan_delete_folder(identifier: str) -> Plan:
    """Plan deletion of one automation folder."""
    env = load_env_config().name
    current = _find_folder(identifier, _live_folders())
    op = PlanOperation(
        action="delete",
        kind="automation_folder",
        key=f"folder:{current.get('name') or identifier}",
        preview={"env": env, "name": current.get("name")},
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env, summary=f"Delete automation folder '{identifier}'", operations=[op]
    )


def _automation_preview(
    env: str, auto: AutomationDef, *, live_active: bool | None = None
) -> dict[str, Any]:
    """Build the `--dry-run` preview cell for one automation.

    ``live_active`` is the current live value, passed by
    ``plan_update_automation`` once ``auto.active`` has been resolved to a
    concrete bool. When the resolved value differs from live, the preview
    names it as a transition (`True → False (DEACTIVATES a live
    automation)`) instead of a bare bool, so a reviewer scanning
    `--dry-run` output sees a change, not a value. ``plan_create_automation``
    has no live state to compare against and omits ``live_active``.
    """
    active_display: Any = auto.active
    if live_active is not None and live_active != auto.active:
        direction = (
            "DEACTIVATES a live automation"
            if auto.active is False
            else "ACTIVATES an inactive automation"
        )
        active_display = f"{live_active} → {auto.active} ({direction})"
    return {
        "env": env,
        "api_name": auto.api_name,
        "name": auto.name,
        "type": auto.type,
        "target_object": auto.target_object,
        "active": active_display,
        "trigger_count": len(auto.triggers),
        "step_count": len(auto.steps),
    }


# ---------------------------------------------------------------------------
# Top-level payload builder
# ---------------------------------------------------------------------------


def _build_automation_payload(auto: AutomationDef, ctx: LiveContext) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": auto.name,
        "api_name": auto.api_name,
        "type": auto.type,
        "active": auto.active,
        "skip_non_working_days": auto.skip_non_working_days,
        "return_all_steps_errors": True,
    }
    if auto.target_object:
        payload["custom_object_id"] = ctx.object_uuid(auto.target_object)
    if auto.user_description:
        payload["user_description"] = auto.user_description
    if auto.error_notification_email is not None:
        payload["error_notification_email"] = auto.error_notification_email
    if auto.priority_rank is not None:
        payload["priority_rank"] = auto.priority_rank

    triggers = list(auto.triggers)
    if not any(t.trigger_type == "manual" for t in triggers):
        from kizen_builder.models.spec import AutomationTriggerDef

        manual = AutomationTriggerDef(trigger_type="manual", order=0)
        triggers = [manual] + [
            t.model_copy(update={"order": i + 1}) for i, t in enumerate(triggers)
        ]
    payload["triggers"] = [_build_trigger_payload(t, ctx) for t in triggers]
    _validate_trigger_orders(payload["triggers"])
    payload["steps"] = [_build_step_payload(s, auto, ctx) for s in auto.steps]
    return payload


def _validate_trigger_orders(triggers: list[dict[str, Any]]) -> None:
    """Trigger orders must be exactly {0, ..., N-1} — the server rejects
    anything else with `HTTP 400: triggers: Trigger orders must be
    sequential from 0 to N-1`. A static, purely arithmetic rule, so
    --dry-run can catch it here instead of requiring a live apply-and-fail.
    Left-unset trigger orders all default to 0 (see `_build_trigger_payload`),
    so two-or-more triggers with no explicit `order` in the spec collide.
    """
    orders = sorted(t["order"] for t in triggers)
    if orders != list(range(len(triggers))):
        seen = ", ".join(f"{t['type']}(order={t['order']})" for t in triggers)
        raise PlanError(
            f"trigger orders must be sequential from 0 to {len(triggers) - 1}, "
            f"got: {seen} — set an explicit, unique `order` on each trigger"
        )


# ---------------------------------------------------------------------------
# Helpers — apply common transformations
# ---------------------------------------------------------------------------

# Fields the server returns but rejects on write (read-only / metadata).
_READ_ONLY_KEYS = {"stats", "has_error", "step_error", "deleted"}

# When stripping nested action items (e.g. inside change_field_value.actions[]),
# also drop the per-item `id` since the server assigns those.
_READ_ONLY_KEYS_ON_ACTION_ITEMS = _READ_ONLY_KEYS | {"id"}


def _strip(d: Any, drop: set[str] = _READ_ONLY_KEYS) -> Any:
    """Recursively drop server-only keys from a value tree."""
    if isinstance(d, dict):
        return {k: _strip(v, drop) for k, v in d.items() if k not in drop}
    if isinstance(d, list):
        return [_strip(x, drop) for x in d]
    return d


def _unwrap_id(value: Any) -> str | None:
    """If ``value`` is a dict with ``id``, return the id; if a string, pass through."""
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, str):
        return value
    return None


def _unwrap_variable_name(value: Any) -> str | None:
    """Automation-variable references are matched by name, not id. A live
    GET expands them to the full variable definition dict; writes take the
    bare name string (same convention as _normalize_variable_sources)."""
    if isinstance(value, dict):
        return value.get("name")
    if isinstance(value, str):
        return value
    return None


def _resolve_field_ref(ref: str, auto: AutomationDef, ctx: LiveContext) -> str:
    """Resolve a 'object.field' reference to a field UUID.

    A bare ``ref`` (no dot) is interpreted against the automation's
    ``target_object``.
    """
    if "." in ref:
        obj_api, fld_api = ref.split(".", 1)
    else:
        if not auto.target_object:
            raise PlanError(
                f"field_ref '{ref}' is unqualified and the automation has no "
                "target_object to default to."
            )
        obj_api, fld_api = auto.target_object, ref
    return ctx.field_uuid(obj_api, fld_api)


def _resolve_field(value: Any, auto: AutomationDef, ctx: LiveContext) -> str | None:
    """Generic field resolver: handles dict {id}, dict {api_name} hint, or string.

    Returns a bare UUID. Used for fields like ``field_to_modify`` and the
    nested ``field`` blocks inside code_step inputs/outputs.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Either already a UUID, or an "object.field" ref.
        if is_uuid(value):
            return value
        return _resolve_field_ref(value, auto, ctx)
    if isinstance(value, dict):
        if value.get("id"):
            return value["id"]
        # If only a field api_name is provided, try to resolve via target_object.
        api_name = value.get("name") or value.get("api_name")
        if api_name and auto.target_object:
            return ctx.field_uuid(auto.target_object, api_name)
    return None


def _resolve_object(value: Any, ctx: LiveContext) -> str | None:
    """Resolve an object reference to a UUID (handles dict, api_name, or UUID)."""
    if value is None:
        return None
    if isinstance(value, str):
        if is_uuid(value):
            return value
        # api_name
        return ctx.object_uuid(value)
    if isinstance(value, dict):
        if value.get("id"):
            return value["id"]
        api_name = value.get("name") or value.get("api_name")
        if api_name:
            return ctx.object_uuid(api_name)
    return None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def _trigger_spec_key(t: AutomationTriggerDef) -> str:
    key = getattr(t, "key", None)
    if key:
        return key
    order = t.order if t.order is not None else 0
    return f"trigger.{t.trigger_type}.{order}"


def _build_trigger_payload(t: AutomationTriggerDef, ctx: LiveContext) -> dict[str, Any]:
    p: dict[str, Any] = {
        "key": _trigger_spec_key(t),
        "type": t.trigger_type,
        "prefix": "trigger",
        "user_description": t.user_description,
        "should_skip_execution": t.should_skip_execution,
        "order": t.order if t.order is not None else 0,
    }
    if t.id:
        p["id"] = t.id
    if t.description:
        p["description"] = t.description
    if t.skip_non_working_days is not None:
        p["skip_non_working_days"] = t.skip_non_working_days

    builder = _TRIGGER_BUILDERS.get(t.trigger_type)
    if builder is None:
        raise PlanError(
            f"trigger type '{t.trigger_type}' not yet supported. "
            f"Supported: {sorted(_TRIGGER_BUILDERS)}"
        )
    cfg_field = f"trigger_{t.trigger_type}"
    spec_block = getattr(t, cfg_field, None)
    block_dict: dict[str, Any] = _to_dict(spec_block)
    p[cfg_field] = builder(block_dict, ctx)
    return p


def _trigger_manual(_block: dict[str, Any], _ctx: LiveContext) -> dict[str, Any]:
    return {}


def _trigger_new_entity_created(
    block: dict[str, Any], _ctx: LiveContext
) -> dict[str, Any]:
    return {"action": block.get("action", "create_only")}


def _trigger_activity_logged(
    block: dict[str, Any], _ctx: LiveContext
) -> dict[str, Any]:
    activity = block.get("activity_type")
    activity_id = _unwrap_id(activity) or block.get("activity_type_id")
    out: dict[str, Any] = {}
    if activity_id:
        out["activity_type_id"] = activity_id
    return out


def _trigger_on_or_around_date(
    block: dict[str, Any], ctx: LiveContext
) -> dict[str, Any]:
    # Wire key is field_id (bare UUID); reads return an expanded `field` object.
    field_ref = block.get("field_ref")
    field_id = _unwrap_id(block.get("field")) or block.get("field_id")
    if not field_id and field_ref and "." in field_ref:
        obj_api, fld_api = field_ref.split(".", 1)
        field_id = ctx.field_uuid(obj_api, fld_api)
    out: dict[str, Any] = {
        k: v
        for k, v in block.items()
        if k not in {"field", "field_id", "field_ref"} and v is not None
    }
    if field_id:
        out["field_id"] = field_id
    # Live API only accepts lowercase "am"/"pm" despite the model allowing
    # uppercase for author convenience.
    if out.get("period"):
        out["period"] = out["period"].lower()
    return _strip(out)


def _trigger_webhook(block: dict[str, Any], _ctx: LiveContext) -> dict[str, Any]:
    # Drop server-assigned id; pass through the rest as-is.
    return _strip(block, drop={"id"})


def _trigger_field_updated(block: dict[str, Any], ctx: LiveContext) -> dict[str, Any]:
    ref = block.get("field_ref", "")
    out: dict[str, Any] = _strip(block, drop={"field_ref"})
    # Reads return an expanded `field` object; the wire key is field_id.
    field = out.pop("field", None)
    if field is not None and not out.get("field_id"):
        out["field_id"] = _unwrap_id(field)
    if ref and "." in ref and not out.get("field_id"):
        obj_api, fld_api = ref.split(".", 1)
        out["field_id"] = ctx.field_uuid(obj_api, fld_api)
    # specific_value matches read from_value/to_value as expanded entity
    # refs ({id, name, …}); the wire takes the bare UUID — the server
    # SILENTLY stores None for the dict form (observed live), so this
    # unwrap is load-bearing. Structured values without an id (e.g.
    # date_between's {date_start, date_end}) pass through unchanged.
    for k in ("from_value", "to_value"):
        v = out.get(k)
        if isinstance(v, dict) and v.get("id"):
            out[k] = v["id"]
    return out


def _trigger_form_submitted(block: dict[str, Any], ctx: LiveContext) -> dict[str, Any]:
    # WIRE SHAPE UNVERIFIED: no live capture of this trigger type exists yet
    # (unlike every other builder here, which was reverse-engineered from a
    # captured UI payload). This follows the bare-scalar-id convention used
    # by the majority of other triggers (on_or_around_date's field_id,
    # activity_logged's activity_type_id) as the best guess. Verify against
    # a real `kizen automations create` before trusting this live, and
    # capture a fixture once confirmed.
    form_id = _unwrap_id(block.get("form")) or block.get("form_id")
    if not form_id and block.get("form_name"):
        form_id = ctx.form_uuid(block["form_name"])
    out = {
        k: v
        for k, v in block.items()
        if k not in {"form", "form_id", "form_name"} and v is not None
    }
    if form_id:
        out["form_id"] = form_id
    return _strip(out)


def _trigger_survey_submitted(
    block: dict[str, Any], ctx: LiveContext
) -> dict[str, Any]:
    # Same wire-shape caveat as _trigger_form_submitted above.
    survey_id = _unwrap_id(block.get("survey")) or block.get("survey_id")
    if not survey_id and block.get("survey_name"):
        survey_id = ctx.survey_uuid(block["survey_name"])
    out = {
        k: v
        for k, v in block.items()
        if k not in {"survey", "survey_id", "survey_name"} and v is not None
    }
    if survey_id:
        out["survey_id"] = survey_id
    return _strip(out)


def _trigger_schedule(block: dict[str, Any], _ctx: LiveContext) -> dict[str, Any]:
    # Wire shape is flat and matches the read shape exactly: {rrule,
    # is_advanced}. Confirmed live (2026-07-22) on a global
    # automation's recurring trigger — no id/expansion to unwrap.
    return _strip(block)


def _trigger_scheduled_activity_overdue(
    block: dict[str, Any], _ctx: LiveContext
) -> dict[str, Any]:
    # Reads expand `activity` to {id, name, deleted}; write wants an {id}
    # association. Null keys (skip_non_working_days) are dropped.
    out = {k: v for k, v in _strip(block).items() if k != "activity" and v is not None}
    activity_id = _unwrap_id(block.get("activity"))
    if activity_id:
        out["activity"] = {"id": activity_id}
    return out


_TRIGGER_BUILDERS: dict[str, Any] = {
    "manual": _trigger_manual,
    "new_entity_created": _trigger_new_entity_created,
    "activity_logged": _trigger_activity_logged,
    "on_or_around_date": _trigger_on_or_around_date,
    "webhook": _trigger_webhook,
    "field_updated": _trigger_field_updated,
    "schedule": _trigger_schedule,
    "scheduled_activity_overdue": _trigger_scheduled_activity_overdue,
    "form_submitted": _trigger_form_submitted,
    "survey_submitted": _trigger_survey_submitted,
}


# ---------------------------------------------------------------------------
# Known enum choices — enriching a real 400, not rejecting a spec value
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownChoices:
    """Values this repo has confirmed for one enum-typed field, plus where
    that confirmation came from.

    Deliberately not "the enum" — for every field these tables cover, what's
    here is a partial, unreviewed sample of values seen live or in a fixture,
    not an exhaustive set the server enforces. Used only to enrich a real
    400's message (:func:`known_choices_addendum`); never to reject a spec
    value the server hasn't rejected yet.
    """

    values: tuple[str, ...]
    source: str


# `KNOWN_ENUM_CHOICES` (steps, next to `_STEP_BUILDERS` below) and
# `KNOWN_ENUM_CHOICES_TRIGGERS` (triggers, here) are keyed by
# `(step_or_trigger_type, dotted_field_path)`, never by bare field name —
# `docs/specs/automation.md` documents at least three different enums all
# named `type`, so a flat `field_name → choices` table would mismatch the
# moment it touched an ambiguous name.
#
# Not exhaustive. Add an entry the moment a value is confirmed elsewhere in
# this repo (a fixture, a docstring, a drift finding, or a captured live
# session) — do not also write the list into `automation.md` prose (check
# the registry, not the prose).
KNOWN_ENUM_CHOICES_TRIGGERS: dict[str, dict[str, KnownChoices]] = {
    "on_or_around_date": {
        "date_offset": KnownChoices(
            values=("on_day", "on_day_and_time", "days_before", "days_after"),
            source=(
                "tests/fixtures/automations/kitchen_sink_triggers.raw.json, "
                "on_or_around_date_goto.raw.json"
            ),
        ),
    },
}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


_ACTION_STEP_TYPES = {
    "assign_team_member",
    "audio_transcription",
    "call_llm",
    "change_field_value",
    "change_tags",
    "code_step",
    "plugin_code_step",
    "create_related_entity",
    "delete_scheduled_activity",
    "archive_record",
    "file_content_extraction",
    "go_to_automation_step",
    "http_request",
    "initialize_variable",
    "math_operator",
    "modify_automation",
    "modify_related_entities",
    "modify_related_entities_automation",
    "notify_member_via_email",
    "notify_member_via_text",
    "request_info_via_text",
    "schedule_activity",
    "search_records",
    "send_email",
    "send_related_contact_email",
    "send_text",
    "send_related_contact_text",
    "start_automation",
    "update_pipeline_status",
    "update_variable",
    "stop_execution",
}


def _prefix_for(step_type: str) -> str:
    return "action" if step_type in _ACTION_STEP_TYPES else "step"


def _block_field_for(step_type: str) -> str:
    """Map step_type to its config field on AutomationStepDef."""
    if step_type in ("condition", "delay", "goal"):
        return f"step_{step_type}"
    return f"action_{step_type}"


def _build_step_payload(
    step: AutomationStepDef, auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    branch = step.parent_branch or ""
    # Kizen rejects notify_continue for condition steps
    action_on_failure = step.action_on_failure
    if step.step_type == "condition" and action_on_failure == "notify_continue":
        action_on_failure = "notify_pause"
    p: dict[str, Any] = {
        "key": step.key,
        "parent_key": step.parent_key,
        "parent_yes_no": branch,
        "parent_condition": branch,
        "type": step.step_type,
        "prefix": _prefix_for(step.step_type),
        "order": step.order,
        "user_description": step.user_description,
        "action_on_failure": action_on_failure,
        "should_skip_execution": step.should_skip_execution,
        "goal_type": step.step_type == "goal",
    }
    if step.id:
        p["id"] = step.id
    if step.description:
        p["description"] = step.description

    builder = _STEP_BUILDERS.get(step.step_type)
    if builder is None:
        raise PlanError(
            f"step type '{step.step_type}' not yet supported. "
            f"Supported: {sorted(_STEP_BUILDERS)}"
        )

    cfg_field = _block_field_for(step.step_type)
    spec_block = getattr(step, cfg_field, None)
    block_dict: dict[str, Any] = _to_dict(spec_block)
    wire_block = builder(block_dict, auto, ctx)
    p[cfg_field] = wire_block
    return p


def _to_dict(block: Any) -> dict[str, Any]:
    """Coerce a typed Pydantic config or a raw dict to a dict.

    Some config blocks (e.g. ``action_stop_execution``) are typed as
    ``dict[str, Any]`` rather than Pydantic models; this lets the dispatch
    treat both shapes uniformly.
    """
    if block is None:
        return {}
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if isinstance(block, dict):
        return dict(block)
    return {}


# --- per-type step builders ------------------------------------------------


def _normalize_llm_decision(ld: Any) -> Any:
    """llm_decision conditions read business_plugin_app expanded (including
    obfuscated secret values — never echo back); write takes
    business_plugin_app_id."""
    if not isinstance(ld, dict):
        return ld
    out = {
        k: v
        for k, v in _strip(ld).items()
        if k != "business_plugin_app" and v is not None
    }
    bpa_id = ld.get("business_plugin_app_id") or _unwrap_id(
        ld.get("business_plugin_app")
    )
    if bpa_id:
        out["business_plugin_app_id"] = bpa_id
    return out


def _step_condition(
    block: dict[str, Any], auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": block.get("type", "custom_filter"),
        "llm_decision": _normalize_llm_decision(block.get("llm_decision")),
    }
    if block.get("filter_config") is not None:
        out["filter_config"] = _render_filter_config(
            block["filter_config"], auto.target_object
        )
    # in_group conditions: reads expand to `groups: [{id, name, …}]`;
    # writes take `group_ids: [uuid]`.
    if block.get("group_ids"):
        out["group_ids"] = [_unwrap_id(g) or g for g in block["group_ids"]]
    elif block.get("groups"):
        out["group_ids"] = [_unwrap_id(g) for g in block["groups"] if _unwrap_id(g)]
    # NEVER set yes_step_ids / no_step_ids — server crashes.
    return out


def _render_filter_config(
    fc: dict[str, Any], object_api_name: str | None
) -> dict[str, Any]:
    """Accept either a JSON filter spec ({"all"|"any": [...]} — compiled via
    the filtering DSL with field names resolved against ``object_api_name``)
    or a raw filter_config dict (normalized: group ids assigned, null clause
    values rejected). Raw is the only form for clause types the DSL doesn't
    cover (e.g. automation-variable comparisons).

    ``object_api_name`` is the condition/search step's own target object —
    the automation's ``target_object`` for a condition step, but a
    search_records step's own ``custom_object`` (which may differ, e.g. on a
    global automation with no target_object of its own).
    """
    from kizen_builder import filtering

    if "all" in fc or "any" in fc:
        if not object_api_name:
            raise PlanError(
                "a filter spec ({'all'|'any': ...}) in filter_config needs a "
                "resolvable object to resolve field names against; global "
                "automations must provide a raw filter_config instead."
            )
        try:
            with filtering.filter_context(object_api_name):
                return filtering.as_filter_config(filtering.from_spec(fc))
        except (ValueError, LookupError) as e:
            raise PlanError(f"invalid filter spec: {e}") from e
    try:
        return filtering.normalize_filter_config(fc)
    except ValueError as e:
        raise PlanError(f"invalid filter_config: {e}") from e


def _step_delay(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    # Flat block; reads include unused keys as null (time, variable) which
    # must be dropped on write.
    out = {k: v for k, v in _strip(block).items() if v is not None}
    # Variable-driven delays (value_origin: automation_variable) read the
    # full variable definition; the wire takes the plain name string.
    if isinstance(out.get("variable"), dict):
        out["variable"] = out["variable"].get("name")
    return out


def _step_goal(
    block: dict[str, Any], _auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Goal steps embed a list of TRIGGERS (wait-until conditions) whose
    blocks read in the same expanded shape as top-level triggers — reuse the
    trigger builders for them."""
    out = {k: v for k, v in _strip(block).items() if k != "triggers" and v is not None}
    # Variable-driven goals read the full variable definition; the wire
    # takes the plain name string.
    if isinstance(out.get("variable"), dict):
        out["variable"] = out["variable"].get("name")
    triggers = []
    for i, t in enumerate(block.get("triggers") or []):
        trigger_type = t.get("trigger_type") or t.get("type")
        p: dict[str, Any] = {
            "key": t.get("key") or f"goal_trigger_{i}",
            "type": trigger_type,
            "prefix": "trigger",
            "user_description": t.get("user_description") or "",
            "should_skip_execution": t.get("should_skip_execution", False),
            "order": t.get("order") if t.get("order") is not None else i,
        }
        if t.get("id"):
            p["id"] = t["id"]
        if t.get("description"):
            p["description"] = t["description"]
        cfg_field = f"trigger_{trigger_type}"
        nested = dict(t.get(cfg_field) or {})
        builder = _TRIGGER_BUILDERS.get(trigger_type)
        p[cfg_field] = builder(nested, ctx) if builder else _strip(nested)
        triggers.append(p)
    out["triggers"] = triggers
    return out


def _step_stop_execution(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    # Block can carry {action, notify}; default empty is also accepted.
    # Drop nulls — the API rejects null for `action` even though live responses
    # contain it.
    cleaned = _strip(block)
    return {k: v for k, v in cleaned.items() if v is not None}


def _step_archive_record(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Write dialect: relationship_field_ids / automation_variable_name (bare
    UUIDs and names); reads return expanded `relationship_fields` objects and
    `automation_variable`. Same trap as start_automation — read-dialect keys
    are silently ignored, so the step would lose its source with no error.
    """
    out: dict[str, Any] = {
        "record_source": block.get("record_source", "this_record"),
    }
    rels = block.get("relationship_field_ids") or block.get("relationship_fields") or []
    ids = [_resolve_field(r, auto, ctx) or _unwrap_id(r) for r in rels if r]
    out["relationship_field_ids"] = [r for r in ids if r]
    var = block.get("automation_variable_name") or block.get("automation_variable")
    if var:
        out["automation_variable_name"] = (
            var.get("name") if isinstance(var, dict) else var
        )
    return out


def _team_member_selector(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize a team-member selector (assign_team_member action,
    cc_team_member on message steps) to write dialect: *_id keys with bare
    UUIDs (role_id, employee_id, employee_ids, field_id, related_field_id).
    Reads return expanded objects under the bare names.
    """
    out: dict[str, Any] = {"type": block.get("type")}
    for read_key, write_key in (
        ("role", "role_id"),
        ("employee", "employee_id"),
        ("field", "field_id"),
        ("related_field", "related_field_id"),
    ):
        val = block.get(write_key) or block.get(read_key)
        if val:
            out[write_key] = _unwrap_id(val) or val
    emps = block.get("employee_ids") or block.get("employees") or []
    ids = [_unwrap_id(e) or e for e in emps if e]
    if ids:
        out["employee_ids"] = ids
    return out


def _step_assign_team_member(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    return _team_member_selector(block)


def _step_go_to_automation_step(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    # `step_key` is the spec key of the target step. Server resolves it.
    out: dict[str, Any] = {"type": "go_to_automation_step"}
    if block.get("step_key"):
        out["step_key"] = block["step_key"]
    elif block.get("step"):
        # round-tripped from live: expand the {id, ...} into bare uuid
        out["step_key"] = _unwrap_id(block["step"])
    if block.get("trigger_key"):
        out["trigger_key"] = block["trigger_key"]
    return out


def _step_start_automation(
    block: dict[str, Any], _auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Write dialect differs from read: automation_ids / relationship_field_ids /
    automation_variable_name (bare UUIDs and names). Read-dialect keys
    (`automations`, `relationship_fields`) are SILENTLY IGNORED by the server
    — the step loses its targets with no error — so always emit write keys.
    """
    out: dict[str, Any] = {
        "record_source": block.get("record_source", "this_record"),
        "resume_paused_automations": block.get("resume_paused_automations") or False,
    }
    rels = block.get("relationship_field_ids") or block.get("relationship_fields") or []
    out["relationship_field_ids"] = [_unwrap_id(r) for r in rels if r]
    var = block.get("automation_variable_name") or block.get("automation_variable")
    if var:
        out["automation_variable_name"] = (
            var.get("name") if isinstance(var, dict) else var
        )
    # automation targets: accept write shape (automation_ids), read shape
    # (automations: [{id, …}]), bare UUIDs, or api_names.
    autos_in: list[Any] = block.get("automation_ids") or block.get("automations") or []
    if not autos_in and block.get("automation_api_name"):
        autos_in = [block["automation_api_name"]]
    if not autos_in and block.get("automation_id"):
        autos_in = [block["automation_id"]]
    resolved: list[str] = []
    for a in autos_in:
        if isinstance(a, dict) and a.get("id"):
            resolved.append(a["id"])
        elif isinstance(a, str):
            if is_uuid(a):
                resolved.append(a)
            else:
                uuid = ctx.automation_uuid(a)
                if uuid is None:
                    raise PlanError(
                        f"start_automation references unknown automation '{a}'"
                    )
                resolved.append(uuid)
    out["automation_ids"] = resolved
    if block.get("automation_variable_overrides"):
        out["automation_variable_overrides"] = block["automation_variable_overrides"]
    return out


def _step_change_field_value(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Wrap one or more field changes in the wire's `actions: [...]` shape.

    Accepts both:
    * the live-shape ``block["actions"] = [{...}, ...]``
    * a flat single-change form (``field_ref``, ``specific_field_value``, ...)
    """
    actions_in = block.get("actions")
    if not actions_in:
        # Flat → wrap into single-element actions
        flat = {k: v for k, v in block.items() if k != "actions"}
        actions_in = [flat]
    return {"actions": [_change_field_value_action(a, auto, ctx) for a in actions_in]}


def _change_field_value_action(
    a: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "field_resolution": a.get("field_resolution") or "overwrite",
        "update_mode": a.get("update_mode") or "update_fields",
        "field_value_mappings": a.get("field_value_mappings") or [],
        "fields_to_clear": a.get("fields_to_clear") or [],
    }
    # Dialect quirk: change_field_value actions use `change_type`;
    # modify_related_entities fields_to_modify items use `value_type`.
    # Emit whichever key the input carried (default: change_type).
    if "value_type" in a and "change_type" not in a:
        out["value_type"] = a.get("value_type") or "specific_value"
    else:
        out["change_type"] = a.get("change_type") or "specific_value"
    # field_to_modify: bare UUID
    field = a.get("field_to_modify") or a.get("field_ref") or a.get("field")
    field_uuid = _resolve_field(field, auto, ctx)
    if field_uuid:
        out["field_to_modify"] = field_uuid

    if "specific_field_value" in a:
        sfv = a["specific_field_value"]
        # Read returns {value: ...} for scalars and an expanded option
        # object ({id, name, deleted}) for choice fields; write takes the
        # bare scalar / bare option UUID.
        if isinstance(sfv, dict) and "value" in sfv and len(sfv) <= 2:
            out["specific_field_value"] = sfv["value"]
        elif isinstance(sfv, dict) and sfv.get("id"):
            out["specific_field_value"] = sfv["id"]
        elif isinstance(sfv, dict) and (
            "tags_to_add" in sfv or "tags_to_remove" in sfv
        ):
            # Tag fields: reads expand both lists to {id, name, deleted};
            # write takes bare UUID strings in both. Any dict form — even
            # the {id, name} shape the OpenAPI spec documents — 500s.
            out["specific_field_value"] = {
                k: [_unwrap_id(t) or t for t in sfv.get(k) or []]
                for k in ("tags_to_add", "tags_to_remove")
                if k in sfv
            }
        else:
            out["specific_field_value"] = sfv
    if a.get("context_entity_field") is not None:
        # A field on the triggering record (auto.target_object) — a bare
        # name (no dot) resolves against it, same as field_ref elsewhere.
        # Previously only _unwrap_id ran here, so a dotted 'object.field'
        # ref was sent to the API as a literal string and 400'd ("Must be
        # a valid UUID") instead of resolving.
        out["context_entity_field"] = _resolve_field(
            a["context_entity_field"], auto, ctx
        ) or _unwrap_id(a["context_entity_field"])

    # Optional reference fields
    if a.get("automation_target_relationship_field") is not None:
        out["automation_target_relationship_field"] = _resolve_field(
            a["automation_target_relationship_field"], auto, ctx
        ) or _unwrap_id(a["automation_target_relationship_field"])
    if a.get("related_object") is not None:
        out["related_object"] = _resolve_object(a["related_object"], ctx)
    if a.get("related_object_field") is not None:
        out["related_object_field"] = _resolve_field(
            a["related_object_field"], auto, ctx
        )
    if a.get("variable") is not None:
        # Wire wants the bare variable name string ({name: …} is rejected
        # with "Not a valid string"); reads return a definition object.
        v = a["variable"]
        out["variable"] = v.get("name") if isinstance(v, dict) else v
    return out


def _step_modify_related_entities(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Wire dialect confirmed live: the relationship hop(s) from
    target_object to object_to_modify go in `automation_target_relationship_fields`
    (a list — supports multi-hop chains). `relationship_field_ref`/
    `relationship_field_id` (singular) are accepted as a convenience alias for
    a single-hop list and folded in here; each entry accepts an 'object.field'
    ref (resolved like any other field_ref) or a raw field UUID.
    """
    rel_fields_in = list(block.get("automation_target_relationship_fields") or [])
    single = block.get("relationship_field_ref") or block.get("relationship_field_id")
    if single:
        rel_fields_in.append(single)
    out: dict[str, Any] = {
        "object_to_modify": _resolve_object(block.get("object_to_modify"), ctx),
        "automation_target_relationship_fields": [
            r
            for r in (
                _resolve_field(r, auto, ctx) or _unwrap_id(r) for r in rel_fields_in
            )
            if r
        ],
        "fields_to_modify": [
            _change_field_value_action(f, auto, ctx)
            for f in (block.get("fields_to_modify") or [])
        ],
    }
    return out


def _step_create_related_entity(
    block: dict[str, Any], _auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "new_entity_name": block.get("new_entity_name"),
        "new_entity_name_html": block.get("new_entity_name_html"),
        "new_entity_owner_type": block.get("new_entity_owner_type"),
        "new_entity_owner_sub_type": block.get("new_entity_owner_sub_type"),
        "new_entity_stage": _unwrap_id(block.get("new_entity_stage")),
        "new_entity_owner_role": block.get("new_entity_owner_role"),
        "new_entity_owner_variable": _unwrap_variable_name(
            block.get("new_entity_owner_variable")
        ),
        "new_entity_owner_employees": [
            _unwrap_id(e) for e in (block.get("new_entity_owner_employees") or []) if e
        ],
        "context_entity_field": _unwrap_id(block.get("context_entity_field")),
        "target_custom_object": _resolve_object(block.get("target_custom_object"), ctx),
        "target_variable": _unwrap_variable_name(block.get("target_variable")),
        "variable_field_resolution": block.get(
            "variable_field_resolution", "overwrite"
        ),
        "context_entity_field_resolution": block.get(
            "context_entity_field_resolution", "overwrite"
        ),
        "automations_to_start": [
            _unwrap_id(a) for a in (block.get("automations_to_start") or []) if a
        ],
        "existing_record_found_action": block.get(
            "existing_record_found_action", "error_do_not_create"
        ),
        "archived_record_found_action": block.get(
            "archived_record_found_action", "overwrite"
        ),
    }
    # Allow field_values pass-through (advanced: caller specifies field UUIDs).
    if block.get("field_values"):
        out["field_values"] = block["field_values"]
    return {k: v for k, v in out.items() if v is not None}


def _step_search_records(
    block: dict[str, Any], _auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Searches `custom_object` and writes the result into an array-type
    automation variable. Confirmed live (2026-07-22) against the
    public `/api/docs/schema` `SearchRecordsRequest` shape and a real create:
    unlike most action-block references in this file (which take bare
    UUIDs), `custom_object`/`filter_groups`/`destination_variable` here are
    each a `{"id": uuid}` or `{"name": str}` object (an HTTP 500 resulted
    from sending bare strings, matching the pattern this codebase already
    warns about elsewhere — OpenAPI-documented shapes aren't always what a
    *different* endpoint's bare-scalar convention would suggest).

    `custom_object` here is independent of the automation's own
    target_object (it must be — global automations have none), so
    filter_config/filter_groups resolve against it directly rather than
    against auto.target_object, unlike a condition step.
    """
    object_ref = block.get("custom_object")
    object_api = _api_name_of(object_ref)
    object_id = _resolve_object(object_ref, ctx)
    if not object_id:
        raise PlanError("search_records step requires a resolvable 'custom_object'")
    var_name = _unwrap_variable_name(block.get("destination_variable"))
    out: dict[str, Any] = {
        "custom_object": {"id": object_id},
        "filter_type": block.get("filter_type"),
        "destination_variable": {"name": var_name} if var_name else None,
        "destination_variable_resolution": block.get(
            "destination_variable_resolution", "overwrite"
        ),
    }
    if block.get("filter_config") is not None:
        out["filter_config"] = _render_filter_config(block["filter_config"], object_api)
    else:
        out["filter_config"] = None
    groups: list[dict[str, str]] = []
    for g in block.get("filter_groups") or []:
        # Unlike _unwrap_id's usual callers, filter_groups entries are
        # commonly authored by NAME, not id — a bare string here isn't
        # necessarily already a UUID (a live read's expanded {id, name, …}
        # dict is handled the same as elsewhere).
        gid: str | None = None
        name: str | None = None
        if isinstance(g, dict):
            gid = g.get("id")
            name = g.get("name") if not gid else None
        elif isinstance(g, str) and is_uuid(g):
            gid = g
        elif isinstance(g, str):
            name = g
        if not gid and name and object_api:
            gid = ctx.filter_group_uuid(object_api, name)
        if gid:
            groups.append({"id": gid})
    out["filter_groups"] = groups
    return out


def _api_name_of(value: Any) -> str | None:
    """Best-effort api_name extraction from an object/field-like reference —
    a bare non-UUID string, or a dict's `name`/`api_name` (the convention a
    live read's expanded object uses for its api_name, not display name)."""
    if isinstance(value, str):
        return None if is_uuid(value) else value
    if isinstance(value, dict):
        return value.get("name") or value.get("api_name")
    return None


def _normalize_variable_sources(sources: list[Any]) -> list[dict[str, Any]]:
    """Normalize variable source items to wire form.

    Read shape includes every possible key with null for the unused ones
    (``value_category``, ``field``, ``relationship_field``, ``variable``) —
    the API rejects those nulls on write ("This field may not be null").
    Reference objects come back expanded; writes take bare UUIDs, and a
    ``variable`` reference takes the plain name string.
    """
    out: list[dict[str, Any]] = []
    for src in _strip(sources or []):
        if not isinstance(src, dict):
            out.append(src)
            continue
        item: dict[str, Any] = {}
        for k, v in src.items():
            if v is None:
                continue
            if k == "variable":
                item[k] = v.get("name") if isinstance(v, dict) else v
            elif isinstance(v, dict) and v.get("id"):
                item[k] = v["id"]
            elif isinstance(v, dict) and "value" in v:
                # webhook_trigger sources read as {value, label} choice dicts
                item[k] = v["value"]
            elif isinstance(v, list):
                # static entity sources read as lists of expanded record
                # refs; the wire takes bare UUID strings
                item[k] = [
                    x["id"] if isinstance(x, dict) and x.get("id") else x for x in v
                ]
            else:
                item[k] = v
        out.append(item)
    return out


def _variable_step_extras(block: dict[str, Any]) -> dict[str, Any]:
    """Carry through the block-level variable-step options.

    ``array_aggregation_mode`` is REQUIRED (non-null) when the variable is an
    array; other keys (e.g. deduplication) pass through when set. Null values
    are dropped — reads include them, writes reject them.
    """
    handled = {"sources", "variable", "is_required"}
    return {k: v for k, v in block.items() if k not in handled and v is not None}


def _step_initialize_variable(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    return {
        **_variable_step_extras(block),
        "sources": _normalize_variable_sources(block.get("sources") or []),
        "variable": _normalize_variable(block.get("variable")),
        "is_required": block.get("is_required", False),
    }


def _step_update_variable(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    # update_variable.variable must be a plain name string (not a definition dict)
    var = block.get("variable")
    var_name = (
        var
        if isinstance(var, str)
        else (var.get("name") if isinstance(var, dict) else var)
    )
    return {
        **_variable_step_extras(block),
        "sources": _normalize_variable_sources(block.get("sources") or []),
        "variable": var_name,
        "is_required": block.get("is_required", False),
    }


def _normalize_variable(v: Any) -> Any:
    """Variables are referenced by name in our spec; server accepts a definition obj."""
    if v is None:
        return None
    if isinstance(v, str):
        return {"name": v}
    if isinstance(v, dict):
        # Entity variables read data_subtype as an expanded object; the wire
        # takes the bare object UUID. Unwrap BEFORE stripping — _strip drops
        # nested `id` keys recursively.
        if isinstance(v.get("data_subtype"), dict):
            v = {**v, "data_subtype": v["data_subtype"].get("id")}
        # Strip server-managed fields
        return _strip(v, drop={"id", "created", "deleted"})
    return v


def _step_call_llm(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_name": block.get("model_name"),
        "prompt": block.get("prompt"),
    }
    # Wire key is business_plugin_app_id (bare UUID). Reads expand it to a
    # full object that includes obfuscated secret values — never echo back.
    bpa_id = block.get("business_plugin_app_id") or _unwrap_id(
        block.get("business_plugin_app")
    )
    if bpa_id:
        out["business_plugin_app_id"] = bpa_id
    if block.get("html_prompt") is not None:
        out["html_prompt"] = block["html_prompt"]
    elif out.get("prompt"):
        # Kizen's builder UI always keeps prompt/html_prompt in sync (same
        # quirk as notify_member_via_text's content/html_content): a
        # plain-prompt-only step still runs fine via the API but the
        # rich-text prompt editor renders blank without this.
        resolve_label, resolve_objectname = _merge_field_resolvers(auto, ctx)
        out["html_prompt"] = (
            f"<p>{merge_fields.render(out['prompt'], resolve_label=resolve_label, resolve_objectname=resolve_objectname)}</p>"
        )
    if block.get("destinations"):
        out["destinations"] = _resolve_llm_destinations(
            block["destinations"], auto, ctx
        )
    if block.get("is_advanced") is not None:
        out["is_advanced"] = block["is_advanced"]
    if block.get("data_type"):
        out["data_type"] = block["data_type"]
    if block.get("merge_field_validation"):
        out["merge_field_validation"] = block["merge_field_validation"]
    return {k: v for k, v in out.items() if v is not None}


def _normalize_destinations(destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mechanical read→write normalization for LLM/extraction destinations.

    Drops null keys (reads include every possible key as null; writes reject
    them), collapses the expanded ``field`` object to a bare UUID, reduces
    the ``variable`` definition object to its name, and defaults
    ``conflict_resolution`` (stored null on old rows, rejected on write).
    """
    resolved = []
    for dest in destinations:
        d = _strip(dict(dest))
        d = {k: v for k, v in d.items() if v is not None}
        if isinstance(d.get("field"), dict):
            d["field"] = _unwrap_id(d["field"])
        if isinstance(d.get("related_object_field"), dict):
            d["related_object_field"] = _unwrap_id(d["related_object_field"])
        if isinstance(d.get("variable"), dict):
            d["variable"] = d["variable"].get("name")
        if isinstance(d.get("options"), list):
            d["options"] = [_unwrap_id(o) or o for o in d["options"]]
        d.setdefault("conflict_resolution", "overwrite_except_null")
        resolved.append(d)
    return resolved


def _step_file_content_extraction(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_name": block.get("model_name"),
        "prompt": block.get("prompt"),
    }
    # business_plugin_app_id (wire key) or legacy business_plugin_app dict
    bpa_id = block.get("business_plugin_app_id") or _unwrap_id(
        block.get("business_plugin_app")
    )
    if bpa_id:
        out["business_plugin_app_id"] = bpa_id
    # input_field — wire key is "input_field"; accept field_ref, field_id, or UUID
    input_ref = (
        block.get("input_field_ref")
        or block.get("input_field")
        or block.get("input_field_id")
        or block.get("file_field")
    )
    if input_ref:
        if isinstance(input_ref, str) and "." in input_ref:
            obj_api, fld_api = input_ref.split(".", 1)
            out["input_field"] = ctx.field_uuid(obj_api, fld_api)
        else:
            out["input_field"] = _resolve_field(input_ref, auto, ctx) or input_ref
    if block.get("html_prompt") is not None:
        out["html_prompt"] = block["html_prompt"]
    elif out.get("prompt"):
        # Same prompt/html_prompt sync quirk as call_llm — see its comment.
        resolve_label, resolve_objectname = _merge_field_resolvers(auto, ctx)
        out["html_prompt"] = (
            f"<p>{merge_fields.render(out['prompt'], resolve_label=resolve_label, resolve_objectname=resolve_objectname)}</p>"
        )
    if block.get("merge_field_validation"):
        out["merge_field_validation"] = block["merge_field_validation"]
    if block.get("destinations"):
        out["destinations"] = _resolve_llm_destinations(
            block["destinations"], auto, ctx
        )
    if block.get("is_advanced") is not None:
        out["is_advanced"] = block["is_advanced"]
    if block.get("data_type"):
        out["data_type"] = block["data_type"]
    return {k: v for k, v in out.items() if v is not None}


_CHOICE_FIELD_TYPES = {"dropdown", "radio", "checkboxes", "choices", "yesnomaybe"}


def _resolve_llm_destinations(
    destinations: list[dict[str, Any]],
    auto: AutomationDef,
    ctx: LiveContext,
) -> list[dict[str, Any]]:
    """Resolve field_refs to UUIDs and auto-populate options for choice fields.

    Shared by call_llm, file_content_extraction, and audio_transcription —
    all three take the same `destinations` shape.

    Two destination shapes, confirmed live 2026-07-27 (see reference.md "LLM
    destinations: related-object writes"):
    - Same-object: `field_ref`/`field` (or `variable`) names the destination
      directly on `target_object`.
    - Related-object: `related_object_field` names the destination field on
      the related record (dotted ref or UUID); `field` is then repurposed by
      the wire as the *relationship hop* — the field on `target_object`
      pointing at that related object — not the destination. The hop can
      come from `relationship_field_ref`/`relationship_field_id`, from a
      caller-supplied `field_ref`/`field`, or be auto-detected when exactly
      one relationship field between `target_object` and the destination's
      object exists.
    """
    resolved = []
    for dest in _normalize_destinations(destinations):
        d = dest
        ref = d.pop("field_ref", None)
        hop_ref = d.pop("relationship_field_ref", None) or d.pop(
            "relationship_field_id", None
        )

        related_ref = d.get("related_object_field")
        dest_obj_api: str | None = None
        dest_fld_api: str | None = None
        if (
            isinstance(related_ref, str)
            and not is_uuid(related_ref)
            and "." in related_ref
        ):
            dest_obj_api, dest_fld_api = related_ref.split(".", 1)
            d["related_object_field"] = ctx.field_uuid(dest_obj_api, dest_fld_api)

        if d.get("related_object_field") is not None:
            # Related-object write: `field` is the relationship hop, not the
            # destination. Resolve whichever of relationship_field_ref/
            # field_ref/field the caller gave; fall back to auto-detection.
            hop = hop_ref or ref or d.get("field")
            hop_uuid = (
                (_resolve_field(hop, auto, ctx) or _unwrap_id(hop)) if hop else None
            )
            if not hop_uuid and dest_obj_api and auto.target_object:
                hop_uuid = ctx.find_relationship_field(auto.target_object, dest_obj_api)
            if not hop_uuid:
                raise PlanError(
                    "LLM destination writes to related_object_field "
                    f"'{related_ref}' but no relationship hop was given and "
                    f"none could be auto-detected between '{auto.target_object}' "
                    f"and '{dest_obj_api or '?'}' (zero or more than one "
                    "relationship field candidate). Set relationship_field_ref "
                    "(or relationship_field_id) naming the field on "
                    f"'{auto.target_object}' that points at the related object."
                )
            d["field"] = hop_uuid
            if not d.get("options") and dest_obj_api and dest_fld_api:
                fdata = ctx.field_data(dest_obj_api, dest_fld_api)
                if fdata.get("field_type") in _CHOICE_FIELD_TYPES:
                    d["options"] = [
                        o["id"] for o in (fdata.get("options") or []) if o.get("id")
                    ]
        elif ref and "." in ref and not d.get("field"):
            # Same-object write: field_ref names the destination directly.
            obj_api, fld_api = ref.split(".", 1)
            d["field"] = ctx.field_uuid(obj_api, fld_api)
            # Auto-populate options for choice-type fields when not already set
            if not d.get("options"):
                fdata = ctx.field_data(obj_api, fld_api)
                if fdata.get("field_type") in _CHOICE_FIELD_TYPES:
                    d["options"] = [
                        o["id"] for o in (fdata.get("options") or []) if o.get("id")
                    ]
        # Apply sensible defaults for keys the caller may have omitted
        d.setdefault("options", [])
        d.setdefault("is_required", False)
        d.setdefault("confidence_threshold", 0.7)
        resolved.append(d)
    return resolved


def _step_schedule_activity(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    activity = block.get("activity_type")
    activity_id = _unwrap_id(activity) or block.get("activity_type_id")
    if activity_id:
        # Wire key is activity_type_id (UUID string), not activity_type
        out["activity_type_id"] = activity_id
    schedule = block.get("schedule")
    if schedule:
        # Drop null sub-fields — API rejects null for with_delay / on_or_around_date
        out["schedule"] = {k: v for k, v in schedule.items() if v is not None}
    if block.get("notifications") is not None:
        out["notifications"] = block["notifications"]
    if block.get("assigned_to"):
        out["assigned_to"] = _normalize_assigned_to(block["assigned_to"])
    if block.get("association_configs") is not None:
        out["association_configs"] = [
            _normalize_association_config(a, auto, ctx)
            for a in block["association_configs"]
        ]
    if block.get("note") is not None:
        out["note"] = block["note"]
    return out


def _normalize_association_config(
    a: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """One entry of schedule_activity's `association_configs` — per-object
    record linkage for activity types associated with more than one custom
    object. Confirmed against the public `/api/docs/schema`
    `ScheduleActivityAssociationConfigRequest` and a live create
    (2026-07-22): the write dialect is entirely different from what a live
    READ returns (and from the ticket's own guess at this shape) — flat
    `custom_object_id`/`relationship_field_id`/`automation_variable_name`
    keys (not nested `custom_object: {id}` / `relationship_field` /
    `automation_variable`), and `association_source` values `related_field`/
    `record_variable`/`variable_related_field` (not `relationship_field`/
    `automation_variable` — those 400 with "not a valid choice").

    Accepts either the friendly author shape (`object`/`source`/
    `relationship_field_ref`/`automation_variable`) or a live read's raw wire
    dict (`custom_object`/`association_source`/`relationship_field`/
    `automation_variable`).
    """
    object_ref = a.get("custom_object") or a.get("object")
    object_id = _resolve_object(object_ref, ctx)
    out: dict[str, Any] = {
        "custom_object_id": object_id,
        "association_source": a.get("association_source") or a.get("source") or "none",
    }
    rel_field = (
        a.get("relationship_field")
        or a.get("relationship_field_id")
        or a.get("relationship_field_ref")
    )
    rel_field_id = _resolve_field(rel_field, auto, ctx) if rel_field else None
    if rel_field_id:
        out["relationship_field_id"] = rel_field_id
    var = a.get("automation_variable") or a.get("automation_variable_name")
    var_name = _unwrap_variable_name(var) if var else None
    if var_name:
        out["automation_variable_name"] = var_name
    return out


def _normalize_assigned_to(at: dict[str, Any]) -> dict[str, Any]:
    """schedule_activity's assignment selector — confirmed against the
    public `/api/docs/schema` `ScheduleActivityAssignmentRequest`, which
    backs all 11 "Assign To" options in the UI (round_robin_all/_role/
    _team_members, owner, team_member, team_selector_field, last_active(_role),
    specific_role, team_member_from_variable, role_from_variable — every
    value of `AssignmentTypeEnum`). Two write-dialect fixes, both same-shaped
    "reads expand, writes take a bare/ided form" quirks:

    - `role_id`/`employee_id`/`employee_ids`/`field_id` (a live 400 from
      a customer sandbox — ticket 20260722-153607 — caught this builder
      emitting bare `role`/`employee`/`field` instead, the same *_id dialect
      `_team_member_selector` already uses for assign_team_member/
      notify_member_via_email).
    - `variable` (used by the two "from Variable" options) must be an
      `{"id"|"name": ...}` object per `VariableRequest` — NOT the bare name
      string this codebase's other variable references use (e.g.
      _unwrap_variable_name's callers) or a live read's expanded definition
      dict. Caught by inspection while confirming the *_id fix above, not by
      a reported ticket — unlike assign_team_member's own selector (a
      different, smaller `AssignTeamMemberWriteTypeEnum` with no
      variable-based options at all), so `_team_member_selector` doesn't
      need this.
    """
    out: dict[str, Any] = {
        # schedule_activity's wire key is assignment_type; assign_team_member
        # and notify_member_via_email use "type" for the same selector, so
        # accept either spelling here.
        "assignment_type": at.get("assignment_type") or at.get("type"),
    }
    var_name = _unwrap_variable_name(at.get("variable"))
    if var_name:
        out["variable"] = {"name": var_name}
    for read_key, write_key in (
        ("role", "role_id"),
        ("employee", "employee_id"),
        ("field", "field_id"),
    ):
        val = at.get(write_key) or at.get(read_key)
        if val:
            out[write_key] = _unwrap_id(val) or val
    emps = at.get("employee_ids") or at.get("employees") or []
    ids = [_unwrap_id(e) or e for e in emps if e]
    if ids:
        out["employee_ids"] = ids
    return {k: v for k, v in out.items() if v is not None}


def _step_send_related_contact_email(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    """Reads expand send_to_contact_field (field object) and email (full
    message resource); writes take the bare field UUID and an {id}
    association."""
    out: dict[str, Any] = {}
    if block.get("send_to_contact_field") is not None:
        out["send_to_contact_field"] = _unwrap_id(block["send_to_contact_field"])
    if block.get("send_from_owner") is not None:
        out["send_from_owner"] = block["send_from_owner"]
    email = block.get("email")
    if isinstance(email, str):
        out["email"] = {"id": email}
    elif isinstance(email, dict) and email.get("id"):
        out["email"] = {"id": email["id"]}
    cc = block.get("cc_team_member")
    if isinstance(cc, dict):
        out["cc_team_member"] = _team_member_selector(cc)
    return out


def _step_send_related_contact_text(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if block.get("send_to_contact_field") is not None:
        out["send_to_contact_field"] = _unwrap_id(block["send_to_contact_field"])
    text = block.get("text")
    if isinstance(text, str):
        out["text"] = {"id": text}
    elif isinstance(text, dict) and text.get("id"):
        out["text"] = {"id": text["id"]}
    for k, v in block.items():
        if k in ("send_to_contact_field", "text") or v is None:
            continue
        if not isinstance(v, (dict, list)):
            out[k] = v
    return out


def _step_notify_member_via_email(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    """Native team-member email notification (owner or another
    team_selector field), as opposed to send_related_contact_email which
    only targets Contact-type relationship fields.

    Wire dialect confirmed against the live OpenAPI schema
    (`WriteActionNotifyMemberViaEmailRequest`): no inline subject/body — the
    email content lives entirely on a referenced message resource, pointed
    to by the bare `id` field (not nested like send_related_contact_email's
    `email: {id}`).
    """
    out: dict[str, Any] = {}
    team_member = block.get("team_member")
    if isinstance(team_member, dict):
        out["team_member"] = _team_member_selector(team_member)
    cc = block.get("cc_team_member")
    if isinstance(cc, dict):
        out["cc_team_member"] = _team_member_selector(cc)
    message_id = (
        block.get("id")
        or block.get("email_template_id")
        or _unwrap_id(block.get("email_template"))
    )
    if message_id:
        out["id"] = message_id
    return out


def _merge_field_resolvers(
    auto: AutomationDef, ctx: LiveContext
) -> tuple[merge_fields.ResolveLabel, merge_fields.ResolveObjectName]:
    """The label/objectname resolvers `merge_fields.render` needs for one
    automation payload build, backed by this plan call's `LiveContext`.

    ``entity_record``/``custom_objects`` (the reserved pseudo-tokens for "the
    triggering record" and "the automation's own target_object") resolve
    against the automation's `target_object`; any other namespace outside
    `merge_fields.RESERVED_NAMESPACES` is treated as a real custom object's
    own api_name and resolved against itself. Neither resolver attempts a
    relationship-hop field path (more than one segment past the namespace,
    e.g. ``primary_document_record.id``) — `LiveContext.field_data` only
    resolves a single field api_name on one object, so a hop falls through to
    `merge_fields`'s own title-cased fallback rather than crashing or, worse,
    resolving the wrong field.
    """

    def resolve_label(namespace: str, field_path: str) -> str | None:
        if "." in field_path:
            return None
        if namespace in ("entity_record", "custom_objects"):
            object_api_name = auto.target_object
        elif namespace not in merge_fields.RESERVED_NAMESPACES:
            object_api_name = namespace
        else:
            return None
        if not object_api_name:
            return None
        try:
            data = ctx.field_data(object_api_name, field_path)
        except PlanError:
            return None
        return data.get("display_name") or field_path

    def resolve_objectname(namespace: str) -> str | None:
        try:
            return ctx.object_data(namespace).get("display_name")
        except PlanError:
            return None

    return resolve_label, resolve_objectname


def _step_notify_member_via_text(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    """Native team-member text notification — see _step_notify_member_via_email.

    Wire dialect confirmed against the live OpenAPI schema
    (`ActionNotifyMemberViaTextRequest`): `content`/`html_content` inline,
    optional `base_message_id` to base it on an existing message resource.
    """
    out: dict[str, Any] = {}
    team_member = block.get("team_member")
    if isinstance(team_member, dict):
        out["team_member"] = _team_member_selector(team_member)
    for key in ("content", "html_content"):
        if block.get(key) is not None:
            out[key] = block[key]
    if out.get("content") and not out.get("html_content"):
        # Kizen's builder UI always keeps content/html_content in sync
        # (confirmed from a live capture); a plain-content-only step still
        # renders in the API but shows blank/wrong in the UI without this.
        # Merge-field tokens (e.g. `{{ entity_record.owner }}`) render into
        # the UI's special span markup rather than being escaped literally.
        resolve_label, resolve_objectname = _merge_field_resolvers(auto, ctx)
        out["html_content"] = (
            f"<p>{merge_fields.render(out['content'], resolve_label=resolve_label, resolve_objectname=resolve_objectname)}</p>"
        )
    base_id = block.get("base_message_id") or block.get("message_template_id")
    if base_id:
        out["base_message_id"] = base_id
    return out


def _step_math_operator(
    block: dict[str, Any], _auto: AutomationDef, _ctx: LiveContext
) -> dict[str, Any]:
    """Wire dialect: field_id (bare UUID, reads expand `field`), variable as
    {name} (ids crash other variable writes — match by name), builder
    argument items with null keys dropped."""
    out: dict[str, Any] = {
        "type": block.get("type"),
        "subtype": block.get("subtype"),
    }
    field = block.get("field") or block.get("field_id")
    field_id = _unwrap_id(field)
    if field_id:
        out["field_id"] = field_id
    var = block.get("variable")
    if isinstance(var, dict) and var.get("name"):
        out["variable"] = {"name": var["name"]}
    elif isinstance(var, str):
        out["variable"] = {"name": var}
    args = []
    for a in block.get("simple_builder_arguments") or []:
        item = {k: v for k, v in _strip(a).items() if v is not None}
        f = item.pop("field", None) or item.pop("field_id", None)
        f_id = _unwrap_id(f)
        if f_id:
            item["field_id"] = f_id
        v = item.get("variable")
        if isinstance(v, dict) and v.get("name"):
            item["variable"] = {"name": v["name"]}
        args.append(item)
    out["simple_builder_arguments"] = args
    return {k: v for k, v in out.items() if v is not None}


# --- code_step (already validated against live API) ------------------------


def _step_code_step(
    block: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    return {
        "script": block.get("script", ""),
        "runtime": block.get("runtime", "python-3-13"),
        "inputs": [_code_input(i, auto, ctx) for i in (block.get("inputs") or [])],
        "outputs": [_code_output(o, auto, ctx) for o in (block.get("outputs") or [])],
        "secrets": [
            {"name": s.get("name") if isinstance(s, dict) else s}
            for s in (block.get("secrets") or [])
        ],
    }


def _code_input(
    inp: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": inp["name"],
        "input_type": inp.get("input_type", "field"),
    }
    it = out["input_type"]
    if it == "field":
        field_uuid = (
            inp.get("field_id")
            or _resolve_field(inp.get("field"), auto, ctx)
            or (
                _resolve_field_ref(inp["field_ref"], auto, ctx)
                if inp.get("field_ref")
                else None
            )
        )
        if not field_uuid:
            raise PlanError(
                f"code_step input '{inp['name']}' is type 'field' but has no field/field_ref/field_id"
            )
        out["field"] = {"id": field_uuid}
    elif it == "variable":
        v = inp.get("variable")
        name = (
            v.get("name")
            if isinstance(v, dict)
            else (v if isinstance(v, str) else inp.get("variable_name"))
        )
        if not name:
            raise PlanError(
                f"code_step input '{inp['name']}' is type 'variable' but has no variable_name"
            )
        out["variable"] = {"name": name}
    elif it == "static_value":
        sv = inp.get("static_value")
        if isinstance(sv, dict):
            out["static_value"] = {
                "value": sv.get("value"),
                "entity_record": sv.get("entity_record"),
                "employee": sv.get("employee"),
            }
        else:
            out["static_value"] = {"value": sv, "entity_record": None, "employee": None}
    elif it == "related_field":
        # Wire requires BOTH the relationship field on this object (`field`)
        # and the field on the related object (`related_field`), each {id}.
        fld = inp.get("field")
        fld_id = _unwrap_id(fld) or inp.get("field_id")
        if not fld_id:
            raise PlanError(
                f"code_step input '{inp['name']}' is type 'related_field' but "
                "has no `field` (the relationship field on the automation's object)"
            )
        out["field"] = {"id": fld_id}
        rf = inp.get("related_field")
        rf_id = _unwrap_id(rf)
        if not rf_id:
            raise PlanError(
                f"code_step input '{inp['name']}' is type 'related_field' but "
                "has no `related_field` (the field on the related object)"
            )
        out["related_field"] = {"id": rf_id}
    if inp.get("data_type"):
        out["data_type"] = inp["data_type"]
    if inp.get("is_list") is not None:
        out["is_list"] = inp["is_list"]
    return out


def _code_output(
    o: dict[str, Any], auto: AutomationDef, ctx: LiveContext
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": o["name"],
        "output_type": o.get("output_type", "field"),
    }
    ot = out["output_type"]
    if ot == "field":
        field_uuid = (
            o.get("field_id")
            or _resolve_field(o.get("field"), auto, ctx)
            or (
                _resolve_field_ref(o["field_ref"], auto, ctx)
                if o.get("field_ref")
                else None
            )
        )
        if not field_uuid:
            raise PlanError(
                f"code_step output '{o['name']}' is type 'field' but has no field/field_ref/field_id"
            )
        out["field"] = {"id": field_uuid}
    elif ot == "variable":
        v = o.get("variable")
        name = (
            v.get("name")
            if isinstance(v, dict)
            else (v if isinstance(v, str) else o.get("variable_name"))
        )
        if not name:
            raise PlanError(
                f"code_step output '{o['name']}' is type 'variable' but has no variable_name"
            )
        out["variable"] = {"name": name}
    elif ot == "related_field":
        # Mirrors the related_field branch in _code_input: wire requires
        # BOTH the relationship field on this object (`field`) and the
        # field on the related object (`related_field`), each {id}.
        fld = o.get("field")
        fld_id = _unwrap_id(fld) or o.get("field_id")
        if not fld_id:
            raise PlanError(
                f"code_step output '{o['name']}' is type 'related_field' but "
                "has no `field` (the relationship field on the automation's object)"
            )
        out["field"] = {"id": fld_id}
        rf = o.get("related_field")
        rf_id = _unwrap_id(rf)
        if not rf_id:
            raise PlanError(
                f"code_step output '{o['name']}' is type 'related_field' but "
                "has no `related_field` (the field on the related object)"
            )
        out["related_field"] = {"id": rf_id}
    if o.get("conflict_resolution"):
        out["conflict_resolution"] = o["conflict_resolution"]
    if o.get("create_missing_option") is not None:
        out["create_missing_option"] = o["create_missing_option"]
    if o.get("data_type"):
        out["data_type"] = o["data_type"]
    if o.get("is_list") is not None:
        out["is_list"] = o["is_list"]
    return out


# --- Step dispatch ---------------------------------------------------------


_STEP_BUILDERS: dict[str, Any] = {
    "condition": _step_condition,
    "delay": _step_delay,
    "goal": _step_goal,
    "stop_execution": _step_stop_execution,
    "code_step": _step_code_step,
    "archive_record": _step_archive_record,
    "go_to_automation_step": _step_go_to_automation_step,
    "start_automation": _step_start_automation,
    "change_field_value": _step_change_field_value,
    "modify_related_entities": _step_modify_related_entities,
    "create_related_entity": _step_create_related_entity,
    "initialize_variable": _step_initialize_variable,
    "update_variable": _step_update_variable,
    "call_llm": _step_call_llm,
    "file_content_extraction": _step_file_content_extraction,
    # Same wire shape as file_content_extraction (model/prompt/plugin/
    # input_field/destinations), just data_type=audio.
    "audio_transcription": _step_file_content_extraction,
    "assign_team_member": _step_assign_team_member,
    "schedule_activity": _step_schedule_activity,
    "send_related_contact_email": _step_send_related_contact_email,
    "send_related_contact_text": _step_send_related_contact_text,
    "notify_member_via_email": _step_notify_member_via_email,
    "notify_member_via_text": _step_notify_member_via_text,
    "math_operator": _step_math_operator,
    "search_records": _step_search_records,
}


# See the module comment above `KNOWN_ENUM_CHOICES_TRIGGERS` for what this
# table is and isn't.
KNOWN_ENUM_CHOICES: dict[str, dict[str, KnownChoices]] = {
    "create_related_entity": {
        "new_entity_owner_type": KnownChoices(
            values=("assign_from_context_record", "newly_assigned_owner"),
            source=(
                "tests/fixtures/automations/create_and_modify_related.raw.json, "
                "form_submission.raw.json"
            ),
        ),
    },
    "notify_member_via_text": {
        "team_member.type": KnownChoices(
            values=("employee",),
            source=(
                "First-Use-Feedback session, confirmed live 2026-08-11 "
                "(00-inbox/First-Use-Feedback.md); no repo fixture yet"
            ),
        ),
    },
}

assert set(KNOWN_ENUM_CHOICES) <= set(_STEP_BUILDERS), (
    "KNOWN_ENUM_CHOICES has an entry for a step type _STEP_BUILDERS doesn't wire"
)
assert set(KNOWN_ENUM_CHOICES_TRIGGERS) <= set(_TRIGGER_BUILDERS), (
    "KNOWN_ENUM_CHOICES_TRIGGERS has an entry for a trigger type "
    "_TRIGGER_BUILDERS doesn't wire"
)


# block key (the wire dict key a step/trigger's config rides under, e.g.
# "action_create_related_entity", "trigger_on_or_around_date" — the exact
# mapping `_block_field_for()` / `trigger_{type}` already use when building a
# payload) -> (step_or_trigger_type, its KnownChoices table).
_KNOWN_CHOICES_BLOCK_TABLES: dict[str, tuple[str, dict[str, KnownChoices]]] = {
    **{
        _block_field_for(step_type): (step_type, table)
        for step_type, table in KNOWN_ENUM_CHOICES.items()
    },
    **{
        f"trigger_{trigger_type}": (trigger_type, table)
        for trigger_type, table in KNOWN_ENUM_CHOICES_TRIGGERS.items()
    },
}


def _flatten_error_leaves(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Dotted-path leaves of a nested DRF error dict.

    ``{"team_member": {"type": ["... not a valid choice."]}}`` ->
    ``[("team_member.type", ["... not a valid choice."])]``.
    """
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten_error_leaves(value, child))
        return out
    return [(prefix, node)]


def _is_invalid_choice_message(leaf: Any) -> bool:
    if isinstance(leaf, list):
        return any(_is_invalid_choice_message(item) for item in leaf)
    return isinstance(leaf, str) and "not a valid choice" in leaf.lower()


def known_choices_addendum(raw: Any) -> str | None:
    """Enrich a failed automation op's raw error body with whatever this
    repo already knows about a rejected enum value, or ``None``.

    Walks the whole error tree (the exact top-level nesting isn't pinned to
    one depth) looking for a dict key naming a wired step/trigger block that
    also has an entry in `KNOWN_ENUM_CHOICES`/`KNOWN_ENUM_CHOICES_TRIGGERS`.
    Within that block, every leaf error message containing "not a valid
    choice" at a known field path gets the known values appended — a single
    400 can carry several simultaneous field rejections. Anything else — an
    unrecognized field, an unrecognized block, a different kind of error —
    is left alone: "we don't know this one" must come back as ``None``, never
    a crash or a misleading guess.
    """
    hits = _search_for_known_choice(raw)
    return " ".join(hits) if hits else None


def _search_for_known_choice(node: Any) -> list[str]:
    if isinstance(node, dict):
        hits = [
            hit for key, value in node.items() for hit in _match_known_block(key, value)
        ]
        for value in node.values():
            hits.extend(_search_for_known_choice(value))
        return hits
    if isinstance(node, list):
        return [hit for item in node for hit in _search_for_known_choice(item)]
    return []


def _match_known_block(key: str, value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    entry = _KNOWN_CHOICES_BLOCK_TABLES.get(key)
    if entry is None:
        return []
    block_type, table = entry
    hits = []
    for path, leaf in _flatten_error_leaves(value):
        known = table.get(path)
        if known is not None and _is_invalid_choice_message(leaf):
            hits.append(
                f"Known valid values for {block_type}.{path}: "
                f"{', '.join(known.values)} (source: {known.source})."
            )
    return hits

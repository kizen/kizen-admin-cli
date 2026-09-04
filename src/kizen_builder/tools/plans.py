"""Plan + apply scaffolding.

Mutations always go through a two-step round-trip:

1. A ``plan_*`` tool produces a :class:`Plan` describing the operations
   that *would* run, without touching Kizen. Plans are JSON-serializable
   and held in conversation context — there is no on-disk plan store.
2. The user reviews and approves.
3. :func:`apply_plan` walks the operations and executes each against the
   target env's API, returning a structured result list.

A plan binds to one ``env``.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kizen_builder.api import activities as act_api
from kizen_builder.api import automations as auto_api
from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import dashboards as dash_api
from kizen_builder.api import forms as forms_api
from kizen_builder.api import layouts as layout_api
from kizen_builder.api import permissions as perm_api
from kizen_builder.api import pipelines as pipelines_api
from kizen_builder.api import records as records_api
from kizen_builder.api import saved_views as sv_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.permissions import LEVELS


class PlanError(ValueError):
    """Raised when planning fails because the input doesn't match live state."""


Action = Literal["create", "update", "delete", "duplicate", "upsert", "apply", "skip"]
Kind = Literal[
    "object",
    "category",
    "field",
    "field_option",
    "automation",
    "automation_message",
    "email_template",
    "automation_folder",
    "dashboard",
    "dashlet",
    "layout",
    "record",
    "activity",
    "activity_field",
    "activity_field_option",
    "stage",
    "record_move",
    "record_archive",
    "record_unarchive",
    "form",
    "form_field",
    "form_field_option",
    "survey",
    "survey_field",
    "survey_field_option",
    "role",
    "permission_group",
    "permission_setting",
    "filter_group",
    "quick_filter",
    "column_template",
    "record_bulk_field_value",
]

# Forms and surveys are structurally identical — one op-kind pair
# ("form"/"survey" etc.) each maps to the same shared api.forms functions
# with a different base path baked in here.
_FORM_LIKE_BASE_PATHS: dict[str, str] = {
    "form": "/api/forms",
    "survey": "/api/surveys",
}


class PlanOperation(BaseModel):
    """One API call that a plan will perform.

    ``preview`` is a human-readable summary used when showing the plan in
    chat. ``payload`` is the actual JSON the API call will receive. Both
    are kept on the plan so the user can see *both* the high-level intent
    and the literal wire body.
    """

    model_config = ConfigDict(extra="forbid")

    action: Action
    kind: Kind
    key: str = Field(
        description=(
            "Human-readable identifier for the entity, e.g. "
            "'conditions.severity_score' for a field, "
            "'condition_severity_alert' for an automation."
        )
    )
    preview: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of what's changing. Shown to the user.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact API request body.",
    )
    existing_uuid: str | None = Field(
        default=None,
        description=(
            "For update/skip ops: the server UUID we're targeting. "
            "Set during planning by looking up live state."
        ),
    )
    parent_object_uuid: str | None = Field(
        default=None,
        description=(
            "For category/field ops: the UUID of the parent custom object. "
            "Resolved during planning."
        ),
    )
    deferred_parent_object_key: str | None = Field(
        default=None,
        description=(
            "If set, the parent object UUID is unknown at plan time and must "
            "be resolved at apply time from the result of an earlier op in the "
            "same plan whose ``key`` matches this value. Useful when an "
            "object is being created in the same plan that creates its "
            "categories or fields."
        ),
    )
    deferred_category_key: str | None = Field(
        default=None,
        description=(
            "Same idea as deferred_parent_object_key but for the field's "
            "category. The resolved server UUID is written into "
            "``payload['category']`` before the API call."
        ),
    )


class Plan(BaseModel):
    """A bundle of operations that produce a coherent change in one env."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Short hash that uniquely identifies this plan.")
    env: str = Field(description="Target env label.")
    summary: str = Field(description="One-line description of what this plan does.")
    operations: list[PlanOperation] = Field(default_factory=list)

    @classmethod
    def build(cls, env: str, summary: str, operations: list[PlanOperation]) -> Plan:
        """Construct a plan with a deterministic id derived from contents.

        The id is short (8 hex chars) and only used for display and decision-log
        cross-referencing; it doesn't have to be globally unique.
        """
        h = hashlib.sha1()
        h.update(env.encode())
        h.update(summary.encode())
        for op in operations:
            h.update(op.model_dump_json().encode())
        h.update(
            str(time.time_ns()).encode()
        )  # nudge so identical plans differ across runs
        return cls(
            id=h.hexdigest()[:8], env=env, summary=summary, operations=operations
        )


class OperationResult(BaseModel):
    """Outcome of executing one operation."""

    model_config = ConfigDict(extra="forbid")

    key: str
    kind: Kind
    action: Action
    # "adjusted": the write succeeded but the server normalized the value
    # away from what was requested (e.g. a cross-field rule) — not a
    # failure, just not exact. Excluded from all_ok the same as "ok".
    status: Literal["ok", "failed", "skipped", "adjusted"]
    server_uuid: str | None = None
    message: str | None = None
    raw: dict[str, Any] | None = None


class ApplyResult(BaseModel):
    """Outcome of applying a whole plan."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    env: str
    results: list[OperationResult]

    @property
    def all_ok(self) -> bool:
        return all(r.status in ("ok", "skipped", "adjusted") for r in self.results)


# ---------------------------------------------------------------------------
# Apply orchestrator
# ---------------------------------------------------------------------------


def apply_plan(plan: Plan) -> ApplyResult:
    """Execute every operation in ``plan`` against ``plan.env``.

    Operations run sequentially in the order they appear. Any single op's
    failure — an API error or an internal error like a malformed op — is
    recorded as a failed :class:`OperationResult` and the batch continues, so
    the caller always gets a complete report (rather than a traceback and a
    half-applied plan). Two kinds of cross-op coordination:

    * **Parent-failure guard** — if an op fails (or is skipped because its
      own parent failed), any later op whose ``key`` is prefixed by the
      failed op's key is skipped with an explanatory message. Catches the
      common "create object → create field on it" → object failed case.
    * **Deferred refs** — an op may reference an earlier op's server UUID
      via ``deferred_parent_object_key`` / ``deferred_category_key``. The
      executor reads ``results_by_key`` and patches the URL/payload before
      dispatching. Useful when an object is being created in the same
      plan that creates its categories or fields.
    """
    config = load_env_config()
    failed_keys: set[str] = set()
    results: list[OperationResult] = []
    results_by_key: dict[str, str] = {}  # op key → server UUID

    with KizenClient(config) as client:
        for op in plan.operations:
            # Parent-failure guard: skip if a prefix of the key is in failed_keys
            # OR if a deferred ref points at a failed op.
            blocked_by = _blocked_by(op, failed_keys)
            if blocked_by:
                results.append(
                    OperationResult(
                        key=op.key,
                        kind=op.kind,
                        action=op.action,
                        status="skipped",
                        message=f"blocked by failed op '{blocked_by}'",
                    )
                )
                failed_keys.add(op.key)
                continue

            # Resolve deferred refs from prior results
            try:
                resolved_op = _resolve_deferred_refs(op, results_by_key)
            except KeyError as e:
                results.append(
                    OperationResult(
                        key=op.key,
                        kind=op.kind,
                        action=op.action,
                        status="failed",
                        message=f"unresolved deferred ref: {e}",
                    )
                )
                failed_keys.add(op.key)
                continue

            try:
                resp = _execute(client, resolved_op)
            except KizenAPIError as e:
                results.append(
                    OperationResult(
                        key=op.key,
                        kind=op.kind,
                        action=op.action,
                        status="failed",
                        message=str(e),
                        raw=e.body if isinstance(e.body, dict) else None,
                    )
                )
                failed_keys.add(op.key)
                continue
            except Exception as e:  # noqa: BLE001
                # A non-API failure mid-batch (e.g. an internal PlanError from a
                # malformed op, or an unexpected error) must not abort the whole
                # apply and leave a half-applied plan with no report. Record it
                # as a failed op and carry on — the parent-failure guard still
                # skips anything downstream that depended on it.
                results.append(
                    OperationResult(
                        key=op.key,
                        kind=op.kind,
                        action=op.action,
                        status="failed",
                        message=f"{type(e).__name__}: {e}",
                    )
                )
                failed_keys.add(op.key)
                continue

            server_uuid = resp.get("id") if isinstance(resp, dict) else None
            if server_uuid:
                results_by_key[op.key] = server_uuid
            message = None
            status: Literal["ok", "failed", "skipped", "adjusted"] = (
                "ok" if op.action != "skip" else "skipped"
            )
            if op.action == "upsert" and isinstance(resp, dict) and resp.get("action"):
                message = resp["action"]  # "created" or "updated"
            elif (
                op.kind == "permission_setting"
                and op.payload.get("mode") == "object_update"
                and isinstance(resp, dict)
            ):
                # object-update silently corrects the level it's given instead
                # of 4xx-ing, and reports it in the response body — but there
                # are two different reasons, and only one is a failure:
                #
                # 1. The control had no entry in the group at plan time
                #    (`control_present=False` — set in `_setting_op`). Insert
                #    always lands at "none" regardless of what was asked, a
                #    follow-up apply doesn't change that, and nothing the
                #    caller wanted happened. Genuine failure.
                # 2. The control was already present — a *legal* write that a
                #    cross-field rule (e.g. `associated_records >= all_records`)
                #    then normalized based on the group's final state, which
                #    this planner doesn't simulate (see docs/specs/
                #    permission-group.md). The write succeeded; it just didn't
                #    land exactly where asked. Not a failure — reported as
                #    "adjusted" so it doesn't flip `kizen apply`'s exit code.
                requested = op.payload["body"].get("permission_level")
                returned = resp.get("permission_level")
                if (
                    requested is not None
                    and returned is not None
                    and returned != requested
                ):
                    detail = (resp.get("details") or {}).get("message")
                    if op.payload.get("control_present") is False:
                        status = "failed"
                        message = (
                            f"server set permission_level={returned}, requested "
                            f"{requested}" + (f" — {detail}" if detail else "")
                        )
                        failed_keys.add(op.key)
                    else:
                        status = "adjusted"
                        requested_name = LEVELS.get(requested, str(requested))
                        returned_name = LEVELS.get(returned, str(returned))
                        message = (
                            f"requested {requested_name}, server normalized to "
                            f"{returned_name}" + (f" ({detail})" if detail else "")
                        )
            results.append(
                OperationResult(
                    key=op.key,
                    kind=op.kind,
                    action=op.action,
                    status=status,
                    server_uuid=server_uuid,
                    message=message,
                    raw=resp if isinstance(resp, dict) else None,
                )
            )

    return ApplyResult(plan_id=plan.id, env=plan.env, results=results)


def _blocked_by(op: PlanOperation, failed_keys: set[str]) -> str | None:
    """Return the key of a failed prior op that should block this one, or None."""
    if op.deferred_parent_object_key and op.deferred_parent_object_key in failed_keys:
        return op.deferred_parent_object_key
    if op.deferred_category_key and op.deferred_category_key in failed_keys:
        return op.deferred_category_key
    # Implicit prefix-based dependency for fields/categories under an object,
    # and for dashlets under a dashboard.
    if (
        op.kind
        in (
            "category",
            "field",
            "dashlet",
            "activity_field",
            "form_field",
            "survey_field",
        )
        and "." in op.key
    ):
        prefix = op.key.split(".", 1)[0]
        if prefix in failed_keys:
            return prefix
    return None


def _resolve_deferred_refs(
    op: PlanOperation, results_by_key: dict[str, str]
) -> PlanOperation:
    """Return ``op`` with deferred refs filled in from prior results.

    Returns a new :class:`PlanOperation` (copies the underlying payload
    where it needs mutation) so the original is left untouched.
    """
    if not (op.deferred_parent_object_key or op.deferred_category_key):
        return op

    new_parent = op.parent_object_uuid
    new_payload = dict(op.payload)

    if op.deferred_parent_object_key:
        if op.deferred_parent_object_key not in results_by_key:
            raise KeyError(op.deferred_parent_object_key)
        new_parent = results_by_key[op.deferred_parent_object_key]

    if op.deferred_category_key:
        if op.deferred_category_key not in results_by_key:
            raise KeyError(op.deferred_category_key)
        new_payload["category"] = results_by_key[op.deferred_category_key]

    return op.model_copy(
        update={"parent_object_uuid": new_parent, "payload": new_payload}
    )


def _execute(client: KizenClient, op: PlanOperation) -> Any:
    """Dispatch one operation to the right API call. Raises on failure."""
    if op.action == "skip":
        return {}  # no-op

    if op.kind == "object":
        if op.action == "create":
            return co_api.create_object(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} object op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return co_api.delete_object(client, op.existing_uuid)
        return co_api.update_object(client, op.existing_uuid, op.payload)

    if op.kind == "category":
        if op.parent_object_uuid is None:
            raise PlanError(
                f"category op '{op.key}' has no parent_object_uuid — planning bug"
            )
        if op.action == "create":
            return co_api.create_category(client, op.parent_object_uuid, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} category op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return co_api.delete_category(
                client, op.parent_object_uuid, op.existing_uuid
            )
        return co_api.update_category(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "field":
        if op.parent_object_uuid is None:
            raise PlanError(
                f"field op '{op.key}' has no parent_object_uuid — planning bug"
            )
        if op.action == "create":
            return co_api.create_field(client, op.parent_object_uuid, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} field op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return co_api.delete_field(client, op.parent_object_uuid, op.existing_uuid)
        return co_api.update_field(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "field_option":
        # parent_object_uuid = object id; payload["field_id"] = field id.
        if op.parent_object_uuid is None:
            raise PlanError(
                f"field_option op '{op.key}' has no parent_object_uuid — planning bug"
            )
        field_id = op.payload.get("field_id")
        if not field_id:
            raise PlanError(
                f"field_option op '{op.key}' has no field_id — planning bug"
            )
        if op.action == "create":
            body = {k: v for k, v in op.payload.items() if k != "field_id"}
            return co_api.add_field_option(
                client, op.parent_object_uuid, field_id, body
            )
        if op.action == "delete":
            if op.existing_uuid is None:
                raise PlanError(
                    f"delete field_option op '{op.key}' has no existing_uuid — planning bug"
                )
            remap_to = op.payload.get("remap_to")
            if remap_to:
                # Move records off the doomed option before it disappears.
                return co_api.replace_field_option(
                    client,
                    op.parent_object_uuid,
                    field_id,
                    op.existing_uuid,
                    {"id": remap_to},
                )
            return co_api.delete_field_option(
                client, op.parent_object_uuid, field_id, op.existing_uuid
            )
        raise PlanError(f"unsupported field_option action '{op.action}'")

    if op.kind == "record":
        # parent_object_uuid carries the object_identifier (api_name or uuid);
        # the records endpoint accepts either in the URL path.
        if op.parent_object_uuid is None:
            raise PlanError(
                f"record op '{op.key}' has no object identifier — planning bug"
            )
        object_identifier = op.parent_object_uuid
        if op.action == "create":
            return records_api.create_record(
                client, object_identifier, op.payload.get("fields", [])
            )
        if op.action == "upsert":
            return records_api.upsert_record(
                client,
                object_identifier,
                op.payload["lookup_value"],
                op.payload.get("fields", []),
                oncreate_unarchive=op.payload.get("oncreate_unarchive"),
                onupdate_archived_conflict=op.payload.get("onupdate_archived_conflict"),
            )
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} record op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return records_api.delete_record(
                client, object_identifier, op.existing_uuid
            )
        return records_api.update_record(
            client, object_identifier, op.existing_uuid, op.payload.get("fields", [])
        )

    if op.kind == "record_archive":
        # parent_object_uuid carries the object's UUID here, not the
        # api_name — the bulk-archive-entity-record endpoint lives under
        # /api/custom-objects, same convention as record_bulk_field_value.
        if op.parent_object_uuid is None or op.existing_uuid is None:
            raise PlanError(
                f"record_archive op '{op.key}' missing object/record id — planning bug"
            )
        return records_api.archive_record(
            client, op.parent_object_uuid, op.existing_uuid
        )

    if op.kind == "record_unarchive":
        # parent_object_uuid carries the object_identifier (api_name or
        # uuid) here, same convention as kind="record".
        if op.parent_object_uuid is None or op.existing_uuid is None:
            raise PlanError(
                f"record_unarchive op '{op.key}' missing object/record id — planning bug"
            )
        return records_api.unarchive_record(
            client, op.parent_object_uuid, op.existing_uuid
        )

    if op.kind == "automation":
        from kizen_builder.tools.automations import (
            _reconcile_notify_member_message_links,
        )

        if op.action == "create":
            created = auto_api.create_automation(client, op.payload)
            _reconcile_notify_member_message_links(client, created)
            return created
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} automation op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return auto_api.delete_automation(client, op.existing_uuid)
        if op.action == "duplicate":
            return auto_api.duplicate_automation(client, op.existing_uuid, op.payload)
        # The plan builder assembles the full PUT body (server-field merge,
        # variable-id injection, last_revision) so the previewed payload is
        # exactly what gets sent. If last_revision is somehow absent (plan
        # JSON from an older build), update_automation refetches it.
        updated = auto_api.update_automation(
            client,
            op.existing_uuid,
            op.payload,
            last_revision=op.payload.get("last_revision"),
        )
        _reconcile_notify_member_message_links(client, updated)
        return updated

    if op.kind == "automation_message":
        from kizen_builder.api import messages as messages_api

        return messages_api.create_automation_message_from_template(
            client, op.payload["automation_id"], op.payload["template"]
        )

    if op.kind == "email_template":
        from kizen_builder.api import messages as messages_api

        if op.action == "create":
            return messages_api.create_template(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} email_template op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return messages_api.delete_template(client, op.existing_uuid)
        return messages_api.update_template(client, op.existing_uuid, op.payload)

    if op.kind == "automation_folder":
        if op.action == "create":
            return auto_api.create_folder(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} automation_folder op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return auto_api.delete_folder(client, op.existing_uuid)
        return auto_api.update_folder(client, op.existing_uuid, op.payload)

    if op.kind == "dashboard":
        if op.action == "create":
            return dash_api.create_dashboard(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"update dashboard op '{op.key}' has no existing_uuid — planning bug"
            )
        return dash_api.update_dashboard(client, op.existing_uuid, op.payload)

    if op.kind == "dashlet":
        # parent_object_uuid carries the parent *dashboard* id (resolved from a
        # deferred ref when the dashboard is created in the same plan).
        if op.parent_object_uuid is None:
            raise PlanError(
                f"dashlet op '{op.key}' has no parent dashboard id — planning bug"
            )
        if op.action == "create":
            return dash_api.create_dashlet(client, op.parent_object_uuid, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"update dashlet op '{op.key}' has no existing_uuid — planning bug"
            )
        return dash_api.update_dashlet(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "layout":
        # Layouts are PUT-replace only (Kizen auto-creates the Standard View).
        # parent_object_uuid = custom object id, existing_uuid = layout id.
        if op.parent_object_uuid is None or op.existing_uuid is None:
            raise PlanError(
                f"layout op '{op.key}' missing object/layout id — planning bug"
            )
        return layout_api.update_layout(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "activity":
        if op.action == "create":
            return act_api.create_activity(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} activity op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return act_api.delete_activity(client, op.existing_uuid)
        return act_api.update_activity(client, op.existing_uuid, op.payload)

    if op.kind == "activity_field":
        # parent_object_uuid carries the parent *activity* id (resolved from a
        # deferred ref when the activity is created in the same plan).
        if op.parent_object_uuid is None:
            raise PlanError(
                f"activity_field op '{op.key}' has no parent activity id — planning bug"
            )
        if op.action == "create":
            return act_api.create_activity_field(
                client, op.parent_object_uuid, op.payload
            )
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} activity_field op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return act_api.delete_activity_field(
                client, op.parent_object_uuid, op.existing_uuid
            )
        return act_api.update_activity_field(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "activity_field_option":
        # parent_object_uuid = activity id; payload["field_id"] = field id.
        if op.parent_object_uuid is None:
            raise PlanError(
                f"activity_field_option op '{op.key}' has no parent activity id — planning bug"
            )
        field_id = op.payload.get("field_id")
        if not field_id:
            raise PlanError(
                f"activity_field_option op '{op.key}' has no field_id — planning bug"
            )
        if op.action == "create":
            body = {k: v for k, v in op.payload.items() if k != "field_id"}
            return act_api.add_activity_field_option(
                client, op.parent_object_uuid, field_id, body
            )
        if op.action == "delete":
            if op.existing_uuid is None:
                raise PlanError(
                    f"delete activity_field_option op '{op.key}' has no existing_uuid — planning bug"
                )
            remap_to = op.payload.get("remap_to")
            if remap_to:
                # Unlike the custom-object endpoint, the activities replace call
                # only reassigns records onto ``option_id`` — it leaves the old
                # option in place — so delete it explicitly afterward. (The
                # OpenAPI spec also mislabels the body key as ``id``.)
                act_api.replace_activity_field_option(
                    client,
                    op.parent_object_uuid,
                    field_id,
                    op.existing_uuid,
                    {"option_id": remap_to},
                )
                return act_api.delete_activity_field_option(
                    client, op.parent_object_uuid, field_id, op.existing_uuid
                )
            return act_api.delete_activity_field_option(
                client, op.parent_object_uuid, field_id, op.existing_uuid
            )
        raise PlanError(f"unsupported activity_field_option action '{op.action}'")

    if op.kind == "stage":
        # parent_object_uuid = pipeline object id.
        if op.parent_object_uuid is None:
            raise PlanError(
                f"stage op '{op.key}' has no parent pipeline id — planning bug"
            )
        if op.action == "create":
            return pipelines_api.create_stage(client, op.parent_object_uuid, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} stage op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            new_stage_id = op.payload.get("new_stage_id")
            if not new_stage_id:
                raise PlanError(
                    f"delete stage op '{op.key}' has no new_stage_id — planning bug"
                )
            return pipelines_api.remove_stage(
                client,
                op.parent_object_uuid,
                {"id": op.existing_uuid, "new_stage_id": new_stage_id},
            )
        return pipelines_api.update_stage(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind == "record_move":
        # parent_object_uuid carries the object_identifier (api_name or uuid),
        # same convention as kind="record".
        if op.parent_object_uuid is None or op.existing_uuid is None:
            raise PlanError(
                f"record_move op '{op.key}' missing object/record id — planning bug"
            )
        return pipelines_api.move_record(
            client, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind in ("form", "survey"):
        base_path = _FORM_LIKE_BASE_PATHS[op.kind]
        if op.action == "create":
            return forms_api.create_form(client, base_path, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} {op.kind} op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return forms_api.delete_form(client, base_path, op.existing_uuid)
        if op.action == "duplicate":
            return forms_api.duplicate_form(
                client, base_path, op.existing_uuid, op.payload
            )
        return forms_api.update_form(client, base_path, op.existing_uuid, op.payload)

    if op.kind in ("form_field", "survey_field"):
        base_path = _FORM_LIKE_BASE_PATHS[op.kind.rsplit("_", 1)[0]]
        # parent_object_uuid carries the parent form/survey id (resolved from a
        # deferred ref when the object is created in the same plan).
        if op.parent_object_uuid is None:
            raise PlanError(f"{op.kind} op '{op.key}' has no parent id — planning bug")
        if op.action == "create":
            return forms_api.create_form_field(
                client, base_path, op.parent_object_uuid, op.payload
            )
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} {op.kind} op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            return forms_api.delete_form_field(
                client, base_path, op.parent_object_uuid, op.existing_uuid
            )
        return forms_api.update_form_field(
            client, base_path, op.parent_object_uuid, op.existing_uuid, op.payload
        )

    if op.kind in ("form_field_option", "survey_field_option"):
        base_path = _FORM_LIKE_BASE_PATHS[op.kind.rsplit("_", 2)[0]]
        # parent_object_uuid = form/survey id; payload["field_id"] = field id.
        if op.parent_object_uuid is None:
            raise PlanError(f"{op.kind} op '{op.key}' has no parent id — planning bug")
        field_id = op.payload.get("field_id")
        if not field_id:
            raise PlanError(f"{op.kind} op '{op.key}' has no field_id — planning bug")
        if op.action == "create":
            body = {k: v for k, v in op.payload.items() if k != "field_id"}
            return forms_api.add_form_field_option(
                client, base_path, op.parent_object_uuid, field_id, body
            )
        if op.action == "delete":
            if op.existing_uuid is None:
                raise PlanError(
                    f"delete {op.kind} op '{op.key}' has no existing_uuid — planning bug"
                )
            remap_to = op.payload.get("remap_to")
            if remap_to:
                # Mirrors the activities quirk: replace only reassigns records
                # onto the replacement, it doesn't remove the old option, so
                # delete it explicitly afterward.
                forms_api.replace_form_field_option(
                    client,
                    base_path,
                    op.parent_object_uuid,
                    field_id,
                    op.existing_uuid,
                    {"option_id": remap_to},
                )
                return forms_api.delete_form_field_option(
                    client, base_path, op.parent_object_uuid, field_id, op.existing_uuid
                )
            return forms_api.delete_form_field_option(
                client, base_path, op.parent_object_uuid, field_id, op.existing_uuid
            )
        raise PlanError(f"unsupported {op.kind} action '{op.action}'")

    if op.kind == "role":
        if op.action == "create":
            return perm_api.create_role(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} role op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            perm_api.delete_role(client, op.existing_uuid)
            return {}
        return perm_api.update_role(client, op.existing_uuid, op.payload)

    if op.kind == "permission_group":
        if op.action == "create":
            return perm_api.create_permission_group(client, op.payload)
        if op.existing_uuid is None:
            raise PlanError(
                f"{op.action} permission_group op '{op.key}' has no existing_uuid — planning bug"
            )
        if op.action == "delete":
            perm_api.delete_permission_group(client, op.existing_uuid)
            return {}
        # update = rename via PUT (metadata only)
        return perm_api.update_permission_group(client, op.existing_uuid, op.payload)

    if op.kind == "permission_setting":
        # parent_object_uuid carries the target permission-group id (resolved
        # from a deferred ref when the group is created in the same plan).
        group_id = op.parent_object_uuid
        if group_id is None:
            raise PlanError(
                f"permission_setting op '{op.key}' has no group id — planning bug"
            )
        mode = op.payload.get("mode")
        if mode == "object_update":
            return perm_api.object_update_permission(
                client, group_id, op.payload["body"]
            )
        if mode == "section":
            return perm_api.patch_permission_group(client, group_id, op.payload["body"])
        raise PlanError(f"permission_setting op '{op.key}' has unknown mode {mode!r}")

    if op.kind == "record_bulk_field_value":
        if op.parent_object_uuid is None:
            raise PlanError(
                f"record_bulk_field_value op '{op.key}' has no object id — planning bug"
            )
        return co_api.bulk_change_field_value(client, op.parent_object_uuid, op.payload)

    if op.kind in ("filter_group", "quick_filter", "column_template"):
        base = {
            "filter_group": sv_api.FILTER_GROUPS_BASE,
            "quick_filter": sv_api.QUICK_FILTERS_BASE,
            "column_template": sv_api.COLUMNS_BASE,
        }[op.kind]
        return _execute_saved_view(client, op, base)

    raise ValueError(f"unknown operation kind: {op.kind}")


def _execute_saved_view(client: KizenClient, op: PlanOperation, base: str) -> Any:
    """Shared dispatch for filter groups / quick filters / column templates —
    identical CRUD + apply-to-* shape, differing only in base path."""
    if op.parent_object_uuid is None:
        raise PlanError(
            f"{op.kind} op '{op.key}' has no parent object id — planning bug"
        )
    if op.action == "create":
        return sv_api.create_saved_view(client, op.parent_object_uuid, base, op.payload)
    if op.existing_uuid is None:
        raise PlanError(
            f"{op.action} {op.kind} op '{op.key}' has no existing_uuid — planning bug"
        )
    if op.action == "delete":
        return sv_api.delete_saved_view(
            client, op.parent_object_uuid, base, op.existing_uuid
        )
    if op.action == "apply":
        target = op.payload.get("target")
        ids = op.payload.get("ids") or []
        if target == "roles":
            return sv_api.apply_to_roles(
                client, op.parent_object_uuid, base, op.existing_uuid, ids
            )
        if target == "users":
            return sv_api.apply_to_users(
                client, op.parent_object_uuid, base, op.existing_uuid, ids
            )
        if target == "permission_groups":
            return sv_api.apply_to_permission_groups(
                client, op.parent_object_uuid, base, op.existing_uuid, ids
            )
        raise PlanError(f"apply {op.kind} op '{op.key}' has unknown target {target!r}")
    return sv_api.update_saved_view(
        client, op.parent_object_uuid, base, op.existing_uuid, op.payload
    )


# ---------------------------------------------------------------------------
# JSON helpers (CLI uses these to round-trip plans through stdin/stdout)
# ---------------------------------------------------------------------------


def plan_to_json(plan: Plan) -> str:
    return plan.model_dump_json(indent=2)


def plan_from_json(text: str) -> Plan:
    return Plan.model_validate_json(text)


def result_to_json(result: ApplyResult) -> str:
    return result.model_dump_json(indent=2)

"""Plan creation/update/delete for forms, surveys, and their fields.

Forms (``/api/forms``) and surveys (``/api/surveys``) are structurally
identical, so every planner function here takes ``base_path`` + ``kind``
keywords instead of being duplicated per object type. ``kind`` ("form" /
"survey") only affects op keys/preview text and the plan-op ``Kind`` literal
used to dispatch in ``tools/plans.py``.

Mirrors ``planners/activities.py`` closely — forms/survey fields are the same
FieldDef-shaped machinery as activity fields (and custom-object fields).
"""

from __future__ import annotations

from typing import Any, Literal

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import FormDef, FormFieldDef
from kizen_builder.tools.form_ui import unplaced_fields
from kizen_builder.tools.forms import FORMS_BASE_PATH, get_form, list_forms
from kizen_builder.tools.objects import get_object
from kizen_builder.tools.plans import (
    Kind,
    Plan,
    PlanError,
    PlanOperation,
    copy_name_preview,
    fetch_existing,
)

_OPTION_FIELD_TYPES = {
    "dropdown",
    "radio",
    "status",
    "choices",
    "selector",
    "checkboxes",
    "dynamictags",
    "yesnomaybe",
}

FormKind = Literal["form", "survey"]


def _kinds(kind: FormKind) -> tuple[Kind, Kind, Kind]:
    """Return the (object, field, field_option) plan-op Kind literals for ``kind``."""
    return kind, f"{kind}_field", f"{kind}_field_option"  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Form / survey object
# ---------------------------------------------------------------------------


def plan_create_form(
    spec: dict[str, Any] | FormDef,
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan creation of one form/survey, plus any inline ``fields``.

    The object is created first; each field is a follow-on op that resolves
    its parent id from the create result via a deferred ref, so the whole
    thing applies in one confirm.
    """
    obj_kind, field_kind, _ = _kinds(kind)
    form_def = spec if isinstance(spec, FormDef) else FormDef.model_validate(spec)
    env = load_env_config().name

    existing = next(
        (
            f
            for f in list_forms(base_path=base_path)
            if f.get("api_name") and f["api_name"] == form_def.api_name
        ),
        None,
    )
    if form_def.api_name and existing is not None:
        raise PlanError(
            f"{kind} '{form_def.api_name}' already exists (uuid {existing['id']}). "
            f"Use plan_update_form instead."
        )
    if not form_def.related_object and not form_def.related_object_id:
        raise PlanError(
            f"creating a {kind} requires 'related_object' (the api_name of the "
            "custom object its submissions attach records to) or a raw "
            "'related_object_id' UUID."
        )

    object_key = f"{kind}:{form_def.api_name or form_def.name}"
    ops: list[PlanOperation] = [
        PlanOperation(
            action="create",
            kind=obj_kind,
            key=object_key,
            preview={
                "env": env,
                "name": form_def.name,
                "api_name": form_def.api_name or "(server-derived)",
                "fields": len(form_def.fields or []),
            },
            payload=_build_form_payload(form_def),
        )
    ]

    seen: set[str] = set()
    for idx, field in enumerate(form_def.fields or []):
        label = field.api_name or field.name
        if label in seen:
            raise PlanError(f"duplicate {kind} field '{label}' in the batch.")
        seen.add(label)
        ops.append(
            PlanOperation(
                action="create",
                kind=field_kind,
                key=f"{object_key}.field:{label}",
                preview={
                    "env": env,
                    "field": label,
                    "field_type": field.field_type,
                    "required": field.required,
                },
                payload=_build_form_field_payload(field, default_order=idx),
                deferred_parent_object_key=object_key,
            )
        )

    return Plan.build(
        env=env,
        summary=(
            f"Create {kind} '{form_def.name}'"
            + (f" with {len(form_def.fields)} field(s)" if form_def.fields else "")
        ),
        operations=ops,
    )


_UPDATABLE_FORM_KEYS = {
    "name": "name",
    "api_name": "api_name",
    "description": "description",
    "template_type": "template_type",
    "submission_action": "submission_action",
    "redirect_url": "redirect_url",
    "pass_variables_on_redirect": "pass_variables_on_redirect",
    "challenge_token_required": "challenge_token_required",
    "subscribers": "subscribers",
    "business_merge_fields": "business_merge_fields",
    "form_ui": "form_ui",
}

# Large/opaque values (the form_ui page-layout blob) get a length summary in
# the plan preview instead of a raw dump — a full diff would be unreadable.
_SUMMARIZE_DIFF_KEYS = {"form_ui"}


def _page_count(value: Any) -> int:
    return len(value.get("pages") or []) if isinstance(value, dict) else 0


def _diff_display(key: str, old: Any, new: Any) -> str:
    if key in _SUMMARIZE_DIFF_KEYS:
        return f"({_page_count(old)} page(s)) → ({_page_count(new)} page(s))"
    return f"{old} → {new}"


def plan_update_form(
    identifier: str,
    changes: dict[str, Any],
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan a PATCH to one form/survey. Only keys present in ``changes`` are sent."""
    obj_kind, _, _ = _kinds(kind)
    env = load_env_config().name
    current = _load_form(identifier, base_path, kind, include_fields=False)

    raw = current.get("raw") or {}
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for key, wire in _UPDATABLE_FORM_KEYS.items():
        if key not in changes:
            continue
        new = changes[key]
        old = raw.get(wire)
        if new != old:
            payload[wire] = new
            diff[wire] = (old, new)

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind=obj_kind,
        key=f"{kind}:{current.get('api_name') or identifier}",
        preview={
            "env": env,
            kind: current.get("name"),
            "diff": {k: _diff_display(k, v[0], v[1]) for k, v in diff.items()}
            or "no changes",
        },
        payload=payload,
        existing_uuid=current["id"],
    )
    summary = (
        f"Update {kind} '{current.get('name')}' ({len(diff)} change(s))"
        if diff
        else f"No changes to {kind} '{current.get('name')}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_form(
    identifier: str,
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan deletion of one form/survey."""
    obj_kind, _, _ = _kinds(kind)
    env = load_env_config().name
    current = _load_form(identifier, base_path, kind, include_fields=False)

    op = PlanOperation(
        action="delete",
        kind=obj_kind,
        key=f"{kind}:{current.get('api_name') or identifier}",
        preview={
            "env": env,
            kind: current.get("name"),
            "n_submissions": current.get("n_submissions"),
        },
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete {kind} '{current.get('name')}'",
        operations=[op],
    )


def plan_duplicate_form(
    identifier: str,
    *,
    name: str | None = None,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan duplication of one form/survey.

    Renaming the copy is an update, which deletes every field not placed on a
    ``form_ui`` page, so ``name`` is refused unless the source places them all.
    """
    obj_kind, _, _ = _kinds(kind)
    env = load_env_config().name
    current = _load_form(identifier, base_path, kind, include_fields=bool(name))

    if name:
        form_ui = current["raw"].get("form_ui")
        unplaced = unplaced_fields(form_ui, current["fields"])
        if unplaced or not (form_ui or {}).get("pages"):
            missing = ", ".join(f["api_name"] or f["id"] for f in unplaced)
            group = f"{kind}s"
            raise PlanError(
                f"can't rename a copy of {kind} '{current.get('name')}': the "
                "rename is an update, and Kizen deletes every field an update "
                "finds on no page. "
                + (f"Not on a page: {missing}. " if unplaced else "It has no pages. ")
                + f"To copy it safely, run `kizen {group} duplicate "
                f"{current['id']}` without --name, place every field on the copy "
                f"with `kizen {group} set-ui <copy id>`, then `kizen {group} "
                f"update <copy id> --name {name!r}`."
            )

    op = PlanOperation(
        action="duplicate",
        kind=obj_kind,
        key=f"{kind}:{current.get('api_name') or identifier}:duplicate",
        preview={
            "env": env,
            "source": current.get("name"),
            "new_name": copy_name_preview(current.get("name"), name),
        },
        payload={"name": name} if name else {},
        existing_uuid=current["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Duplicate {kind} '{current.get('name')}'",
        operations=[op],
    )


# ---------------------------------------------------------------------------
# Form / survey fields
# ---------------------------------------------------------------------------


def _load_form(
    identifier: str, base_path: str, kind: FormKind, *, include_fields: bool = True
) -> dict[str, Any]:
    return fetch_existing(
        kind,
        identifier,
        lambda: get_form(
            identifier, base_path=base_path, include_fields=include_fields
        ),
    )


def _find_form_field(
    form: dict[str, Any], field_api_name: str, kind: FormKind
) -> dict[str, Any]:
    match = next(
        (f for f in form["fields"] if f.get("api_name") == field_api_name), None
    )
    if match is None:
        available = [f.get("api_name") for f in form["fields"]]
        raise PlanError(
            f"field '{field_api_name}' not found on {kind} "
            f"'{form.get('name')}'. Available: {available}"
        )
    return match


def plan_create_form_fields(
    identifier: str,
    fields: list[dict[str, Any] | FormFieldDef],
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan creation of one or more fields on an existing form/survey."""
    if not fields:
        raise PlanError("no fields provided to create")
    _, field_kind, _ = _kinds(kind)
    env = load_env_config().name
    form = _load_form(identifier, base_path, kind)
    form_id = form["id"]

    have = {f.get("api_name") for f in form["fields"] if f.get("api_name")}
    base_order = len(form["fields"])
    ops: list[PlanOperation] = []
    seen: set[str] = set()
    for idx, field in enumerate(fields):
        fd = (
            field
            if isinstance(field, FormFieldDef)
            else FormFieldDef.model_validate(field)
        )
        label = fd.api_name or fd.name
        if fd.api_name and fd.api_name in have:
            raise PlanError(
                f"field '{fd.api_name}' already exists on {kind} "
                f"'{form.get('name')}'. Use plan_update_form_field."
            )
        if label in seen:
            raise PlanError(f"duplicate {kind} field '{label}' in the batch.")
        seen.add(label)
        ops.append(
            PlanOperation(
                action="create",
                kind=field_kind,
                key=f"{kind}:{form.get('api_name') or identifier}.field:{label}",
                preview={
                    "env": env,
                    kind: form.get("name"),
                    "field": label,
                    "field_type": fd.field_type,
                    "required": fd.required,
                },
                payload=_build_form_field_payload(fd, default_order=base_order + idx),
                parent_object_uuid=form_id,
            )
        )
    return Plan.build(
        env=env,
        summary=f"Create {len(ops)} field(s) on {kind} '{form.get('name')}'",
        operations=ops,
    )


def plan_update_form_field(
    identifier: str,
    field_api_name: str,
    changes: dict[str, Any],
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan an update to one field on a form/survey."""
    _, field_kind, _ = _kinds(kind)
    env = load_env_config().name
    form = _load_form(identifier, base_path, kind)
    existing = _find_form_field(form, field_api_name, kind)

    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    if "name" in changes and changes["name"] != existing.get("display_name"):
        payload["display_name"] = changes["name"]
        diff["display_name"] = (existing.get("display_name"), changes["name"])
    # Form and survey field reads carry no `description` (confirmed live
    # 2026-10-01, docs/specs/form.md), so there's nothing to diff: send it.
    if "description" in changes:
        payload["description"] = changes["description"]
        diff["description"] = (existing.get("description"), changes["description"])
    if "required" in changes and changes["required"] != existing.get("is_required"):
        payload["is_required"] = changes["required"]
        diff["is_required"] = (existing.get("is_required"), changes["required"])
    if "read_only" in changes and changes["read_only"] != existing.get("is_read_only"):
        payload["is_read_only"] = changes["read_only"]
        diff["is_read_only"] = (existing.get("is_read_only"), changes["read_only"])
    if "hidden" in changes and changes["hidden"] != existing.get("is_hidden"):
        payload["is_hidden"] = changes["hidden"]
        diff["is_hidden"] = (existing.get("is_hidden"), changes["hidden"])
    if "order" in changes and changes["order"] != existing.get("order"):
        payload["order"] = changes["order"]
        diff["order"] = (existing.get("order"), changes["order"])

    action = "update" if payload else "skip"
    op = PlanOperation(
        action=action,  # type: ignore[arg-type]
        kind=field_kind,
        key=f"{kind}:{form.get('api_name') or identifier}.field:{field_api_name}",
        preview={
            "env": env,
            kind: form.get("name"),
            "field": field_api_name,
            "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
        },
        payload=payload,
        existing_uuid=existing["id"],
        parent_object_uuid=form["id"],
    )
    summary = (
        f"Update field '{field_api_name}' on {kind} '{form.get('name')}'"
        if diff
        else f"No changes to field '{field_api_name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_form_field(
    identifier: str,
    field_api_name: str,
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan deletion of one field from a form/survey."""
    _, field_kind, _ = _kinds(kind)
    env = load_env_config().name
    form = _load_form(identifier, base_path, kind)
    existing = _find_form_field(form, field_api_name, kind)

    op = PlanOperation(
        action="delete",
        kind=field_kind,
        key=f"{kind}:{form.get('api_name') or identifier}.field:{field_api_name}",
        preview={
            "env": env,
            kind: form.get("name"),
            "field": field_api_name,
            "field_type": existing.get("field_type"),
        },
        existing_uuid=existing["id"],
        parent_object_uuid=form["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Delete field '{field_api_name}' from {kind} '{form.get('name')}'",
        operations=[op],
    )


def plan_add_form_field_options(
    identifier: str,
    field_api_name: str,
    options: list[str],
    *,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan adding options to a select-type field on a form/survey."""
    if not options:
        raise PlanError("no options provided to add")
    _, _, option_kind = _kinds(kind)
    env = load_env_config().name
    form = _load_form(identifier, base_path, kind)
    existing = _find_form_field(form, field_api_name, kind)
    if existing.get("field_type") not in _OPTION_FIELD_TYPES:
        raise PlanError(
            f"field '{field_api_name}' is type '{existing.get('field_type')}', "
            f"which has no options. Option fields are: {sorted(_OPTION_FIELD_TYPES)}"
        )

    have = {(o.get("name") or "").lower() for o in (existing.get("options") or [])}
    ops: list[PlanOperation] = []
    for name in options:
        already = name.lower() in have
        ops.append(
            PlanOperation(
                action="skip" if already else "create",
                kind=option_kind,
                key=f"{kind}:{form.get('api_name') or identifier}.field:{field_api_name}.option:{name}",
                preview={
                    "env": env,
                    "field": f"{form.get('name')}.{field_api_name}",
                    "option": name,
                    "note": "already exists" if already else "add",
                },
                payload={"field_id": existing["id"], "name": name},
                parent_object_uuid=form["id"],
            )
        )
    n_add = sum(1 for o in ops if o.action == "create")
    return Plan.build(
        env=env,
        summary=f"Add {n_add} option(s) to {form.get('name')}.{field_api_name}",
        operations=ops,
    )


def plan_remove_form_field_option(
    identifier: str,
    field_api_name: str,
    option: str,
    *,
    remap_to: str | None = None,
    base_path: str = FORMS_BASE_PATH,
    kind: FormKind = "form",
) -> Plan:
    """Plan removal of one option from a select-type field on a form/survey."""
    _, _, option_kind = _kinds(kind)
    env = load_env_config().name
    form = _load_form(identifier, base_path, kind)
    existing = _find_form_field(form, field_api_name, kind)
    opts = existing.get("options") or []

    def _match(token: str) -> dict[str, Any]:
        for o in opts:
            if token in (o.get("id"), o.get("name"), o.get("code")) or (
                (o.get("name") or "").lower() == token.lower()
            ):
                return o
        raise PlanError(
            f"option '{token}' not found on {form.get('name')}.{field_api_name}. "
            f"Available: {[o.get('name') for o in opts]}"
        )

    target = _match(option)
    payload: dict[str, Any] = {"field_id": existing["id"]}
    remap_note = "dropped (records lose this value)"
    if remap_to:
        replacement = _match(remap_to)
        payload["remap_to"] = replacement["id"]
        remap_note = f"records remapped to '{replacement.get('name')}'"

    op = PlanOperation(
        action="delete",
        kind=option_kind,
        key=f"{kind}:{form.get('api_name') or identifier}.field:{field_api_name}.option:{target.get('name')}",
        preview={
            "env": env,
            "field": f"{form.get('name')}.{field_api_name}",
            "option": target.get("name"),
            "on_delete": remap_note,
        },
        payload=payload,
        existing_uuid=target["id"],
        parent_object_uuid=form["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Remove option '{target.get('name')}' from {form.get('name')}.{field_api_name}",
        operations=[op],
    )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _resolve_related_object_id(form: FormDef) -> str | None:
    if form.related_object_id:
        return form.related_object_id
    if form.related_object:
        return get_object(form.related_object)["id"]
    return None


def _build_form_payload(form: FormDef) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": form.name, "template_type": form.template_type}
    related_object_id = _resolve_related_object_id(form)
    if related_object_id:
        payload["related_object_id"] = related_object_id
    optional = {
        "api_name": form.api_name,
        "description": form.description,
        "submission_action": form.submission_action,
        "redirect_url": form.redirect_url,
        "pass_variables_on_redirect": form.pass_variables_on_redirect,
        "challenge_token_required": form.challenge_token_required,
        "subscribers": form.subscribers,
        "business_merge_fields": form.business_merge_fields,
        "form_ui": form.form_ui,
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return payload


def _build_form_field_payload(
    field: FormFieldDef, *, default_order: int | None = None
) -> dict[str, Any]:
    # Unlike custom-object/activity fields, `wysiwyg` IS a valid field_type on
    # forms/surveys per the live FormFieldFieldTypeEnum — no longtext remap here.
    payload: dict[str, Any] = {
        "display_name": field.name,
        "field_type": field.field_type,
        "is_required": field.required,
        "is_read_only": field.read_only,
        "is_hidden": field.hidden,
    }
    if field.api_name:
        payload["name"] = field.api_name
    if field.description:
        payload["description"] = field.description
    order = field.order if field.order is not None else default_order
    if order is not None:
        payload["order"] = order

    if field.options is not None:
        payload["options"] = [{"name": o, "code": o} for o in field.options]
    if field.status_options is not None:
        payload["options"] = [
            {"name": s.name, "code": s.code or s.name.lower()}
            for s in field.status_options
        ]
    if field.money_options is not None:
        payload["money_options"] = field.money_options.model_dump(exclude_none=True)
    if field.rating is not None:
        payload["rating"] = field.rating.model_dump(exclude_none=True)
    if field.decimal_options is not None:
        payload["decimal_options"] = field.decimal_options.model_dump(exclude_none=True)
    if field.phone_options is not None:
        payload["phonenumber_options"] = field.phone_options.model_dump(
            exclude_none=True
        )
    return payload

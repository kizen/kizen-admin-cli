"""Plan creation for automation-scoped messages.

See :mod:`kizen_builder.api.messages` for why notify_member_via_email steps
need a real, template-backed message resource rather than inline content.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.models.spec.email_templates import EmailTemplateDef
from kizen_builder.tools import email_craft
from kizen_builder.tools.automations import get_automation
from kizen_builder.tools.messages import craft_summary, resolve_template
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation

# Only value ever observed live for a created template (see
# docs/specs/email-templates.md). No enum is declared anywhere in the repo
# (BCLI-015 left this field unwired since no create path existed until now),
# so this is hard-coded rather than exposed as an unguessable --sender-type
# flag.
_DEFAULT_SENDER_TYPE = "business"

# `create_automation_message_from_template` (api/messages.py) already sends
# this for the automation-message resource; `POST /api/messages/templates`
# turns out to require it too — confirmed live 2026-08-25 the hard way (a
# `400 {"from_name_type": ["This field is required."]}` from a create
# missing it, not something the earlier probe's PATCH-only testing surfaced).
_DEFAULT_FROM_NAME_TYPE = "default"

# Copied onto a clone; everything else is server-assigned (id, created,
# updated, is_editable) or a back-reference that must not be carried over.
_CLONED_FIELDS = (
    "type",
    "subject",
    "content",
    "craft_json",
    "sender_type",
    "from_name_type",
    "custom_from_name",
    "external_account",
    "sender_field",
    "sender_role",
    "sender_team_member",
)


def plan_create_automation_message(automation_api_name: str, template: str) -> Plan:
    """Plan creating an automation-scoped message from an email template.

    The result's UUID is what a notify_member_via_email step's
    `email_template_id` should reference — Kizen's builder UI "select
    email" picker only recognizes a message seeded from a real template
    (via `base_message_id`) as selected.
    """
    config = load_env_config()
    try:
        automation = get_automation(automation_api_name)
    except LookupError as e:
        raise PlanError(str(e)) from e
    with KizenClient(config) as client:
        try:
            tmpl = resolve_template(client, template)
        except (LookupError, ValueError) as e:
            raise PlanError(str(e)) from e

    payload = {"automation_id": automation["id"], "template": tmpl}
    op = PlanOperation(
        action="create",
        kind="automation_message",
        key=f"{automation_api_name}:{tmpl.get('name')}",
        preview={
            "env": config.name,
            "automation": automation_api_name,
            "template": tmpl.get("name"),
            "template_id": tmpl.get("id"),
            "subject": tmpl.get("subject"),
        },
        payload=payload,
    )
    return Plan.build(
        env=config.name,
        summary=(
            f"Create automation message on '{automation_api_name}' "
            f"from template '{tmpl.get('name')}'"
        ),
        operations=[op],
    )


def _resolve(name_or_id: str) -> tuple[Any, dict[str, Any]]:
    config = load_env_config()
    with KizenClient(config) as client:
        try:
            return config, resolve_template(client, name_or_id)
        except (LookupError, ValueError) as e:
            raise PlanError(str(e)) from e


def plan_clone_template(source: str, new_name: str) -> Plan:
    """Plan copying an email template, content fields and all.

    Both content fields are carried over verbatim so the copy stays
    internally consistent — `craft_json` and `content` are coupled by node
    id, so copying one without the other produces a template whose builder
    view and real output disagree.
    """
    config, src = _resolve(source)
    payload: dict[str, Any] = {"name": new_name}
    payload.update({f: src.get(f) for f in _CLONED_FIELDS if src.get(f) is not None})

    op = PlanOperation(
        action="create",
        kind="email_template",
        key=new_name,
        preview={
            "env": config.name,
            "source": src.get("name"),
            "source_id": src.get("id"),
            "new_name": new_name,
            "subject": src.get("subject"),
            **craft_summary(src),
        },
        payload=payload,
    )
    return Plan.build(
        env=config.name,
        summary=f"Clone email template '{src.get('name')}' to '{new_name}'",
        operations=[op],
    )


def plan_create_template_from_spec(
    spec: EmailTemplateDef, resolved_sections: list[dict[str, Any]]
) -> Plan:
    """Plan creating an email template whose ``craft_json``/``content`` are
    built from a spec, not hand-authored.

    ``resolved_sections`` comes from ``tools.email_craft.resolve_spec_images``
    — image blocks are already uploaded by the time this runs (a real write,
    but not one this function performs; see that function's docstring for
    why it can't happen here — planners never write, ``CLAUDE.md``). Building
    the tree and compiling the HTML both happen inside
    ``email_craft.build_email_content()``, in one pass over one set of ids,
    so the two fields cannot go out of sync.
    """
    config = load_env_config()
    try:
        sections = email_craft.assemble_sections(resolved_sections)
        craft_json, content = email_craft.build_email_content(sections)
    except ValueError as e:
        raise PlanError(str(e)) from e

    payload: dict[str, Any] = {
        "name": spec.name,
        "subject": spec.subject,
        "type": "email",
        "sender_type": _DEFAULT_SENDER_TYPE,
        "from_name_type": _DEFAULT_FROM_NAME_TYPE,
        "craft_json": craft_json,
        "content": content,
    }
    op = PlanOperation(
        action="create",
        kind="email_template",
        key=spec.name,
        preview={
            "env": config.name,
            "name": spec.name,
            "subject": spec.subject,
            "sections": len(spec.sections),
            "craft_json": f"{len(craft_json)} nodes",
            "content": f"{len(content)} chars",
        },
        payload=payload,
    )
    return Plan.build(
        env=config.name,
        summary=f"Create email template '{spec.name}' from spec",
        operations=[op],
    )


def plan_update_template(
    template: str,
    patch: dict[str, Any] | None = None,
    *,
    spec: EmailTemplateDef | None = None,
    resolved_sections: list[dict[str, Any]] | None = None,
) -> Plan:
    """Plan a PATCH of one email template's fields.

    Two mutually exclusive input modes, matching the CLI's two update paths:

    - ``patch`` — the raw field-level PATCH (``--craft-json-file``/
      ``--content-file``/``--name``/``--subject``), applied verbatim
      including explicit ``None`` values (clearing ``content`` is the way to
      ask whether the server recompiles it from ``craft_json`` — it doesn't).
    - ``spec``/``resolved_sections`` — rebuilds both content fields from a
      spec file the same way ``create`` does (``--spec-file``); overrides
      ``name``/``subject`` too if the spec sets them.
    """
    if spec is not None:
        try:
            sections = email_craft.assemble_sections(resolved_sections or [])
            craft_json, content = email_craft.build_email_content(sections)
        except ValueError as e:
            raise PlanError(str(e)) from e
        patch = {
            "name": spec.name,
            "subject": spec.subject,
            "craft_json": craft_json,
            "content": content,
        }

    config, tmpl = _resolve(template)
    if not patch:
        raise PlanError("nothing to update — pass at least one field")

    changing = {
        k: ("<null>" if v is None else f"{len(v)} chars" if isinstance(v, str) else v)
        for k, v in patch.items()
        if k not in ("craft_json",)
    }
    if "craft_json" in patch:
        cj = patch["craft_json"]
        changing["craft_json"] = "<null>" if cj is None else f"{len(cj)} nodes"

    op = PlanOperation(
        action="update",
        kind="email_template",
        key=tmpl.get("name") or template,
        existing_uuid=tmpl["id"],
        preview={
            "env": config.name,
            "template": tmpl.get("name"),
            "id": tmpl.get("id"),
            "fields": changing,
            "before": craft_summary(tmpl),
        },
        payload=patch,
    )
    return Plan.build(
        env=config.name,
        summary=f"Update email template '{tmpl.get('name')}' ({', '.join(patch)})",
        operations=[op],
    )


def plan_delete_template(template: str) -> Plan:
    """Plan deleting an email template."""
    config, tmpl = _resolve(template)
    op = PlanOperation(
        action="delete",
        kind="email_template",
        key=tmpl.get("name") or template,
        existing_uuid=tmpl["id"],
        preview={
            "env": config.name,
            "template": tmpl.get("name"),
            "id": tmpl.get("id"),
            "subject": tmpl.get("subject"),
        },
    )
    return Plan.build(
        env=config.name,
        summary=f"Delete email template '{tmpl.get('name')}'",
        operations=[op],
    )

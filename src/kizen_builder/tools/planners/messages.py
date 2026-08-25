"""Plan creation for automation-scoped messages.

See :mod:`kizen_builder.api.messages` for why notify_member_via_email steps
need a real, template-backed message resource rather than inline content.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.automations import get_automation
from kizen_builder.tools.messages import craft_summary, resolve_template
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation

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


def plan_update_template(template: str, patch: dict[str, Any]) -> Plan:
    """Plan a PATCH of one email template's fields.

    ``patch`` is applied verbatim, including explicit ``None`` values —
    that is deliberate, since clearing ``content`` is the way to ask
    whether the server recompiles it from ``craft_json``.
    """
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

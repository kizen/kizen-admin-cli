"""Automation-scoped message resources.

Backs the notify_member_via_email / notify_member_via_text automation
steps: their content lives on a separate ``AutomationMessage`` resource
(``/api/messages/automations/...``), referenced from the step's config
block rather than stored inline.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from kizen_builder.api.client import KizenClient


def set_automation_message_step(
    client: KizenClient, message_id: str, step_id: str
) -> None:
    """PATCH /api/messages/automations/{message_id} to stamp its automation_step.

    A PUT that creates or clones a notify_member_via_email/text step's
    message resource does not set this back-reference on its own — Kizen's
    automation builder UI matches the step's "select email/text" picker by
    ``automation_step``, so without this the picker shows unset even though
    the step already has content wired up.
    """
    client.patch(
        f"/api/messages/automations/{message_id}", json={"automation_step": step_id}
    )


def list_templates(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/messages/templates, paginated."""
    results: list[dict[str, Any]] = []
    path: str | None = "/api/messages/templates"
    params: dict[str, Any] | None = {"page_size": 100}
    while path:
        data = client.get(path, params=params)
        if isinstance(data, list):
            results.extend(data)
            break
        results.extend(data.get("results", []))
        nxt = data.get("next")
        if not nxt:
            break
        parts = urlsplit(nxt)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        params = None
    return results


def get_template(client: KizenClient, template_id: str) -> dict[str, Any]:
    """GET /api/messages/templates/{id}."""
    return client.get(f"/api/messages/templates/{template_id}")


def create_template(client: KizenClient, body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/messages/templates."""
    return client.post("/api/messages/templates", json=body)


def update_template(
    client: KizenClient, template_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/messages/templates/{id}.

    ``craft_json`` (the editable tree) and ``content`` (the compiled,
    Outlook-safe HTML that is actually sent) are independent stored fields
    coupled by node id — see `kizen docs show email-templates`. Sending one
    without the other is what leaves a template's builder view and its real
    output out of sync.
    """
    return client.patch(f"/api/messages/templates/{template_id}", json=body)


def delete_template(client: KizenClient, template_id: str) -> Any:
    """DELETE /api/messages/templates/{id}."""
    return client.delete(f"/api/messages/templates/{template_id}")


def create_automation_message_from_template(
    client: KizenClient, automation_id: str, template: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/messages/automations/automation/{automation_id}, seeded from
    an email template via ``base_message_id``.

    This is the linkage Kizen's builder UI "select email" picker actually
    renders from — an AutomationMessage created without a real template
    behind it (no ``base_message_id``) shows as unselected in the picker
    even though a step technically references it (confirmed live: a message
    created from raw content alone didn't show as linked, one created via
    ``base_message_id`` from a real template did).
    """
    body: dict[str, Any] = {
        "name": template.get("name") or "Untitled",
        "type": template.get("type") or "email",
        "subject": template.get("subject") or "",
        "content": template.get("content") or "",
        "base_message_id": template["id"],
        "from_name_type": "default",
        "sender_type": "last_team_member",
    }
    if template.get("craft_json") is not None:
        body["craft_json"] = template["craft_json"]
    if template.get("html_content"):
        body["html_content"] = template["html_content"]
    return client.post(
        f"/api/messages/automations/automation/{automation_id}", json=body
    )

"""Tools for automation-scoped messages (notify_member_via_email step content).

See :mod:`kizen_builder.api.messages` for why this is a separate resource
from the step itself, and why it must be seeded from a real template.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from kizen_builder.api import messages as messages_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.automations import get_automation
from kizen_builder.utils import is_uuid

# A compiled-HTML section class, emitted once per Section/Row craft node.
_SECTION_CLASS = re.compile(r"section-([0-9a-f]{6,})")


def list_templates() -> list[dict[str, Any]]:
    """List email templates available to base a notify_member_via_email
    step's message on (see `kizen automations messages create`)."""
    config = load_env_config()
    with KizenClient(config) as client:
        return messages_api.list_templates(client)


def resolve_template(client: KizenClient, name_or_id: str) -> dict[str, Any]:
    """Resolve a template name or UUID to its full detail record.

    The list endpoint's items omit ``content``/``craft_json`` (confirmed
    live: a message created from a by-name match 400'd on a blank
    ``content``) — always follow up with a detail GET.
    """
    if is_uuid(name_or_id):
        return messages_api.get_template(client, name_or_id)
    templates = messages_api.list_templates(client)
    matches = [
        t for t in templates if (t.get("name") or "").lower() == name_or_id.lower()
    ]
    if not matches:
        available = [t.get("name") for t in templates]
        raise LookupError(
            f"no email template named '{name_or_id}'. Available: {available}"
        )
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise ValueError(f"{len(matches)} templates named '{name_or_id}': {ids}")
    return messages_api.get_template(client, matches[0]["id"])


def create_automation_message(
    automation_api_name: str, template: str
) -> dict[str, Any]:
    """Create an automation-scoped message from an email template, ready to
    reference from a notify_member_via_email step's `email_template_id`.

    ``template`` is a template name or UUID (see :func:`list_templates`).
    Kizen's builder UI "select email" picker only recognizes messages
    created this way (seeded from a real template via ``base_message_id``) —
    a message authored from raw content alone shows as unselected even
    though a step technically references it.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        automation_id = get_automation(automation_api_name)["id"]
        tmpl = resolve_template(client, template)
        created = messages_api.create_automation_message_from_template(
            client, automation_id, tmpl
        )
    return {"env": config.name, "automation_api_name": automation_api_name, **created}


def get_template_detail(name_or_id: str) -> dict[str, Any]:
    """One email template's full record, including ``craft_json`` and
    ``content`` (the list endpoint omits both)."""
    config = load_env_config()
    with KizenClient(config) as client:
        return resolve_template(client, name_or_id)


def _resolved_name(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if isinstance(node_type, dict):
        return str(node_type.get("resolvedName") or "?")
    return str(node_type or "?")


def _plain_text(html: str) -> str:
    """Visible text of an HTML fragment, whitespace-collapsed.

    Applied to *both* sides of a drift comparison. The compiled ``content``
    embeds each Text node's markup verbatim rather than its stripped text,
    so comparing stripped craft text against raw content HTML reports
    false drift on any node with inline markup (a styled span, a merge
    field) — confirmed live 2026-08-25.
    """
    stripped = re.sub(r"<[^>]+>", " ", html)
    return " ".join(unescape(stripped).split())


def _text_chunks(html: str) -> list[str]:
    """A Text node's paragraphs, as plain text.

    Compared per-paragraph rather than whole-node: a multi-paragraph node
    flattens to one string that no longer appears contiguously in the
    compiled HTML, since the renderer puts markup between the paragraphs.
    """
    parts = re.split(r"</p>|<br\s*/?>", html)
    return [c for c in (_plain_text(p) for p in parts) if c]


def craft_summary(template: dict[str, Any]) -> dict[str, Any]:
    """Summarize a template's two content fields and how well they agree.

    ``craft_json`` (what the builder edits) and ``content`` (the compiled
    HTML that is actually sent) are stored independently. The server does
    **not** derive one from the other — confirmed live 2026-08-25 by
    PATCHing a modified ``craft_json`` alone and observing ``content`` come
    back byte-identical. So the two can disagree, which is invisible in the
    builder and only shows up in what recipients receive.

    Two independent checks, because they catch different failures:

    - ``structure_coupled`` — every ``Section``/``Row`` node has its
      matching ``section-<nodeId>`` class in the HTML and vice versa. Catches
      added, removed or re-parented containers.
    - ``text_in_sync`` — every ``Text`` node's visible text appears in the
      HTML. Catches edited copy, which leaves the structure untouched and so
      passes the id check entirely.
    """
    craft = template.get("craft_json") or {}
    content = template.get("content") or ""
    flat_content = _plain_text(content)

    node_types: dict[str, int] = {}
    container_ids: set[str] = set()
    missing_text: list[str] = []
    for node_id, node in craft.items():
        name = _resolved_name(node)
        node_types[name] = node_types.get(name, 0) + 1
        if name in ("Section", "Row"):
            container_ids.add(node_id)
        elif name == "Text":
            chunks = _text_chunks(str((node.get("custom") or {}).get("text") or ""))
            if any(c not in flat_content for c in chunks):
                missing_text.append(node_id)

    classes = set(_SECTION_CLASS.findall(content))
    orphan_classes = sorted(classes - container_ids)
    orphan_nodes = sorted(container_ids - classes)
    structure_coupled = (
        bool(craft) and bool(content) and not orphan_classes and not orphan_nodes
    )
    return {
        "node_count": len(craft),
        "node_types": dict(sorted(node_types.items())),
        "content_bytes": len(content),
        "section_classes": len(classes),
        "classes_without_node": orphan_classes,
        "containers_without_class": orphan_nodes,
        "text_nodes_missing_from_content": missing_text,
        "structure_coupled": structure_coupled,
        "text_in_sync": bool(craft) and bool(content) and not missing_text,
        "coupled": structure_coupled and not missing_text,
    }

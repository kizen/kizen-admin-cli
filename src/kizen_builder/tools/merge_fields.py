"""Merge-field markup: ``{{ ns.field }}`` -> the ``<span class="kzn-merge-field">``
wrapper Kizen's builder UI writes and resolves at send time.

A bare ``{{ ns.field }}`` token is *not* the wire/authoring format — it is
inert text. Every real merge field the Kizen UI has ever produced (automation
notify steps, `call_llm`/`file_content_extraction` prompts, dashboard static
text, email templates) wraps the token in this span, confirmed live
2026-08-26 from two `cli-testing` captures (an email template and an
automation)::

    <span class="kzn-merge-field"
          data-merge-field-fallback-label="Stage"
          data-merge-field-relationship="object_with_workflow.stage"
          data-merge-field-objectname="object with workflow">{{ object_with_workflow.stage }}</span>

Both captures agreed on every rule below. `data-merge-field-objectname` is
present **only** when the namespace is a real custom object's api_name (its
value is that object's *display* name, not the api_name) — never for the
fixed reserved namespaces. Attribute order is always fallback-label,
relationship, objectname.

This module owns token parsing, span rendering, and namespace
classification, but it does **not** know how to look up live field or object
display names — callers hand that in as small resolver callables, so this
module has no dependency on any particular caller's types (in particular,
no dependency on automation types — an email-template emitter can drive it
too). A resolver that can't answer for a given token returns ``None``; the
module then applies its own best-effort fallback rather than leaving the
token unrendered.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

# One or more dot-separated segments after the namespace. The earlier
# two-segment-only pattern silently failed to match relationship-hop tokens
# like `custom_objects.primary_document_record.id` (real example:
# tests/fixtures/automations/activity_logged_schedule_activity.raw.json) —
# `finditer` skipped them entirely, so the literal `{{ ... }}` text fell
# through to `html.escape` and rendered as visible braces in the recipient's
# message. Never narrow this back to exactly one field segment.
MERGE_FIELD_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\s*\}\}")

# The fixed set of namespace tokens that are *not* a custom object's
# api_name — confirmed live 2026-08-26 (`business`, `team_member`, `contact`,
# `automation_variable`) plus `entity_record`/`custom_objects`, the two
# pseudo-tokens `tools/planners/automations.py` already used for "the
# triggering record" and "the automation's own target_object", and
# `automation_history`, whose labels vary by containing automation
# (`merge-field-markup-captured-live.md`) — it is reserved because it has no
# queryable live metadata source, not because its labels are stable.
#
# This must be an explicit allowlist, not "did a live object lookup for this
# token succeed" — `contact` is itself a real, live-queryable object api_name
# but is reserved and carries no `objectname` per the live capture; lookup
# success would misclassify it as a custom object.
RESERVED_NAMESPACES = frozenset(
    {
        "entity_record",
        "custom_objects",
        "team_member",
        "business",
        "contact",
        "automation_variable",
        "automation_history",
    }
)

# Labels Kizen stores for reserved-namespace fields that are not reachable by
# any casing transform of the field's api_name. `Zip/Postal Code` cannot be
# derived from `postal_code`, and `Business Primary Name` cannot be derived
# from `primary_marketing_contact_name` by any prefix rule either. A static
# table is only defensible for namespaces with no queryable live metadata at
# all — `business`, `team_member`, `automation_variable` (handled separately
# above). It is NOT valid for `automation_history`: the live capture shows
# the same token (`automation_history.execution_id`,
# `automation_history.automation_id`) carrying different stored labels in
# different automations (`"Automation Execution ID"` in three committed
# fixtures vs. `"Agentic Workflow Execution ID"` in `kitchen_sink_triggers`
# and in live capture), so no single value belongs here — see
# merge-field-markup-captured-live.md's "label is NOT a function of the
# token" finding.
#
# For `business`/`team_member` this table IS complete, not a sample: every
# entry in the email builder's Business and Team Member merge-field pickers
# was inserted into a template and read back on 2026-08-26, and the counts
# match the pickers exactly (12 and 5). These labels cannot be derived or
# fetched — `GET /api/docs/schema`'s `Business` component lists 40 properties
# (not these 12, and missing `country_code`/`state_code` entirely), and no
# merge-field catalog endpoint exists (0 of 564 schema paths match "merge").
# The picker is curated. Adding to this table means capturing from the UI.
_KNOWN_LABELS: dict[tuple[str, str], str] = {
    # Complete Business picker, confirmed live 2026-08-26
    # (merge-field-markup-captured-live.md). Six of these are unreachable by
    # any `"Business " + title(api_name)` rule, which is why the whole list is
    # pinned rather than a prefix rule with exceptions.
    ("business", "name"): "Business Name",
    ("business", "reply_to_email"): "Business Notification Email",
    ("business", "phone"): "Business Phone",
    ("business", "street_address"): "Business Street Address",
    ("business", "country"): "Business Country",
    ("business", "country_code"): "Business Country Code",
    ("business", "state"): "Business State/Province",
    ("business", "state_code"): "Business State/Province Code",
    ("business", "city"): "Business City",
    ("business", "postal_code"): "Business Zip/Postal Code",
    ("business", "primary_marketing_contact_name"): "Business Primary Name",
    ("business", "primary_marketing_contact_email"): "Business Primary Email",
    # Complete Team Member picker, same capture.
    ("team_member", "first_name"): "Team Member First Name",
    ("team_member", "last_name"): "Team Member Last Name",
    ("team_member", "email"): "Team Member Email",
    ("team_member", "phone"): "Team Member Phone",
    ("team_member", "signature"): "Team Member Signature",
}

# Namespaces whose picker list is fully captured in `_KNOWN_LABELS` above.
# A token in one of these that is NOT in the table was hand-authored rather
# than picked, so it still gets the namespace prefix — `Business Timezone`
# beats a bare `Timezone`, which would read as a field of the wrong object.
_LABEL_PREFIXES = {"business": "Business", "team_member": "Team Member"}

ResolveLabel = Callable[[str, str], "str | None"]
ResolveObjectName = Callable[[str], "str | None"]


def _fallback_label(namespace: str, field_path: str) -> str:
    """The label to use when no resolver answers for this token.

    Three tiers, in order:

    1. `automation_variable.<name>` is a confirmed special case: Kizen does
       not title-case these at all — the label is the literal variable name
       as authored (`tests/fixtures/automations/llm_comparison.raw.json`,
       `on_or_around_date_goto.raw.json`).
    2. An exact captured label from :data:`_KNOWN_LABELS`, which holds the
       complete `business`/`team_member` picker lists.
    3. A title-cased reading of the field path, prefixed with the namespace's
       display name for namespaces in :data:`_LABEL_PREFIXES`. This tier is a
       readable guess, **not** a verified value — it is only reached by a
       hand-authored token outside the picker lists, or by an
       `entity_record` pseudo-field the live resolver could not answer.
    """
    if namespace == "automation_variable":
        return field_path
    known = _KNOWN_LABELS.get((namespace, field_path))
    if known is not None:
        return known
    words = field_path.replace("_", " ").replace(".", " ").title()
    prefix = _LABEL_PREFIXES.get(namespace)
    return f"{prefix} {words}" if prefix else words


def render(
    text: str,
    *,
    resolve_label: ResolveLabel | None = None,
    resolve_objectname: ResolveObjectName | None = None,
) -> str:
    """Render ``{{ ns.field }}``/``{{ ns.rel.field }}`` tokens in ``text``
    into ``<span class="kzn-merge-field">`` markup; everything else is
    HTML-escaped around them.

    ``resolve_label(namespace, field_path)`` and ``resolve_objectname(namespace)``
    are small caller-supplied lookups (typically backed by live field/object
    metadata) — this module has no fetching of its own. Either may return
    ``None`` to say "I don't know," in which case this module's own
    best-effort fallback applies. `resolve_objectname` is only ever called
    for a namespace outside :data:`RESERVED_NAMESPACES` — the reserved set is
    decided here, not by whether the resolver happens to succeed.
    """
    out: list[str] = []
    last = 0
    for m in MERGE_FIELD_RE.finditer(text):
        out.append(html.escape(text[last : m.start()]))
        namespace, _, field_path = m.group(1).partition(".")

        label = resolve_label(namespace, field_path) if resolve_label else None
        if label is None:
            label = _fallback_label(namespace, field_path)

        attrs = [
            f'data-merge-field-fallback-label="{html.escape(label)}"',
            f'data-merge-field-relationship="{namespace}.{field_path}"',
        ]
        if namespace not in RESERVED_NAMESPACES and resolve_objectname is not None:
            objectname = resolve_objectname(namespace)
            if objectname:
                attrs.append(f'data-merge-field-objectname="{html.escape(objectname)}"')

        out.append(
            f'<span class="kzn-merge-field" {" ".join(attrs)}>'
            f"{html.escape(m.group(0))}</span>"
        )
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)

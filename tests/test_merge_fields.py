"""Unit tests for the shared merge-field module in isolation from any
caller (automations, or eventually email templates) — the module takes
only resolved labels/small resolver callables, never AutomationDef/
LiveContext, so it can be exercised standalone.
"""

from __future__ import annotations

import pytest

from kizen_builder.tools.merge_fields import MERGE_FIELD_RE, RESERVED_NAMESPACES, render


def test_regex_matches_single_segment_token():
    assert MERGE_FIELD_RE.search("{{ entity_record.owner }}")


def test_regex_matches_multi_segment_relationship_hop_token():
    """The pre-existing regex in tools/planners/automations.py matched
    exactly one dot and could not match this real captured token at all —
    tests/fixtures/automations/activity_logged_schedule_activity.raw.json:246
    has `{{ custom_objects.primary_document_record.id }}` in the wild, and
    the old regex silently skipped it, letting literal braces reach a
    recipient's message. This must match, with the namespace as group 1
    up to (but not including) the first dot."""
    m = MERGE_FIELD_RE.search("{{ custom_objects.primary_document_record.id }}")
    assert m is not None
    assert m.group(1) == "custom_objects.primary_document_record.id"


def test_regex_does_not_match_a_bare_namespace_with_no_field():
    assert MERGE_FIELD_RE.search("{{ entity_record }}") is None


def test_render_escapes_surrounding_text_and_wraps_token_in_span():
    out = render("Hi {{ entity_record.owner }}, please review.")
    assert out == (
        'Hi <span class="kzn-merge-field" '
        'data-merge-field-fallback-label="Owner" '
        'data-merge-field-relationship="entity_record.owner">'
        "{{ entity_record.owner }}</span>, please review."
    )


def test_render_escapes_html_special_characters_outside_tokens():
    out = render("A <b> & {{ entity_record.owner }}")
    assert out.startswith("A &lt;b&gt; &amp; ")


def test_render_falls_back_to_title_case_when_no_resolver_given():
    out = render("{{ entity_record.link_url }}")
    assert 'data-merge-field-fallback-label="Link Url"' in out


def test_render_uses_resolve_label_when_it_answers():
    out = render(
        "{{ entity_record.owner }}",
        resolve_label=lambda ns, fp: "Custom Label",
    )
    assert 'data-merge-field-fallback-label="Custom Label"' in out


def test_render_falls_back_when_resolve_label_returns_none():
    out = render(
        "{{ entity_record.owner }}",
        resolve_label=lambda ns, fp: None,
    )
    assert 'data-merge-field-fallback-label="Owner"' in out


def test_render_automation_variable_label_is_literal_not_title_cased():
    """Confirmed from committed fixtures (not re-verified live for this
    module directly): automation_variable.<name> tokens keep the variable
    name as authored — no casing transform. See
    tests/fixtures/automations/llm_comparison.raw.json and
    on_or_around_date_goto.raw.json."""
    out = render("{{ automation_variable.llm_extract_output }}")
    assert 'data-merge-field-fallback-label="llm_extract_output"' in out
    assert "data-merge-field-objectname" not in out


def test_render_reserved_namespace_never_gets_objectname_even_if_resolver_would_answer():
    """Namespace classification must be an explicit allowlist, not "did a
    live lookup succeed" — `contact` is itself a real, queryable object
    api_name but is reserved and carries no `objectname` (confirmed from a
    live capture). A resolver that would happily answer for "contact" must
    still never be called/used for it."""
    assert "contact" in RESERVED_NAMESPACES
    out = render(
        "{{ contact.first_name }}",
        resolve_objectname=lambda ns: "Contact",  # would "succeed" if called
    )
    assert "data-merge-field-objectname" not in out


def test_render_non_reserved_namespace_gets_objectname_from_resolver():
    out = render(
        "{{ county.name }}",
        resolve_objectname=lambda ns: "County" if ns == "county" else None,
    )
    assert 'data-merge-field-objectname="County"' in out


def test_render_non_reserved_namespace_omits_objectname_when_resolver_cannot_answer():
    out = render("{{ county.name }}", resolve_objectname=lambda ns: None)
    assert "data-merge-field-objectname" not in out


def test_render_attribute_order_is_label_then_relationship_then_objectname():
    """Confirmed live 2026-08-26: fallback-label, relationship, objectname,
    in that order, for a real custom-object namespace."""
    out = render(
        "{{ object_with_workflow.stage }}",
        resolve_label=lambda ns, fp: "Stage",
        resolve_objectname=lambda ns: "object with workflow",
    )
    assert out == (
        '<span class="kzn-merge-field" '
        'data-merge-field-fallback-label="Stage" '
        'data-merge-field-relationship="object_with_workflow.stage" '
        'data-merge-field-objectname="object with workflow">'
        "{{ object_with_workflow.stage }}</span>"
    )


def test_render_never_calls_resolve_objectname_for_reserved_namespace():
    """Belt-and-suspenders on the allowlist rule: the resolver isn't even
    invoked for a reserved namespace, not just that its answer is ignored."""
    calls: list[str] = []
    render(
        "{{ team_member.first_name }}",
        resolve_objectname=lambda ns: calls.append(ns) or "should not happen",
    )
    assert calls == []


@pytest.mark.parametrize(
    ("token", "label"),
    [
        ("business.name", "Business Name"),
        ("business.reply_to_email", "Business Notification Email"),
        ("business.phone", "Business Phone"),
        ("business.street_address", "Business Street Address"),
        ("business.country", "Business Country"),
        ("business.country_code", "Business Country Code"),
        ("business.state", "Business State/Province"),
        ("business.state_code", "Business State/Province Code"),
        ("business.city", "Business City"),
        ("business.postal_code", "Business Zip/Postal Code"),
        ("business.primary_marketing_contact_name", "Business Primary Name"),
        ("business.primary_marketing_contact_email", "Business Primary Email"),
        ("team_member.first_name", "Team Member First Name"),
        ("team_member.last_name", "Team Member Last Name"),
        ("team_member.email", "Team Member Email"),
        ("team_member.phone", "Team Member Phone"),
        ("team_member.signature", "Team Member Signature"),
    ],
)
def test_render_known_label_for_business_and_team_member(token, label):
    """The complete Business (12) and Team Member (5) merge-field picker
    lists, confirmed live 2026-08-26 — every entry was inserted from the email
    builder's picker and read back, and the counts match the picker exactly.
    See merge-field-markup-captured-live.md, "COMPLETE business / team_member
    picker list".

    Six of the twelve Business labels are NOT reachable by a
    `"Business " + title(api_name)` rule — `postal_code` → "Zip/Postal Code",
    `state` → "State/Province", `state_code` → "State/Province Code",
    `reply_to_email` → "Notification Email",
    `primary_marketing_contact_name` → "Primary Name",
    `primary_marketing_contact_email` → "Primary Email". The other six and all
    five Team Member entries would work under such a rule; the whole list is
    pinned anyway because it is finite, captured, and exact.
    """
    out = render("{{ " + token + " }}")
    assert f'data-merge-field-fallback-label="{label}"' in out


@pytest.mark.parametrize(
    ("token", "label"),
    [
        ("business.timezone", "Business Timezone"),
        ("team_member.nickname", "Team Member Nickname"),
    ],
)
def test_render_prefixes_the_namespace_for_tokens_outside_the_picker_list(token, label):
    """A hand-authored token in a reserved namespace still gets the namespace
    prefix rather than a bare title-cased field name.

    Not verified against Kizen — these tokens are not in either picker, so no
    captured label exists for them and none could. This pins a deliberate
    fallback choice: "Business Timezone" reads correctly where a bare
    "Timezone" would look like a field of whatever object the surrounding
    content is about.
    """
    out = render("{{ " + token + " }}")
    assert f'data-merge-field-fallback-label="{label}"' in out


def test_render_resolve_label_takes_precedence_over_known_label_table():
    """Live field metadata must win over the static table when a caller can
    supply it — the table is a fallback for namespaces with no live
    metadata source, not an override. `team_member.first_name` has both a
    table entry ("Team Member First Name") and, here, a resolver answer
    ("Resolved Live Label"); the resolver's answer must be what renders."""
    out = render(
        "{{ team_member.first_name }}",
        resolve_label=lambda ns, fp: "Resolved Live Label",
    )
    assert 'data-merge-field-fallback-label="Resolved Live Label"' in out
    assert "Team Member First Name" not in out

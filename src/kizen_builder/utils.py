"""Small shared helpers."""

from __future__ import annotations

import re
import unicodedata
import uuid

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")
_SLUG_EDGES = re.compile(r"^_+|_+$")


def slugify(text: str) -> str:
    """Convert a display name to a spec-style api_name.

    Used by the importer when Kizen doesn't surface an api_name for an entity
    (categories, today). Lowercase, letters/digits/underscore only, starting
    with a letter.

    Example: "Contact Info" -> "contact_info", "FHIR R4 Resource" -> "fhir_r4_resource".
    If the result would start with a digit, it is prefixed with ``x_``. The
    spec validator no longer requires that, but the prefix is kept so a
    category imported earlier still matches on re-import.
    """
    lowered = text.strip().lower()
    slug = _SLUG_INVALID.sub("_", lowered)
    slug = _SLUG_EDGES.sub("", slug)
    if not slug:
        return "unnamed"
    if slug[0].isdigit():
        slug = f"x_{slug}"
    return slug


def derived_object_api_name(object_name: str) -> str:
    """Predict the api_name Kizen derives from a new object's display name.

    "Señor's  Policy & Claims" -> "senors_policy_claims" (confirmed live
    2026-10-14). A slug a live object already holds gets a random suffix
    instead, which no prediction can know.
    """
    folded = unicodedata.normalize("NFKD", object_name).encode("ascii", "ignore")
    lowered = folded.decode().lower().replace("'", "")
    return _SLUG_EDGES.sub("", _SLUG_INVALID.sub("_", lowered))


def is_uuid(value: str) -> bool:
    """True if ``value`` parses as a UUID.

    Used to tell bare server UUIDs apart from api_name / "object.field"
    references in automation specs.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True

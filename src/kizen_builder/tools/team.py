"""Team member read tools."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import team as team_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.utils import is_uuid


def search_team(name: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search team members by name or email, returning up to ``limit`` results."""
    config = load_env_config()
    with KizenClient(config) as client:
        raw = team_api.search_team(client, name, limit=limit)
    return [
        {
            "id": m.get("id"),
            "full_name": m.get("full_name")
            or f"{m.get('first_name', '')} {m.get('last_name', '')}".strip(),
            "email": m.get("email"),
            "title": m.get("title"),
        }
        for m in raw
    ]


def team_member_candidates(
    client: KizenClient, ref: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Search team members for ``ref``, narrowed to a case-insensitive exact
    name/email match — or the raw search results if nothing matches exactly.

    Shared by ``get_team_member`` and
    ``smart_connectors.webhooks.resolve_team_member``; each owns its own
    zero/multiple-match error handling since one is a CLI lookup and the
    other feeds plan authoring.
    """
    matches = team_api.search_team(client, ref, limit=limit)
    token = ref.lower()
    exact = [
        m
        for m in matches
        if token in {(m.get("email") or "").lower(), (m.get("full_name") or "").lower()}
    ]
    return exact or matches


def get_team_member(ref: str) -> dict[str, Any]:
    """Resolve a team member by id, name, or email and return their roles.

    ``GET /api/team/{id}`` only returns bare role UUIDs (see
    ``api.team.get_team_member``), so this cross-references them against
    ``GET /api/role`` to attach names — the same shape ``roles get``'s
    ``describe_role`` uses for permission groups.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        if is_uuid(ref):
            member_id = ref
        else:
            candidates = team_member_candidates(client, ref, limit=5)
            if not candidates:
                raise LookupError(f"team member '{ref}' not found.")
            if len(candidates) > 1:
                names = [m.get("full_name") or m.get("email") for m in candidates]
                raise LookupError(
                    f"team member '{ref}' is ambiguous ({len(candidates)} matches: "
                    f"{names}). Use the id."
                )
            member_id = candidates[0]["id"]
        try:
            detail = team_api.get_team_member(client, member_id)
        except KizenAPIError as exc:
            if exc.status_code == 404:
                raise LookupError(
                    f"team member with id '{member_id}' not found."
                ) from exc
            raise
        role_names = {r["id"]: r.get("name") for r in team_api.list_roles(client)}
    return {
        "id": detail.get("id"),
        "full_name": detail.get("full_name"),
        "email": detail.get("email"),
        "title": detail.get("title"),
        "roles": [
            {"id": rid, "name": role_names.get(rid, "(unknown)")}
            for rid in (detail.get("roles") or [])
        ],
    }

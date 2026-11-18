"""Team member reads and role changes against the Kizen API."""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def search_team(
    client: KizenClient,
    name: str,
    limit: int | None = 25,
) -> list[dict[str, Any]]:
    """GET /api/team/typeahead?search=<name> — fast text search over team members.

    Follows ``next`` until ``limit`` matches are in hand or none remain.
    ``None`` returns every match. Pass ``None``, not ``0``, for "all": ``0``
    would send ``page_size=0`` and return ``[]``.
    """
    page_size = 100 if limit is None else min(limit, 100)
    rows = client.paginate(
        "/api/team/typeahead",
        {"search": name, "page_size": page_size},
        limit=limit,
    )
    return rows if limit is None else rows[:limit]


def list_team(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/team — every member, with ``roles`` expanded to ``{id, name, ...}``."""
    return client.paginate("/api/team", {"page_size": 100})


def get_team_member(client: KizenClient, member_id: str) -> dict[str, Any]:
    """GET /api/team/{id} — one employee's full detail.

    Unlike the list endpoint (``/api/team``, whose entries embed expanded
    ``roles: [{id, name, ...}]``), this retrieve endpoint's ``roles`` is a
    bare list of role UUIDs — confirmed live 2026-09-01. Callers that need
    names must cross-reference against ``api.permissions.list_roles``.
    """
    return client.get(f"/api/team/{member_id}")


def change_team_member_roles(
    client: KizenClient,
    member_ids: list[str],
    add: list[str],
    remove: list[str],
) -> None:
    """POST /api/team/change-roles — add and remove roles on team members.

    Roles not named in either list are left alone. Applies synchronously and
    answers 200 with an empty body — confirmed live 2026-10-14.
    """
    client.post(
        "/api/team/change-roles",
        json={
            "team_member_ids": member_ids,
            "role_ids_to_add": add,
            "role_ids_to_remove": remove,
        },
    )

"""Plan role changes for one team member.

The op carries the ``POST /api/team/change-roles`` body, which names only the
roles that change. A replace (``set_roles``) is turned into add and remove
lists against the member's live roles.
"""

from __future__ import annotations

from collections.abc import Sequence

from kizen_builder.api import permissions as perm_api
from kizen_builder.api import team as team_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation, fetch_existing
from kizen_builder.tools.team import resolve_team_member_id


def plan_update_team_member_roles(
    member_ref: str,
    *,
    set_roles: list[str] | None = None,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
) -> Plan:
    """Role refs are uuids. ``set_roles`` replaces the member's set; otherwise
    ``add`` and ``remove`` skip roles already held or not held.
    """
    config = load_env_config()
    env = config.name
    with KizenClient(config) as c:
        try:
            member_id = resolve_team_member_id(c, member_ref)
        except LookupError as exc:
            raise PlanError(str(exc)) from exc
        member = fetch_existing(
            "team member", member_id, lambda: team_api.get_team_member(c, member_id)
        )
        roles = perm_api.list_roles(c)
        names = {r["id"]: r.get("name") or r["id"] for r in roles}

        current: list[str] = list(member.get("roles") or [])
        if set_roles is not None:
            to_add = [r for r in dict.fromkeys(set_roles) if r not in current]
            to_remove = [r for r in current if r not in set_roles]
        else:
            both = set(add) & set(remove)
            if both:
                raise PlanError(
                    f"role(s) {sorted(names.get(r, r) for r in both)} "
                    "are both added and removed."
                )
            to_add = [r for r in dict.fromkeys(add) if r not in current]
            to_remove = [r for r in dict.fromkeys(remove) if r in current]

        name = member.get("full_name") or member.get("email") or member_id
        admin = next((r["id"] for r in roles if r.get("name") == "Admin"), None)
        # Count other active Admins from the team list: the role's user_count
        # may include deactivated members.
        if admin in to_remove and not any(
            m["id"] != member_id
            and m.get("verified")
            and not m.get("deleted")
            and any(r.get("id") == admin for r in m.get("roles") or [])
            for m in team_api.list_team(c)
        ):
            raise PlanError(
                f"removing Admin from {name} would leave the business with no Admin"
            )

    after = [r for r in current if r not in to_remove] + to_add
    changed = bool(to_add or to_remove)
    op = PlanOperation(
        action="update" if changed else "skip",
        kind="team_member",
        key=name,
        preview={
            "env": env,
            "member": name,
            "roles": f"{[names.get(r, r) for r in current]} → "
            f"{[names.get(r, r) for r in after]}"
            if changed
            else "no changes",
        },
        payload={
            "team_member_ids": [member_id],
            "role_ids_to_add": to_add,
            "role_ids_to_remove": to_remove,
        }
        if changed
        else {},
        existing_uuid=member_id,
    )
    summary = (
        f"Change roles for '{name}' (+{len(to_add)} / -{len(to_remove)})"
        if changed
        else f"No role changes for '{name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])

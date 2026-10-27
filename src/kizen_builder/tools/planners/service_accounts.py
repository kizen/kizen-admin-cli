"""Plan creation/update/deletion of integration service accounts.

The write body is three keys: ``name``, ``api_name`` and
``permission_groups``. Kizen derives ``api_name`` from ``name`` on create and
on every PATCH that sends ``name``, so the update planner sends only what
changed and pins the current ``api_name`` through a rename. Automation and
plugin accounts are Kizen's own and are refused.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import permissions as perm_api
from kizen_builder.api import service_accounts as sa_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation, fetch_existing
from kizen_builder.tools.service_accounts import INTEGRATION
from kizen_builder.utils import derived_object_api_name

_NO_GROUPS_NOTE = (
    "no permission groups — Kizen creates the account disabled until it has "
    "at least one."
)


def _client() -> KizenClient:
    return KizenClient(load_env_config())


def _group_names(c: KizenClient) -> dict[str, str]:
    return {
        g["id"]: g.get("name") or g["id"] for g in perm_api.list_permission_groups(c)
    }


def _check_groups(group_ids: list[str], groups: dict[str, str]) -> None:
    unknown = [g for g in group_ids if g not in groups]
    if unknown:
        raise PlanError(
            f"permission group id(s) not found: {unknown}. Available: {list(groups)}"
        )


def _require_integration(account: dict[str, Any]) -> None:
    kind = account.get("account_type")
    if kind != INTEGRATION:
        raise PlanError(
            f"'{account.get('name')}' is a {kind} account, which Kizen or a plugin "
            "manages. Only integration service accounts can be changed from the CLI."
        )


def _fetch(c: KizenClient, account_id: str) -> dict[str, Any]:
    return fetch_existing(
        "service account",
        account_id,
        lambda: sa_api.get_service_account(c, account_id),
    )


def plan_create_service_account(name: str, group_ids: list[str] | None = None) -> Plan:
    env = load_env_config().name
    group_ids = list(group_ids or [])
    predicted = derived_object_api_name(name)
    with _client() as c:
        accounts = sa_api.list_service_accounts(c)
        groups = _group_names(c)

    same_name = next(
        (a for a in accounts if (a.get("name") or "").lower() == name.lower()), None
    )
    if same_name is not None:
        raise PlanError(
            f"service account '{name}' already exists (id {same_name['id']})."
        )
    same_api = next((a for a in accounts if a.get("api_name") == predicted), None)
    if same_api is not None:
        raise PlanError(
            f"api_name '{predicted}' (derived from the name) is already used by "
            f"service account '{same_api.get('name')}'."
        )
    _check_groups(group_ids, groups)

    preview: dict[str, Any] = {
        "env": env,
        "name": name,
        "predicted api_name": predicted,
        "permission_groups": [groups[g] for g in group_ids],
    }
    if not group_ids:
        preview["note"] = _NO_GROUPS_NOTE
    op = PlanOperation(
        action="create",
        kind="service_account",
        key=name,
        preview=preview,
        payload={"name": name, "permission_groups": group_ids},
    )
    return Plan.build(
        env=env, summary=f"Create service account '{name}'", operations=[op]
    )


def plan_update_service_account(account_id: str, changes: dict[str, Any]) -> Plan:
    """``changes`` may carry ``name``, ``api_name`` and ``permission_groups``
    (ids; the list replaces the account's set)."""
    env = load_env_config().name
    with _client() as c:
        current = _fetch(c, account_id)
        _require_integration(current)
        groups = _group_names(c) if "permission_groups" in changes else {}

    current_groups = [g["id"] for g in current.get("permission_groups") or []]
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for field in ("name", "api_name"):
        if field in changes and changes[field] != current.get(field):
            payload[field] = changes[field]
            diff[field] = (current.get(field), changes[field])
    if "permission_groups" in changes:
        wanted = list(dict.fromkeys(changes["permission_groups"]))
        _check_groups(wanted, groups)
        if set(wanted) != set(current_groups):
            payload["permission_groups"] = wanted
            diff["permission_groups"] = (
                sorted(groups.get(g, g) for g in current_groups),
                sorted(groups[g] for g in wanted),
            )
    if "name" in payload and "api_name" not in payload:
        # Without it, Kizen re-derives api_name from the new name.
        payload["api_name"] = current.get("api_name")

    name = current.get("name") or account_id
    preview: dict[str, Any] = {
        "env": env,
        "service account": name,
        "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()} or "no changes",
    }
    if payload.get("permission_groups") == []:
        preview["note"] = "no permission groups — Kizen disables the account."
    op = PlanOperation(
        action="update" if payload else "skip",
        kind="service_account",
        key=name,
        preview=preview,
        payload=payload,
        existing_uuid=account_id,
    )
    summary = (
        f"Update service account '{name}' ({len(diff)} change(s))"
        if diff
        else f"No changes to service account '{name}'"
    )
    return Plan.build(env=env, summary=summary, operations=[op])


def plan_delete_service_account(account_id: str) -> Plan:
    env = load_env_config().name
    with _client() as c:
        current = _fetch(c, account_id)
    _require_integration(current)
    name = current.get("name") or account_id
    op = PlanOperation(
        action="delete",
        kind="service_account",
        key=name,
        preview={
            "env": env,
            "service account": name,
            "id": account_id,
            "note": (
                "Kizen archives the account; it disappears from every read and "
                "can't be restored through the API."
            ),
        },
        existing_uuid=account_id,
    )
    return Plan.build(
        env=env, summary=f"Delete service account '{name}'", operations=[op]
    )

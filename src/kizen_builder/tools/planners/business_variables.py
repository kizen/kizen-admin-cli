"""Plan creating, changing and deleting one business variable.

Kizen writes variables only as the business's whole list (``PATCH
/api/business/{id}``), so each op carries the full list as it should be after
the change: every other variable unchanged, by id. ``existing_uuid`` is the
business id the PATCH targets. Apply re-reads the list and refuses if any
other variable changed after planning (see ``tools/plans.py``).
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import business as business_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import Plan, PlanError, PlanOperation


def _read() -> tuple[str, str, list[dict[str, Any]]]:
    config = load_env_config()
    with KizenClient(config) as c:
        live = business_api.list_business_variables(c, config.business_id)
    return config.name, config.business_id, live


def plan_set_business_variable(
    api_name: str, *, value: str | None = None, name: str | None = None
) -> Plan:
    """Create the variable ``api_name`` or change its value and/or name.

    Creating needs both ``name`` and ``value``; an unchanged variable is a skip.
    """
    env, business_id, live = _read()
    current = next((v for v in live if v["api_name"] == api_name), None)
    if name is not None and any(
        v["name"] == name and v["api_name"] != api_name for v in live
    ):
        raise PlanError(f"another business variable is already named '{name}'")

    if current is None:
        if name is None or value is None:
            raise PlanError(
                f"business variable '{api_name}' doesn't exist; "
                "pass --name and --value to create it"
            )
        new = {"name": name, "api_name": api_name, "value_string": value}
        op = PlanOperation(
            action="create",
            kind="business_variable",
            key=api_name,
            preview={"env": env, "variable": api_name, "name": name, "value": value},
            payload={"business_variables": [*live, new]},
            existing_uuid=business_id,
        )
        return Plan.build(
            env=env, summary=f"Create business variable '{api_name}'", operations=[op]
        )

    wanted = {"name": name, "value_string": value}
    changes = {k: v for k, v in wanted.items() if v is not None and current.get(k) != v}
    if not changes:
        op = PlanOperation(
            action="skip",
            kind="business_variable",
            key=api_name,
            preview={"env": env, "variable": api_name, "changes": "no changes"},
            existing_uuid=business_id,
        )
        return Plan.build(
            env=env,
            summary=f"No changes to business variable '{api_name}'",
            operations=[op],
        )

    preview: dict[str, Any] = {"env": env, "variable": api_name}
    for k, v in changes.items():
        preview["value" if k == "value_string" else k] = f"{current.get(k)!r} → {v!r}"
    variables = [{**v, **changes} if v is current else v for v in live]
    op = PlanOperation(
        action="update",
        kind="business_variable",
        key=api_name,
        preview=preview,
        payload={"business_variables": variables},
        existing_uuid=business_id,
    )
    return Plan.build(
        env=env, summary=f"Update business variable '{api_name}'", operations=[op]
    )


def plan_delete_business_variable(api_name: str) -> Plan:
    """Delete the variable ``api_name`` by sending the list without it."""
    env, business_id, live = _read()
    current = next((v for v in live if v["api_name"] == api_name), None)
    if current is None:
        raise PlanError(f"business variable '{api_name}' not found")
    op = PlanOperation(
        action="delete",
        kind="business_variable",
        key=api_name,
        preview={
            "env": env,
            "variable": api_name,
            "name": current["name"],
            "value": current.get("value_string"),
        },
        payload={"business_variables": [v for v in live if v is not current]},
        existing_uuid=business_id,
    )
    return Plan.build(
        env=env, summary=f"Delete business variable '{api_name}'", operations=[op]
    )

"""Plan creation/update/duplication for dashboards / homepages and their dashlets.

Dashboards follow the passthrough model: the dashlet ``config`` surface is wide
and largely undocumented, so a spec carries ``config``/``layout`` opaquely
(copy them from a live dashlet via ``kizen dashboards get <id> -o json --raw``).
Only the envelope (name, api_name, type, sharing) is typed.

A create plan is one ``dashboard`` op followed by one ``dashlet`` op per
dashlet; the dashlet ops carry ``deferred_parent_object_key`` pointing at the
dashboard op's key, so the apply orchestrator injects the new dashboard id as
each dashlet's parent (reusing the same machinery objects/fields use).
"""

from __future__ import annotations

from typing import Any

from kizen_builder.config import load_env_config
from kizen_builder.models.spec import DashboardDef, DashletDef
from kizen_builder.tools.dashboards import (
    DEFAULT_STYLE_SETTINGS,
    default_sharing_settings,
    get_dashboard_detail,
    list_dashboards,
    normalize_sharing_settings,
)
from kizen_builder.tools.plans import (
    Plan,
    PlanError,
    PlanOperation,
    copy_name_preview,
    fetch_existing,
)

# entity_type=custom_object dashlets (table_of_records/field_breakdown/
# field_sum/pivot_table/field_range_breakdown) 400 with "Cannot create
# Custom Object Dashlet for generic Dashboards" on anything but a homepage —
# confirmed live 2026-07-20. Catch it at plan time instead of on apply.
_CUSTOM_OBJECT_ONLY_ON_HOMEPAGE_MSG = (
    "dashlet '{name}' has entity_type=custom_object, which Kizen only allows "
    "on a 'homepage'-type dashboard (this one is '{dash_type}'). Rebuild it "
    "with type='homepage', or drop this dashlet."
)


def _check_custom_object_dashlets_need_homepage(
    dash_type: str | None, dashlets: list[DashletDef]
) -> None:
    if dash_type == "homepage":
        return
    for dl in dashlets:
        if dl.config.get("entity_type") == "custom_object":
            raise PlanError(
                _CUSTOM_OBJECT_ONLY_ON_HOMEPAGE_MSG.format(
                    name=dl.name or "[default]", dash_type=dash_type
                )
            )


def _dashboard_payload(d: DashboardDef) -> dict[str, Any]:
    # An explicit sharing block might be copied from `dashboards get --raw`
    # (read-shape {id, display_name} objects) — normalize to bare UUIDs. When
    # omitted, build the default (all team members + Admin role as admin).
    sharing = (
        normalize_sharing_settings(d.sharing_settings)
        if d.sharing_settings
        else default_sharing_settings()
    )
    payload: dict[str, Any] = {
        "name": d.name,
        "api_name": d.api_name,
        "type": d.type,
        "custom_object": d.custom_object,
        "hidden": d.hidden,
        "published": d.published,
        "style_settings": d.style_settings or DEFAULT_STYLE_SETTINGS,
        "sharing_settings": sharing,
    }
    return payload


def _dashlet_payload(dl: DashletDef) -> dict[str, Any]:
    return {
        "name": dl.name,
        "custom_object": dl.custom_object,
        "layout": dl.layout,
        "config": dl.config,
    }


def _dashlet_label(dl: DashletDef, index: int) -> str:
    """Human token for a dashlet op key: its name, else its report_type, else i."""
    if dl.name and dl.name != "[default]":
        base = dl.name
    else:
        base = str(dl.config.get("report_type") or f"dashlet{index}")
    return f"{index}:{base}"


def plan_create_dashboard(spec: dict[str, Any] | DashboardDef) -> Plan:
    """Plan the creation of one dashboard/homepage and its dashlets."""
    d = spec if isinstance(spec, DashboardDef) else DashboardDef.model_validate(spec)
    env = load_env_config().name

    if d.type == "chart_group" and not d.custom_object:
        raise PlanError("chart_group dashboards require a 'custom_object' UUID.")
    _check_custom_object_dashlets_need_homepage(d.type, d.dashlets)

    existing = next(
        (x for x in list_dashboards() if x.get("api_name") == d.api_name), None
    )
    if existing is not None:
        raise PlanError(
            f"dashboard '{d.api_name}' already exists (uuid {existing['id']}). "
            "Use `dashboards update` instead."
        )

    ops: list[PlanOperation] = [
        PlanOperation(
            action="create",
            kind="dashboard",
            key=d.api_name,
            preview={
                "env": env,
                "api_name": d.api_name,
                "name": d.name,
                "type": d.type,
                "dashlets": len(d.dashlets),
            },
            payload=_dashboard_payload(d),
        )
    ]
    for i, dl in enumerate(d.dashlets):
        ops.append(
            PlanOperation(
                action="create",
                kind="dashlet",
                key=f"{d.api_name}.{_dashlet_label(dl, i)}",
                preview={
                    "env": env,
                    "name": dl.name,
                    "report_type": dl.config.get("report_type"),
                    "chart_type": dl.config.get("chart_type"),
                },
                payload=_dashlet_payload(dl),
                deferred_parent_object_key=d.api_name,
            )
        )
    return Plan.build(
        env=env,
        summary=f"Create {d.type} '{d.api_name}' with {len(d.dashlets)} dashlet(s)",
        operations=ops,
    )


def plan_update_dashboard(dashboard: str, spec: dict[str, Any] | DashboardDef) -> Plan:
    """Plan an update to an existing dashboard's metadata and/or dashlets.

    ``dashboard`` is the dashboard's UUID or api_name. Metadata fields that
    differ from live state produce a PATCH op. Dashlets are diffed by ``id``:
    a dashlet in the spec with an ``id`` that exists → update op; without an
    ``id`` → create op. Dashlets present live but absent from the spec are
    left untouched (removal is out of scope for this verb).
    """
    d = spec if isinstance(spec, DashboardDef) else DashboardDef.model_validate(spec)
    env = load_env_config().name

    try:
        live = get_dashboard_detail(dashboard)
    except LookupError as e:
        raise PlanError(str(e)) from e

    dash_id = live["id"]
    raw = live["raw"]
    _check_custom_object_dashlets_need_homepage(d.type or raw.get("type"), d.dashlets)

    # --- dashboard metadata diff ---------------------------------------
    payload: dict[str, Any] = {}
    diff: dict[str, Any] = {}
    for key, new in (
        ("name", d.name),
        ("type", d.type),
        ("custom_object", d.custom_object),
        ("hidden", d.hidden),
        ("published", d.published),
    ):
        if new is not None and new != raw.get(key):
            payload[key] = new
            diff[key] = (raw.get(key), new)
    if d.style_settings is not None and d.style_settings != raw.get("style_settings"):
        payload["style_settings"] = d.style_settings
        diff["style_settings"] = ("…", "…(changed)")
    if d.sharing_settings is not None and d.sharing_settings != raw.get(
        "sharing_settings"
    ):
        payload["sharing_settings"] = normalize_sharing_settings(d.sharing_settings)
        diff["sharing_settings"] = ("…", "…(changed)")

    ops: list[PlanOperation] = [
        PlanOperation(
            action="update" if payload else "skip",  # type: ignore[arg-type]
            kind="dashboard",
            key=d.api_name or dash_id,
            preview={
                "env": env,
                "dashboard": d.api_name or dash_id,
                "diff": {k: f"{v[0]} → {v[1]}" for k, v in diff.items()}
                or "no metadata changes",
            },
            payload=payload,
            existing_uuid=dash_id,
        )
    ]

    # --- dashlet diff (create new / update by id) ----------------------
    live_dashlet_ids = {dl.get("id") for dl in (raw.get("dashlets") or [])}
    for i, dl in enumerate(d.dashlets):
        if dl.id and dl.id in live_dashlet_ids:
            ops.append(
                PlanOperation(
                    action="update",
                    kind="dashlet",
                    key=f"{d.api_name or dash_id}.{_dashlet_label(dl, i)}",
                    preview={
                        "env": env,
                        "dashlet": dl.name,
                        "id": dl.id[:8],
                        "report_type": dl.config.get("report_type"),
                    },
                    payload=_dashlet_payload(dl),
                    existing_uuid=dl.id,
                    parent_object_uuid=dash_id,
                )
            )
        else:
            ops.append(
                PlanOperation(
                    action="create",
                    kind="dashlet",
                    key=f"{d.api_name or dash_id}.{_dashlet_label(dl, i)}",
                    preview={
                        "env": env,
                        "dashlet": dl.name,
                        "report_type": dl.config.get("report_type"),
                    },
                    payload=_dashlet_payload(dl),
                    parent_object_uuid=dash_id,
                )
            )

    n_changes = sum(1 for op in ops if op.action != "skip")
    return Plan.build(
        env=env,
        summary=(
            f"Update dashboard '{d.api_name or dash_id}' ({n_changes} change(s))"
            if n_changes
            else f"No changes to dashboard '{d.api_name or dash_id}'"
        ),
        operations=ops,
    )


def plan_duplicate_dashboard(
    dashboard: str, *, name: str | None = None, api_name: str | None = None
) -> Plan:
    """Plan a copy of one dashboard that keeps the source's sharing.

    The server's copy has empty sharing (only its creator sees it) and is
    unpublished; the executor PATCHes the source's sharing, plus any ``name``
    and ``api_name``, onto it. ``published`` stays false.
    """
    env = load_env_config().name
    live = fetch_existing(
        "dashboard", dashboard, lambda: get_dashboard_detail(dashboard)
    )

    payload: dict[str, Any] = {
        "sharing_settings": normalize_sharing_settings(live["sharing_settings"])
    }
    if name:
        payload["name"] = name
    if api_name:
        payload["api_name"] = api_name

    op = PlanOperation(
        action="duplicate",
        kind="dashboard",
        key=f"dashboard:{live.get('api_name') or live['id']}:duplicate",
        preview={
            "env": env,
            "source": live.get("name"),
            "new_name": copy_name_preview(live.get("name"), name),
            "new_api_name": api_name
            or f"server default: '{live.get('api_name')}_copy_N'",
            "published": "false (a copy is always unpublished; publish with "
            "dashboards update)",
            "sharing": "copied from source",
        },
        payload=payload,
        existing_uuid=live["id"],
    )
    return Plan.build(
        env=env,
        summary=f"Duplicate dashboard '{live.get('name')}'",
        operations=[op],
    )

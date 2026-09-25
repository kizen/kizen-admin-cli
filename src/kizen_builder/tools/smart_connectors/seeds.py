"""kizen_data_seeds: reading from other Kizen objects.

A seed exposes rows from another Kizen object to the SQL script as a
`kizen.<table>` view, so a connector can join incoming data against what's
already in Kizen. Three things about the wire format are easy to get wrong:

* `group_id` is a **saved filter group** (segment) id on the seeded object,
  from GET /api/custom-objects/{object}/filter-groups. A field *category* id
  400s with a misleading "object does not exist".
* `fields_ids` is write-only — it doesn't come back on a read, so the CLI shows
  what the generated seed table actually carries instead.
* Saving a seed does nothing on its own. The `kizen.<table>` view only appears
  in a script's `config_metadata.seed_tables` when a template is regenerated
  afterwards; PATCHing seeds does not retroactively update an existing script.
  That's why these commands refresh the config by default.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api import custom_objects as co_api
from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors.authoring._helpers import (
    _config_keeping_sql,
    _connector_ref,
    _fresh_template,
    _object_lookup,
    _resolved,
)

# A seed exposes rows from another Kizen object to the SQL script as a
# `kizen.<table>` view, so a connector can join incoming data against what's
# already in Kizen. Three things about the wire format are easy to get wrong:
#
# * `group_id` is a **saved filter group** (segment) id on the seeded object,
#   from GET /api/custom-objects/{object}/filter-groups. A field *category* id
#   400s with a misleading "object does not exist".
# * `fields_ids` is write-only — it doesn't come back on a read, so the CLI shows
#   what the generated seed table actually carries instead.
# * Saving a seed does nothing on its own. The `kizen.<table>` view only appears
#   in a script's `config_metadata.seed_tables` when a template is regenerated
#   afterwards; PATCHing seeds does not retroactively update an existing script.
#   That's why these commands refresh the config by default.


def _seed_rows(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return list(detail.get("kizen_data_seeds") or [])


def _seed_tables_by_object_name(
    client: KizenClient, connector: str, detail: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The connector's generated seed tables, keyed by seeded object name."""
    script_id = (detail.get("last_draft_script") or {}).get("id")
    if not script_id:
        return {}
    script = sc_api.get_sql_script(client, connector, script_id)
    cfg = script.get("config_metadata") or {}
    tables = cfg.get("seed_tables") or [] if isinstance(cfg, dict) else []
    return {t.get("table_name"): t for t in tables}


def _kept_seed_fields_ids(
    client: KizenClient,
    seed: dict[str, Any],
    seed_tables: dict[str, dict[str, Any]],
) -> list[str] | None:
    """Recover a seed's field restriction for a seed we're *not* touching.

    `fields_ids` is write-only on the API and never comes back on a GET, so a
    naive "preserve the seeds I'm not changing" pass silently drops it. The
    generated seed table's `columns_mapping` is the one place the restriction
    survives (same source `list_seeds` uses to show it) — reconstruct
    `fields_ids` from it by mapping column names back to field ids. Returns
    None when the table shows no restriction (or the seed was never
    regenerated into a table, so there's nothing to reconstruct from — that
    seed's restriction, if any, is unrecoverable and is dropped as before).
    """
    object_name = (seed.get("custom_object") or {}).get("name")
    if not isinstance(object_name, str):
        return None
    table = seed_tables.get(object_name)
    if not table:
        return None
    cols = {c.get("col") for c in (table.get("columns_mapping") or []) if c.get("col")}
    cols.discard("kizen_id")  # always included, never part of fields_ids
    if not cols:
        return None
    object_id = seed.get("custom_object_id")
    if not isinstance(object_id, str):
        return None
    live = {
        f["name"]: f["id"]
        for f in co_api.list_fields(client, object_id)
        if f.get("name") and f.get("id") and not f.get("deleted")
    }
    if cols >= set(live):
        return None  # every seedable field is exposed: not actually restricted
    ids = [live[c] for c in cols if c in live]
    return ids or None


def _seed_wire(
    seed: dict[str, Any], *, fields_ids: list[str] | None = None
) -> dict[str, Any]:
    """Reduce a read seed to the keys a write accepts, preserving its id.

    Pass `fields_ids` (from `_kept_seed_fields_ids`) for a seed being kept
    rather than actively set, since the seed's own (read) `fields_ids` is
    always empty — see `_kept_seed_fields_ids`.
    """
    body = {
        "custom_object_id": seed.get("custom_object_id"),
        "group_id": seed.get("group_id"),
    }
    if seed.get("id"):
        body["id"] = seed["id"]
    ids = seed.get("fields_ids") or fields_ids
    if ids:
        body["fields_ids"] = list(ids)
    return body


def list_seeds(connector: str) -> list[dict[str, Any]]:
    """The connector's configured seeds, with the columns each one exposes."""
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)
        by_table = _seed_tables_by_object_name(client, connector, detail)

    out = []
    for seed in _seed_rows(detail):
        obj = seed.get("custom_object") or {}
        name = obj.get("name")
        table = (by_table.get(name) if isinstance(name, str) else None) or {}
        out.append(
            {
                "id": seed.get("id"),
                "custom_object": name or seed.get("custom_object_id"),
                "filter_group": (seed.get("group") or {}).get("name")
                or seed.get("group_id"),
                "group_id": seed.get("group_id"),
                # What the script can actually select — the authoritative answer,
                # since fields_ids is write-only.
                "view": f"kizen.{name}" if name else None,
                "columns": [c.get("col") for c in (table.get("columns_mapping") or [])],
                "in_script": bool(table),
            }
        )
    return out


def _resolve_filter_group(
    client: KizenClient, object_id: str, token: str
) -> dict[str, Any]:
    """Resolve a saved filter group on an object by name or UUID."""
    from kizen_builder.api import saved_views as sv_api

    groups = sv_api.list_saved_views(client, object_id, sv_api.FILTER_GROUPS_BASE)
    for group in groups:
        if token in (group.get("id"), group.get("name")):
            return group
    raise PlanError(
        f"no saved filter group '{token}' on that object. Available: "
        f"{sorted(g.get('name') or '' for g in groups)}. A filter group is a "
        f"saved segment (`kizen filter-groups list <object>`), not a field category."
    )


def plan_add_seed(
    connector: str,
    *,
    custom_object: str,
    group: str,
    fields: list[str] | None = None,
    regenerate: bool = True,
) -> dict[str, Any]:
    """Preview adding (or replacing) one seeded object on a connector."""
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)
        by_api, by_id = _object_lookup(client)
        object_id = _resolved(custom_object, by_api, by_id, "custom object")
        object_name = by_id.get(object_id, custom_object)
        filter_group = _resolve_filter_group(client, object_id, group)

        field_ids: list[str] = []
        field_names: list[str] = []
        if fields:
            allowed = set(
                (sc_api.get_metadata(client) or {}).get(
                    "kizen_data_seeds_allowed_field_types"
                )
                or []
            )
            live = {
                f["name"]: f
                for f in co_api.list_fields(client, object_id)
                if f.get("name") and not f.get("deleted")
            }
            for token in fields:
                match = live.get(token) or next(
                    (f for f in live.values() if f.get("id") == token), None
                )
                if match is None:
                    raise PlanError(
                        f"field '{token}' not found on '{object_name}'. "
                        f"Available: {sorted(live)}"
                    )
                ftype = match.get("field_type")
                if allowed and ftype not in allowed:
                    raise PlanError(
                        f"field '{match['name']}' is a {ftype} field, which can't "
                        f"be seeded. Seedable types: {sorted(allowed)}"
                    )
                field_ids.append(match["id"])
                field_names.append(match["name"])

        existing = _seed_rows(detail)
        replacing = next(
            (s for s in existing if s.get("custom_object_id") == object_id), None
        )
        keeping = [s for s in existing if s is not replacing]
        seed_tables = (
            _seed_tables_by_object_name(client, connector, detail) if keeping else {}
        )
        keep = [
            _seed_wire(s, fields_ids=_kept_seed_fields_ids(client, s, seed_tables))
            for s in keeping
        ]
        new_seed: dict[str, Any] = {
            "custom_object_id": object_id,
            "group_id": filter_group["id"],
        }
        if field_ids:
            new_seed["fields_ids"] = field_ids
        if replacing and replacing.get("id"):
            # Reuse the row so the seed is updated rather than swapped out.
            new_seed["id"] = replacing["id"]

    draft = detail.get("last_draft_script") or {}
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "custom_object": object_name,
        "filter_group": filter_group.get("name") or filter_group["id"],
        "fields": field_names or None,
        "view": f"kizen.{object_name}",
        "replacing": bool(replacing),
        "payload": keep + [new_seed],
        "regenerate": regenerate,
        "script_id": draft.get("id"),
        "source_file_id": (detail.get("source_file") or {}).get("id"),
    }


def plan_remove_seed(
    connector: str, custom_object: str, *, regenerate: bool = True
) -> dict[str, Any]:
    """Preview removing a seeded object from a connector."""
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)
        by_api, by_id = _object_lookup(client)
        object_id = _resolved(custom_object, by_api, by_id, "custom object")
        object_name = by_id.get(object_id, custom_object)

        existing = _seed_rows(detail)
        target = next(
            (s for s in existing if s.get("custom_object_id") == object_id), None
        )
        if target is None:
            raise PlanError(
                f"'{detail.get('api_name')}' doesn't seed '{object_name}'. Seeded: "
                f"{[(s.get('custom_object') or {}).get('name') for s in existing]}"
            )
        keeping = [s for s in existing if s is not target]
        seed_tables = (
            _seed_tables_by_object_name(client, connector, detail) if keeping else {}
        )
        keep = [
            _seed_wire(s, fields_ids=_kept_seed_fields_ids(client, s, seed_tables))
            for s in keeping
        ]

    draft = detail.get("last_draft_script") or {}
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "custom_object": object_name,
        "view": f"kizen.{object_name}",
        "payload": keep,
        "regenerate": regenerate,
        "script_id": draft.get("id"),
        "source_file_id": (detail.get("source_file") or {}).get("id"),
    }


def apply_seed_change(plan: dict[str, Any]) -> dict[str, Any]:
    """Save the seed list, then refresh the script's seed tables.

    The refresh is the part that matters: a saved seed is inert until a template
    regeneration teaches the script about the `kizen.<table>` view. The
    regeneration keeps the script's **existing** ``user_script`` — it takes only
    the freshly generated ``config_metadata``, so iterating on the SQL and then
    adding a seed doesn't throw the SQL away.
    """
    config = load_env_config()
    connector = plan["connector"]
    with KizenClient(config) as client:
        sc_api.update_smart_connector(
            client, connector, {"kizen_data_seeds": plan["payload"]}
        )
        result: dict[str, Any] = {
            "connector": plan["connector_api_name"],
            "seeds": len(plan["payload"]),
            "refreshed": False,
        }
        if not plan.get("regenerate"):
            return result

        source_file_id = plan.get("source_file_id")
        if not source_file_id:
            result["warning"] = (
                "no reference file attached, so the script's seed tables can't be "
                "refreshed yet — `set-input` a file and the seed will be picked up"
            )
            return result

        before = sc_api.get_sql_script(client, connector, plan["script_id"])
        template, target_id = _fresh_template(
            client, connector, source_file_id, plan["script_id"]
        )
        cfg = template.get("config_metadata")
        if not isinstance(cfg, dict):
            result["warning"] = "the server returned no config to refresh from"
            return result
        sc_api.update_sql_script(
            client, connector, target_id, _config_keeping_sql(before, template)
        )

        result.update(
            {
                "refreshed": True,
                "script_id": target_id,
                "seed_tables": [
                    t.get("table_name") for t in (cfg.get("seed_tables") or [])
                ],
                "kept_user_script": bool(before.get("user_script")),
            }
        )
    return result

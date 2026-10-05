"""kizen_data_seeds: reading from other Kizen objects.

A seed exposes rows from another Kizen object to the SQL script as a
`kizen.<table>` view, so a connector can join incoming data against what's
already in Kizen. Three things about the wire format are easy to get wrong:

* `group_id` is a **saved filter group** (segment) id on the seeded object,
  from GET /api/custom-objects/{object}/filter-groups, or `null` for every
  record of the object. A field *category* id 400s with a misleading "object
  does not exist".
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
from kizen_builder.api import records as records_api
from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
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
#   from GET /api/custom-objects/{object}/filter-groups, or `null` for every
#   record of the object. A field *category* id 400s with a misleading "object
#   does not exist".
# * `fields_ids` is write-only — it doesn't come back on a read, so the CLI shows
#   what the generated seed table actually carries instead.
# * Saving a seed does nothing on its own. The `kizen.<table>` view only appears
#   in a script's `config_metadata.seed_tables` when a template is regenerated
#   afterwards; PATCHing seeds does not retroactively update an existing script.
#   That's why these commands refresh the config by default.

# How a null-group seed is labelled wherever a filter group name would appear.
ALL_RECORDS = "all records"


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


def _seed_columns(
    client: KizenClient,
    seed: dict[str, Any],
    seed_tables: dict[str, dict[str, Any]],
) -> list[tuple[str, str | None]] | None:
    """A seed's current columns, in table order, each with its live field id.

    `fields_ids` is write-only on the API and never comes back on a GET, so
    re-saving a seed from read data alone silently narrows it to `kizen_id`.
    The generated seed table's `columns_mapping` is the one place the field
    list survives (same source `list_seeds` uses to show it). `kizen_id` is
    left out, since it's always included and never part of `fields_ids`; a
    column that no longer maps to a live field gets None. Returns None when
    the seed was never regenerated into a table, so there's nothing to
    rebuild from.
    """
    object_name = (seed.get("custom_object") or {}).get("name")
    table = seed_tables.get(object_name) if isinstance(object_name, str) else None
    if not table:
        return None
    cols = [
        c["col"]
        for c in (table.get("columns_mapping") or [])
        if c.get("col") and c["col"] != "kizen_id"
    ]
    if not cols:
        return []
    object_id = seed.get("custom_object_id")
    if not isinstance(object_id, str):
        return None
    live = {
        f["name"]: f["id"]
        for f in co_api.list_fields(client, object_id)
        if f.get("name") and f.get("id") and not f.get("deleted")
    }
    return [(c, live.get(c)) for c in cols]


def _kept_seed_fields_ids(
    columns: list[tuple[str, str | None]] | None,
) -> list[str] | None:
    """The `fields_ids` that re-save a seed's current columns (from
    `_seed_columns`), or None when it exposes only `kizen_id`, or no table."""
    ids = [fid for _, fid in columns or [] if fid]
    return ids or None


def _lost_columns(columns: list[tuple[str, str | None]] | None) -> list[str] | None:
    """The columns re-saving a seed as-is drops: those no longer mapping to a
    live field, or None when it has no table to rebuild them from."""
    return None if columns is None else [c for c, fid in columns if not fid]


def _kept_seeds(
    client: KizenClient,
    seeds: list[dict[str, Any]],
    seed_tables: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str] | None]]:
    """Wire bodies for the seeds a change re-saves untouched, plus, per seeded
    object, what re-saving it drops (see `_lost_columns`)."""
    wire: list[dict[str, Any]] = []
    dropped: dict[str, list[str] | None] = {}
    for seed in seeds:
        columns = _seed_columns(client, seed, seed_tables)
        wire.append(_seed_wire(seed, fields_ids=_kept_seed_fields_ids(columns)))
        lost = _lost_columns(columns)
        if lost is None or lost:
            name = (seed.get("custom_object") or {}).get("name")
            dropped[str(name or seed.get("custom_object_id"))] = lost
    return wire, dropped


def _seed_wire(
    seed: dict[str, Any], *, fields_ids: list[str] | None = None
) -> dict[str, Any]:
    """Reduce a read seed to the keys a write accepts, preserving its id.

    Pass `fields_ids` (from `_kept_seed_fields_ids`) for a seed being kept
    rather than actively set, since the seed's own (read) `fields_ids` is
    always empty — see `_seed_columns`.
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
                "filter_group": (
                    ((seed.get("group") or {}).get("name") or seed["group_id"])
                    if seed.get("group_id")
                    else ALL_RECORDS
                ),
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


def _segment_coverage(
    client: KizenClient, object_id: str, filter_group: dict[str, Any]
) -> dict[str, int] | None:
    """How many of the object's records the segment covers, or None if the
    counts can't be had — they only feed a warning, so they never sink a plan."""
    query = (filter_group.get("config") or {}).get("query") or []
    try:
        return {
            "segment": records_api.count_records(client, object_id, query),
            "total": records_api.count_records(client, object_id, []),
        }
    except (KizenAPIError, KeyError, TypeError, ValueError):
        return None


def plan_add_seed(
    connector: str,
    *,
    custom_object: str,
    group: str | None = None,
    fields: list[str] | None = None,
    regenerate: bool = True,
) -> dict[str, Any]:
    """Preview adding (or replacing) one seeded object on a connector.

    Without a ``group`` the seed covers every record of the object. With one,
    the plan also carries ``coverage`` (segment vs. object record counts) so the
    preview can say how many records the segment leaves out.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)
        by_api, by_id = _object_lookup(client)
        object_id = _resolved(custom_object, by_api, by_id, "custom object")
        object_name = by_id.get(object_id, custom_object)
        filter_group = (
            _resolve_filter_group(client, object_id, group) if group else None
        )

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
            _seed_tables_by_object_name(client, connector, detail) if existing else {}
        )
        keep, dropped = _kept_seeds(client, keeping, seed_tables)
        fields_kept = False
        if replacing:
            current = _seed_columns(client, replacing, seed_tables)
            lost: list[str] | None
            if field_ids:
                lost = [c for c, _ in current or [] if c not in field_names]
            else:
                field_ids = _kept_seed_fields_ids(current) or []
                field_names = [c for c, fid in current or [] if fid]
                fields_kept = bool(field_ids)
                lost = _lost_columns(current)
            if lost is None or lost:
                dropped[object_name] = lost
        new_seed: dict[str, Any] = {
            "custom_object_id": object_id,
            "group_id": filter_group["id"] if filter_group else None,
        }
        if field_ids:
            new_seed["fields_ids"] = field_ids
        if replacing and replacing.get("id"):
            # Reuse the row so the seed is updated rather than swapped out.
            new_seed["id"] = replacing["id"]

        coverage = (
            _segment_coverage(client, object_id, filter_group) if filter_group else None
        )

    draft = detail.get("last_draft_script") or {}
    plan: dict[str, Any] = {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "custom_object": object_name,
        "filter_group": (
            (filter_group.get("name") or filter_group["id"])
            if filter_group
            else ALL_RECORDS
        ),
        "fields": field_names or None,
        "fields_kept": fields_kept,
        "view": f"kizen.{object_name}",
        "replacing": bool(replacing),
        "payload": keep + [new_seed],
        "dropped_columns": dropped,
        "regenerate": regenerate,
        "script_id": draft.get("id"),
        "source_file_id": (detail.get("source_file") or {}).get("id"),
    }
    if coverage:
        plan["coverage"] = coverage
    return plan


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
        keep, dropped = _kept_seeds(client, keeping, seed_tables)

    draft = detail.get("last_draft_script") or {}
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "custom_object": object_name,
        "view": f"kizen.{object_name}",
        "payload": keep,
        "dropped_columns": dropped,
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

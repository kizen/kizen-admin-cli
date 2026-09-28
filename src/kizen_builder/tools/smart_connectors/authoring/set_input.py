"""Authoring: ``set-input`` — upload the reference file, attach it, and (by
default) regenerate the draft script + config from its columns."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from kizen_builder.api import files as files_api
from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors.authoring._helpers import (
    _SAMPLE_FILE_SHAPES,
    _config_keeping_sql,
    _connector_ref,
    _fresh_template,
    _object_lookup,
)
from kizen_builder.tools.smart_connectors.authoring.sample import (
    generate_output_sample,
)

# What a replace leaves stale, in the order to fix it: the draft's sample still
# describes the old file, the live script runs the old file until a publish
# (which is also what refreshes `headers`), and execution variables whose
# column went away survive the publish. See docs/specs/smart-connectors.md.
_REPLACE_STEPS = [
    "push --publish",
    "suggest-variables (re-check execution variables against the new columns)",
]


def plan_set_input(
    connector: str,
    file_path: str | os.PathLike[str],
    *,
    regenerate: bool = True,
    template_sql: bool = False,
) -> dict[str, Any]:
    """Preview attaching a local file to a connector as its reference file.

    A connector that already has one gets it replaced. On a replace the draft's
    SQL is kept and only its config regenerated, unless ``template_sql``.
    """
    src = Path(file_path)
    if not src.is_file():
        raise FileNotFoundError(f"{src} is not a file")

    config = load_env_config()
    with KizenClient(config) as client:
        detail = sc_api.get_smart_connector(client, connector)

    existing = detail.get("source_file") or {}
    replacing = existing.get("name") or existing.get("id") or None
    draft = detail.get("last_draft_script") or {}
    if regenerate and not draft.get("id"):
        raise PlanError(
            f"'{detail.get('api_name')}' has no draft SQL script to write the "
            f"generated template onto — pass regenerate=False (--no-regenerate) "
            f"to just attach the file"
        )

    ctype = detail.get("connector_type")
    return {
        "env": config.name,
        "connector": _connector_ref(detail),
        "connector_api_name": detail.get("api_name"),
        "connector_type": ctype,
        "file": str(src),
        "file_size": src.stat().st_size,
        "replacing": replacing,
        "regenerate": regenerate,
        "template_sql": template_sql,
        "next_steps": [
            "generate-sample (run automatically)" if regenerate else "generate-sample",
            *_REPLACE_STEPS,
        ]
        if replacing
        else [],
        "script_id": draft.get("id"),
        "sql_version": draft.get("sql_version"),
        "expected_shape": _SAMPLE_FILE_SHAPES.get(ctype or ""),
    }


_CREATE_OUTPUT_TABLE = re.compile(
    r"create\s+table\s+output\.(?P<table>\w+)\b[^;]*;\s*", re.IGNORECASE
)


def _drop_phantom_output_tables(
    user_script: str, real_objects: set[str]
) -> tuple[str, list[str]]:
    """Remove generated ``create table output.X`` statements where X isn't an object.

    The webhook template ships a second statement building
    ``output.webhooks`` — a debug echo of the input, since ``webhooks`` is not a
    Kizen object. Leaving it in makes sample generation crash, so it goes. Kept
    general (any output table with no matching object) rather than special-cased
    to webhooks, because that's the actual rule: an output table is a load
    target, and a load target has to exist.

    Only ever applied to a freshly generated template — the ``[^;]*`` span would
    mis-split hand-written SQL with a semicolon inside a string literal.
    Returns ``(script, dropped_table_names)``.
    """
    dropped: list[str] = []

    def _keep(match: re.Match[str]) -> str:
        table = match.group("table")
        if table in real_objects:
            return match.group(0)
        dropped.append(table)
        return ""

    return _CREATE_OUTPUT_TABLE.sub(_keep, user_script), dropped


def apply_set_input(plan: dict[str, Any]) -> dict[str, Any]:
    """Upload the file, attach it, and (by default) regenerate the draft script.

    The S3 upload + File registration, then the connector's ``source_file_id``,
    then the generated ``user_script`` / ``config_metadata`` onto the draft.
    Generation is server-side and reads the file's real columns, so it has to
    happen after the attach.

    Two things about ``get-file-template`` make the last step fiddlier than a
    PATCH (both confirmed live 2026-07-30):

    * It **creates a new draft script** carrying the generated template, so the
      draft that existed at plan time is superseded. Writing to that stale id
      would leave the template on a script nothing looks at, so the target is
      re-read here rather than taken from the plan.
    * The draft it creates comes back at ``sql_version: 1.3.x`` regardless of
      what the connector's draft was on. That's a silent downgrade, and for a
      webhook connector it's fatal — sample generation 500s below 4.1.x. So the
      version is restored when it regressed, before anything runs.

    A replace (the connector already had a file) keeps the draft's
    ``user_script`` and ``sql_version`` and takes only the fresh
    ``config_metadata``, unless ``template_sql``. Either way it reports the
    input tables the new file renamed, then runs the output sample, which
    re-stamps the file the executor reads. A failed sample is reported in
    ``result["sample"]``, not raised: the file is attached by then.
    """
    replacing = bool(plan.get("replacing"))
    config = load_env_config()
    with KizenClient(config) as client:
        # Before the attach: PATCHing the connector's file rewrites the draft's
        # input_tables, which would hide the rename.
        before = (
            sc_api.get_sql_script(client, plan["connector"], plan["script_id"])
            if replacing and plan.get("regenerate")
            else {}
        )
        uploaded = files_api.upload_file(
            client, plan["file"], source=files_api.SMART_CONNECTOR_IMPORT
        )
        sc_api.update_smart_connector(
            client, plan["connector"], {"source_file_id": uploaded["id"]}
        )

        result: dict[str, Any] = {
            "file_id": uploaded["id"],
            "file_name": uploaded.get("name"),
            "connector": plan["connector_api_name"],
            "regenerated": False,
        }
        if not plan.get("regenerate"):
            return result

        if replacing and not plan.get("template_sql"):
            template, script_id = _fresh_template(
                client, plan["connector"], uploaded["id"], plan["script_id"]
            )
            if not template.get("user_script") or not isinstance(
                template.get("config_metadata"), dict
            ):
                raise _empty_template(plan, _run_sample(plan["connector"], script_id))
            sc_api.update_sql_script(
                client,
                plan["connector"],
                script_id,
                _config_keeping_sql(before, template),
            )
            user_script = before.get("user_script") or template["user_script"]
            dropped: list[str] = []
            sql_version, restored = before.get("sql_version"), None
        else:
            template = sc_api.get_file_template(
                client, plan["connector"], uploaded["id"]
            )
            if not template.get("user_script"):
                raise _empty_template(
                    plan, _run_sample(plan["connector"], None) if replacing else None
                )

            refreshed = sc_api.get_smart_connector(client, plan["connector"])
            draft = refreshed.get("last_draft_script") or {}
            script_id = draft.get("id") or plan["script_id"]

            by_api, _ = _object_lookup(client)
            user_script, dropped = _drop_phantom_output_tables(
                template["user_script"], set(by_api)
            )

            script_payload: dict[str, Any] = {"user_script": user_script}
            if template.get("config_metadata") is not None:
                script_payload["config_metadata"] = template["config_metadata"]
            was, now = plan.get("sql_version"), draft.get("sql_version")
            if was and now and was != now:
                script_payload["sql_version"] = was
            updated = sc_api.update_sql_script(
                client, plan["connector"], script_id, script_payload
            )
            sql_version = updated.get("sql_version") or now
            restored = was if "sql_version" in script_payload else None

        cfg = template.get("config_metadata") or {}
        result.update(
            {
                "regenerated": True,
                "script_id": script_id,
                "new_draft": script_id != plan["script_id"],
                "sql_version": sql_version,
                "sql_version_restored": restored,
                "dropped_output_tables": dropped,
                "sql_lines": len(user_script.splitlines()),
                "input_tables": [
                    t.get("name") or t.get("table_name")
                    for t in (cfg.get("input_tables") or [])
                ],
                "seed_tables": [
                    t.get("name") or t.get("table_name")
                    for t in (cfg.get("seed_tables") or [])
                ],
            }
        )
        if replacing:
            result["kept_user_script"] = not plan.get("template_sql") and bool(
                before.get("user_script")
            )
            result["renamed_input_tables"] = _renamed_input_tables(
                before.get("config_metadata") or {}, cfg
            )

    if replacing:
        result["sample"] = _run_sample(plan["connector"], script_id)
    return result


def _run_sample(connector: str, script_id: str | None) -> dict[str, Any]:
    # A replace runs this once the new file is attached, including when the
    # template comes back empty: PATCH never resets a script's state, so the
    # draft would otherwise sit at `success` against the old file.
    try:
        return generate_output_sample(connector, script_id=script_id)
    except KizenAPIError as exc:
        return {"state": "failed", "error": str(exc)}


def _empty_template(
    plan: dict[str, Any], sample: dict[str, Any] | None = None
) -> PlanError:
    message = (
        "the server returned an empty template for this file. The file's "
        "shape is validated per connector type — "
        f"{plan.get('expected_shape') or 'see `kizen docs show reference`'}"
    )
    if sample is not None:
        message += (
            ". The new file is attached anyway, so the draft's output sample was "
            f"re-run on it: {sample.get('state')}"
        )
    return PlanError(message)


def _renamed_input_tables(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, str]]:
    """Input tables whose ``table_name`` changed, paired by position.

    The name comes from the file name (``b.csv`` → ``input.b_csv``), so a
    replace usually renames the table the kept SQL reads. A table with no
    counterpart on the other side isn't a rename and isn't listed.
    """
    old = [t.get("table_name") for t in before.get("input_tables") or []]
    new = [t.get("table_name") for t in after.get("input_tables") or []]
    return [
        {"old": o, "new": n}
        for o, n in zip(old, new, strict=False)
        if o and n and o != n
    ]

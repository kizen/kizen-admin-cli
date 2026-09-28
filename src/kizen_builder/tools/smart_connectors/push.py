"""Dev loop: ``push`` — write the local connector.sql back onto the draft SQL
script, and optionally publish the draft live. Always previews a diff first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors._common import MARKER_NAME
from kizen_builder.tools.smart_connectors.authoring.sample import (
    SAMPLE_RUNNING_STATES,
    run_output_sample,
)


def _read_marker(workdir: Path) -> dict[str, Any]:
    marker_path = workdir / MARKER_NAME
    if not marker_path.exists():
        raise FileNotFoundError(
            f"no {MARKER_NAME} in {workdir} — pass the connector explicitly, or "
            f"run from a directory produced by `smart-connectors pull`."
        )
    return json.loads(marker_path.read_text())


def plan_push(
    workdir: str | os.PathLike[str] = ".",
    *,
    connector: str | None = None,
    script_id: str | None = None,
) -> dict[str, Any]:
    """Compute what a push would change: the current remote draft's SQL vs the
    local connector.sql, as a preview the CLI can render + confirm.

    Guards against the marker going stale behind the CLI's back: if
    ``script_id`` has since been promoted live, a PATCH against it would
    silently no-op (the server 200s without applying the change, then
    ``publish`` 400s with a generic "already live"), so that's rejected here
    with a clear error instead of surfacing downstream. If it's still a draft
    but no longer the connector's *current* one — another stray draft has
    accumulated ahead of it — that's surfaced as a warning rather than a hard
    failure, since an explicit ``--script`` may be intentional.

    Returns ``{connector, script_id, changed, local_sql, remote_sql, diff,
    script_status, current_draft_id, warning}``.
    """
    wd = Path(workdir).resolve()
    from_marker = connector is None or script_id is None
    if from_marker:
        marker = _read_marker(wd)
        connector = (
            connector or marker.get("connector_id") or marker.get("connector_api_name")
        )
        script_id = script_id or marker.get("script_id")
    if connector is None or script_id is None:
        raise PlanError(
            f"could not determine connector/script from {MARKER_NAME} in {wd} — "
            "pass --connector/--script explicitly."
        )

    sql_path = wd / "connector.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"no connector.sql in {wd}.")
    local_sql = sql_path.read_text()

    config = load_env_config()
    with KizenClient(config) as client:
        remote = sc_api.get_sql_script(client, connector, script_id)
        detail = sc_api.get_smart_connector(client, connector)
    remote_sql = remote.get("user_script") or ""
    status = remote.get("status")
    current_draft_id = (detail.get("last_draft_script") or {}).get("id")

    if status and status != "draft":
        source = "the pull marker" if from_marker else "--script"
        hint = (
            f" The connector's current draft is {current_draft_id}."
            if current_draft_id and current_draft_id != script_id
            else ""
        )
        raise PlanError(
            f"script {script_id} (from {source}) is now '{status}', not a "
            f"draft — pushing to it would silently no-op instead of updating "
            f"anything.{hint} Re-run `pull` to pick up the current draft, or "
            f"pass --script explicitly."
        )

    warning = None
    if current_draft_id and current_draft_id != script_id:
        warning = (
            f"script {script_id} is a draft, but it's no longer the "
            f"connector's current one ({current_draft_id}) — likely a stray "
            f"draft left behind by an earlier session. Pushing here won't "
            f"reach what `pull`/other tooling will see next. Re-run `pull`, "
            f"or pass --script {current_draft_id} if that's the intended target."
        )

    import difflib

    diff = "".join(
        difflib.unified_diff(
            remote_sql.splitlines(keepends=True),
            local_sql.splitlines(keepends=True),
            fromfile=f"remote {status or 'draft'} {script_id}",
            tofile="local connector.sql",
        )
    )
    return {
        "connector": connector,
        "script_id": script_id,
        "script_status": status,
        "current_draft_id": current_draft_id,
        "changed": remote_sql != local_sql,
        "local_sql": local_sql,
        "remote_sql": remote_sql,
        "diff": diff,
        "warning": warning,
    }


def apply_push(
    connector: str,
    script_id: str,
    local_sql: str,
    *,
    publish: bool = False,
) -> dict[str, Any]:
    """Write local_sql onto the draft script (PATCH), optionally publish it.

    ``publish`` runs the output sample for the SQL just written first, then
    publishes only if it succeeds. The existing ``state`` can't be trusted: a
    PATCH never resets it, so it's ``setup`` on a draft that publish just forked
    and a stale ``success`` on one edited since its last sample. On failure or
    timeout this raises ``PlanError`` and publishes nothing.

    Publish keeps the script's id (now live) and forks a new draft, returned as
    ``new_draft_id`` for the caller to point its marker at. If the connector
    can't be re-read after the publish, the result still says ``published``,
    with ``new_draft_id`` None and the failure in ``warning``.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        updated = sc_api.update_sql_script(
            client, connector, script_id, {"user_script": local_sql}
        )
        result: dict[str, Any] = {
            "updated_script_id": updated.get("id") or script_id,
            "published": False,
        }
        if not publish:
            return result

        detail = sc_api.get_smart_connector(client, connector)
        script = run_output_sample(
            client,
            connector,
            script_id,
            source_file_id=(detail.get("source_file") or {}).get("id"),
        )
        state = script.get("state")
        if state != "success":
            prefix = f"draft script {script_id} was updated but not published: "
            if state in SAMPLE_RUNNING_STATES:
                raise PlanError(
                    prefix + "its output sample is still running. Check on it "
                    f"with `smart-connectors scripts {connector}`, then "
                    "`push --publish` again."
                )
            error = script.get("error") or script.get("error_details")
            raise PlanError(
                prefix
                + f"its output sample ended '{state or 'none'}'"
                + (f": {error}" if error else "")
                + ". Fix the SQL (try it with `run`), then `push --publish` again."
            )

        pub = sc_api.publish_sql_script(client, connector, script_id)
        result.update(
            published=True,
            published_id=pub.get("id") or script_id,
            new_draft_id=None,
            connector_status=None,
        )
        try:
            after = sc_api.get_smart_connector(client, connector)
        except KizenAPIError as e:
            result["warning"] = (
                f"the script is published, but re-reading the connector failed "
                f"({e}), so its new draft is unknown"
            )
            return result

    new_draft_id = (after.get("last_draft_script") or {}).get("id")
    result.update(
        new_draft_id=new_draft_id if new_draft_id != script_id else None,
        connector_status=after.get("status"),
    )
    return result


def advance_marker(
    workdir: str | os.PathLike[str], *, from_script_id: str, to_script_id: str
) -> bool:
    """Point a pull marker at the draft publish forked, keeping every other key.

    Only a marker that names ``from_script_id`` is rewritten, so a push with an
    explicit ``--script`` for some other script leaves the directory alone.
    Returns whether the marker changed.
    """
    marker_path = Path(workdir).resolve() / MARKER_NAME
    if not marker_path.exists():
        return False
    marker = json.loads(marker_path.read_text())
    if marker.get("script_id") != from_script_id:
        return False
    marker.update(script_id=to_script_id, script_status="draft")
    marker_path.write_text(json.dumps(marker, indent=2))
    return True

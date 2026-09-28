"""Authoring: ``generate-sample`` — run the draft server-side to produce its
output sample, the gate in front of ``publish`` — and ``download-sample``, which
saves a script's sample zip locally."""

from __future__ import annotations

import csv
import io
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from kizen_builder.api import files as files_api
from kizen_builder.api import smart_connectors as sc_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.config import load_env_config
from kizen_builder.tools.plans import PlanError
from kizen_builder.tools.smart_connectors._common import save_file
from kizen_builder.tools.smart_connectors.authoring._helpers import _scopes


def _sample_outputs(content: bytes) -> list[dict[str, Any]]:
    """One ``{table, rows, columns}`` per ``<scope>.csv`` in a sample zip.

    ``rows`` excludes the header. Counted with ``csv`` so a quoted value that
    spans lines is one row.
    """
    outputs = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member in zf.namelist():
            path = PurePosixPath(member)
            if path.suffix.lower() != ".csv":
                continue
            with zf.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                reader = csv.reader(text)
                header = next(reader, [])
                rows = sum(1 for _ in reader)
            outputs.append({"table": path.stem, "rows": rows, "columns": len(header)})
    return outputs


def _summarise(
    content: bytes, file_id: str, warnings: list[str]
) -> list[dict[str, Any]] | None:
    try:
        return _sample_outputs(content)
    except (zipfile.BadZipFile, zlib.error, csv.Error, ValueError) as exc:
        warnings.append(f"could not read output sample {file_id}: {exc}")
        return None


def generate_output_sample(
    connector: str,
    *,
    script_id: str | None = None,
    wait: bool = True,
    timeout: float = 300.0,
    poll_interval: float = 3.0,
) -> dict[str, Any]:
    """Run the draft server-side to produce its output sample.

    Writes no records — it populates the sample that ``publish`` requires.
    Blocks until the script leaves ``in_progress`` unless ``wait=False``.

    ``outputs`` is read from the sample the run just produced (``None`` unless
    it succeeded and the sample could be read). ``scopes`` is the connector's
    ``headers``, which ``configure-flow`` validates against and which can name
    a different set of tables.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        if script_id is None:
            detail = sc_api.get_smart_connector(client, connector)
            draft = detail.get("last_draft_script") or {}
            script_id = draft.get("id")
            if not script_id:
                raise PlanError(f"'{connector}' has no draft SQL script to run.")

        sc_api.start_sql_script(client, connector, script_id)
        script = sc_api.get_sql_script(client, connector, script_id)

        deadline = time.monotonic() + timeout
        while wait and script.get("state") == "in_progress":
            if time.monotonic() > deadline:
                break
            time.sleep(poll_interval)
            script = sc_api.get_sql_script(client, connector, script_id)

        detail = sc_api.get_smart_connector(client, connector)

    warnings: list[str] = []
    outputs = None
    sample_file = None
    sample = script.get("output_csv_file")
    if script.get("state") == "success":
        if isinstance(sample, dict) and sample.get("id"):
            sample_file = {"id": sample["id"], "name": sample.get("name")}
            try:
                content, _ = files_api.download_file(config, sample["id"])
            except KizenAPIError as exc:
                warnings.append(f"could not download output sample: {exc}")
            else:
                outputs = _summarise(content, sample["id"], warnings)
        else:
            warnings.append("the script succeeded but has no output sample file.")

    return {
        "connector": connector,
        "script_id": script_id,
        "state": script.get("state"),
        "error": script.get("error") or script.get("error_details"),
        "scopes": {k: len(v) for k, v in _scopes(detail).items()},
        "outputs": outputs,
        "sample_file": sample_file,
        "warnings": warnings,
        "timed_out": bool(wait and script.get("state") == "in_progress"),
    }


def download_sample(
    connector: str,
    *,
    use_live: bool = False,
    script_id: str | None = None,
    dest: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Save a script's output-sample zip to local disk and summarise it.

    Defaults to the latest draft; ``use_live`` picks the live script and
    ``script_id`` overrides both. Raises ``LookupError`` when the script has no
    sample and ``FileExistsError`` when the path exists and ``force`` is off.
    """
    config = load_env_config()
    with KizenClient(config) as client:
        if script_id is None:
            detail = sc_api.get_smart_connector(client, connector)
            which = "live" if use_live else "draft"
            ref = detail.get("live_script" if use_live else "last_draft_script") or {}
            script_id = ref.get("id")
            if not script_id:
                raise LookupError(f"'{connector}' has no {which} SQL script.")
        script = sc_api.get_sql_script(client, connector, script_id)

    state = script.get("state")
    sample = script.get("output_csv_file")
    if not (isinstance(sample, dict) and sample.get("id")):
        raise LookupError(
            f"script {script_id} (state: {state or 'none'}) has no "
            f"output sample. Generate one with `smart-connectors generate-sample "
            f"{connector}`."
        )

    saved = save_file(
        config,
        sample["id"],
        dest,
        fallback_name=sample.get("name") or f"{connector}_sample_output.zip",
        force=force,
    )
    warnings: list[str] = []
    if state != "success":
        warnings.append(
            f"script state is {state or 'none'}, so this sample may be from an "
            "earlier run."
        )
    return {
        "connector": connector,
        "script_id": script_id,
        "script_status": script.get("status"),
        "state": state,
        "path": str(saved.path),
        "name": saved.name,
        "bytes": len(saved.content),
        "outputs": _summarise(saved.content, sample["id"], warnings),
        "warnings": warnings,
    }

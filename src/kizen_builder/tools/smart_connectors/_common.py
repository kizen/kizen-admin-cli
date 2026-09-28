"""Shared state that needs nothing from the ``connectors`` extra: the pull/push
marker filename, the current_execution.json column list, the one UUID-sniffing
helper used by both the inspection and webhook clusters, and the one place a
downloaded Kizen file is written to local disk.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import NamedTuple

from kizen_builder.api import files as files_api
from kizen_builder.config import EnvConfig

# Marker file dropped into a pulled working directory so `run`/`push` know which
# connector + script the directory belongs to without re-passing it each time.
MARKER_NAME = ".kizen-connector.json"

# The columns script_runner expects in data/current_execution.json (mirrors
# META_CURRENT_EXECUTION_COLUMNS in the vendored runtime).
_META_KEYS = [
    "business_id",
    "connector_id",
    "execution_id",
    "trigger_type",
    "triggered_by_id",
    "triggered_by_desc",
    "trigger_auth",
    "fileupload_file_size_bytes",
    "fileupload_file_name",
    "fileupload_file_id",
    "is_dry_run",
    "cadence",
    "timeframe_start",
    "bulkaction_fields",
    "entity_records_set_key",
    "activity_object_id",
]


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


class SavedFile(NamedTuple):
    path: Path
    name: str
    content: bytes


def _safe_name(name: str | None) -> str:
    # Separators are replaced rather than cut at, so a name like
    # "Orders / Returns.xlsx" keeps its meaning and "../x" can't climb out.
    name = re.sub(r"[/\\\x00]", "_", name or "").strip()
    return "" if name in (".", "..") else name


def _refuse_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists. Pass --force to overwrite it.")


def save_file(
    config: EnvConfig,
    file_id: str,
    dest: str | Path | None = None,
    *,
    fallback_name: str,
    force: bool = False,
) -> SavedFile:
    """Download a stored file and write its bytes, unchanged, to local disk.

    ``name`` is the server's filename (Content-Disposition), else
    ``fallback_name``, with path separators replaced. The file lands at
    ``dest``, inside ``dest`` when that is an existing directory, or at
    ``./<name>`` when ``dest`` is omitted. An existing file is only replaced
    with ``force``.
    """
    path = Path(dest) if dest else Path()
    into_dir = path.is_dir()
    if not into_dir:
        _refuse_overwrite(path, force)
    content, server_name = files_api.download_file(config, file_id)
    name = _safe_name(server_name) or _safe_name(fallback_name) or "download"
    if into_dir:
        path = path / name
        _refuse_overwrite(path, force)
    # Write beside the target and swap it in, so a failed write under --force
    # leaves the old file intact.
    part = path.with_name(f".{path.name}.part")
    try:
        part.write_bytes(content)
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    return SavedFile(path, name, content)

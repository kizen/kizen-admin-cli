"""Dev loop: ``run`` — execute connector.sql locally against embedded
ClickHouse using the vendored ``ChDBScriptRunner`` (same engine Kizen runs in
production), and ``add-input`` — normalize a new input file into a pulled
working directory.

Both need the optional ``connectors`` extra (embedded ClickHouse via chdb);
the vendored runtime is imported lazily so the inspection commands stay
dependency-free.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kizen_builder.tools.smart_connectors.pull import _normalize_input


class ConnectorRuntimeMissing(RuntimeError):
    """The optional ``connectors`` extra (chdb etc.) isn't installed."""


def _load_runner():
    try:
        from kizen_builder.vendor.connector_runtime.script_runner import (
            ChDBScriptRunner,
        )
    except ModuleNotFoundError as exc:  # chdb / python-calamine / charset-normalizer
        raise ConnectorRuntimeMissing(_missing_runtime_message()) from exc
    return ChDBScriptRunner


def _missing_runtime_message() -> str:
    """Explain the missing extra with a command that works *here*.

    The command depends on how the CLI was installed, so it's resolved rather
    than quoted from the README — see ``upgrade.extra_install_hint``. A generic
    `uv sync --extra connectors` is worse than no advice for a `uv tool` or
    `pipx` install: it succeeds, changes the wrong environment, and leaves the
    same error in place.
    """
    from kizen_builder import upgrade

    return (
        "running a connector locally needs the optional 'connectors' extra — "
        "embedded ClickHouse plus the spreadsheet readers, a ~100 MB download "
        "that the rest of the CLI works fine without.\n\n"
        f"    {upgrade.extra_install_hint('connectors')}\n\n"
        "What's in the extra, and why the runtime is vendored: "
        '`kizen docs show smart-connectors`, "Installing the `connectors` extra".'
    )


def run_connector(
    workdir: str | os.PathLike[str] = ".", *, dry_run: bool = False
) -> dict[str, Any]:
    """Execute connector.sql locally against embedded ClickHouse.

    Mirrors the dev package's ``python -m my-connector-package`` entrypoint but
    targets an arbitrary working directory. Returns the runner's output
    metadata (output_files, per-table row/column counts, timing).
    """
    # Check the directory before loading the runtime, not after: being told to
    # install 100 MB of embedded ClickHouse is a bad way to find out you're in
    # the wrong folder, and this check is free.
    wd = Path(workdir).resolve()
    config_path = wd / "__config.json"
    sql_path = wd / "connector.sql"
    if not config_path.exists() or not sql_path.exists():
        raise FileNotFoundError(
            f"{wd} doesn't look like a connector working directory "
            f"(needs __config.json and connector.sql). Run `smart-connectors "
            f"pull <connector>` first."
        )

    ChDBScriptRunner = _load_runner()

    config = json.loads(config_path.read_text())
    user_script = sql_path.read_text()

    # The vendored runner logs INFO-level ``{'log_event': ...}`` dicts straight
    # to stdout via its own handler. That would corrupt `--json` and just adds
    # noise otherwise, so quiet it to WARNING (errors still reach stderr) for the
    # duration of the run; we surface a clean summary from the returned metadata.
    import logging

    runner_logger = logging.getLogger(
        "kizen_builder.vendor.connector_runtime.script_runner"
    )
    prev_level = runner_logger.level

    # script_runner uses a relative data_dir ("data") and relative file() paths,
    # so it must run with cwd == the working directory (matches __main__.py).
    prev_cwd = os.getcwd()
    os.chdir(wd)
    runner_logger.setLevel(logging.WARNING)
    try:
        runner = ChDBScriptRunner(
            config,
            user_script=user_script,
            config_file_path="data/current_execution.json",
            data_dir="data",
            dry_run=dry_run,
        )
        metadata = runner.run()
    finally:
        os.chdir(prev_cwd)
        runner_logger.setLevel(prev_level)

    # Rewrite output paths as absolute for display.
    for f in metadata.get("output_files", []):
        fp = f.get("file_path")
        if fp and not os.path.isabs(fp):
            f["file_path"] = str(wd / fp)
    metadata["workdir"] = str(wd)
    return metadata


def add_input(workdir: str | os.PathLike[str], input_path: str) -> str:
    """Normalize a new input file into a working directory's data/ and patch
    __config.json to use it (Excel/CSV/ZIP). Needs the 'connectors' extra.

    Returns the normalizer's captured progress text (header remaps etc.).
    """
    wd = Path(workdir).resolve()
    if not (wd / "__config.json").exists():
        raise FileNotFoundError(
            f"{wd} has no __config.json — pull the connector there first."
        )
    try:
        return _normalize_input(str(Path(input_path).resolve()), str(wd))
    except ModuleNotFoundError as exc:
        raise ConnectorRuntimeMissing(
            "processing input files needs the 'connectors' extra. Install it "
            "with `uv sync --extra connectors` or `pip install -e '.[connectors]'`."
        ) from exc

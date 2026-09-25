"""Smart-connector tools: the inspection surface, the authoring path, and the
local dev loop (``pull`` → ``run`` → ``push``).

The inspection functions (list/get/executions/scripts/events/metadata) are thin
reshapes over ``api.smart_connectors`` for the CLI to render. See
``inspection.py``.

The authoring functions walk the create → execute path, which is a sequence of
stateful calls rather than a set of independent writes — hence the local
``plan_*`` / ``apply_*`` pairs (same shape ``push`` has always used) spread
across ``authoring/``:

* ``create``           — POST the connector (lands in ``status: "setup"``)
* ``set-input``        — upload a reference file, attach it, and regenerate the
  draft script + config from its columns
* ``generate-sample``  — run the draft to produce the output sample and the
  connector's ``headers``; publish is blocked until this succeeds
* ``configure-flow``   — save execution variables and load steps from a spec
* ``activate``         — the ``status: "operational"`` flip a live run needs
* ``start-flow``       — queue a dry or live execution

The dev-loop functions turn the manual "download the dev package from the UI,
iterate locally, copy-paste the SQL back" workflow into three commands, one
module each:

* ``pull``  — assemble a local working directory (connector.sql + __config.json
  + data/) from the connector's draft (or live) SQL script and its uploaded
  input file. Assembled client-side; see ``api.smart_connectors`` for why the
  server ``/dev-package`` endpoints aren't used.
* ``run``   — execute connector.sql locally against embedded ClickHouse using
  the vendored ``ChDBScriptRunner`` (same engine Kizen runs in production), and
  report the output tables it wrote to ``data/output/``.
* ``push``  — write the local connector.sql back onto the draft SQL script, and
  optionally publish the draft live. Always previews a diff first.

``run``/``add-input`` need the optional ``connectors`` extra (embedded
ClickHouse via chdb); the vendored runtime is imported lazily, inside
``run.py``, so the inspection commands stay dependency-free. Never import
``kizen_builder.vendor`` at module scope anywhere in this package.

This is a package split of what used to be one 2,360-line module, along the
banner sections it already had. Every name below is re-exported here so
``from kizen_builder.tools import smart_connectors as sct`` (or ``sc_tools``)
keeps working exactly as before; the CLI and tests only ever reach this
surface through that module-attribute style, never a wildcard or a submodule
import.
"""

from __future__ import annotations

from kizen_builder.tools.smart_connectors._common import (
    _META_KEYS,
    MARKER_NAME,
    SavedFile,
    _looks_like_uuid,
    save_file,
)
from kizen_builder.tools.smart_connectors.authoring._helpers import (
    _SAMPLE_FILE_SHAPES,
    CONNECTOR_TYPES,
    WEBHOOK_SQL_VERSION,
    _connector_ref,
    _field_lookup,
    _object_lookup,
    _resolved,
    _scopes,
    _sole_scope,
)
from kizen_builder.tools.smart_connectors.authoring.configure_flow import (
    _LOAD_WIRE_KEYS,
    _MATCH_ACTION_KEYS,
    _load_refs,
    _resolve_field,
    _validated_ref,
    _variable_ids,
    _wire_load,
    apply_configure_flow,
    plan_configure_flow,
)
from kizen_builder.tools.smart_connectors.authoring.create import (
    apply_create_connector,
    plan_create_connector,
)
from kizen_builder.tools.smart_connectors.authoring.sample import (
    download_sample,
    generate_output_sample,
)
from kizen_builder.tools.smart_connectors.authoring.set_input import (
    _CREATE_OUTPUT_TABLE,
    _SWAP_WARNING,
    _drop_phantom_output_tables,
    apply_set_input,
    plan_set_input,
)
from kizen_builder.tools.smart_connectors.authoring.start_flow import (
    apply_start_flow,
    plan_start_flow,
)
from kizen_builder.tools.smart_connectors.authoring.status import (
    CONNECTOR_STATUSES,
    apply_set_status,
    plan_set_status,
)
from kizen_builder.tools.smart_connectors.authoring.variables import (
    _VARIABLE_WIRE_KEYS,
    suggest_execution_variables,
)
from kizen_builder.tools.smart_connectors.inspection import (
    EXECUTION_FILES,
    ExecutionFile,
    download_execution_file,
    get_connector,
    get_execution,
    get_execution_script,
    get_metadata,
    list_connectors,
    list_events,
    list_executions,
    list_scripts,
)
from kizen_builder.tools.smart_connectors.pull import (
    _build_config,
    _current_execution,
    _export_seed_data,
    _flatten_field_value,
    _normalize_input,
    _pick_script,
    pull_connector,
)
from kizen_builder.tools.smart_connectors.push import (
    _read_marker,
    apply_push,
    plan_push,
)
from kizen_builder.tools.smart_connectors.run import (
    ConnectorRuntimeMissing,
    _load_runner,
    _missing_runtime_message,
    add_input,
    run_connector,
)
from kizen_builder.tools.smart_connectors.seeds import (
    _resolve_filter_group,
    _seed_rows,
    _seed_wire,
    apply_seed_change,
    list_seeds,
    plan_add_seed,
    plan_remove_seed,
)
from kizen_builder.tools.smart_connectors.webhooks import (
    WEBHOOK_SAMPLE_COLUMNS,
    apply_send_webhook,
    build_webhook_sample,
    plan_send_webhook,
    resolve_team_member,
)

__all__ = [
    "CONNECTOR_STATUSES",
    "CONNECTOR_TYPES",
    "ConnectorRuntimeMissing",
    "EXECUTION_FILES",
    "ExecutionFile",
    "MARKER_NAME",
    "SavedFile",
    "WEBHOOK_SAMPLE_COLUMNS",
    "WEBHOOK_SQL_VERSION",
    "add_input",
    "apply_configure_flow",
    "apply_create_connector",
    "apply_push",
    "apply_seed_change",
    "apply_send_webhook",
    "apply_set_input",
    "apply_set_status",
    "apply_start_flow",
    "build_webhook_sample",
    "download_execution_file",
    "download_sample",
    "generate_output_sample",
    "get_connector",
    "get_execution",
    "get_execution_script",
    "get_metadata",
    "list_connectors",
    "list_events",
    "list_executions",
    "list_scripts",
    "list_seeds",
    "plan_add_seed",
    "plan_configure_flow",
    "plan_create_connector",
    "plan_push",
    "plan_remove_seed",
    "plan_send_webhook",
    "plan_set_input",
    "plan_set_status",
    "plan_start_flow",
    "pull_connector",
    "resolve_team_member",
    "run_connector",
    "save_file",
    "suggest_execution_variables",
    # Private, but reached directly by tests and/or other modules through the
    # `sct.` / `sc_tools.` module-attribute style — re-exported for the same
    # reason the public names above are.
    "_CREATE_OUTPUT_TABLE",
    "_LOAD_WIRE_KEYS",
    "_MATCH_ACTION_KEYS",
    "_META_KEYS",
    "_SAMPLE_FILE_SHAPES",
    "_SWAP_WARNING",
    "_VARIABLE_WIRE_KEYS",
    "_build_config",
    "_connector_ref",
    "_current_execution",
    "_drop_phantom_output_tables",
    "_export_seed_data",
    "_field_lookup",
    "_flatten_field_value",
    "_load_refs",
    "_load_runner",
    "_looks_like_uuid",
    "_missing_runtime_message",
    "_normalize_input",
    "_object_lookup",
    "_pick_script",
    "_read_marker",
    "_resolve_field",
    "_resolve_filter_group",
    "_resolved",
    "_scopes",
    "_seed_rows",
    "_seed_wire",
    "_sole_scope",
    "_validated_ref",
    "_variable_ids",
    "_wire_load",
]

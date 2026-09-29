"""Waiting on Kizen's asynchronous bulk actions.

Any endpoint that starts a server-side bulk job (the CSV uploader, bulk
archive) records its progress in a ``bulk-action-progress`` row; this polls
one row to a terminal status.
"""

from __future__ import annotations

import time
from typing import Any

from kizen_builder.api import bulk_actions as bulk_api
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.tools.automations import MAX_CONSECUTIVE_POLL_ERRORS

# From the live `BulkActionProgressResponseStatusEnum` (confirmed live
# 2026-09-28): `queued, initialize, in_progress, completed, failed,
# cancelled, skipped`. An allowlist, so a status added later keeps polling
# rather than reading as done.
TERMINAL_BULK_ACTION_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "skipped"}
)


def wait_for_bulk_action(
    client: KizenClient,
    progress_id: str,
    *,
    timeout: float = 900.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    """Poll ``progress_id`` until its status is terminal or ``timeout`` passes.

    Returns the last row read, plus ``timed_out`` and ``polls`` (successful
    reads). A 5xx or network error is retried up to
    ``MAX_CONSECUTIVE_POLL_ERRORS`` times in a row before it is raised; a 4xx
    is raised at once. Uses the caller's ``client`` rather than opening one.
    """
    deadline = time.monotonic() + timeout
    row: dict[str, Any] = {}
    polls = 0
    consecutive_errors = 0
    while True:
        try:
            row = bulk_api.get_bulk_action(client, progress_id)
        except KizenAPIError as exc:
            if exc.status_code and exc.status_code < 500:
                raise
            consecutive_errors += 1
            if consecutive_errors > MAX_CONSECUTIVE_POLL_ERRORS:
                raise
        else:
            consecutive_errors = 0
            polls += 1
            if row.get("status") in TERMINAL_BULK_ACTION_STATUSES:
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    return {
        **row,
        "timed_out": row.get("status") not in TERMINAL_BULK_ACTION_STATUSES,
        "polls": polls,
    }

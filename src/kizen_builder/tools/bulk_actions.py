"""Waiting on Kizen's asynchronous bulk actions.

Any endpoint that starts a server-side bulk job (the CSV uploader, bulk
archive) records its progress in a ``bulk-action-progress`` row; this polls
one row to a terminal status.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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


def poll_until(
    read: Callable[[], Any],
    done: Callable[[Any], bool],
    *,
    timeout: float,
    poll_interval: float = 2.0,
) -> tuple[Any, int]:
    """Call ``read`` until ``done`` accepts its value or ``timeout`` passes.

    Returns the last value read (``None`` if no read succeeded) and the number
    of successful reads. A 5xx or network error is retried up to
    ``MAX_CONSECUTIVE_POLL_ERRORS`` times in a row before it is raised; a 4xx
    is raised at once.
    """
    deadline = time.monotonic() + timeout
    value = None
    polls = 0
    consecutive_errors = 0
    while True:
        try:
            value = read()
        except KizenAPIError as exc:
            if exc.status_code and exc.status_code < 500:
                raise
            consecutive_errors += 1
            if consecutive_errors > MAX_CONSECUTIVE_POLL_ERRORS:
                raise
        else:
            consecutive_errors = 0
            polls += 1
            if done(value):
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    return value, polls


def wait_for_bulk_action(
    client: KizenClient,
    progress_id: str,
    *,
    timeout: float = 900.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    """Poll ``progress_id`` until its status is terminal or ``timeout`` passes.

    Returns the last row read, plus ``timed_out`` and ``polls`` (successful
    reads), with :func:`poll_until`'s error tolerance. Uses the caller's
    ``client`` rather than opening one.
    """
    last, polls = poll_until(
        lambda: bulk_api.get_bulk_action(client, progress_id),
        lambda r: r.get("status") in TERMINAL_BULK_ACTION_STATUSES,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    row = last or {}
    return {
        **row,
        "timed_out": row.get("status") not in TERMINAL_BULK_ACTION_STATUSES,
        "polls": polls,
    }

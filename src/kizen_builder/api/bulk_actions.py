"""Reads against ``/api/bulk-action-progress`` — the server-side job rows that
back Kizen's asynchronous bulk actions (CSV uploads, bulk archive, exports).

A row carries ``status`` (``BulkActionProgressResponseStatusEnum``),
``success_count`` / ``failed_count`` / ``pending_count``, and file refs such as
``failure_report``. There is no create or delete: the endpoint that starts a
job creates its row.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def get_bulk_action(client: KizenClient, progress_id: str) -> dict[str, Any]:
    """GET /api/bulk-action-progress/{id}."""
    return client.get(f"/api/bulk-action-progress/{progress_id}")


def list_bulk_actions(
    client: KizenClient,
    *,
    custom_object_id: str | None = None,
    action: str | None = None,
    completed: bool | None = None,
    started_after: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/bulk-action-progress, filtered and paginated.

    ``action`` is a ``BulkActionProgressResponseActionEnum`` value such as
    ``custom_object_upload`` or ``custom_object_archive``. ``started_after`` is
    an ISO-8601 timestamp. Note that a row's ``started_at`` is rewritten when
    processing begins, so a time window alone can match the wrong job.
    """
    params: dict[str, Any] = {}
    if custom_object_id is not None:
        params["custom_object_id"] = custom_object_id
    if action is not None:
        params["action"] = action
    if completed is not None:
        params["completed"] = "true" if completed else "false"
    if started_after is not None:
        params["started_after"] = started_after

    results: list[dict[str, Any]] = []
    page = 1
    while True:
        data = client.get("/api/bulk-action-progress", params={**params, "page": page})
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        page += 1
    return results

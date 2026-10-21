"""Activity (activity-type / loggable-definition) CRUD against the Kizen API.

In Kizen's model an "activity" under ``/api/activities`` is the activity
**type** — a template with a name and its own set of custom fields. Individual
instances are "logged activities" (``/api/activities/logged/{id}``) and
"scheduled activities" (``/api/activities/scheduled-activity/*``), read-only
here.

Each write method takes a raw payload dict (built by the planner) and returns
the parsed JSON response. Errors surface as KizenAPIError.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from kizen_builder.api.client import KizenClient

# ---------------------------------------------------------------------------
# Activity types
# ---------------------------------------------------------------------------


def list_activities(
    client: KizenClient,
    *,
    custom_object_id: str | None = None,
    search: str | None = None,
    show_no_objects_associated: bool = False,
) -> list[dict[str, Any]]:
    """GET /api/activities, transparently paginated.

    Each item is an ``ActivityObject`` summary: ``id``, ``name``, ``api_name``,
    ``n_submissions``, ``created``, plus association metadata.
    """
    params: list[str] = []
    if custom_object_id:
        params.append(f"custom_object_id={custom_object_id}")
    if search:
        from urllib.parse import quote

        params.append(f"search={quote(search)}")
    if show_no_objects_associated:
        params.append("show_no_objects_associated=true")
    path = "/api/activities"
    if params:
        path += "?" + "&".join(params)
    return client.paginate(path)


def get_activity(client: KizenClient, identifier: str) -> dict[str, Any]:
    """GET /api/activities/{identifier} — the full ActivityObjectDetail."""
    return client.get(f"/api/activities/{identifier}")


def create_activity(client: KizenClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/activities. ``name`` is the only required field."""
    return client.post("/api/activities", json=payload)


def update_activity(
    client: KizenClient, identifier: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/activities/{identifier} — partial update."""
    return client.patch(f"/api/activities/{identifier}", json=payload)


def delete_activity(client: KizenClient, identifier: str) -> dict[str, Any]:
    """DELETE /api/activities/{identifier}. Answers 204 No Content."""
    resp = client.delete(f"/api/activities/{identifier}")
    return resp if isinstance(resp, dict) else {}


def duplicate_activity(client: KizenClient, identifier: str) -> dict[str, Any]:
    """POST /api/activities/{identifier}/duplicate with an empty body.

    The server ignores the body and names the copy ``<name> (copy #N)``; rename
    it with :func:`update_activity`.
    """
    return client.post(f"/api/activities/{identifier}/duplicate", json={})


# ---------------------------------------------------------------------------
# Activity fields (sub-resource of an activity type)
# ---------------------------------------------------------------------------


def list_activity_fields(client: KizenClient, identifier: str) -> list[dict[str, Any]]:
    """GET /api/activities/{identifier}/fields — plain list of ActivityField."""
    resp = client.get(f"/api/activities/{identifier}/fields?ordering=order")
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []


def create_activity_field(
    client: KizenClient, identifier: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/activities/{identifier}/fields."""
    return client.post(f"/api/activities/{identifier}/fields", json=payload)


def update_activity_field(
    client: KizenClient, identifier: str, field_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/activities/{identifier}/fields/{field_id}."""
    return client.patch(f"/api/activities/{identifier}/fields/{field_id}", json=payload)


def delete_activity_field(
    client: KizenClient, identifier: str, field_id: str
) -> dict[str, Any]:
    """DELETE /api/activities/{identifier}/fields/{field_id}."""
    resp = client.delete(f"/api/activities/{identifier}/fields/{field_id}")
    return resp if isinstance(resp, dict) else {}


def add_activity_field_option(
    client: KizenClient, identifier: str, field_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/activities/{identifier}/fields/{field_id}/options.

    ``payload`` is a FieldOptionRequest (``{"name", "code"?, "order"?, ...}``).
    """
    return client.post(
        f"/api/activities/{identifier}/fields/{field_id}/options", json=payload
    )


def delete_activity_field_option(
    client: KizenClient, identifier: str, field_id: str, option_id: str
) -> dict[str, Any]:
    """DELETE .../fields/{field_id}/options/{option_id}."""
    resp = client.delete(
        f"/api/activities/{identifier}/fields/{field_id}/options/{option_id}"
    )
    return resp if isinstance(resp, dict) else {}


def replace_activity_field_option(
    client: KizenClient,
    identifier: str,
    field_id: str,
    option_id: str,
    replacement: dict[str, Any],
) -> dict[str, Any]:
    """POST .../fields/{field_id}/options/{option_id}/replace.

    Remaps records set to ``option_id`` onto ``replacement`` (a FieldOption
    request, e.g. ``{"id": <other_uuid>}``) and removes the old option.
    """
    return client.post(
        f"/api/activities/{identifier}/fields/{field_id}/options/{option_id}/replace",
        json=replacement,
    )


# ---------------------------------------------------------------------------
# Logged activities (instances) — read-only
# ---------------------------------------------------------------------------


def get_logged_activity(client: KizenClient, logged_id: str) -> dict[str, Any]:
    """GET /api/activities/logged/{id} — one LogActivityRead instance."""
    return client.get(f"/api/activities/logged/{logged_id}")


def list_responses(
    client: KizenClient,
    identifier: str,
    *,
    custom_object_id: str | None = None,
    search: str | None = None,
    body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """POST /api/activities/{identifier}/responses — logged instances of a type.

    Kizen models this as a POST (the body can carry filter criteria) with a
    paginated response. We send an empty body by default to list everything.
    """
    from urllib.parse import quote

    params: list[str] = []
    if custom_object_id:
        params.append(f"custom_object_id={custom_object_id}")
    if search:
        params.append(f"search={quote(search)}")
    path = f"/api/activities/{identifier}/responses"
    if params:
        path += "?" + "&".join(params)

    items: list[dict[str, Any]] = []
    nxt: str | None = path
    payload = body or {}
    while nxt:
        # Only the first request carries the filter body; ``next`` links are
        # followed with a bare POST.
        resp = client.post(nxt, json=payload)
        payload = {}
        if isinstance(resp, dict) and "results" in resp:
            items.extend(resp["results"])
            following = resp.get("next")
            if following:
                parts = urlsplit(following)
                nxt = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                nxt = None
        elif isinstance(resp, list):
            items.extend(resp)
            nxt = None
        else:
            nxt = None
    return items


# ---------------------------------------------------------------------------
# Scheduled activities (instances) — read-only
# ---------------------------------------------------------------------------


def list_scheduled(
    client: KizenClient,
    *,
    activity_id: str | None = None,
    assigned_to_me: bool | None = None,
    completed: bool | None = None,
) -> list[dict[str, Any]]:
    """GET /api/activities/scheduled-activity[/search] — scheduled instances.

    Uses the ``/search`` variant when ``activity_id`` is given (it accepts an
    activity filter); otherwise the plain list endpoint.
    """
    params: list[str] = []
    if activity_id:
        params.append(f"activity_id={activity_id}")
    if assigned_to_me is not None:
        params.append(f"assigned_to_me={'true' if assigned_to_me else 'false'}")
    if completed is not None:
        params.append(f"completed={'true' if completed else 'false'}")
    base = (
        "/api/activities/scheduled-activity/search"
        if activity_id
        else "/api/activities/scheduled-activity"
    )
    path = base + ("?" + "&".join(params) if params else "")
    return client.paginate(path)


def get_scheduled(client: KizenClient, scheduled_id: str) -> dict[str, Any]:
    """GET /api/activities/scheduled-activity/{id} — one scheduled instance."""
    return client.get(f"/api/activities/scheduled-activity/{scheduled_id}")

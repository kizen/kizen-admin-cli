"""Form / Survey CRUD against the Kizen API.

Forms (``/api/forms``) and Surveys (``/api/surveys``) are structurally
identical data-capture objects — same 24 endpoints, same request/response
shapes, different base path. Rather than duplicate the module, every function
here takes ``base_path`` as its first argument (``"/api/forms"`` or
``"/api/surveys"``); ``tools/forms.py`` and the CLI bake in the right base
path per command group.

Each write method takes a raw payload dict (built by the planner) and returns
the parsed JSON response. Errors surface as KizenAPIError.

This slice covers the object itself (list/get/create/update/delete/duplicate)
and its field sub-resource (list/create/update/delete + options add/remove).
Submissions, subscribers, page-view, and upload endpoints are explicitly
deferred to a follow-up slice.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient

# ---------------------------------------------------------------------------
# Form / survey object
# ---------------------------------------------------------------------------


def list_forms(
    client: KizenClient, base_path: str, *, search: str | None = None
) -> list[dict[str, Any]]:
    """GET {base_path}, transparently paginated."""
    path = base_path
    if search:
        from urllib.parse import quote

        path += f"?search={quote(search)}"
    return client.paginate(path)


def get_form(client: KizenClient, base_path: str, identifier: str) -> dict[str, Any]:
    """GET {base_path}/{identifier} — the full detail object."""
    return client.get(f"{base_path}/{identifier}")


def create_form(
    client: KizenClient, base_path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST {base_path}. ``name`` is the only required field."""
    return client.post(base_path, json=payload)


def update_form(
    client: KizenClient, base_path: str, identifier: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH {base_path}/{identifier} — partial update.

    The API also allows a full PUT, but the CLI's `update` verb only ever
    sends the flags that changed, matching every other `update` command in
    this repo — so PATCH is the right primitive here.
    """
    return client.patch(f"{base_path}/{identifier}", json=payload)


def delete_form(client: KizenClient, base_path: str, identifier: str) -> dict[str, Any]:
    """DELETE {base_path}/{identifier}. Answers 204 No Content."""
    resp = client.delete(f"{base_path}/{identifier}")
    return resp if isinstance(resp, dict) else {}


def duplicate_form(
    client: KizenClient, base_path: str, identifier: str
) -> dict[str, Any]:
    """POST {base_path}/{identifier}/duplicate with an empty body.

    The server ignores the body and names the copy ``<name> (copy #N)``; rename
    it with :func:`update_form`.
    """
    return client.post(f"{base_path}/{identifier}/duplicate", json={})


# ---------------------------------------------------------------------------
# Fields (sub-resource of a form/survey)
# ---------------------------------------------------------------------------


def list_form_fields(
    client: KizenClient, base_path: str, identifier: str
) -> list[dict[str, Any]]:
    """GET {base_path}/{identifier}/fields — plain list of fields."""
    resp = client.get(f"{base_path}/{identifier}/fields?ordering=order")
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []


def create_form_field(
    client: KizenClient, base_path: str, identifier: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST {base_path}/{identifier}/fields."""
    return client.post(f"{base_path}/{identifier}/fields", json=payload)


def update_form_field(
    client: KizenClient,
    base_path: str,
    identifier: str,
    field_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """PATCH {base_path}/{identifier}/fields/{field_id}."""
    return client.patch(f"{base_path}/{identifier}/fields/{field_id}", json=payload)


def delete_form_field(
    client: KizenClient, base_path: str, identifier: str, field_id: str
) -> dict[str, Any]:
    """DELETE {base_path}/{identifier}/fields/{field_id}."""
    resp = client.delete(f"{base_path}/{identifier}/fields/{field_id}")
    return resp if isinstance(resp, dict) else {}


def add_form_field_option(
    client: KizenClient,
    base_path: str,
    identifier: str,
    field_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST {base_path}/{identifier}/fields/{field_id}/options.

    ``payload`` is a FieldOptionRequest (``{"name", "code"?, "order"?, ...}``).
    """
    return client.post(
        f"{base_path}/{identifier}/fields/{field_id}/options", json=payload
    )


def delete_form_field_option(
    client: KizenClient,
    base_path: str,
    identifier: str,
    field_id: str,
    option_id: str,
) -> dict[str, Any]:
    """DELETE .../fields/{field_id}/options/{option_id}."""
    resp = client.delete(
        f"{base_path}/{identifier}/fields/{field_id}/options/{option_id}"
    )
    return resp if isinstance(resp, dict) else {}


def replace_form_field_option(
    client: KizenClient,
    base_path: str,
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
        f"{base_path}/{identifier}/fields/{field_id}/options/{option_id}/replace",
        json=replacement,
    )

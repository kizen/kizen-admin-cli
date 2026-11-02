"""Pipeline stage CRUD and record stage-move against the Kizen API.

Stages are a sub-resource of a pipeline object (``/api/pipelines/{object_pk}``)
distinct from the object's own fields — see `kizen docs show reference` ("Pipeline
stages") for why they can't be managed through `fields options`.
"""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def list_stages(client: KizenClient, object_pk: str) -> list[dict[str, Any]]:
    """GET /api/pipelines/{object_pk}/stages?ordering=order."""
    resp = client.get(
        f"/api/pipelines/{object_pk}/stages", params={"ordering": "order"}
    )
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "results" in resp:
        return list(resp["results"])
    return []


def create_stage(
    client: KizenClient, object_pk: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/pipelines/{object_pk}/stages. Requires name, status, order."""
    return client.post(f"/api/pipelines/{object_pk}/stages", json=payload)


def update_stage(
    client: KizenClient, object_pk: str, stage_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/pipelines/{object_pk}/stages/{stage_id} — partial update."""
    return client.patch(f"/api/pipelines/{object_pk}/stages/{stage_id}", json=payload)


def remove_stage(
    client: KizenClient, object_pk: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/pipelines/{object_pk}/stages/remove-stage.

    ``payload`` is ``{"id": <stage_to_remove>, "new_stage_id": <destination>}``.
    Deletes the stage and migrates its records onto ``new_stage_id``.
    """
    return client.post(f"/api/pipelines/{object_pk}/stages/remove-stage", json=payload)


def move_record(
    client: KizenClient, object_identifier: str, entity_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /api/records/{object_identifier}/{entity_id}/move.

    ``object_identifier`` must be the object UUID; the api_name answers 500
    (confirmed live 2026-10-30).

    ``payload`` carries ``stage_id`` (and optionally ``move_before_record_id`` /
    ``move_after_record_id`` for board-order placement within the stage).
    """
    return client.patch(
        f"/api/records/{object_identifier}/{entity_id}/move", json=payload
    )

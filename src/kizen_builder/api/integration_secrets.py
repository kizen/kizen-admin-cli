"""Integration secret reads against the Kizen API."""

from __future__ import annotations

from typing import Any

from kizen_builder.api.client import KizenClient


def list_integration_secrets(client: KizenClient) -> list[dict[str, Any]]:
    """GET /api/integration-secrets — every secret's ``api_name``,
    ``description`` and ``obfuscated_value``. Callers keep what they need and
    drop ``obfuscated_value``."""
    return client.paginate("/api/integration-secrets")

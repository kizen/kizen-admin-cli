"""Business variable read tools."""

from __future__ import annotations

from typing import Any

from kizen_builder.api import business as business_api
from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config


def list_business_variables() -> list[dict[str, Any]]:
    """Every business variable on the active profile's business."""
    config = load_env_config()
    with KizenClient(config) as client:
        return business_api.list_business_variables(client, config.business_id)

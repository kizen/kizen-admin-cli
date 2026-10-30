"""Tests for automations.list_llm_models() — the call_llm business_plugin_app_id catalog."""

from __future__ import annotations

import pytest

from kizen_builder.tools import automations as auto_tools

_FAKE_METADATA = {
    "llm": {
        "provider_model_details": [
            {
                "provider_name": "kizen",
                "plugin_app": {"id": "145fa07b-ffe0-49cb-89af-8f126a0a2f13"},
                "models": [
                    {
                        "model_value": "kizen/pro",
                        "model_label": "Kizen AI Pro",
                        "is_deprecated": False,
                        "suggested_replacement": None,
                        "usage": {
                            "text": {"call": True, "decision": True},
                            "image": {"extraction": True},
                            "audio": {"transcription": False},
                        },
                    }
                ],
            },
            {
                "provider_name": "gemini",
                "plugin_app": {"id": "92dfef31-8b9b-49dc-b54b-3e47cd6b4523"},
                "models": [
                    {
                        "model_value": "gemini/gemini-2.5-flash",
                        "model_label": "Gemini 2.5 Flash",
                        "is_deprecated": False,
                        "suggested_replacement": None,
                        "usage": {
                            "text": {"call": True, "decision": True},
                            "image": {"extraction": True},
                            "audio": {"transcription": True},
                        },
                    },
                    {
                        "model_value": "gemini/gemini-1.5-flash",
                        "model_label": "Gemini 1.5 Flash (old)",
                        "is_deprecated": True,
                        "suggested_replacement": "gemini/gemini-2.5-flash",
                        "usage": {
                            "text": {"call": True, "decision": False},
                            "image": {"extraction": False},
                            "audio": {"transcription": False},
                        },
                    },
                ],
            },
        ]
    }
}


@pytest.fixture(autouse=True)
def _patch_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_tools.auto_api, "get_metadata", lambda client: _FAKE_METADATA
    )


def test_kizen_models_carry_the_kizen_ai_install_id():
    rows = auto_tools.list_llm_models()
    kizen_row = next(r for r in rows if r["model_value"] == "kizen/pro")
    assert kizen_row["business_plugin_app_id"] == "145fa07b-ffe0-49cb-89af-8f126a0a2f13"


def test_non_native_models_carry_their_provider_instance_id():
    rows = auto_tools.list_llm_models()
    gemini_row = next(r for r in rows if r["model_value"] == "gemini/gemini-2.5-flash")
    assert (
        gemini_row["business_plugin_app_id"] == "92dfef31-8b9b-49dc-b54b-3e47cd6b4523"
    )


def test_flattens_one_row_per_model_across_providers():
    rows = auto_tools.list_llm_models()
    assert [r["model_value"] for r in rows] == [
        "kizen/pro",
        "gemini/gemini-2.5-flash",
        "gemini/gemini-1.5-flash",
    ]


def test_carries_capability_and_deprecation_flags():
    rows = auto_tools.list_llm_models()
    deprecated = next(r for r in rows if r["model_value"] == "gemini/gemini-1.5-flash")
    assert deprecated["is_deprecated"] is True
    assert deprecated["suggested_replacement"] == "gemini/gemini-2.5-flash"
    assert deprecated["supports_decision"] is False
    assert deprecated["supports_call"] is True

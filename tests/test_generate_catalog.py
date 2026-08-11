"""Tests for the models.dev catalog generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts/generate_catalog.py"
_SPEC = importlib.util.spec_from_file_location("generate_catalog", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_GENERATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GENERATOR)
CatalogGenerationError = _GENERATOR.CatalogGenerationError
generate_catalog = _GENERATOR.generate_catalog
metadata_from_upstream = _GENERATOR.metadata_from_upstream
validate_strict_sets = _GENERATOR.validate_strict_sets


def _upstream_model() -> dict[str, object]:
    return {
        "id": "model-1",
        "name": "Model One",
        "tool_call": True,
        "reasoning": True,
        "modalities": {"input": ["text", "image", "pdf"]},
        "limit": {"context": 200_000, "output": 32_000},
        "cost": {"input": 3, "output": 15, "cache_read": 0.3},
    }


def test_metadata_from_upstream_maps_tau_fields() -> None:
    assert metadata_from_upstream(_upstream_model()) == {
        "name": "Model One",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 0},
        "context_window": 200_000,
        "max_tokens": 32_000,
    }


def test_generate_catalog_applies_tau_metadata_overlays() -> None:
    overlays = {
        "schema_version": 1,
        "providers": [
            {
                "name": "anthropic",
                "models": ["model-1"],
                "model_metadata": {
                    "model-1": {
                        "context_window": 150_000,
                        "api": "anthropic-messages",
                        "compat": {"forceAdaptiveThinking": True},
                    }
                },
                "context_windows": {"model-1": 150_000},
            }
        ],
    }
    upstream = {"anthropic": {"models": {"model-1": _upstream_model()}}}

    generated = generate_catalog(overlays, upstream)
    provider = generated["providers"][0]
    metadata = provider["model_metadata"]["model-1"]

    assert metadata["max_tokens"] == 32_000
    assert metadata["context_window"] == 150_000
    assert metadata["compat"] == {"forceAdaptiveThinking": True}
    assert provider["context_windows"] == {"model-1": 150_000}


def test_strict_mode_reports_upstream_model_drift() -> None:
    upstream = {
        "anthropic": {"models": {"new-model": _upstream_model()}},
        "github-copilot": {"models": {}},
        "openai": {"models": {}},
    }
    manifest = {
        "expected_tool_model_ids": {
            "anthropic": ["old-model"],
            "github-copilot": [],
            "openai": [],
        }
    }

    with pytest.raises(CatalogGenerationError, match=r"added=\['new-model'\]"):
        validate_strict_sets(upstream, manifest)

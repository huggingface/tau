from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding import models_dev
from tau_coding.catalog_loader import builtin_source_catalog
from tau_coding.models_dev import (
    bundled_models_dev_catalog_overlay,
    models_dev_catalog_document,
    models_dev_catalog_overlay,
    thinking_level_map_from_reasoning_options,
)

FIXTURE = Path(__file__).parent / "fixtures/models_dev_catalog.json"


def test_effort_options_use_pi_level_map_semantics() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    options = source["huggingface"]["models"]["zai-org/GLM-5.2"]["reasoning_options"]

    assert thinking_level_map_from_reasoning_options(options) == {
        "off": "none",
        "minimal": None,
        "low": None,
        "medium": None,
        "high": "high",
        "xhigh": None,
        "max": "max",
    }


def test_full_effort_options_preserve_every_pi_level() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    options = source["huggingface"]["models"]["moonshotai/Kimi-K3"]["reasoning_options"]

    assert thinking_level_map_from_reasoning_options(options) == {
        "off": "none",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max",
    }


def test_empty_or_toggle_only_options_do_not_override_manual_behavior() -> None:
    assert thinking_level_map_from_reasoning_options([]) is None
    assert thinking_level_map_from_reasoning_options([{"type": "toggle"}]) is None


def test_generation_adds_new_tool_models_and_provider_aliases() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))

    document = models_dev_catalog_document(source, builtin_source_catalog())

    assert set(document["providers"]) == {"huggingface", "together"}
    huggingface = document["providers"]["huggingface"]
    assert "example/new-tool-model" in huggingface["models"]
    assert huggingface["model_metadata"]["example/new-tool-model"] == {
        "name": "New Tool Model",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0.1, "output": 0.2, "cacheRead": 0.0, "cacheWrite": 0.0},
        "context_window": 65536,
        "max_tokens": 8192,
    }
    together = document["providers"]["together"]
    assert together["model_metadata"]["zai-org/GLM-5.1"]["thinking_level_map"] == {
        "high": "high",
        "max": "max",
    }


def test_invalid_generated_catalog_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported schema"):
        models_dev_catalog_overlay({"schema_version": 2, "providers": {}})


def test_missing_bundled_catalog_falls_back_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_files(_package: str) -> None:
        raise OSError("offline package fixture")

    monkeypatch.setattr(models_dev, "files", missing_files)

    assert bundled_models_dev_catalog_overlay() is None

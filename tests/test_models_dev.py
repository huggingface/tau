from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding import models_dev
from tau_coding.catalog_loader import builtin_catalog
from tau_coding.models_dev import (
    bundled_reasoning_catalog_overlay,
    reasoning_catalog_overlay,
    reasoning_maps_from_models_dev,
    thinking_level_map_from_reasoning_options,
)

FIXTURE = Path(__file__).parent / "fixtures/models_dev_reasoning.json"


def test_effort_options_map_only_verified_values_and_max_to_xhigh() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    options = source["huggingface"]["models"]["zai-org/GLM-5.2"]["reasoning_options"]

    assert thinking_level_map_from_reasoning_options(options) == {
        "off": "none",
        "minimal": None,
        "low": None,
        "medium": None,
        "high": "high",
        "xhigh": "max",
    }


def test_full_effort_options_preserve_every_tau_level() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    options = source["huggingface"]["models"]["moonshotai/Kimi-K3"]["reasoning_options"]

    assert thinking_level_map_from_reasoning_options(options) == {
        "off": "none",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
    }


def test_generation_is_provider_and_model_scoped() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))

    maps = reasoning_maps_from_models_dev(source, builtin_catalog())

    assert set(maps) == {"huggingface", "together"}
    assert set(maps["huggingface"]) == {"moonshotai/Kimi-K3", "zai-org/GLM-5.2"}
    assert maps["together"]["zai-org/GLM-5.1"]["xhigh"] == "max"


def test_empty_reasoning_options_disable_unverified_provider_defaults() -> None:
    assert thinking_level_map_from_reasoning_options([]) == {
        "off": None,
        "minimal": None,
        "low": None,
        "medium": None,
        "high": None,
        "xhigh": None,
    }


def test_invalid_generated_metadata_falls_back_to_catalog() -> None:
    with pytest.raises(ValueError, match="unsupported schema"):
        reasoning_catalog_overlay({"schema_version": 2, "providers": {}})


def test_missing_bundled_metadata_falls_back_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_files(_package: str) -> None:
        raise OSError("offline package fixture")

    monkeypatch.setattr(models_dev, "files", missing_files)

    assert bundled_reasoning_catalog_overlay() is None

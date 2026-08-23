"""Build-time models.dev reasoning metadata for Tau's bundled catalog."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib.resources import files
from typing import Any

from tau_coding.provider_catalog import ProviderCatalogEntry
from tau_coding.thinking import THINKING_LEVELS, ThinkingLevel

MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_REASONING_RESOURCE = "data/models-dev-reasoning.json"

# Tau names providers for users; models.dev names them for its own catalog.
_MODELS_DEV_PROVIDER_KEYS = {
    "kimi-code": "kimi-for-coding",
    "together": "togetherai",
}


def thinking_level_map_from_reasoning_options(
    reasoning_options: object,
) -> dict[ThinkingLevel, str | None] | None:
    """Convert models.dev effort values to Tau's six UI levels.

    ``None`` means the source field was malformed or unavailable. An empty map of
    verified effort values is represented by six unavailable levels, preventing
    Tau from sending a provider-wide default that the model did not advertise.
    """
    if not isinstance(reasoning_options, list):
        return None

    values: set[str] = set()
    for option in reasoning_options:
        if not isinstance(option, Mapping) or option.get("type") != "effort":
            continue
        raw_values = option.get("values")
        if not isinstance(raw_values, list):
            continue
        values.update(value for value in raw_values if isinstance(value, str))

    return {
        "off": "none" if "none" in values else None,
        "minimal": "minimal" if "minimal" in values else None,
        "low": "low" if "low" in values else None,
        "medium": "medium" if "medium" in values else None,
        "high": "high" if "high" in values else None,
        # Tau exposes a provider's maximum effort as xhigh. Prefer a literal
        # xhigh value when available, then use the established xhigh -> max map.
        "xhigh": "xhigh" if "xhigh" in values else "max" if "max" in values else None,
    }


def reasoning_maps_from_models_dev(
    source: object,
    catalog: Iterable[ProviderCatalogEntry],
) -> dict[str, dict[str, dict[ThinkingLevel, str | None]]]:
    """Return deterministic per-model effort maps for relevant catalog providers."""
    if not isinstance(source, Mapping):
        raise ValueError("models.dev data must be a JSON object")

    output: dict[str, dict[str, dict[ThinkingLevel, str | None]]] = {}
    for provider in sorted(catalog, key=lambda item: item.name):
        if provider.kind != "openai-compatible" or provider.thinking_parameter not in {
            "reasoning_effort",
            "reasoning.effort",
        }:
            continue
        source_key = _MODELS_DEV_PROVIDER_KEYS.get(provider.name, provider.name)
        source_provider = source.get(source_key)
        if not isinstance(source_provider, Mapping):
            continue
        source_models = source_provider.get("models")
        if not isinstance(source_models, Mapping):
            continue

        model_maps: dict[str, dict[ThinkingLevel, str | None]] = {}
        for model in sorted(provider.models):
            source_model = source_models.get(model)
            if not isinstance(source_model, Mapping) or "reasoning_options" not in source_model:
                continue
            level_map = thinking_level_map_from_reasoning_options(source_model["reasoning_options"])
            if level_map is not None:
                model_maps[model] = level_map
        if model_maps:
            output[provider.name] = model_maps
    return output


def reasoning_maps_document(
    source: object,
    catalog: Iterable[ProviderCatalogEntry],
) -> dict[str, Any]:
    """Return the checked-in JSON document generated from models.dev."""
    return {
        "schema_version": 1,
        "source": MODELS_DEV_URL,
        "providers": reasoning_maps_from_models_dev(source, catalog),
    }


def bundled_reasoning_catalog_overlay() -> dict[str, Any] | None:
    """Load generated metadata as a raw catalog overlay, or fall back silently."""
    try:
        text = (
            files("tau_coding").joinpath(MODELS_DEV_REASONING_RESOURCE).read_text(encoding="utf-8")
        )
        document = json.loads(text)
        return reasoning_catalog_overlay(document)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def reasoning_catalog_overlay(document: object) -> dict[str, Any]:
    """Validate a generated document and convert it to catalog overlay tables."""
    if not isinstance(document, Mapping) or document.get("schema_version") != 1:
        raise ValueError("models.dev reasoning metadata has an unsupported schema")
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        raise ValueError("models.dev reasoning metadata providers must be an object")

    raw_providers: list[dict[str, Any]] = []
    for provider_name, models in providers.items():
        if (
            not isinstance(provider_name, str)
            or not provider_name
            or not isinstance(models, Mapping)
        ):
            raise ValueError("models.dev reasoning provider entries are invalid")
        raw_metadata: dict[str, Any] = {}
        for model, raw_map in models.items():
            if not isinstance(model, str) or not model:
                raise ValueError("models.dev reasoning model names must be non-empty strings")
            level_map = _validated_level_map(raw_map)
            mapped = {level: value for level, value in level_map.items() if value is not None}
            unsupported = [level for level, value in level_map.items() if value is None]
            raw_metadata[model] = {
                **({"thinking_level_map": mapped} if mapped else {}),
                **({"unsupported_thinking_levels": unsupported} if unsupported else {}),
            }
        raw_providers.append({"name": provider_name, "model_metadata": raw_metadata})

    return {"schema_version": 1, "providers": raw_providers}


def _validated_level_map(value: object) -> dict[ThinkingLevel, str | None]:
    if not isinstance(value, Mapping) or set(value) != set(THINKING_LEVELS):
        raise ValueError("models.dev reasoning maps must define every Tau thinking level")
    output: dict[ThinkingLevel, str | None] = {}
    for level in THINKING_LEVELS:
        mapped = value[level]
        if mapped is not None and (not isinstance(mapped, str) or not mapped):
            raise ValueError("models.dev reasoning map values must be strings or null")
        output[level] = mapped
    return output

"""Build-time models.dev catalog generation for Tau's bundled providers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib.resources import files
from typing import Any

from tau_coding.provider_catalog import ProviderCatalogEntry
from tau_coding.thinking import THINKING_LEVELS, ThinkingLevel

MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_CATALOG_RESOURCE = "data/models-dev-catalog.json"

# Tau names providers for users; models.dev names them for its own catalog.
_MODELS_DEV_PROVIDER_KEYS = {
    "kimi-code": "kimi-for-coding",
    "together": "togetherai",
}


def thinking_level_map_from_reasoning_options(
    reasoning_options: object,
) -> dict[ThinkingLevel, str | None] | None:
    """Convert verified models.dev effort values using Pi's exact semantics."""
    if not isinstance(reasoning_options, list):
        return None

    values: set[str | None] = set()
    for option in reasoning_options:
        if not isinstance(option, Mapping) or option.get("type") != "effort":
            continue
        raw_values = option.get("values")
        if isinstance(raw_values, list):
            values.update(value for value in raw_values if isinstance(value, str) or value is None)

    # Pi emits no map when models.dev has no usable effort values. Toggle-only
    # and empty option arrays therefore retain provider/manual behavior.
    selectable = set(THINKING_LEVELS) - {"off"}
    if not values or not (selectable.intersection(values) or "none" in values):
        return None

    return {
        "off": "none" if "none" in values else None,
        **{
            level: level if level in values else None for level in THINKING_LEVELS if level != "off"
        },
    }


def models_dev_catalog_document(
    source: object,
    catalog: Iterable[ProviderCatalogEntry],
    *,
    provider_model_filters: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Generate complete model inventories and metadata for Tau providers.

    Like Pi, models.dev supplies the model rows while provider transport/auth
    configuration remains application-owned. Existing catalog-only rows are
    retained as explicit Tau corrections; source rows that are deprecated or do
    not advertise tool calling are removed unless they are the provider default.
    """
    if not isinstance(source, Mapping):
        raise ValueError("models.dev data must be a JSON object")

    providers: dict[str, Any] = {}
    for provider in sorted(catalog, key=lambda item: item.name):
        source_key = _MODELS_DEV_PROVIDER_KEYS.get(provider.name, provider.name)
        source_provider = source.get(source_key)
        if not isinstance(source_provider, Mapping):
            continue
        source_models = source_provider.get("models")
        if not isinstance(source_models, Mapping):
            continue

        allowed_models = (provider_model_filters or {}).get(provider.name)
        eligible = {
            model_id: model
            for model_id, model in source_models.items()
            if isinstance(model_id, str)
            and isinstance(model, Mapping)
            and (allowed_models is None or model_id in allowed_models)
            and _is_eligible_model(model)
        }
        source_ids = {model_id for model_id in source_models if isinstance(model_id, str)}
        retained_manual = [
            model
            for model in provider.models
            if model not in source_ids or model == provider.default_model
        ]
        existing_eligible = [model for model in provider.models if model in eligible]
        new_models = sorted(set(eligible) - set(provider.models))
        models = list(dict.fromkeys([*existing_eligible, *retained_manual, *new_models]))
        if not models:
            continue

        metadata = {
            model_id: _model_metadata(model_id, model, provider)
            for model_id, model in sorted(eligible.items())
        }
        providers[provider.name] = {
            "models": models,
            "model_metadata": metadata,
        }

    return {
        "schema_version": 1,
        "source": MODELS_DEV_URL,
        "providers": providers,
    }


def bundled_models_dev_catalog_overlay() -> dict[str, Any] | None:
    """Load and validate generated model data, or fall back silently."""
    try:
        text = files("tau_coding").joinpath(MODELS_DEV_CATALOG_RESOURCE).read_text(encoding="utf-8")
        return models_dev_catalog_overlay(json.loads(text))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def models_dev_catalog_overlay(document: object) -> dict[str, Any]:
    """Validate a generated document and return raw partial provider tables."""
    if not isinstance(document, Mapping) or document.get("schema_version") != 1:
        raise ValueError("models.dev catalog has an unsupported schema")
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        raise ValueError("models.dev catalog providers must be an object")

    raw_providers: list[dict[str, Any]] = []
    for provider_name, provider in providers.items():
        if not isinstance(provider_name, str) or not provider_name:
            raise ValueError("models.dev provider names must be non-empty strings")
        if not isinstance(provider, Mapping):
            raise ValueError("models.dev provider entries must be objects")
        models = provider.get("models")
        metadata = provider.get("model_metadata")
        if (
            not isinstance(models, list)
            or not models
            or not all(isinstance(model, str) and model for model in models)
            or len(models) != len(set(models))
            or not isinstance(metadata, Mapping)
        ):
            raise ValueError("models.dev provider model data is invalid")
        if any(model not in models for model in metadata):
            raise ValueError("models.dev metadata references an unknown model")
        raw_providers.append(
            {
                "name": provider_name,
                "models": list(models),
                "model_metadata": dict(metadata),
            }
        )
    return {"schema_version": 1, "providers": raw_providers}


def _is_eligible_model(model: Mapping[object, object]) -> bool:
    if model.get("tool_call") is not True or model.get("status") == "deprecated":
        return False
    modalities = model.get("modalities")
    if not isinstance(modalities, Mapping):
        return True
    inputs = modalities.get("input")
    outputs = modalities.get("output")
    return not (
        isinstance(inputs, list)
        and "text" not in inputs
        or isinstance(outputs, list)
        and "text" not in outputs
    )


def _model_metadata(
    model_id: str,
    model: Mapping[object, object],
    provider: ProviderCatalogEntry,
) -> dict[str, Any]:
    modalities = model.get("modalities")
    inputs = modalities.get("input") if isinstance(modalities, Mapping) else None
    cost = model.get("cost")
    limits = model.get("limit")
    metadata: dict[str, Any] = {
        "name": _string_or(model.get("name"), model_id),
        "reasoning": model.get("reasoning") is True,
        "input": ["text", "image"] if isinstance(inputs, list) and "image" in inputs else ["text"],
        "cost": _cost_rates(cost),
        "context_window": _positive_int_or(
            limits.get("context") if isinstance(limits, Mapping) else None, 4096
        ),
        "max_tokens": _positive_int_or(
            limits.get("output") if isinstance(limits, Mapping) else None, 4096
        ),
    }
    cost_tiers = _cost_tiers(cost)
    if cost_tiers:
        metadata["cost_tiers"] = cost_tiers

    existing = provider.model_metadata.get(model_id)
    supports_direct_effort = (
        provider.kind == "openai-compatible"
        and provider.thinking_parameter in {"reasoning_effort", "reasoning.effort"}
        or provider.kind == "anthropic"
        and existing is not None
        and existing.compat.get("forceAdaptiveThinking") is True
    )
    if supports_direct_effort:
        level_map = thinking_level_map_from_reasoning_options(model.get("reasoning_options"))
        if level_map is not None:
            mapped = {level: value for level, value in level_map.items() if value is not None}
            unsupported = [level for level, value in level_map.items() if value is None]
            if mapped:
                metadata["thinking_level_map"] = mapped
            if unsupported:
                metadata["unsupported_thinking_levels"] = unsupported
    return metadata


def _cost_rates(value: object) -> dict[str, float]:
    cost = value if isinstance(value, Mapping) else {}
    return {
        "input": _number_or_zero(cost.get("input")),
        "output": _number_or_zero(cost.get("output")),
        "cacheRead": _number_or_zero(cost.get("cache_read")),
        "cacheWrite": _number_or_zero(cost.get("cache_write")),
    }


def _cost_tiers(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    raw_tiers = value.get("tiers")
    if not isinstance(raw_tiers, list):
        return []
    tiers: list[tuple[int, dict[str, float]]] = []
    for raw_tier in raw_tiers:
        if not isinstance(raw_tier, Mapping):
            continue
        tier = raw_tier.get("tier")
        if not isinstance(tier, Mapping) or tier.get("type") != "context":
            continue
        size = tier.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            continue
        tiers.append((size, _cost_rates(raw_tier)))
    tiers.sort(key=lambda item: item[0])
    if not tiers:
        return []

    output: list[dict[str, Any]] = [{"max_input_tokens": tiers[0][0], **_cost_rates(value)}]
    for index, (_, rates) in enumerate(tiers):
        next_limit = tiers[index + 1][0] if index + 1 < len(tiers) else None
        output.append(
            {
                **({"max_input_tokens": next_limit} if next_limit is not None else {}),
                **rates,
            }
        )
    return output


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _number_or_zero(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _positive_int_or(value: object, fallback: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else fallback
    )

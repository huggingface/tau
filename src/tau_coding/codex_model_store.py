"""Persisted cache for authenticated OpenAI Codex model catalogs."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from tau_ai.model_catalog import (
    RuntimeInputModality,
    RuntimeModel,
    RuntimeModelCatalog,
    RuntimeThinkingLevel,
)
from tau_ai.model_limits import RuntimeModelLimits
from tau_coding.paths import TauPaths

CODEX_MODEL_STORE_SCHEMA_VERSION = 1


def codex_model_store_path(paths: TauPaths | None = None) -> Path:
    """Return the user-level Codex model-catalog cache path."""
    return (paths or TauPaths()).codex_models_store_path


def cached_codex_model_catalog(
    paths: TauPaths | None = None,
    *,
    account_id: str | None,
) -> RuntimeModelCatalog | None:
    """Return the cached catalog only when it belongs to the active account."""
    if not account_id:
        return None
    try:
        raw = json.loads(codex_model_store_path(paths).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != CODEX_MODEL_STORE_SCHEMA_VERSION:
        return None
    if raw.get("account_id") != account_id:
        return None
    return _catalog_from_json(raw.get("catalog"))


def save_codex_model_catalog(
    catalog: RuntimeModelCatalog,
    *,
    account_id: str,
    paths: TauPaths | None = None,
    now: float | None = None,
) -> Path:
    """Persist one validated, account-scoped Codex model snapshot atomically."""
    normalized_account_id = account_id.strip()
    if not normalized_account_id:
        raise ValueError("Codex account_id must be non-empty")
    path = codex_model_store_path(paths)
    value: dict[str, Any] = {
        "schema_version": CODEX_MODEL_STORE_SCHEMA_VERSION,
        "account_id": normalized_account_id,
        "saved_at": time.time() if now is None else now,
        "catalog": _catalog_to_json(catalog),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(value, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_file.flush()
        temporary_path.replace(path)
    except Exception:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
        raise
    return path


def _catalog_to_json(catalog: RuntimeModelCatalog) -> dict[str, Any]:
    return {"models": [_model_to_json(model) for model in catalog.models]}


def _model_to_json(model: RuntimeModel) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": model.id,
        "name": model.name,
        "input_modalities": list(model.input_modalities),
        "thinking_levels": list(model.thinking_levels),
        "default_thinking_level": model.default_thinking_level,
    }
    if model.limits is not None:
        result["limits"] = {
            "context_window": model.limits.context_window,
            "max_output_tokens": model.limits.max_output_tokens,
            "effective_context_window_percent": model.limits.effective_context_window_percent,
            "auto_compact_token_limit": model.limits.auto_compact_token_limit,
        }
    return result


def _catalog_from_json(value: object) -> RuntimeModelCatalog | None:
    if not isinstance(value, Mapping):
        return None
    models_value = value.get("models")
    if not isinstance(models_value, list) or not models_value:
        return None
    models: list[RuntimeModel] = []
    for item in models_value:
        model = _model_from_json(item)
        if model is None or any(existing.id == model.id for existing in models):
            return None
        models.append(model)
    return RuntimeModelCatalog(tuple(models))


def _model_from_json(value: object) -> RuntimeModel | None:
    if not isinstance(value, Mapping):
        return None
    model_id = value.get("id")
    name = value.get("name")
    modalities_value = value.get("input_modalities")
    thinking_value = value.get("thinking_levels")
    default_value = value.get("default_thinking_level")
    if not isinstance(model_id, str) or not model_id:
        return None
    if name is not None and not isinstance(name, str):
        return None
    if not isinstance(modalities_value, list) or not modalities_value:
        return None
    modalities = tuple(
        cast(RuntimeInputModality, modality)
        for modality in modalities_value
        if modality in {"text", "image"}
    )
    if len(modalities) != len(modalities_value) or len(set(modalities)) != len(modalities):
        return None
    if not isinstance(thinking_value, list):
        return None
    thinking_levels = tuple(
        cast(RuntimeThinkingLevel, level)
        for level in thinking_value
        if level in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
    )
    if len(thinking_levels) != len(thinking_value) or len(set(thinking_levels)) != len(
        thinking_levels
    ):
        return None
    if default_value is not None and default_value not in thinking_levels:
        return None
    limits = _limits_from_json(value.get("limits"))
    if value.get("limits") is not None and limits is None:
        return None
    try:
        return RuntimeModel(
            id=model_id,
            name=name,
            limits=limits,
            input_modalities=modalities,
            thinking_levels=thinking_levels,
            default_thinking_level=cast(RuntimeThinkingLevel | None, default_value),
        )
    except (TypeError, ValueError):
        return None


def _limits_from_json(value: object) -> RuntimeModelLimits | None:
    if not isinstance(value, Mapping):
        return None
    context_window = value.get("context_window")
    max_output_tokens = value.get("max_output_tokens")
    effective_percent = value.get("effective_context_window_percent", 100)
    auto_compact_token_limit = value.get("auto_compact_token_limit")
    if not isinstance(context_window, int) or isinstance(context_window, bool):
        return None
    if max_output_tokens is not None and (
        not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
    ):
        return None
    if not isinstance(effective_percent, int) or isinstance(effective_percent, bool):
        return None
    if auto_compact_token_limit is not None and (
        not isinstance(auto_compact_token_limit, int) or isinstance(auto_compact_token_limit, bool)
    ):
        return None
    try:
        return RuntimeModelLimits(
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            effective_context_window_percent=effective_percent,
            auto_compact_token_limit=auto_compact_token_limit,
        )
    except ValueError:
        return None

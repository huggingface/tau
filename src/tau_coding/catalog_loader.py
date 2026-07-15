"""Load Tau's provider catalog from packaged and user TOML files."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Mapping
from contextlib import suppress
from functools import cache
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
)

from tau_agent.types import JSONValue
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import (
    ModelCatalogMetadata,
    ModelCostTier,
    ModelInput,
    ProviderApi,
    ProviderCatalogEntry,
    ProviderKind,
)
from tau_coding.thinking import ThinkingLevel, ThinkingParameter

CATALOG_SCHEMA_VERSION = 1
USER_CATALOG_FILENAME = "catalog.toml"

# Thinking fields are merged as a group: an overlay that sets thinking_levels
# replaces all four, mirroring _merge_provider_config in provider_config.
_THINKING_FIELDS = ("thinking_levels", "thinking_models", "thinking_default", "thinking_parameter")

_NonEmptyString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]
_NonEmptyStringTuple = Annotated[tuple[_NonEmptyString, ...], Field(min_length=1)]
_PositiveInt = Annotated[StrictInt, Field(gt=0)]
_NonNegativeFloat = Annotated[float, Field(ge=0)]


class CatalogError(ValueError):
    """Raised when a Tau catalog file is invalid."""


class _CatalogCostTier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_input_tokens: _PositiveInt | None = None
    input: _NonNegativeFloat
    output: _NonNegativeFloat
    cacheRead: _NonNegativeFloat
    cacheWrite: _NonNegativeFloat


class _CatalogModelMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _NonEmptyString | None = None
    api: ProviderApi | None = None
    base_url: _NonEmptyString | None = None
    reasoning: StrictBool | None = None
    input: tuple[ModelInput, ...] = ()
    cost: dict[_NonEmptyString, _NonNegativeFloat] | None = None
    cost_tiers: tuple[_CatalogCostTier, ...] = ()
    context_window: _PositiveInt | None = None
    max_tokens: _PositiveInt | None = None
    headers: dict[_NonEmptyString, _NonEmptyString] = {}
    compat: dict[_NonEmptyString, Any] = {}
    thinking_level_map: dict[ThinkingLevel, _NonEmptyString] = {}
    unsupported_thinking_levels: tuple[ThinkingLevel, ...] = ()


class _CatalogProvider(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _NonEmptyString
    display_name: _NonEmptyString
    kind: ProviderKind
    base_url: _NonEmptyString
    api_key_env: _NonEmptyString
    credential_name: _NonEmptyString | None = None
    models: _NonEmptyStringTuple
    default_model: _NonEmptyString
    docs_url: _NonEmptyString
    api: ProviderApi | None = None
    context_windows: dict[_NonEmptyString, _PositiveInt] | None = None
    headers: dict[_NonEmptyString, _NonEmptyString] = {}
    compat: dict[_NonEmptyString, Any] = {}
    model_metadata: dict[_NonEmptyString, _CatalogModelMetadata] = {}
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[_NonEmptyString, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None


class _CatalogFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    providers: tuple[_CatalogProvider, ...] = ()


def builtin_catalog_resource_text() -> str:
    """Return the packaged builtin catalog TOML text."""
    return files("tau_coding").joinpath("data/catalog.toml").read_text(encoding="utf-8")


@cache
def builtin_catalog() -> tuple[ProviderCatalogEntry, ...]:
    """Return Tau's built-in provider catalog from the packaged data file."""
    return _entries_from_raw(_builtin_raw(), source="built-in catalog.toml")


def user_catalog_path(paths: TauPaths | None = None) -> Path:
    """Return the user-level catalog overlay path."""
    return (paths or TauPaths()).home / USER_CATALOG_FILENAME


def effective_catalog(paths: TauPaths | None = None) -> tuple[ProviderCatalogEntry, ...]:
    """Return the builtin catalog with the user's ~/.tau/catalog.toml overlaid."""
    path = user_catalog_path(paths)
    if not path.exists():
        return builtin_catalog()
    overlay_raw = _parse_catalog_text(path.read_text(encoding="utf-8"), source=str(path))
    _validate_catalog_root(overlay_raw, source=str(path))
    merged = _merge_raw_catalogs(_builtin_raw(), overlay_raw)
    return _entries_from_raw(merged, source=str(path))


def save_user_catalog_entries(
    entries: Iterable[ProviderCatalogEntry],
    paths: TauPaths | None = None,
) -> Path:
    """Upsert full provider definitions into the user-level catalog file."""
    path = user_catalog_path(paths)
    if path.exists():
        raw = _parse_catalog_text(path.read_text(encoding="utf-8"), source=str(path))
        _validate_catalog_root(raw, source=str(path))
    else:
        raw = {"schema_version": CATALOG_SCHEMA_VERSION, "providers": []}

    providers = list(_raw_providers(raw))
    provider_indexes = {
        _raw_provider_name(provider): index for index, provider in enumerate(providers)
    }
    for entry in entries:
        raw_provider = _raw_provider_from_entry(entry)
        if entry.name in provider_indexes:
            providers[provider_indexes[entry.name]] = raw_provider
        else:
            provider_indexes[entry.name] = len(providers)
            providers.append(raw_provider)

    updated = {
        "schema_version": raw.get("schema_version", CATALOG_SCHEMA_VERSION),
        "providers": providers,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _catalog_to_toml(updated))
    return path


@cache
def _builtin_raw() -> dict[str, Any]:
    return _parse_catalog_text(builtin_catalog_resource_text(), source="built-in catalog.toml")


def _parse_catalog_text(text: str, *, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{source}: invalid TOML: {error}") from error


def _validate_catalog_root(raw: dict[str, Any], *, source: str) -> None:
    allowed = {"schema_version", "providers"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CatalogError(f"{source}: unknown catalog keys: {', '.join(unknown)}")
    if "schema_version" not in raw:
        raise CatalogError(f"{source}: schema_version is required")
    if raw["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise CatalogError(f"{source}: unsupported schema_version: {raw['schema_version']!r}")
    _raw_providers(raw)


def _merge_raw_catalogs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay provider tables over base ones; overlay values win."""
    base_providers = _raw_providers(base)
    overlay_providers = _raw_providers(overlay)
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for provider in base_providers:
        name = _raw_provider_name(provider)
        by_name[name] = provider
        order.append(name)
    for provider in overlay_providers:
        name = _raw_provider_name(provider)
        if name in by_name:
            by_name[name] = _merge_raw_provider(by_name[name], provider)
        else:
            by_name[name] = provider
            order.append(name)
    return {
        "schema_version": overlay.get("schema_version", base.get("schema_version")),
        "providers": [by_name[name] for name in order],
    }


def _merge_raw_provider(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = {**base, **overlay}
    base_models = base.get("models", [])
    overlay_models = overlay.get("models", [])
    if isinstance(base_models, list) and isinstance(overlay_models, list):
        merged["models"] = list(dict.fromkeys([*overlay_models, *base_models]))
    for key in ("context_windows", "headers", "compat"):
        base_mapping = base.get(key)
        overlay_mapping = overlay.get(key)
        if isinstance(base_mapping, dict) and isinstance(overlay_mapping, dict):
            merged[key] = {**base_mapping, **overlay_mapping}
    base_metadata = base.get("model_metadata")
    overlay_metadata = overlay.get("model_metadata")
    if isinstance(base_metadata, dict) and isinstance(overlay_metadata, dict):
        merged["model_metadata"] = _merge_model_metadata(base_metadata, overlay_metadata)
    if "thinking_levels" in overlay:
        for field in _THINKING_FIELDS:
            if field in overlay:
                merged[field] = overlay[field]
            else:
                merged.pop(field, None)
    return merged


def _merge_model_metadata(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {**base}
    for model, overlay_metadata in overlay.items():
        base_metadata = merged.get(model)
        if isinstance(base_metadata, dict) and isinstance(overlay_metadata, dict):
            next_metadata = {**base_metadata, **overlay_metadata}
            for key in ("headers", "compat", "thinking_level_map"):
                base_mapping = base_metadata.get(key)
                overlay_mapping = overlay_metadata.get(key)
                if isinstance(base_mapping, dict) and isinstance(overlay_mapping, dict):
                    next_metadata[key] = {**base_mapping, **overlay_mapping}
            merged[model] = next_metadata
        else:
            merged[model] = overlay_metadata
    return merged


def _raw_providers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    providers = raw.get("providers", [])
    if not isinstance(providers, list) or not all(isinstance(item, dict) for item in providers):
        raise CatalogError("catalog providers must be an array of tables ([[providers]])")
    return providers


def _raw_provider_name(provider: dict[str, Any]) -> str:
    name = provider.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError("catalog provider entries must have a non-empty string name")
    return name.strip()


def _entries_from_raw(raw: dict[str, Any], *, source: str) -> tuple[ProviderCatalogEntry, ...]:
    try:
        catalog = _CatalogFile.model_validate(raw)
    except ValidationError as error:
        raise CatalogError(f"{source}: {_format_validation_error(raw, error)}") from error
    entries = tuple(_entry_from_provider(provider, source=source) for provider in catalog.providers)
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise CatalogError(f"{source}: duplicate provider names: {', '.join(duplicates)}")
    return entries


def _entry_from_provider(provider: _CatalogProvider, *, source: str) -> ProviderCatalogEntry:
    prefix = f"{source}: providers.{provider.name}"
    if provider.default_model not in provider.models:
        raise CatalogError(f"{prefix}.default_model: {provider.default_model!r} is not in models")
    for model in provider.thinking_models:
        if model not in provider.models:
            raise CatalogError(f"{prefix}.thinking_models: {model!r} is not in models")
    for model in provider.context_windows or {}:
        if model not in provider.models:
            raise CatalogError(f"{prefix}.context_windows: {model!r} is not in models")
    for model in provider.model_metadata:
        if model not in provider.models:
            raise CatalogError(f"{prefix}.model_metadata: {model!r} is not in models")
    if provider.thinking_default is not None and (
        provider.thinking_levels is None
        or provider.thinking_default not in provider.thinking_levels
    ):
        raise CatalogError(
            f"{prefix}.thinking_default: {provider.thinking_default!r} is not in thinking_levels"
        )

    for model, catalog_metadata in provider.model_metadata.items():
        _validate_cost_tiers(
            catalog_metadata.cost_tiers,
            field_name=f"{prefix}.model_metadata.{model}",
        )

    model_metadata = {
        model: _model_metadata_from_provider(metadata)
        for model, metadata in provider.model_metadata.items()
    }
    context_windows = dict(provider.context_windows or {})
    for model, metadata in model_metadata.items():
        if metadata.context_window is not None and model not in context_windows:
            context_windows[model] = metadata.context_window

    return ProviderCatalogEntry(
        name=provider.name,
        display_name=provider.display_name,
        kind=provider.kind,
        base_url=provider.base_url,
        api_key_env=provider.api_key_env,
        credential_name=provider.credential_name,
        models=provider.models,
        default_model=provider.default_model,
        docs_url=provider.docs_url,
        api=provider.api,
        context_windows=context_windows or None,
        headers=dict(provider.headers),
        compat=_json_object(provider.compat, f"{prefix}.compat"),
        model_metadata=model_metadata,
        thinking_levels=provider.thinking_levels,
        thinking_models=provider.thinking_models,
        thinking_default=provider.thinking_default,
        thinking_parameter=provider.thinking_parameter,
    )


def _validate_cost_tiers(
    tiers: tuple[_CatalogCostTier, ...],
    *,
    field_name: str,
) -> None:
    if not tiers:
        return
    if tiers[-1].max_input_tokens is not None:
        raise CatalogError(f"{field_name}.cost_tiers: final tier must omit max_input_tokens")
    previous_limit = 0
    for index, tier in enumerate(tiers[:-1]):
        limit = tier.max_input_tokens
        if limit is None or limit <= previous_limit:
            raise CatalogError(
                f"{field_name}.cost_tiers.{index}.max_input_tokens: "
                "limits must be strictly increasing"
            )
        previous_limit = limit


def _model_metadata_from_provider(metadata: _CatalogModelMetadata) -> ModelCatalogMetadata:
    thinking_level_map: dict[ThinkingLevel, str | None] = dict(metadata.thinking_level_map)
    for level in metadata.unsupported_thinking_levels:
        thinking_level_map[level] = None
    return ModelCatalogMetadata(
        name=metadata.name,
        api=metadata.api,
        base_url=metadata.base_url,
        reasoning=metadata.reasoning,
        input=metadata.input,
        cost=dict(metadata.cost) if metadata.cost else None,
        cost_tiers=tuple(
            ModelCostTier(
                max_input_tokens=tier.max_input_tokens,
                cost={
                    "input": tier.input,
                    "output": tier.output,
                    "cacheRead": tier.cacheRead,
                    "cacheWrite": tier.cacheWrite,
                },
            )
            for tier in metadata.cost_tiers
        ),
        context_window=metadata.context_window,
        max_tokens=metadata.max_tokens,
        headers=dict(metadata.headers),
        compat=_json_object(metadata.compat, "model_metadata.compat"),
        thinking_level_map=thinking_level_map,
    )


def _json_object(value: Mapping[str, Any], field_name: str) -> dict[str, JSONValue]:
    return {key: _json_value(item, f"{field_name}.{key}") for key, item in value.items()}


def _json_value(value: Any, field_name: str) -> JSONValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_value(item, field_name) for item in value]
    if isinstance(value, dict):
        output: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CatalogError(f"{field_name}: object keys must be strings")
            output[key] = _json_value(item, f"{field_name}.{key}")
        return output
    raise CatalogError(f"{field_name}: unsupported value {value!r}")


def _format_validation_error(raw: dict[str, Any], error: ValidationError) -> str:
    messages = []
    for issue in error.errors():
        location = ".".join(_dotted_location(raw, issue["loc"]))
        messages.append(f"{location}: {issue['msg']}")
    return "; ".join(messages)


def _dotted_location(raw: dict[str, Any], location: tuple[int | str, ...]) -> list[str]:
    parts: list[str] = []
    for part in location:
        if parts and parts[-1] == "providers" and isinstance(part, int):
            providers = raw.get("providers")
            name = None
            if isinstance(providers, list) and part < len(providers):
                item = providers[part]
                if isinstance(item, dict):
                    name = item.get("name")
            parts.append(str(name) if isinstance(name, str) else str(part))
        else:
            parts.append(str(part))
    return parts


def _raw_provider_from_entry(entry: ProviderCatalogEntry) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "name": entry.name,
        "display_name": entry.display_name,
        "kind": entry.kind,
        "base_url": entry.base_url,
        "api_key_env": entry.api_key_env,
        "models": list(entry.models),
        "default_model": entry.default_model,
        "docs_url": entry.docs_url,
    }
    if entry.api is not None:
        raw["api"] = entry.api
    if entry.credential_name is not None:
        raw["credential_name"] = entry.credential_name
    if entry.context_windows:
        raw["context_windows"] = dict(entry.context_windows)
    if entry.headers:
        raw["headers"] = dict(entry.headers)
    if entry.compat:
        raw["compat"] = dict(entry.compat)
    if entry.model_metadata:
        raw["model_metadata"] = {
            model: _raw_model_metadata_from_entry(metadata)
            for model, metadata in entry.model_metadata.items()
        }
    if entry.thinking_levels is not None:
        raw["thinking_levels"] = list(entry.thinking_levels)
    if entry.thinking_models:
        raw["thinking_models"] = list(entry.thinking_models)
    if entry.thinking_default is not None:
        raw["thinking_default"] = entry.thinking_default
    if entry.thinking_parameter is not None:
        raw["thinking_parameter"] = entry.thinking_parameter
    return raw


def _raw_model_metadata_from_entry(metadata: ModelCatalogMetadata) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if metadata.name is not None:
        raw["name"] = metadata.name
    if metadata.api is not None:
        raw["api"] = metadata.api
    if metadata.base_url is not None:
        raw["base_url"] = metadata.base_url
    if metadata.reasoning is not None:
        raw["reasoning"] = metadata.reasoning
    if metadata.input:
        raw["input"] = list(metadata.input)
    if metadata.cost:
        raw["cost"] = dict(metadata.cost)
    if metadata.cost_tiers:
        raw["cost_tiers"] = [
            {
                **(
                    {"max_input_tokens": tier.max_input_tokens}
                    if tier.max_input_tokens is not None
                    else {}
                ),
                **tier.cost,
            }
            for tier in metadata.cost_tiers
        ]
    if metadata.context_window is not None:
        raw["context_window"] = metadata.context_window
    if metadata.max_tokens is not None:
        raw["max_tokens"] = metadata.max_tokens
    if metadata.headers:
        raw["headers"] = dict(metadata.headers)
    if metadata.compat:
        raw["compat"] = dict(metadata.compat)
    thinking_level_map = {
        level: value for level, value in metadata.thinking_level_map.items() if value is not None
    }
    unsupported = [level for level, value in metadata.thinking_level_map.items() if value is None]
    if thinking_level_map:
        raw["thinking_level_map"] = thinking_level_map
    if unsupported:
        raw["unsupported_thinking_levels"] = unsupported
    return raw


def _catalog_to_toml(raw: dict[str, Any]) -> str:
    lines = [f"schema_version = {raw.get('schema_version', CATALOG_SCHEMA_VERSION)}", ""]
    for provider in _raw_providers(raw):
        lines.append("[[providers]]")
        for key in (
            "name",
            "display_name",
            "kind",
            "base_url",
            "api_key_env",
            "credential_name",
            "models",
            "default_model",
            "docs_url",
            "api",
            "headers",
            "compat",
            "thinking_levels",
            "thinking_models",
            "thinking_default",
            "thinking_parameter",
        ):
            if key in provider:
                lines.append(f"{key} = {_toml_value(provider[key])}")
        context_windows = provider.get("context_windows")
        if isinstance(context_windows, dict) and context_windows:
            lines.append("")
            lines.append("[providers.context_windows]")
            for model, context_window in context_windows.items():
                lines.append(f"{_toml_key(model)} = {_toml_value(context_window)}")
        model_metadata = provider.get("model_metadata")
        if isinstance(model_metadata, dict) and model_metadata:
            for model, metadata in model_metadata.items():
                if not isinstance(metadata, dict):
                    continue
                lines.append("")
                lines.append(f"[providers.model_metadata.{_toml_key(model)}]")
                for key, value in metadata.items():
                    lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_key(value: str) -> str:
    if value.replace("_", "").replace("-", "").isalnum() and not value[0].isdigit():
        return value
    return json.dumps(value)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{_toml_key(str(key))} = {_toml_value(item)}" for key, item in value.items()
            )
            + " }"
        )
    raise TypeError(f"Unsupported TOML value: {value!r}")


def _atomic_write_text(path: Path, text: str) -> None:
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(text)
            temp_file.flush()
        temp_path.replace(path)
    except Exception:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink()
        raise

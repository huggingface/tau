"""Load Tau's provider catalog from packaged and user TOML files."""

from __future__ import annotations

import tomllib
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import ProviderCatalogEntry, ProviderKind
from tau_coding.thinking import ThinkingLevel, ThinkingParameter

CATALOG_SCHEMA_VERSION = 1
USER_CATALOG_FILENAME = "catalog.toml"

# Thinking fields are merged as a group: an overlay that sets thinking_levels
# replaces all four, mirroring _merge_provider_config in provider_config.
_THINKING_FIELDS = ("thinking_levels", "thinking_models", "thinking_default", "thinking_parameter")


class CatalogError(ValueError):
    """Raised when a Tau catalog file is invalid."""


class _CatalogProvider(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    display_name: str
    kind: ProviderKind
    base_url: str
    api_key_env: str
    credential_name: str
    models: tuple[str, ...]
    default_model: str
    docs_url: str
    context_windows: dict[str, int] | None = None
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
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
    merged = _merge_raw_catalogs(_builtin_raw(), overlay_raw)
    return _entries_from_raw(merged, source=str(path))


@cache
def _builtin_raw() -> dict[str, Any]:
    return _parse_catalog_text(builtin_catalog_resource_text(), source="built-in catalog.toml")


def _parse_catalog_text(text: str, *, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{source}: invalid TOML: {error}") from error


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
    base_windows = base.get("context_windows")
    overlay_windows = overlay.get("context_windows")
    if isinstance(base_windows, dict) and isinstance(overlay_windows, dict):
        merged["context_windows"] = {**base_windows, **overlay_windows}
    if "thinking_levels" in overlay:
        for field in _THINKING_FIELDS:
            if field in overlay:
                merged[field] = overlay[field]
            else:
                merged.pop(field, None)
    return merged


def _raw_providers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    providers = raw.get("providers", [])
    if not isinstance(providers, list) or not all(isinstance(item, dict) for item in providers):
        raise CatalogError("catalog providers must be an array of tables ([[providers]])")
    return providers


def _raw_provider_name(provider: dict[str, Any]) -> str:
    name = provider.get("name")
    if not isinstance(name, str) or not name:
        raise CatalogError("catalog provider entries must have a non-empty string name")
    return name


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
    if provider.thinking_default is not None and (
        provider.thinking_levels is None
        or provider.thinking_default not in provider.thinking_levels
    ):
        raise CatalogError(
            f"{prefix}.thinking_default: {provider.thinking_default!r} is not in thinking_levels"
        )
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
        context_windows=dict(provider.context_windows) if provider.context_windows else None,
        thinking_levels=provider.thinking_levels,
        thinking_models=provider.thinking_models,
        thinking_default=provider.thinking_default,
        thinking_parameter=provider.thinking_parameter,
    )


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

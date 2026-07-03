"""Config-driven provider catalog for Tau login/setup flows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.resources import files
from json import loads
from pathlib import Path
from typing import Any, Literal

from tau_coding.thinking import (
    ThinkingLevel,
    ThinkingParameter,
    normalize_thinking_level,
    normalize_thinking_levels,
)

ProviderKind = Literal["openai-compatible", "anthropic", "openai-codex"]
SUPPORTED_PROVIDER_KINDS: tuple[ProviderKind, ...] = (
    "openai-compatible",
    "anthropic",
    "openai-codex",
)


class ProviderCatalogError(ValueError):
    """Raised when a provider catalog file is invalid."""


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """A provider Tau can present during login or use as a built-in default."""

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

    def to_json(self) -> dict[str, Any]:
        """Serialize this catalog entry to JSON-compatible data."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "kind": self.kind,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "docs_url": self.docs_url,
            "context_windows": dict(self.context_windows or {}),
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
        }


def load_provider_catalog(
    *,
    user_catalog_path: Path | None = None,
    project_catalog_path: Path | None = None,
) -> tuple[ProviderCatalogEntry, ...]:
    """Load built-in provider catalog entries plus optional user/project overrides.

    Later files override entries with the same provider name while preserving
    bundled entries that are not mentioned. This keeps Tau's built-in providers
    config-driven and allows local provider definitions without source changes.
    """
    entries = _catalog_entries_from_json(_bundled_catalog_data(), source="bundled provider catalog")
    for path in (user_catalog_path, project_catalog_path):
        if path is not None and path.exists():
            entries = _merge_catalog_entries(
                entries,
                _catalog_entries_from_json(
                    _json_file_data(path),
                    source=str(path),
                    existing={entry.name: entry for entry in entries},
                ),
            )
    return entries


def builtin_provider_entry(name: str) -> ProviderCatalogEntry | None:
    """Return a bundled catalog entry by provider name."""
    return provider_catalog_entry(name, BUILTIN_PROVIDER_CATALOG)


def provider_catalog_entry(
    name: str,
    catalog: tuple[ProviderCatalogEntry, ...],
) -> ProviderCatalogEntry | None:
    """Return a catalog entry by provider name."""
    for entry in catalog:
        if entry.name == name:
            return entry
    return None


def provider_catalog_from_paths(
    paths_home: Path, cwd: Path | None = None
) -> tuple[ProviderCatalogEntry, ...]:
    """Load bundled catalog with user and optional project catalog files."""
    return load_provider_catalog(
        user_catalog_path=paths_home / "provider-catalog.json",
        project_catalog_path=(cwd / ".tau" / "provider-catalog.json") if cwd else None,
    )


def _merge_catalog_entries(
    existing: tuple[ProviderCatalogEntry, ...],
    incoming: tuple[ProviderCatalogEntry, ...],
) -> tuple[ProviderCatalogEntry, ...]:
    by_name = {entry.name: entry for entry in existing}
    for entry in incoming:
        by_name[entry.name] = entry
    return tuple(by_name[name] for name in sorted(by_name))


def _bundled_catalog_data() -> dict[str, Any]:
    resource = files("tau_coding.data").joinpath("provider_catalog.json")
    data = loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProviderCatalogError("Bundled provider catalog must be a JSON object")
    return data


def _json_file_data(path: Path) -> dict[str, Any]:
    data = loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProviderCatalogError(f"Provider catalog must be a JSON object: {path}")
    return data


def _catalog_entries_from_json(
    data: dict[str, Any],
    *,
    source: str,
    existing: dict[str, ProviderCatalogEntry] | None = None,
) -> tuple[ProviderCatalogEntry, ...]:
    providers_data = data.get("providers")
    if not isinstance(providers_data, list) or not providers_data:
        raise ProviderCatalogError(f"Provider catalog must include providers: {source}")
    entries = tuple(
        _catalog_entry_from_json(item, source=source, existing=existing) for item in providers_data
    )
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ProviderCatalogError(f"Provider catalog names must be unique: {source}")
    return entries


def _catalog_entry_from_json(
    data: object,
    *,
    source: str,
    existing: dict[str, ProviderCatalogEntry] | None,
) -> ProviderCatalogEntry:
    if not isinstance(data, dict):
        raise ProviderCatalogError(f"Provider catalog entries must be objects: {source}")
    name = _string(data.get("name"), "providers[].name", source=source)
    base = existing.get(name) if existing else None
    kind = _kind(data.get("kind", data.get("type", base.kind if base else None)), source=source)
    display_name = _string(
        data.get("display_name", base.display_name if base else name),
        f"providers[{name}].display_name",
        source=source,
    )
    base_url = _string(
        data.get("base_url", base.base_url if base else None),
        f"providers[{name}].base_url",
        source=source,
    ).rstrip("/")
    api_key_env = _string(
        data.get("api_key_env", base.api_key_env if base else None),
        f"providers[{name}].api_key_env",
        source=source,
    )
    credential_name = _optional_string(
        data.get("credential_name", base.credential_name if base else None),
        f"providers[{name}].credential_name",
        source=source,
    )
    docs_url = _string(
        data.get("docs_url", base.docs_url if base else ""),
        f"providers[{name}].docs_url",
        source=source,
    )
    models = _string_tuple(
        data.get("models", base.models if base else None),
        f"providers[{name}].models",
        source=source,
    )
    default_model = _string(
        data.get("default_model", base.default_model if base else None),
        f"providers[{name}].default_model",
        source=source,
    )
    if default_model not in models:
        models = (*models, default_model)
    context_windows = _int_dict(
        data.get("context_windows", base.context_windows if base else {}),
        f"providers[{name}].context_windows",
        source=source,
    )
    thinking_levels = _optional_thinking_levels(
        data.get("thinking_levels", base.thinking_levels if base else None),
        f"providers[{name}].thinking_levels",
        source=source,
    )
    thinking_models = _string_tuple(
        data.get("thinking_models", base.thinking_models if base else ()),
        f"providers[{name}].thinking_models",
        source=source,
        allow_empty=True,
    )
    thinking_default = _optional_thinking_level(
        data.get("thinking_default", base.thinking_default if base else None),
        f"providers[{name}].thinking_default",
        source=source,
    )
    thinking_parameter = _optional_thinking_parameter(
        data.get("thinking_parameter", base.thinking_parameter if base else None),
        f"providers[{name}].thinking_parameter",
        source=source,
    )
    entry = ProviderCatalogEntry(
        name=name,
        display_name=display_name,
        kind=kind,
        base_url=base_url,
        api_key_env=api_key_env,
        credential_name=credential_name or name,
        models=models,
        default_model=default_model,
        docs_url=docs_url,
        context_windows=context_windows,
        thinking_levels=thinking_levels,
        thinking_models=thinking_models,
        thinking_default=thinking_default,
        thinking_parameter=thinking_parameter,
    )
    if thinking_levels is None:
        return replace(
            entry,
            thinking_models=(),
            thinking_default=None,
            thinking_parameter=None,
        )
    if thinking_default is not None and thinking_default not in thinking_levels:
        raise ProviderCatalogError(f"thinking_default must be in thinking_levels: {source}")
    return entry


def _kind(value: object, *, source: str) -> ProviderKind:
    if value not in SUPPORTED_PROVIDER_KINDS:
        raise ProviderCatalogError(f"Unsupported provider kind in catalog: {value!r} ({source})")
    return value


def _string(value: object, field_name: str, *, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderCatalogError(
            f"Provider catalog field must be a non-empty string: {field_name} ({source})"
        )
    return value.strip()


def _optional_string(value: object, field_name: str, *, source: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name, source=source)


def _string_tuple(
    value: object,
    field_name: str,
    *,
    source: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not value and not allow_empty):
        raise ProviderCatalogError(
            f"Provider catalog field must be a string list: {field_name} ({source})"
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProviderCatalogError(
            f"Provider catalog field must be a string list: {field_name} ({source})"
        )
    return tuple(dict.fromkeys(item.strip() for item in value))


def _int_dict(value: object, field_name: str, *, source: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderCatalogError(
            f"Provider catalog field must be an integer object: {field_name} ({source})"
        )
    parsed: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, int) or item <= 0:
            raise ProviderCatalogError(
                f"Provider catalog field must be an integer object: {field_name} ({source})"
            )
        parsed[key] = item
    return parsed


def _optional_thinking_levels(
    value: object,
    field_name: str,
    *,
    source: str,
) -> tuple[ThinkingLevel, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ProviderCatalogError(
            f"Provider catalog field must be a thinking mode list: {field_name} ({source})"
        )
    try:
        return normalize_thinking_levels(value)
    except ValueError as exc:
        raise ProviderCatalogError(f"{field_name}: {exc} ({source})") from exc


def _optional_thinking_level(
    value: object, field_name: str, *, source: str
) -> ThinkingLevel | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderCatalogError(
            f"Provider catalog field must be a thinking mode: {field_name} ({source})"
        )
    try:
        return normalize_thinking_level(value)
    except ValueError as exc:
        raise ProviderCatalogError(f"{field_name}: {exc} ({source})") from exc


def _optional_thinking_parameter(
    value: object,
    field_name: str,
    *,
    source: str,
) -> ThinkingParameter | None:
    if value is None:
        return None
    if value == "reasoning_effort":
        return "reasoning_effort"
    if value == "reasoning.effort":
        return "reasoning.effort"
    if value == "anthropic.thinking":
        return "anthropic.thinking"
    raise ProviderCatalogError(
        f"Provider catalog field must be a thinking parameter: {field_name} ({source})"
    )


BUILTIN_PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = load_provider_catalog()

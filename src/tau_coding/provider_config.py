"""Durable provider configuration for Tau coding sessions."""

from __future__ import annotations

import tomllib
from contextlib import suppress
from dataclasses import dataclass, field, replace
from json import dumps, loads
from os import environ
from pathlib import Path
from shutil import copy2
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from tau_ai import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_CODEX_BASE_URL,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    AnthropicConfig,
    OpenAICompatibleConfig,
)
from tau_ai.env import DEFAULT_OPENAI_COMPATIBLE_BASE_URL
from tau_coding.catalog_loader import effective_catalog
from tau_coding.credentials import FileCredentialStore, credentials_path
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    ProviderCatalogEntry,
    ProviderKind,
)
from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    ThinkingLevel,
    ThinkingParameter,
    anthropic_thinking_budget_for_level,
    normalize_thinking_level,
    normalize_thinking_levels,
    reasoning_effort_for_level,
)

DEFAULT_PROVIDER_NAME = "openai"
DEFAULT_MODEL = "gpt-5.5"


class ProviderConfigError(ValueError):
    """Raised when Tau provider configuration is invalid."""


class CredentialReader(Protocol):
    """Credential lookup used while building runtime provider config."""

    def get(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProviderConfig:
    """Durable settings for one OpenAI-compatible provider."""

    name: str
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    api_key_env: str = "OPENAI_API_KEY"
    credential_name: str | None = None
    models: tuple[str, ...] = (DEFAULT_MODEL,)
    default_model: str = DEFAULT_MODEL
    context_windows: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None

    def __post_init__(self) -> None:
        _validate_provider_numbers(
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
        )
        _validate_context_windows(self.context_windows)
        _validate_thinking_config(
            thinking_levels=self.thinking_levels,
            thinking_models=self.thinking_models,
            thinking_default=self.thinking_default,
            thinking_parameter=self.thinking_parameter,
        )

    def to_json(self) -> dict[str, Any]:
        """Serialize this provider config to JSON-compatible data."""
        return {
            "name": self.name,
            "type": "openai-compatible",
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "context_windows": dict(self.context_windows),
            "headers": dict(self.headers),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_retry_delay_seconds": self.max_retry_delay_seconds,
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
        }


@dataclass(frozen=True, slots=True)
class AnthropicProviderConfig:
    """Durable settings for Anthropic's Messages API."""

    name: str = "anthropic"
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    api_key_env: str = "ANTHROPIC_API_KEY"
    credential_name: str | None = "anthropic"
    models: tuple[str, ...] = ("claude-sonnet-4-6",)
    default_model: str = "claude-sonnet-4-6"
    context_windows: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None

    def __post_init__(self) -> None:
        _validate_provider_numbers(
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
        )
        _validate_context_windows(self.context_windows)
        _validate_thinking_config(
            thinking_levels=self.thinking_levels,
            thinking_models=self.thinking_models,
            thinking_default=self.thinking_default,
            thinking_parameter=self.thinking_parameter,
        )

    def to_json(self) -> dict[str, Any]:
        """Serialize this provider config to JSON-compatible data."""
        return {
            "name": self.name,
            "type": "anthropic",
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "context_windows": dict(self.context_windows),
            "headers": dict(self.headers),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_retry_delay_seconds": self.max_retry_delay_seconds,
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
        }


@dataclass(frozen=True, slots=True)
class OpenAICodexProviderConfig:
    """Durable settings for OpenAI Codex subscription OAuth."""

    name: str = "openai-codex"
    base_url: str = DEFAULT_OPENAI_CODEX_BASE_URL
    api_key_env: str = "OPENAI_CODEX_ACCESS_TOKEN"
    credential_name: str | None = "openai-codex"
    models: tuple[str, ...] = (
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
    )
    default_model: str = "gpt-5.5"
    context_windows: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None

    def __post_init__(self) -> None:
        _validate_provider_numbers(
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
        )
        _validate_context_windows(self.context_windows)
        _validate_thinking_config(
            thinking_levels=self.thinking_levels,
            thinking_models=self.thinking_models,
            thinking_default=self.thinking_default,
            thinking_parameter=self.thinking_parameter,
        )

    def to_json(self) -> dict[str, Any]:
        """Serialize this provider config to JSON-compatible data."""
        return {
            "name": self.name,
            "type": "openai-codex",
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "context_windows": dict(self.context_windows),
            "headers": dict(self.headers),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_retry_delay_seconds": self.max_retry_delay_seconds,
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
        }


type ProviderConfig = (
    OpenAICompatibleProviderConfig | AnthropicProviderConfig | OpenAICodexProviderConfig
)


@dataclass(frozen=True, slots=True)
class ScopedModelConfig:
    """A provider/model pair enabled for quick model cycling."""

    provider: str
    model: str

    def to_json(self) -> dict[str, str]:
        """Serialize this scoped model reference."""
        return {"provider": self.provider, "model": self.model}


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Tau provider settings loaded from Tau home."""

    default_provider: str = DEFAULT_PROVIDER_NAME
    providers: tuple[ProviderConfig, ...] = field(
        default_factory=lambda: builtin_provider_configs()
    )
    scoped_models: tuple[ScopedModelConfig, ...] = ()

    def get_provider(self, name: str | None = None) -> ProviderConfig:
        """Return a configured provider by name."""
        target = name or self.default_provider
        for provider in self.providers:
            if provider.name == target:
                return provider
        raise ProviderConfigError(f"Unknown provider: {target}")

    def to_json(self) -> dict[str, Any]:
        """Serialize these settings to JSON-compatible data."""
        return {
            "default_provider": self.default_provider,
            "providers": [provider.to_json() for provider in self.providers],
            "scoped_models": [model.to_json() for model in self.scoped_models],
        }


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """Resolved provider/model selection for a Tau run."""

    provider: ProviderConfig
    model: str


def builtin_provider_configs() -> tuple[ProviderConfig, ...]:
    """Return Tau's built-in provider configs."""
    return tuple(
        provider_config_from_catalog_entry(entry.name) for entry in BUILTIN_PROVIDER_CATALOG
    )


def provider_config_from_catalog_entry(name: str) -> ProviderConfig:
    """Create a durable provider config from a built-in catalog entry."""
    for entry in BUILTIN_PROVIDER_CATALOG:
        if entry.name == name:
            return provider_config_from_entry(entry)
    raise ProviderConfigError(f"Unknown built-in provider: {name}")


def provider_config_from_entry(entry: ProviderCatalogEntry) -> ProviderConfig:
    """Create a durable provider config from a catalog entry."""
    context_windows = dict(entry.context_windows or {})
    if entry.kind == "anthropic":
        return AnthropicProviderConfig(
            name=entry.name,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            credential_name=entry.credential_name,
            models=entry.models,
            default_model=entry.default_model,
            context_windows=context_windows,
            thinking_levels=entry.thinking_levels,
            thinking_models=entry.thinking_models,
            thinking_default=entry.thinking_default,
            thinking_parameter=entry.thinking_parameter,
        )
    if entry.kind == "openai-codex":
        return OpenAICodexProviderConfig(
            name=entry.name,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            credential_name=entry.credential_name,
            models=entry.models,
            default_model=entry.default_model,
            context_windows=context_windows,
            thinking_levels=entry.thinking_levels,
            thinking_models=entry.thinking_models,
            thinking_default=entry.thinking_default,
            thinking_parameter=entry.thinking_parameter,
        )
    return OpenAICompatibleProviderConfig(
        name=entry.name,
        base_url=entry.base_url,
        api_key_env=entry.api_key_env,
        credential_name=entry.credential_name,
        models=entry.models,
        default_model=entry.default_model,
        context_windows=context_windows,
        thinking_levels=entry.thinking_levels,
        thinking_models=entry.thinking_models,
        thinking_default=entry.thinking_default,
        thinking_parameter=entry.thinking_parameter,
    )


def default_openai_provider_config() -> OpenAICompatibleProviderConfig:
    """Return Tau's default OpenAI-compatible provider entry."""
    provider = provider_config_from_catalog_entry(DEFAULT_PROVIDER_NAME)
    if not isinstance(provider, OpenAICompatibleProviderConfig):
        raise AssertionError("default OpenAI provider must be OpenAI-compatible")
    return provider


def provider_settings_path(paths: TauPaths | None = None) -> Path:
    """Return the durable provider settings path."""
    return (paths or TauPaths()).home / "providers.json"


def load_provider_settings(paths: TauPaths | None = None) -> ProviderSettings:
    """Load durable provider settings, falling back to env-compatible defaults."""
    path = provider_settings_path(paths)
    if not path.exists():
        return ProviderSettings(providers=_effective_provider_configs(paths))
    raw = loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProviderConfigError("Provider settings must be a JSON object")
    return _with_builtin_catalog_models(provider_settings_from_json(raw, paths=paths), paths=paths)


def save_provider_settings(settings: ProviderSettings, paths: TauPaths | None = None) -> Path:
    """Write durable provider settings and return the path."""
    path = provider_settings_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with suppress(OSError):
            copy2(path, path.with_suffix(path.suffix + ".bak"))
    _atomic_write_text(path, dumps(settings.to_json(), indent=2, sort_keys=True) + "\n")
    return path


def save_default_provider_model(
    *,
    provider_name: str,
    model: str,
    paths: TauPaths | None = None,
    fallback_settings: ProviderSettings | None = None,
) -> ProviderSettings:
    """Reload settings, persist one default provider/model change, and return them."""
    settings = _load_provider_settings_for_write(paths, fallback_settings=fallback_settings)
    updated = set_default_provider_model(settings, provider_name=provider_name, model=model)
    save_provider_settings(updated, paths)
    return updated


def toggle_saved_scoped_model(
    *,
    provider_name: str,
    model: str,
    paths: TauPaths | None = None,
    fallback_settings: ProviderSettings | None = None,
) -> ProviderSettings:
    """Reload settings, toggle one scoped model, persist them, and return them."""
    settings = _load_provider_settings_for_write(paths, fallback_settings=fallback_settings)
    provider = settings.get_provider(provider_name)
    if model not in provider.models:
        raise ProviderConfigError(f"Model is not configured: {provider_name}:{model}")

    existing = list(settings.scoped_models)
    target = ScopedModelConfig(provider=provider_name, model=model)
    if target in existing:
        existing = [item for item in existing if item != target]
    else:
        existing.append(target)
    updated = replace(settings, scoped_models=tuple(existing))
    save_provider_settings(updated, paths)
    return updated


def upsert_saved_provider(
    provider: ProviderConfig,
    *,
    set_default: bool = False,
    paths: TauPaths | None = None,
    fallback_settings: ProviderSettings | None = None,
) -> ProviderSettings:
    """Reload settings, upsert one provider entry, persist them, and return them."""
    settings = _load_provider_settings_for_write(paths, fallback_settings=fallback_settings)
    updated = upsert_provider(settings, provider, set_default=set_default)
    save_provider_settings(updated, paths)
    return updated


def _load_provider_settings_for_write(
    paths: TauPaths | None,
    *,
    fallback_settings: ProviderSettings | None = None,
) -> ProviderSettings:
    """Load the latest on-disk settings, falling back only when no file exists."""
    if provider_settings_path(paths).exists():
        return load_provider_settings(paths)
    if fallback_settings is not None:
        return fallback_settings
    return load_provider_settings(paths)


def set_default_provider_model(
    settings: ProviderSettings,
    *,
    provider_name: str,
    model: str,
) -> ProviderSettings:
    """Return settings with the default provider/model preference updated."""
    provider = settings.get_provider(provider_name)
    validate_provider_model(provider, model)
    updated_provider = replace(provider, default_model=model)
    providers = tuple(
        updated_provider if item.name == provider_name else item for item in settings.providers
    )
    return ProviderSettings(
        default_provider=provider_name,
        providers=providers,
        scoped_models=settings.scoped_models,
    )


def upsert_openai_compatible_provider(
    settings: ProviderSettings,
    provider: OpenAICompatibleProviderConfig,
    *,
    set_default: bool = False,
) -> ProviderSettings:
    """Return settings with an OpenAI-compatible provider added or replaced."""
    return upsert_provider(settings, provider, set_default=set_default)


def upsert_provider(
    settings: ProviderSettings,
    provider: ProviderConfig,
    *,
    set_default: bool = False,
) -> ProviderSettings:
    """Return settings with a provider added or replaced."""
    providers_by_name = {item.name: item for item in settings.providers}
    builtin_names = {entry.name for entry in BUILTIN_PROVIDER_CATALOG}
    if provider.name in providers_by_name and provider.name in builtin_names:
        provider = _merge_provider_config(providers_by_name[provider.name], provider)
    providers_by_name[provider.name] = provider
    default_provider = provider.name if set_default else settings.default_provider
    providers = tuple(providers_by_name[name] for name in sorted(providers_by_name))
    updated = ProviderSettings(
        default_provider=default_provider,
        providers=providers,
        scoped_models=settings.scoped_models,
    )
    updated.get_provider(default_provider)
    return updated


def _with_builtin_catalog_models(
    settings: ProviderSettings,
    *,
    paths: TauPaths | None = None,
) -> ProviderSettings:
    """Return settings with the current provider catalog merged in."""
    catalog_configs = {config.name: config for config in _effective_provider_configs(paths)}
    providers = tuple(
        _merge_provider_config(provider, catalog_configs[provider.name])
        if provider.name in catalog_configs
        else provider
        for provider in settings.providers
    )
    providers = _append_catalog_providers(providers, catalog_configs, paths=paths)
    default_provider = settings.default_provider
    if default_provider not in {provider.name for provider in providers}:
        default_provider = providers[0].name if providers else DEFAULT_PROVIDER_NAME
    return ProviderSettings(
        default_provider=default_provider,
        providers=providers,
        scoped_models=settings.scoped_models,
    )


def _effective_provider_configs(paths: TauPaths | None = None) -> tuple[ProviderConfig, ...]:
    """Return provider configs for the effective catalog (builtin + user overlay)."""
    return tuple(provider_config_from_entry(entry) for entry in effective_catalog(paths))


def _append_catalog_providers(
    providers: tuple[ProviderConfig, ...],
    catalog_configs: dict[str, ProviderConfig],
    *,
    paths: TauPaths | None,
) -> tuple[ProviderConfig, ...]:
    """Append catalog providers: user-catalog ones always, builtins when credentialed."""
    credential_store = FileCredentialStore(credentials_path(paths) if paths else None)
    builtin_names = {entry.name for entry in BUILTIN_PROVIDER_CATALOG}
    provider_names = {provider.name for provider in providers}
    appended = list(providers)
    for provider in catalog_configs.values():
        if provider.name in provider_names:
            continue
        if provider.name not in builtin_names or provider_has_usable_credentials(
            provider, credential_reader=credential_store
        ):
            appended.append(provider)
            provider_names.add(provider.name)
    return tuple(appended)


def _merge_provider_config(existing: ProviderConfig, incoming: ProviderConfig) -> ProviderConfig:
    """Merge a replacement provider config without losing local customizations."""
    if type(existing) is not type(incoming):
        return incoming
    if isinstance(incoming, OpenAICodexProviderConfig):
        models = incoming.models
    else:
        models = _unique_strings((*incoming.models, *existing.models))
    default_model = (
        existing.default_model if existing.default_model in models else incoming.default_model
    )
    headers = {**existing.headers, **incoming.headers}
    context_windows = {**incoming.context_windows, **existing.context_windows}
    thinking_levels = (
        existing.thinking_levels
        if existing.thinking_levels is not None
        else incoming.thinking_levels
    )
    thinking_models = (
        existing.thinking_models
        if existing.thinking_levels is not None
        else incoming.thinking_models
    )
    thinking_default = (
        existing.thinking_default
        if existing.thinking_levels is not None
        else incoming.thinking_default
    )
    thinking_parameter = (
        existing.thinking_parameter
        if existing.thinking_levels is not None
        else incoming.thinking_parameter
    )
    return replace(
        incoming,
        models=models,
        default_model=default_model,
        headers=headers,
        context_windows=context_windows,
        thinking_levels=thinking_levels,
        thinking_models=thinking_models,
        thinking_default=thinking_default,
        thinking_parameter=thinking_parameter,
    )


def _unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return values with duplicates removed while preserving order."""
    return tuple(dict.fromkeys(values))


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text through a sibling temp file and atomically replace the target."""
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


def provider_settings_from_json(
    data: dict[str, Any],
    *,
    paths: TauPaths | None = None,
) -> ProviderSettings:
    """Parse provider settings from JSON-compatible data.

    Tau briefly wrote a compact ``provider_preferences`` shape that referenced
    provider definitions from the built-in/user catalog. Accept it here so those
    installs continue to start, then future saves will rewrite full
    ``providers`` entries.
    """
    default_provider = _string(data.get("default_provider"), "default_provider")
    scoped_models = _scoped_models_from_json(data.get("scoped_models"))
    if "provider_preferences" in data:
        return ProviderSettings(
            default_provider=default_provider,
            providers=_providers_with_preferences(data.get("provider_preferences"), paths=paths),
            scoped_models=scoped_models,
        )

    providers_data = data.get("providers")
    if not isinstance(providers_data, list) or not providers_data:
        raise ProviderConfigError("Provider settings must include at least one provider")
    providers = tuple(_provider_from_json(item) for item in providers_data)
    names = [provider.name for provider in providers]
    if len(set(names)) != len(names):
        raise ProviderConfigError("Provider names must be unique")
    return ProviderSettings(
        default_provider=default_provider,
        providers=providers,
        scoped_models=scoped_models,
    )


def _providers_with_preferences(
    value: object,
    *,
    paths: TauPaths | None,
) -> tuple[ProviderConfig, ...]:
    """Build provider configs from the legacy provider_preferences map."""
    if not isinstance(value, dict):
        raise ProviderConfigError("Provider settings field must be an object: provider_preferences")
    catalog_configs = {provider.name: provider for provider in _effective_provider_configs(paths)}
    providers: list[ProviderConfig] = []
    seen: set[str] = set()
    for name, preference_data in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ProviderConfigError("Provider preference names must be non-empty strings")
        provider_name = name.strip()
        if provider_name in seen:
            raise ProviderConfigError("Provider preference names must be unique")
        provider = catalog_configs.get(provider_name)
        if provider is None:
            raise ProviderConfigError(f"Unknown provider preference: {provider_name}")
        providers.append(_apply_provider_preference(provider, preference_data))
        seen.add(provider_name)
    return tuple(providers)


def _apply_provider_preference(provider: ProviderConfig, value: object) -> ProviderConfig:
    """Apply saved runtime preferences to a catalog provider definition."""
    if not isinstance(value, dict):
        raise ProviderConfigError("Provider preference entries must be objects")
    allowed = {
        "default_model",
        "headers",
        "timeout_seconds",
        "max_retries",
        "max_retry_delay_seconds",
        "thinking_defaults",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProviderConfigError(
            f"Unknown provider preference fields for {provider.name}: {', '.join(unknown)}"
        )
    default_model = (
        _string(value.get("default_model"), f"provider_preferences.{provider.name}.default_model")
        if "default_model" in value
        else provider.default_model
    )
    models = (
        provider.models if default_model in provider.models else (*provider.models, default_model)
    )
    headers = (
        _string_dict(value.get("headers"), f"provider_preferences.{provider.name}.headers")
        if "headers" in value
        else provider.headers
    )
    timeout_seconds = (
        _positive_float(
            value.get("timeout_seconds"),
            f"provider_preferences.{provider.name}.timeout_seconds",
        )
        if "timeout_seconds" in value
        else provider.timeout_seconds
    )
    max_retries = (
        _non_negative_int(
            value.get("max_retries"), f"provider_preferences.{provider.name}.max_retries"
        )
        if "max_retries" in value
        else provider.max_retries
    )
    max_retry_delay_seconds = (
        _non_negative_float(
            value.get("max_retry_delay_seconds"),
            f"provider_preferences.{provider.name}.max_retry_delay_seconds",
        )
        if "max_retry_delay_seconds" in value
        else provider.max_retry_delay_seconds
    )
    thinking_default = provider.thinking_default
    if "thinking_defaults" in value:
        thinking_default = _thinking_default_preference(
            value.get("thinking_defaults"),
            provider=provider,
            default_model=default_model,
        )
    return replace(
        provider,
        models=models,
        default_model=default_model,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
        thinking_default=thinking_default,
    )


def _thinking_default_preference(
    value: object,
    *,
    provider: ProviderConfig,
    default_model: str,
) -> ThinkingLevel | None:
    """Return the saved thinking default for the provider default model, if any."""
    if not isinstance(value, dict):
        raise ProviderConfigError("Provider preference thinking_defaults must be an object")
    if default_model not in value:
        return provider.thinking_default
    return normalize_thinking_level(
        _string(
            value.get(default_model),
            f"provider_preferences.{provider.name}.thinking_defaults.{default_model}",
        )
    )


def _effective_provider_configs(paths: TauPaths | None = None) -> tuple[ProviderConfig, ...]:
    """Return built-in provider configs plus user catalog.toml definitions, if present."""
    providers = list(builtin_provider_configs())
    provider_names = {provider.name for provider in providers}
    catalog_path = (paths or TauPaths()).home / "catalog.toml"
    if not catalog_path.exists():
        return tuple(providers)
    raw = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    entries = raw.get("providers", [])
    if not isinstance(entries, list):
        raise ProviderConfigError("Provider catalog field must be a list: providers")
    for item in entries:
        if not isinstance(item, dict):
            raise ProviderConfigError("Provider catalog entries must be objects")
        entry = _catalog_entry_from_json(item)
        provider = provider_config_from_entry(entry)
        if provider.name not in provider_names:
            providers.append(provider)
            provider_names.add(provider.name)
    return tuple(providers)


def _catalog_entry_from_json(data: dict[str, Any]) -> ProviderCatalogEntry:
    name = _string(data.get("name"), "catalog.providers.name")
    kind = data.get("kind")
    if kind not in {"openai-compatible", "anthropic", "openai-codex"}:
        raise ProviderConfigError(f"Provider catalog entry has unknown kind: {kind!r}")
    models = _string_tuple(data.get("models"), f"catalog.providers[{name}].models")
    default_model = _string(data.get("default_model"), f"catalog.providers[{name}].default_model")
    credential_name = data.get("credential_name")
    if credential_name is not None:
        credential_name = _string(credential_name, f"catalog.providers[{name}].credential_name")
    return ProviderCatalogEntry(
        name=name,
        display_name=_string(
            data.get("display_name", name), f"catalog.providers[{name}].display_name"
        ),
        kind=kind,
        base_url=_string(data.get("base_url"), f"catalog.providers[{name}].base_url"),
        api_key_env=_string(data.get("api_key_env"), f"catalog.providers[{name}].api_key_env"),
        credential_name=credential_name,
        models=models,
        default_model=default_model,
        docs_url=_string(
            data.get("docs_url", data.get("base_url")), f"catalog.providers[{name}].docs_url"
        ),
        context_windows=_context_window_dict(
            data.get("context_windows", {}), f"catalog.providers[{name}].context_windows"
        ),
        thinking_levels=_optional_thinking_levels(
            data.get("thinking_levels"), f"catalog.providers[{name}].thinking_levels"
        ),
        thinking_models=_optional_string_tuple(
            data.get("thinking_models"), f"catalog.providers[{name}].thinking_models"
        ),
        thinking_default=_optional_thinking_level(
            data.get("thinking_default"), f"catalog.providers[{name}].thinking_default"
        ),
        thinking_parameter=_optional_thinking_parameter(
            data.get("thinking_parameter"), f"catalog.providers[{name}].thinking_parameter"
        ),
    )


def _scoped_models_from_json(value: object) -> tuple[ScopedModelConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProviderConfigError("Provider settings field must be a list: scoped_models")
    scoped: list[ScopedModelConfig] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ProviderConfigError("Provider scoped_models entries must be objects")
        provider = _string(item.get("provider"), "scoped_models.provider")
        model = _string(item.get("model"), "scoped_models.model")
        key = (provider, model)
        if key not in seen:
            scoped.append(ScopedModelConfig(provider=provider, model=model))
            seen.add(key)
    return tuple(scoped)


def resolve_provider_selection(
    settings: ProviderSettings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
) -> ProviderSelection:
    """Resolve the provider and model for a run."""
    provider = settings.get_provider(provider_name)
    selected_model = model or provider.default_model
    if not selected_model:
        raise ProviderConfigError(f"Provider {provider.name} does not define a default model")
    validate_provider_model(provider, selected_model)
    return ProviderSelection(provider=provider, model=selected_model)


def validate_provider_model(provider: ProviderConfig, model: str) -> None:
    """Raise when ``model`` is not declared by ``provider``."""
    if model in provider.models:
        return
    available = ", ".join(sorted(provider.models)) or "none"
    raise ProviderConfigError(
        f"Model is not configured for provider {provider.name}: {model}. "
        f"Available models: {available}"
    )


def provider_thinking_levels(
    provider: ProviderConfig,
    *,
    model: str | None = None,
) -> tuple[ThinkingLevel, ...]:
    """Return thinking levels supported by a provider/model pair."""
    if provider.thinking_levels is None:
        return ()
    selected_model = model or provider.default_model
    if provider.thinking_models and selected_model not in provider.thinking_models:
        return ()
    return provider.thinking_levels


def provider_thinking_unavailable_reason(
    provider: ProviderConfig,
    *,
    model: str | None = None,
) -> str | None:
    """Explain why a provider/model pair has no configurable thinking modes."""
    selected_model = model or provider.default_model
    if provider.thinking_levels is None:
        if isinstance(provider, OpenAICodexProviderConfig):
            return (
                "OpenAI Codex subscription can stream reasoning output, but Tau does "
                "not have a validated Codex transport mapping for changing reasoning "
                "effort yet"
            )
        return f"Provider {provider.name} does not declare thinking_levels"
    if provider.thinking_models and selected_model not in provider.thinking_models:
        return f"{provider.name}:{selected_model} is not declared in thinking_models"
    return None


def provider_default_thinking_level(
    provider: ProviderConfig,
    *,
    model: str | None = None,
) -> ThinkingLevel | None:
    """Return the preferred thinking level for a provider/model pair."""
    levels = provider_thinking_levels(provider, model=model)
    if not levels:
        return None
    if provider.thinking_default in levels:
        return provider.thinking_default
    if DEFAULT_THINKING_LEVEL in levels:
        return DEFAULT_THINKING_LEVEL
    return levels[0]


def openai_compatible_config_from_provider(
    provider: OpenAICompatibleProviderConfig,
    *,
    credential_reader: CredentialReader | None = None,
    model: str | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> OpenAICompatibleConfig:
    """Build OpenAI-compatible runtime config from durable settings."""
    api_key = _api_key_from_provider(provider, credential_reader=credential_reader)
    base_url = provider.base_url
    if provider.name == DEFAULT_PROVIDER_NAME and provider.api_key_env == "OPENAI_API_KEY":
        base_url = environ.get("OPENAI_BASE_URL", provider.base_url)
    reasoning_effort = _reasoning_effort_from_provider(
        provider,
        model=model,
        thinking_level=thinking_level,
    )
    return OpenAICompatibleConfig(
        api_key=api_key,
        provider_name=provider.name,
        base_url=base_url.rstrip("/"),
        headers=provider.headers,
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        max_retry_delay_seconds=provider.max_retry_delay_seconds,
        reasoning_effort=reasoning_effort,
        reasoning_effort_parameter=provider.thinking_parameter or "reasoning_effort",
    )


def anthropic_config_from_provider(
    provider: AnthropicProviderConfig,
    *,
    credential_reader: CredentialReader | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> AnthropicConfig:
    """Build Anthropic runtime config from durable settings."""
    api_key = _api_key_from_provider(provider, credential_reader=credential_reader)
    thinking_budget_tokens = _anthropic_thinking_budget_from_provider(
        provider,
        thinking_level=thinking_level,
    )
    return AnthropicConfig(
        api_key=api_key,
        provider_name=provider.name,
        base_url=provider.base_url.rstrip("/"),
        headers=provider.headers,
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        max_retry_delay_seconds=provider.max_retry_delay_seconds,
        thinking_budget_tokens=thinking_budget_tokens,
    )


def provider_kind(provider: ProviderConfig) -> ProviderKind:
    """Return the durable provider kind."""
    if isinstance(provider, AnthropicProviderConfig):
        return "anthropic"
    if isinstance(provider, OpenAICodexProviderConfig):
        return "openai-codex"
    return "openai-compatible"


def provider_has_usable_credentials(
    provider: ProviderConfig,
    *,
    credential_reader: CredentialReader | None = None,
) -> bool:
    """Return whether Tau can attempt calls for this provider without prompting setup."""
    if provider.credential_name and credential_reader is not None:
        if isinstance(provider, OpenAICodexProviderConfig):
            get_oauth = getattr(credential_reader, "get_oauth", None)
            if get_oauth is not None and get_oauth(provider.credential_name) is not None:
                return True
        elif credential_reader.get(provider.credential_name):
            return True
    return bool(environ.get(provider.api_key_env))


def _reasoning_effort_from_provider(
    provider: OpenAICompatibleProviderConfig,
    *,
    model: str | None,
    thinking_level: ThinkingLevel | None,
) -> str | None:
    if thinking_level is None or provider.thinking_parameter not in {
        "reasoning_effort",
        "reasoning.effort",
    }:
        return None

    levels = provider_thinking_levels(provider, model=model)
    if not levels:
        return None

    normalized = normalize_thinking_level(thinking_level)
    if normalized not in levels:
        selected_model = model or provider.default_model
        available = ", ".join(levels)
        raise ProviderConfigError(
            f"Thinking mode {normalized} is not available for "
            f"{provider.name}:{selected_model}. Available modes: {available}"
        )
    return reasoning_effort_for_level(normalized)


def _anthropic_thinking_budget_from_provider(
    provider: AnthropicProviderConfig,
    *,
    thinking_level: ThinkingLevel | None,
) -> int | None:
    if thinking_level is None or provider.thinking_parameter != "anthropic.thinking":
        return None

    levels = provider_thinking_levels(provider)
    if not levels:
        return None

    normalized = normalize_thinking_level(thinking_level)
    if normalized not in levels:
        available = ", ".join(levels)
        raise ProviderConfigError(
            f"Thinking mode {normalized} is not available for "
            f"{provider.name}:{provider.default_model}. Available modes: {available}"
        )
    return anthropic_thinking_budget_for_level(normalized)


def _provider_from_json(data: object) -> ProviderConfig:
    if not isinstance(data, dict):
        raise ProviderConfigError("Provider entries must be JSON objects")
    provider_type = _string(data.get("type"), "providers[].type")
    if provider_type not in {"openai-compatible", "anthropic", "openai-codex"}:
        raise ProviderConfigError(f"Unsupported provider type: {provider_type}")
    name = _string(data.get("name"), "providers[].name")
    base_url = _string(data.get("base_url"), f"providers[{name}].base_url").rstrip("/")
    api_key_env = _string(data.get("api_key_env"), f"providers[{name}].api_key_env")
    credential_name = _optional_string(
        data.get("credential_name"), f"providers[{name}].credential_name"
    )
    models = _string_tuple(data.get("models"), f"providers[{name}].models")
    default_model = _string(data.get("default_model"), f"providers[{name}].default_model")
    context_windows = _context_window_dict(
        data.get("context_windows", {}), f"providers[{name}].context_windows"
    )
    headers = _string_dict(data.get("headers", {}), f"providers[{name}].headers")
    timeout_seconds = _positive_float(
        data.get("timeout_seconds", DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS),
        f"providers[{name}].timeout_seconds",
    )
    max_retries = _non_negative_int(
        data.get("max_retries", DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES),
        f"providers[{name}].max_retries",
    )
    max_retry_delay_seconds = _non_negative_float(
        data.get(
            "max_retry_delay_seconds",
            DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
        ),
        f"providers[{name}].max_retry_delay_seconds",
    )
    thinking_levels = _optional_thinking_levels(
        data.get("thinking_levels"), f"providers[{name}].thinking_levels"
    )
    thinking_models = _optional_string_tuple(
        data.get("thinking_models"), f"providers[{name}].thinking_models"
    )
    thinking_default = _optional_thinking_level(
        data.get("thinking_default"), f"providers[{name}].thinking_default"
    )
    thinking_parameter = _optional_thinking_parameter(
        data.get("thinking_parameter"), f"providers[{name}].thinking_parameter"
    )
    if default_model not in models:
        models = (*models, default_model)
    if provider_type == "anthropic":
        return AnthropicProviderConfig(
            name=name,
            base_url=base_url,
            api_key_env=api_key_env,
            credential_name=credential_name,
            models=models,
            default_model=default_model,
            context_windows=context_windows,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_retry_delay_seconds=max_retry_delay_seconds,
            thinking_levels=thinking_levels,
            thinking_models=thinking_models,
            thinking_default=thinking_default,
            thinking_parameter=thinking_parameter,
        )
    if provider_type == "openai-codex":
        return OpenAICodexProviderConfig(
            name=name,
            base_url=base_url,
            api_key_env=api_key_env,
            credential_name=credential_name,
            models=models,
            default_model=default_model,
            context_windows=context_windows,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_retry_delay_seconds=max_retry_delay_seconds,
            thinking_levels=thinking_levels,
            thinking_models=thinking_models,
            thinking_default=thinking_default,
            thinking_parameter=thinking_parameter,
        )
    return OpenAICompatibleProviderConfig(
        name=name,
        base_url=base_url,
        api_key_env=api_key_env,
        credential_name=credential_name,
        models=models,
        default_model=default_model,
        context_windows=context_windows,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
        thinking_levels=thinking_levels,
        thinking_models=thinking_models,
        thinking_default=thinking_default,
        thinking_parameter=thinking_parameter,
    )


def _api_key_from_provider(
    provider: ProviderConfig,
    *,
    credential_reader: CredentialReader | None,
) -> str:
    if provider.credential_name and credential_reader is not None:
        credential = credential_reader.get(provider.credential_name)
        if credential:
            return credential

    api_key = environ.get(provider.api_key_env)
    if api_key:
        return api_key
    credential_hint = f" or run /login {provider.name}" if provider.credential_name else ""
    raise RuntimeError(f"Missing provider API key. Set {provider.api_key_env}{credential_hint}.")


def _validate_provider_numbers(
    *,
    timeout_seconds: float,
    max_retries: int,
    max_retry_delay_seconds: float,
) -> None:
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ProviderConfigError("Provider timeout_seconds must be greater than 0")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ProviderConfigError("Provider max_retries must be 0 or greater")
    if (
        not isinstance(max_retry_delay_seconds, int | float)
        or isinstance(max_retry_delay_seconds, bool)
        or max_retry_delay_seconds < 0
    ):
        raise ProviderConfigError("Provider max_retry_delay_seconds must be 0 or greater")


def _validate_context_windows(context_windows: dict[str, int]) -> None:
    for model, context_window in context_windows.items():
        if not isinstance(model, str) or not model.strip():
            raise ProviderConfigError("Provider context_windows keys must be non-empty strings")
        if (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window <= 0
        ):
            raise ProviderConfigError("Provider context_windows values must be positive integers")


def _validate_thinking_config(
    *,
    thinking_levels: tuple[ThinkingLevel, ...] | None,
    thinking_models: tuple[str, ...],
    thinking_default: ThinkingLevel | None,
    thinking_parameter: ThinkingParameter | None,
) -> None:
    if thinking_levels is None:
        if thinking_models or thinking_default is not None or thinking_parameter is not None:
            raise ProviderConfigError(
                "Provider thinking_levels must be set before thinking metadata"
            )
        return
    try:
        normalized = normalize_thinking_levels(thinking_levels)
    except ValueError as exc:
        raise ProviderConfigError(str(exc)) from exc
    if normalized != thinking_levels:
        raise ProviderConfigError("Provider thinking_levels must be normalized")
    if any(not isinstance(model, str) or not model.strip() for model in thinking_models):
        raise ProviderConfigError("Provider thinking_models must contain non-empty strings")
    if thinking_default is not None and thinking_default not in thinking_levels:
        raise ProviderConfigError("Provider thinking_default must be in thinking_levels")
    if thinking_parameter not in {
        None,
        "reasoning_effort",
        "reasoning.effort",
        "anthropic.thinking",
    }:
        raise ProviderConfigError(
            "Provider thinking_parameter must be reasoning_effort, reasoning.effort, "
            "or anthropic.thinking"
        )


def _reject_unimplemented_thinking_config(
    *,
    provider_type: str,
    thinking_levels: tuple[ThinkingLevel, ...] | None,
) -> None:
    if thinking_levels is not None:
        raise ProviderConfigError(f"{provider_type} thinking controls are not implemented yet")


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError(f"Provider field must be a non-empty string: {field_name}")
    return value.strip()


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError(f"Provider field must be a non-empty string: {field_name}")
    return value.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ProviderConfigError(f"Provider field must be a non-empty string list: {field_name}")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value):
        raise ProviderConfigError(f"Provider field must be a string list: {field_name}")
    return items


def _optional_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProviderConfigError(f"Provider field must be a string list: {field_name}")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value):
        raise ProviderConfigError(f"Provider field must be a string list: {field_name}")
    return items


def _optional_thinking_levels(
    value: object,
    field_name: str,
) -> tuple[ThinkingLevel, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ProviderConfigError(f"Provider field must be a thinking mode list: {field_name}")
    try:
        return normalize_thinking_levels(value)
    except ValueError as exc:
        raise ProviderConfigError(str(exc)) from exc


def _optional_thinking_level(value: object, field_name: str) -> ThinkingLevel | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderConfigError(f"Provider field must be a thinking mode: {field_name}")
    try:
        return normalize_thinking_level(value)
    except ValueError as exc:
        raise ProviderConfigError(str(exc)) from exc


def _optional_thinking_parameter(
    value: object,
    field_name: str,
) -> ThinkingParameter | None:
    if value is None:
        return None
    if value == "reasoning_effort":
        return "reasoning_effort"
    if value == "reasoning.effort":
        return "reasoning.effort"
    if value == "anthropic.thinking":
        return "anthropic.thinking"
    raise ProviderConfigError(
        f"Provider field must be reasoning_effort, reasoning.effort, "
        f"or anthropic.thinking: {field_name}"
    )


def _string_dict(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be a string object: {field_name}")
    items: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"Provider field must be a string object: {field_name}")
        if not isinstance(item, str) or not item.strip():
            raise ProviderConfigError(f"Provider field must be a string object: {field_name}")
        items[key.strip()] = item.strip()
    return items


def _context_window_dict(value: object, field_name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be an integer object: {field_name}")
    items: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"Provider field must be an integer object: {field_name}")
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise ProviderConfigError(
                f"Provider field values must be positive integers: {field_name}"
            )
        items[key.strip()] = item
    return items


def _positive_float(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ProviderConfigError(f"Provider field must be a positive number: {field_name}")
    converted = float(value)
    if converted <= 0:
        raise ProviderConfigError(f"Provider field must be greater than 0: {field_name}")
    return converted


def _non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProviderConfigError(f"Provider field must be a non-negative integer: {field_name}")
    if value < 0:
        raise ProviderConfigError(f"Provider field must be 0 or greater: {field_name}")
    return value


def _non_negative_float(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ProviderConfigError(f"Provider field must be a non-negative number: {field_name}")
    converted = float(value)
    if converted < 0:
        raise ProviderConfigError(f"Provider field must be 0 or greater: {field_name}")
    return converted

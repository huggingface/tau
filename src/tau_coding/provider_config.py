"""Durable provider configuration for Tau coding sessions."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field, replace
from json import dumps, loads
from os import environ
from pathlib import Path
from shutil import copy2
from tempfile import NamedTemporaryFile
from typing import Any, Protocol, cast

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
from tau_coding.catalog_loader import effective_catalog, save_user_catalog_entries
from tau_coding.credentials import FileCredentialStore, credentials_path
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    ModelCatalogMetadata,
    ModelCostTier,
    ProviderApi,
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
DEFAULT_MODEL = "gpt-5.4"


class ProviderConfigError(ValueError):
    """Raised when Tau provider configuration is invalid."""


class CredentialReader(Protocol):
    """Credential lookup used while building runtime provider config."""

    def get(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ProviderModelMetadata:
    """Runtime metadata for one configured model."""

    name: str | None = None
    api: ProviderApi | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    input: tuple[str, ...] = ()
    cost: dict[str, float] = field(default_factory=dict)
    cost_tiers: tuple[ModelCostTier, ...] = ()
    context_window: int | None = None
    max_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, Any] = field(default_factory=dict)
    thinking_level_map: dict[ThinkingLevel, str | None] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialize this model metadata to JSON-compatible data."""
        return {
            "name": self.name,
            "api": self.api,
            "base_url": self.base_url,
            "reasoning": self.reasoning,
            "input": list(self.input),
            "cost": dict(self.cost),
            "cost_tiers": [
                {
                    **(
                        {"max_input_tokens": tier.max_input_tokens}
                        if tier.max_input_tokens is not None
                        else {}
                    ),
                    **tier.cost,
                }
                for tier in self.cost_tiers
            ],
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "headers": dict(self.headers),
            "compat": dict(self.compat),
            "thinking_level_map": dict(self.thinking_level_map),
        }


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProviderConfig:
    """Durable settings for one OpenAI-compatible provider."""

    name: str
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    api: ProviderApi = "openai-completions"
    api_key_env: str = "OPENAI_API_KEY"
    credential_name: str | None = None
    models: tuple[str, ...] = (DEFAULT_MODEL,)
    default_model: str = DEFAULT_MODEL
    context_windows: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, Any] = field(default_factory=dict)
    model_metadata: dict[str, ProviderModelMetadata] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None
    thinking_defaults: dict[str, ThinkingLevel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_provider_numbers(
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
        )
        _validate_context_windows(self.context_windows)
        _validate_model_metadata(self.models, self.model_metadata)
        _validate_json_object(self.compat, "Provider compat")
        _validate_thinking_config(
            thinking_levels=self.thinking_levels,
            thinking_models=self.thinking_models,
            thinking_default=self.thinking_default,
            thinking_parameter=self.thinking_parameter,
        )
        _validate_thinking_defaults(self.thinking_defaults)

    def to_json(self) -> dict[str, Any]:
        """Serialize this provider config to JSON-compatible data."""
        return {
            "name": self.name,
            "type": "openai-compatible",
            "base_url": self.base_url,
            "api": self.api,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "context_windows": dict(self.context_windows),
            "headers": dict(self.headers),
            "compat": dict(self.compat),
            "model_metadata": {
                model: metadata.to_json() for model, metadata in self.model_metadata.items()
            },
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_retry_delay_seconds": self.max_retry_delay_seconds,
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
            "thinking_defaults": dict(self.thinking_defaults),
        }


@dataclass(frozen=True, slots=True)
class AnthropicProviderConfig:
    """Durable settings for Anthropic's Messages API."""

    name: str = "anthropic"
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    api: ProviderApi = "anthropic-messages"
    api_key_env: str = "ANTHROPIC_API_KEY"
    credential_name: str | None = "anthropic"
    models: tuple[str, ...] = ("claude-sonnet-4-6",)
    default_model: str = "claude-sonnet-4-6"
    context_windows: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, Any] = field(default_factory=dict)
    model_metadata: dict[str, ProviderModelMetadata] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None
    thinking_defaults: dict[str, ThinkingLevel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_provider_numbers(
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
        )
        _validate_context_windows(self.context_windows)
        _validate_model_metadata(self.models, self.model_metadata)
        _validate_json_object(self.compat, "Provider compat")
        _validate_thinking_config(
            thinking_levels=self.thinking_levels,
            thinking_models=self.thinking_models,
            thinking_default=self.thinking_default,
            thinking_parameter=self.thinking_parameter,
        )
        _validate_thinking_defaults(self.thinking_defaults)

    def to_json(self) -> dict[str, Any]:
        """Serialize this provider config to JSON-compatible data."""
        return {
            "name": self.name,
            "type": "anthropic",
            "base_url": self.base_url,
            "api": self.api,
            "api_key_env": self.api_key_env,
            "credential_name": self.credential_name,
            "models": list(self.models),
            "default_model": self.default_model,
            "context_windows": dict(self.context_windows),
            "headers": dict(self.headers),
            "compat": dict(self.compat),
            "model_metadata": {
                model: metadata.to_json() for model, metadata in self.model_metadata.items()
            },
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_retry_delay_seconds": self.max_retry_delay_seconds,
            "thinking_levels": (
                list(self.thinking_levels) if self.thinking_levels is not None else None
            ),
            "thinking_models": list(self.thinking_models),
            "thinking_default": self.thinking_default,
            "thinking_parameter": self.thinking_parameter,
            "thinking_defaults": dict(self.thinking_defaults),
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
    thinking_defaults: dict[str, ThinkingLevel] = field(default_factory=dict)

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
        _validate_thinking_defaults(self.thinking_defaults)

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
            "thinking_defaults": dict(self.thinking_defaults),
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
        """Serialize runtime preferences to JSON-compatible data."""
        return {
            "default_provider": self.default_provider,
            "provider_preferences": {
                provider.name: _provider_preference_to_json(provider) for provider in self.providers
            },
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
    model_metadata = _provider_model_metadata_from_catalog(entry.model_metadata)
    if entry.kind == "anthropic":
        return AnthropicProviderConfig(
            name=entry.name,
            base_url=entry.base_url,
            api=_default_api_for_kind(entry.kind),
            api_key_env=entry.api_key_env,
            credential_name=entry.credential_name,
            models=entry.models,
            default_model=entry.default_model,
            context_windows=context_windows,
            headers=dict(entry.headers),
            compat=dict(entry.compat),
            model_metadata=model_metadata,
            thinking_levels=entry.thinking_levels,
            thinking_models=entry.thinking_models,
            thinking_default=entry.thinking_default,
            thinking_parameter=entry.thinking_parameter,
            thinking_defaults={},
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
            thinking_defaults={},
        )
    return OpenAICompatibleProviderConfig(
        name=entry.name,
        base_url=entry.base_url,
        api=entry.api or _default_api_for_kind(entry.kind),
        api_key_env=entry.api_key_env,
        credential_name=entry.credential_name,
        models=entry.models,
        default_model=entry.default_model,
        context_windows=context_windows,
        headers=dict(entry.headers),
        compat=dict(entry.compat),
        model_metadata=model_metadata,
        thinking_levels=entry.thinking_levels,
        thinking_models=entry.thinking_models,
        thinking_default=entry.thinking_default,
        thinking_parameter=entry.thinking_parameter,
        thinking_defaults={},
    )


def _default_api_for_kind(kind: str) -> ProviderApi:
    if kind == "anthropic":
        return "anthropic-messages"
    if kind == "openai-codex":
        return "openai-codex-responses"
    if kind == "google-generative-ai":
        return "google-generative-ai"
    if kind == "mistral-conversations":
        return "mistral-conversations"
    return "openai-completions"


def _provider_model_metadata_from_catalog(
    model_metadata: dict[str, ModelCatalogMetadata],
) -> dict[str, ProviderModelMetadata]:
    return {
        model: ProviderModelMetadata(
            name=metadata.name,
            api=metadata.api,
            base_url=metadata.base_url,
            reasoning=metadata.reasoning,
            input=tuple(metadata.input),
            cost=dict(metadata.cost or {}),
            cost_tiers=metadata.cost_tiers,
            context_window=metadata.context_window,
            max_tokens=metadata.max_tokens,
            headers=dict(metadata.headers),
            compat=dict(metadata.compat),
            thinking_level_map=dict(metadata.thinking_level_map),
        )
        for model, metadata in model_metadata.items()
    }


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
    resolved_paths = paths or TauPaths()
    path = provider_settings_path(resolved_paths)
    if not path.exists():
        return ProviderSettings(providers=_effective_provider_configs(resolved_paths))
    raw = loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProviderConfigError("Provider settings must be a JSON object")
    settings = provider_settings_from_json(raw, paths=resolved_paths)
    return _with_builtin_catalog_models(settings, paths=resolved_paths)


def save_provider_settings(settings: ProviderSettings, paths: TauPaths | None = None) -> Path:
    """Write durable provider preferences and return the path."""
    resolved_paths = paths or TauPaths()
    _save_provider_definitions_to_catalog(settings, paths=resolved_paths)
    path = provider_settings_path(resolved_paths)
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


def save_provider_thinking_level(
    *,
    provider_name: str,
    model: str,
    thinking_level: ThinkingLevel,
    paths: TauPaths | None = None,
    fallback_settings: ProviderSettings | None = None,
) -> ProviderSettings:
    """Reload settings, persist one provider/model thinking preference, and return them."""
    settings = _load_provider_settings_for_write(paths, fallback_settings=fallback_settings)
    updated = set_provider_thinking_level(
        settings,
        provider_name=provider_name,
        model=model,
        thinking_level=thinking_level,
    )
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
    resolved_paths = paths or TauPaths()
    if provider_settings_path(resolved_paths).exists():
        return load_provider_settings(resolved_paths)
    if fallback_settings is not None:
        return fallback_settings
    return load_provider_settings(resolved_paths)


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


def set_provider_thinking_level(
    settings: ProviderSettings,
    *,
    provider_name: str,
    model: str,
    thinking_level: ThinkingLevel,
) -> ProviderSettings:
    """Return settings with a remembered thinking level for one provider/model."""
    provider = settings.get_provider(provider_name)
    validate_provider_model(provider, model)
    normalized = normalize_thinking_level(thinking_level)
    available = provider_thinking_levels(provider, model=model)
    if normalized not in available:
        modes = ", ".join(available) or "none"
        raise ProviderConfigError(
            f"Thinking mode {normalized} is not available for "
            f"{provider_name}:{model}. Available modes: {modes}"
        )
    updated_provider = replace(
        provider,
        thinking_defaults={**provider.thinking_defaults, model: normalized},
    )
    providers = tuple(
        updated_provider if item.name == provider_name else item for item in settings.providers
    )
    return ProviderSettings(
        default_provider=settings.default_provider,
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

    if isinstance(existing, OpenAICodexProviderConfig) and isinstance(
        incoming, OpenAICodexProviderConfig
    ):
        return replace(
            incoming,
            default_model=(
                existing.default_model
                if existing.default_model in incoming.models
                else incoming.default_model
            ),
            headers={**incoming.headers, **existing.headers},
            timeout_seconds=existing.timeout_seconds,
            max_retries=existing.max_retries,
            max_retry_delay_seconds=existing.max_retry_delay_seconds,
            context_windows={**incoming.context_windows, **existing.context_windows},
            thinking_levels=(
                existing.thinking_levels
                if existing.thinking_levels is not None
                else incoming.thinking_levels
            ),
            thinking_models=(
                existing.thinking_models
                if existing.thinking_levels is not None
                else incoming.thinking_models
            ),
            thinking_default=(
                existing.thinking_default
                if existing.thinking_levels is not None
                else incoming.thinking_default
            ),
            thinking_parameter=(
                existing.thinking_parameter
                if existing.thinking_levels is not None
                else incoming.thinking_parameter
            ),
            thinking_defaults=existing.thinking_defaults,
        )

    if isinstance(existing, OpenAICompatibleProviderConfig) and isinstance(
        incoming, OpenAICompatibleProviderConfig
    ):
        return _merge_openai_compatible_provider(existing, incoming)

    if isinstance(existing, AnthropicProviderConfig) and isinstance(
        incoming, AnthropicProviderConfig
    ):
        return _merge_anthropic_provider(existing, incoming)

    return incoming


def _merge_openai_compatible_provider(
    existing: OpenAICompatibleProviderConfig,
    incoming: OpenAICompatibleProviderConfig,
) -> OpenAICompatibleProviderConfig:
    models = _unique_strings((*incoming.models, *existing.models))
    return replace(
        incoming,
        models=models,
        default_model=(
            existing.default_model if existing.default_model in models else incoming.default_model
        ),
        headers={**incoming.headers, **existing.headers},
        compat={**incoming.compat, **existing.compat},
        model_metadata=_merge_provider_model_metadata(
            incoming.model_metadata,
            existing.model_metadata,
        ),
        timeout_seconds=existing.timeout_seconds,
        max_retries=existing.max_retries,
        max_retry_delay_seconds=existing.max_retry_delay_seconds,
        context_windows={**incoming.context_windows, **existing.context_windows},
        thinking_levels=(
            existing.thinking_levels
            if existing.thinking_levels is not None
            else incoming.thinking_levels
        ),
        thinking_models=(
            existing.thinking_models
            if existing.thinking_levels is not None
            else incoming.thinking_models
        ),
        thinking_default=(
            existing.thinking_default
            if existing.thinking_levels is not None
            else incoming.thinking_default
        ),
        thinking_parameter=(
            existing.thinking_parameter
            if existing.thinking_levels is not None
            else incoming.thinking_parameter
        ),
        thinking_defaults=existing.thinking_defaults,
    )


def _merge_anthropic_provider(
    existing: AnthropicProviderConfig,
    incoming: AnthropicProviderConfig,
) -> AnthropicProviderConfig:
    models = _unique_strings((*incoming.models, *existing.models))
    return replace(
        incoming,
        models=models,
        default_model=(
            existing.default_model if existing.default_model in models else incoming.default_model
        ),
        headers={**incoming.headers, **existing.headers},
        compat={**incoming.compat, **existing.compat},
        model_metadata=_merge_provider_model_metadata(
            incoming.model_metadata,
            existing.model_metadata,
        ),
        timeout_seconds=existing.timeout_seconds,
        max_retries=existing.max_retries,
        max_retry_delay_seconds=existing.max_retry_delay_seconds,
        context_windows={**incoming.context_windows, **existing.context_windows},
        thinking_levels=(
            existing.thinking_levels
            if existing.thinking_levels is not None
            else incoming.thinking_levels
        ),
        thinking_models=(
            existing.thinking_models
            if existing.thinking_levels is not None
            else incoming.thinking_models
        ),
        thinking_default=(
            existing.thinking_default
            if existing.thinking_levels is not None
            else incoming.thinking_default
        ),
        thinking_parameter=(
            existing.thinking_parameter
            if existing.thinking_levels is not None
            else incoming.thinking_parameter
        ),
        thinking_defaults=existing.thinking_defaults,
    )


def _merge_provider_model_metadata(
    incoming: dict[str, ProviderModelMetadata],
    existing: dict[str, ProviderModelMetadata],
) -> dict[str, ProviderModelMetadata]:
    merged = dict(incoming)
    for model, metadata in existing.items():
        if model not in merged:
            merged[model] = metadata
            continue
        base = merged[model]
        merged[model] = replace(
            base,
            name=metadata.name or base.name,
            api=metadata.api or base.api,
            base_url=metadata.base_url or base.base_url,
            reasoning=metadata.reasoning if metadata.reasoning is not None else base.reasoning,
            input=metadata.input or base.input,
            cost={**base.cost, **metadata.cost},
            cost_tiers=metadata.cost_tiers or base.cost_tiers,
            context_window=metadata.context_window or base.context_window,
            max_tokens=metadata.max_tokens or base.max_tokens,
            headers={**base.headers, **metadata.headers},
            compat={**base.compat, **metadata.compat},
            thinking_level_map={**base.thinking_level_map, **metadata.thinking_level_map},
        )
    return merged


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


def _provider_preference_to_json(provider: ProviderConfig) -> dict[str, Any]:
    """Serialize only runtime preferences for one provider."""
    return {
        "default_model": provider.default_model,
        "headers": dict(provider.headers),
        "timeout_seconds": provider.timeout_seconds,
        "max_retries": provider.max_retries,
        "max_retry_delay_seconds": provider.max_retry_delay_seconds,
        "thinking_defaults": dict(provider.thinking_defaults),
    }


def _save_provider_definitions_to_catalog(
    settings: ProviderSettings,
    *,
    paths: TauPaths | None,
) -> None:
    """Persist provider definitions that are not already represented by the catalog."""
    catalog_by_name = {entry.name: entry for entry in effective_catalog(paths)}
    entries_to_save = []
    for provider in settings.providers:
        entry = catalog_by_name.get(provider.name)
        if entry is None or _provider_definition_differs_from_catalog(provider, entry):
            entries_to_save.append(_catalog_entry_from_provider(provider, existing=entry))
    if entries_to_save:
        save_user_catalog_entries(entries_to_save, paths=paths)


def _provider_definition_differs_from_catalog(
    provider: ProviderConfig,
    entry: ProviderCatalogEntry,
) -> bool:
    """Return whether provider metadata changed enough to belong in catalog.toml."""
    if provider_kind(provider) != entry.kind:
        return True
    if provider.base_url != entry.base_url:
        return True
    if provider.api_key_env != entry.api_key_env:
        return True
    if provider.credential_name != entry.credential_name:
        return True
    if provider.models != entry.models:
        return True
    if getattr(provider, "api", None) != entry.api and entry.api is not None:
        return True
    if provider.context_windows != dict(entry.context_windows or {}):
        return True
    if provider.headers != dict(entry.headers):
        return True
    if getattr(provider, "compat", {}) != dict(entry.compat):
        return True
    if _catalog_model_metadata_from_provider(provider) != entry.model_metadata:
        return True
    if provider.thinking_levels != entry.thinking_levels:
        return True
    if provider.thinking_models != entry.thinking_models:
        return True
    if provider.thinking_default != entry.thinking_default:
        return True
    return provider.thinking_parameter != entry.thinking_parameter


def _catalog_entry_from_provider(
    provider: ProviderConfig,
    *,
    existing: ProviderCatalogEntry | None = None,
) -> ProviderCatalogEntry:
    """Create catalog metadata from a runtime provider config."""
    return ProviderCatalogEntry(
        name=provider.name,
        display_name=existing.display_name if existing is not None else provider.name,
        kind=provider_kind(provider),
        base_url=provider.base_url,
        api_key_env=provider.api_key_env,
        api=getattr(provider, "api", None),
        credential_name=provider.credential_name,
        models=provider.models,
        default_model=(
            existing.default_model
            if existing is not None and existing.default_model in provider.models
            else provider.default_model
        ),
        docs_url=existing.docs_url if existing is not None else provider.base_url,
        context_windows=dict(provider.context_windows) or None,
        headers=dict(provider.headers),
        compat=dict(getattr(provider, "compat", {})),
        model_metadata=_catalog_model_metadata_from_provider(provider),
        thinking_levels=provider.thinking_levels,
        thinking_models=provider.thinking_models,
        thinking_default=provider.thinking_default,
        thinking_parameter=provider.thinking_parameter,
    )


def _catalog_model_metadata_from_provider(
    provider: ProviderConfig,
) -> dict[str, ModelCatalogMetadata]:
    metadata_by_model = getattr(provider, "model_metadata", {})
    return {
        model: ModelCatalogMetadata(
            name=metadata.name,
            api=metadata.api,
            base_url=metadata.base_url,
            reasoning=metadata.reasoning,
            input=tuple(item for item in metadata.input if item in {"text", "image"}),
            cost=dict(metadata.cost) or None,
            cost_tiers=metadata.cost_tiers,
            context_window=metadata.context_window,
            max_tokens=metadata.max_tokens,
            headers=dict(metadata.headers),
            compat=dict(metadata.compat),
            thinking_level_map=dict(metadata.thinking_level_map),
        )
        for model, metadata in metadata_by_model.items()
    }


def provider_settings_from_json(
    data: dict[str, Any],
    *,
    paths: TauPaths | None = None,
) -> ProviderSettings:
    """Parse provider preferences from JSON-compatible data.

    The current providers.json shape stores runtime preferences under
    provider_preferences. The older providers[] shape is still accepted for
    migration and compatibility; saves rewrite it to provider_preferences and
    move custom provider definitions to catalog.toml.
    """
    default_provider = _string(data.get("default_provider"), "default_provider")
    scoped_models = _scoped_models_from_json(data.get("scoped_models"))
    if "provider_preferences" in data:
        providers = _providers_with_preferences(
            data.get("provider_preferences"),
            paths=paths,
        )
        return ProviderSettings(
            default_provider=default_provider,
            providers=providers,
            scoped_models=scoped_models,
        )

    providers_data = data.get("providers")
    if not isinstance(providers_data, list) or not providers_data:
        raise ProviderConfigError(
            "Provider settings must include provider_preferences or legacy providers"
        )
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
    if not isinstance(value, dict):
        raise ProviderConfigError("Provider settings field must be an object: provider_preferences")
    catalog_configs = {provider.name: provider for provider in _effective_provider_configs(paths)}
    providers = []
    seen: set[str] = set()
    for name, preference_data in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ProviderConfigError("Provider preference names must be non-empty strings")
        provider_name = name.strip()
        if provider_name in seen:
            raise ProviderConfigError("Provider preference names must be unique")
        if provider_name not in catalog_configs:
            # Preferences contain runtime overrides, not provider definitions. A
            # catalog entry may be removed independently, leaving an orphaned
            # preference behind. Ignore it so one stale entry cannot prevent Tau
            # from starting or running `tau setup` to register it again.
            continue
        providers.append(
            _apply_provider_preference(
                catalog_configs[provider_name],
                preference_data,
            )
        )
        seen.add(provider_name)
    return tuple(providers)


def _apply_provider_preference(
    provider: ProviderConfig,
    value: object,
) -> ProviderConfig:
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
            value.get("max_retries"),
            f"provider_preferences.{provider.name}.max_retries",
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
    thinking_defaults = (
        _thinking_defaults_dict(
            value.get("thinking_defaults"),
            provider,
            f"provider_preferences.{provider.name}.thinking_defaults",
        )
        if "thinking_defaults" in value
        else provider.thinking_defaults
    )
    return replace(
        provider,
        models=models,
        default_model=default_model,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
        thinking_defaults=thinking_defaults,
    )


def _thinking_defaults_dict(
    value: object,
    provider: ProviderConfig,
    field_name: str,
) -> dict[str, ThinkingLevel]:
    raw = _raw_thinking_defaults_dict(value, field_name)
    for model, thinking_level in raw.items():
        validate_provider_model(provider, model)
        available = provider_thinking_levels(provider, model=model)
        if thinking_level not in available:
            modes = ", ".join(available) or "none"
            raise ProviderConfigError(
                f"Provider thinking default {thinking_level} is not available for "
                f"{provider.name}:{model}. Available modes: {modes}"
            )
    return raw


def _raw_thinking_defaults_dict(value: object, field_name: str) -> dict[str, ThinkingLevel]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be a thinking mode object: {field_name}")
    defaults: dict[str, ThinkingLevel] = {}
    for key, item in value.items():
        model = _string(key, field_name)
        thinking_level = _optional_thinking_level(item, f"{field_name}.{model}")
        if thinking_level is None:
            raise ProviderConfigError(f"Provider field must be a thinking mode: {field_name}")
        defaults[model] = thinking_level
    return defaults


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
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    if metadata is not None and metadata.reasoning is False:
        return ()
    if provider.thinking_levels is None:
        if metadata is None or metadata.reasoning is not True:
            return ()
        return _levels_from_thinking_map(metadata.thinking_level_map)
    if provider.thinking_models and selected_model not in provider.thinking_models:
        return ()
    return tuple(
        level
        for level in provider.thinking_levels
        if metadata is None or _metadata_supports_thinking_level(metadata, level)
    )


def provider_thinking_unavailable_reason(
    provider: ProviderConfig,
    *,
    model: str | None = None,
) -> str | None:
    """Explain why a provider/model pair has no configurable thinking modes."""
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    if metadata is not None and metadata.reasoning is False:
        return f"{provider.name}:{selected_model} is not a reasoning model"
    if provider.thinking_levels is None:
        if metadata is not None and metadata.reasoning is True:
            return None
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


def _levels_from_thinking_map(
    thinking_level_map: dict[ThinkingLevel, str | None],
) -> tuple[ThinkingLevel, ...]:
    levels: tuple[ThinkingLevel, ...] = ("off", "minimal", "low", "medium", "high", "xhigh")
    return tuple(
        level for level in levels if _thinking_level_map_supports(thinking_level_map, level)
    )


def _metadata_supports_thinking_level(
    metadata: ProviderModelMetadata,
    level: ThinkingLevel,
) -> bool:
    return _thinking_level_map_supports(metadata.thinking_level_map, level)


def _thinking_level_map_supports(
    thinking_level_map: dict[ThinkingLevel, str | None],
    level: ThinkingLevel,
) -> bool:
    if level in thinking_level_map:
        return thinking_level_map[level] is not None
    return level != "xhigh"


def _metadata_for_model(provider: ProviderConfig, model: str) -> ProviderModelMetadata | None:
    return getattr(provider, "model_metadata", {}).get(model)


def _provider_api(provider: ProviderConfig, model: str | None = None) -> ProviderApi | str:
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    if metadata is not None and metadata.api is not None:
        return metadata.api
    if isinstance(provider, OpenAICodexProviderConfig):
        return "openai-codex-responses"
    return getattr(provider, "api", "openai-completions")


def _model_base_url(provider: ProviderConfig, model: str | None = None) -> str:
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    return metadata.base_url if metadata is not None and metadata.base_url else provider.base_url


def _model_headers(provider: ProviderConfig, model: str | None = None) -> dict[str, str]:
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    return {**provider.headers, **(metadata.headers if metadata is not None else {})}


def _model_compat(provider: ProviderConfig, model: str | None = None) -> dict[str, Any]:
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    return {
        **_detected_compat(provider, selected_model),
        **getattr(provider, "compat", {}),
        **(metadata.compat if metadata is not None else {}),
    }


def _detected_compat(provider: ProviderConfig, model: str) -> dict[str, Any]:
    base_url = _model_base_url(provider, model)
    is_together = provider.name == "together" or "api.together.ai" in base_url
    is_zai = provider.name == "zai" or "api.z.ai" in base_url
    is_moonshot = provider.name in {"moonshotai", "moonshotai-cn"} or "moonshot." in base_url
    is_grok = provider.name == "xai" or "api.x.ai" in base_url
    is_deepseek = provider.name == "deepseek" or "deepseek.com" in base_url
    is_cerebras = provider.name == "cerebras" or "cerebras.ai" in base_url
    is_openrouter = provider.name == "openrouter" or "openrouter.ai" in base_url
    is_nonstandard = is_cerebras or is_grok or is_together or is_deepseek or is_zai or is_moonshot
    use_max_tokens = is_moonshot or is_together
    return {
        "supportsStore": not is_nonstandard,
        "supportsReasoningEffort": not (is_grok or is_zai or is_moonshot or is_together),
        "supportsUsageInStreaming": True,
        "maxTokensField": "max_tokens" if use_max_tokens else "max_completion_tokens",
        "thinkingFormat": (
            "deepseek"
            if is_deepseek
            else "zai"
            if is_zai
            else "together"
            if is_together
            else "openrouter"
            if is_openrouter
            else "openai"
        ),
        "supportsStrictMode": not (is_moonshot or is_together),
        "supportsLongCacheRetention": not is_together,
    }


def _model_max_tokens(provider: ProviderConfig, model: str | None = None) -> int | None:
    selected_model = model or provider.default_model
    metadata = _metadata_for_model(provider, selected_model)
    return metadata.max_tokens if metadata is not None else None


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
    selected_model = model or provider.default_model
    base_url = _model_base_url(provider, selected_model)
    if provider.name == DEFAULT_PROVIDER_NAME and provider.api_key_env == "OPENAI_API_KEY":
        base_url = environ.get("OPENAI_BASE_URL", base_url)
    reasoning_effort = _reasoning_effort_from_provider(
        provider,
        model=selected_model,
        thinking_level=thinking_level,
    )
    compat = _model_compat(provider, selected_model)
    return OpenAICompatibleConfig(
        api_key=api_key,
        provider_name=provider.name,
        api=str(_provider_api(provider, selected_model)),
        base_url=base_url.rstrip("/"),
        headers=_model_headers(provider, selected_model),
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        max_retry_delay_seconds=provider.max_retry_delay_seconds,
        reasoning_effort=reasoning_effort,
        reasoning_effort_parameter=provider.thinking_parameter or "reasoning_effort",
        thinking_format=_thinking_format(provider, selected_model),
        compat=compat,
        include_reasoning_effort_none=_include_reasoning_effort_none(
            provider,
            model=selected_model,
            thinking_level=thinking_level,
        ),
    )


def anthropic_config_from_provider(
    provider: AnthropicProviderConfig,
    *,
    credential_reader: CredentialReader | None = None,
    model: str | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> AnthropicConfig:
    """Build Anthropic runtime config from durable settings."""
    api_key = _api_key_from_provider(provider, credential_reader=credential_reader)
    selected_model = model or provider.default_model
    thinking_budget_tokens = _anthropic_thinking_budget_from_provider(
        provider,
        model=selected_model,
        thinking_level=thinking_level,
    )
    return AnthropicConfig(
        api_key=api_key,
        provider_name=provider.name,
        base_url=_normalize_anthropic_base_url(_model_base_url(provider, selected_model)),
        headers=_model_headers(provider, selected_model),
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        max_retry_delay_seconds=provider.max_retry_delay_seconds,
        thinking_budget_tokens=thinking_budget_tokens,
        thinking_effort=_reasoning_effort_from_anthropic_provider(
            provider,
            model=selected_model,
            thinking_level=thinking_level,
        ),
        thinking_mode=_anthropic_thinking_mode(provider, selected_model),
    )


def provider_kind(provider: ProviderConfig) -> ProviderKind:
    """Return the durable provider kind."""
    if isinstance(provider, AnthropicProviderConfig):
        return "anthropic"
    if isinstance(provider, OpenAICodexProviderConfig):
        return "openai-codex"
    if isinstance(provider, OpenAICompatibleProviderConfig):
        if provider.api == "google-generative-ai":
            return "google-generative-ai"
        if provider.api == "mistral-conversations":
            return "mistral-conversations"
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

    selected_model = model or provider.default_model
    normalized = normalize_thinking_level(thinking_level)
    if normalized not in levels:
        available = ", ".join(levels)
        raise ProviderConfigError(
            f"Thinking mode {normalized} is not available for "
            f"{provider.name}:{selected_model}. Available modes: {available}"
        )
    mapped = _metadata_thinking_value(provider, selected_model, normalized)
    if mapped is not None:
        return mapped
    if provider.name == "huggingface" and normalized == "minimal":
        # Hugging Face's router currently accepts low/medium/high/xhigh/max/none
        # for reasoning_effort, but rejects Pi/Tau's "minimal" label.
        return "low"
    return reasoning_effort_for_level(normalized)


def _anthropic_thinking_budget_from_provider(
    provider: AnthropicProviderConfig,
    *,
    model: str | None,
    thinking_level: ThinkingLevel | None,
) -> int | None:
    if thinking_level is None or provider.thinking_parameter != "anthropic.thinking":
        return None

    selected_model = model or provider.default_model
    if _anthropic_thinking_mode(provider, selected_model) == "adaptive":
        return None

    levels = provider_thinking_levels(provider, model=selected_model)
    if not levels:
        return None

    normalized = normalize_thinking_level(thinking_level)
    if normalized not in levels:
        available = ", ".join(levels)
        raise ProviderConfigError(
            f"Thinking mode {normalized} is not available for "
            f"{provider.name}:{selected_model}. Available modes: {available}"
        )
    return anthropic_thinking_budget_for_level(normalized)


def _metadata_thinking_value(
    provider: ProviderConfig,
    model: str,
    level: ThinkingLevel,
) -> str | None:
    metadata = _metadata_for_model(provider, model)
    if metadata is None:
        return None
    value = metadata.thinking_level_map.get(level)
    return value if isinstance(value, str) else None


def _thinking_format(provider: ProviderConfig, model: str) -> str:
    compat = _model_compat(provider, model)
    value = compat.get("thinkingFormat")
    if isinstance(value, str) and value:
        return value
    base_url = _model_base_url(provider, model)
    if provider.name == "deepseek" or "deepseek.com" in base_url:
        return "deepseek"
    if provider.name == "zai" or "api.z.ai" in base_url:
        return "zai"
    if provider.name == "together" or "api.together.ai" in base_url:
        return "together"
    if provider.name == "openrouter" or "openrouter.ai" in base_url:
        return "openrouter"
    return "openai"


def _include_reasoning_effort_none(
    provider: ProviderConfig,
    *,
    model: str,
    thinking_level: ThinkingLevel | None,
) -> bool:
    if thinking_level is None:
        return False
    try:
        normalized = normalize_thinking_level(thinking_level)
    except ValueError:
        return False
    if normalized != "off":
        return False
    return _metadata_thinking_value(provider, model, "off") == "none"


def _reasoning_effort_from_anthropic_provider(
    provider: AnthropicProviderConfig,
    *,
    model: str,
    thinking_level: ThinkingLevel | None,
) -> str | None:
    if thinking_level is None:
        return None
    selected_model = model
    normalized = normalize_thinking_level(thinking_level)
    if normalized == "off":
        return None
    mapped = _metadata_thinking_value(provider, selected_model, normalized)
    return mapped or normalized


def _anthropic_thinking_mode(provider: AnthropicProviderConfig, model: str) -> str:
    compat = _model_compat(provider, model)
    if compat.get("forceAdaptiveThinking") is True:
        return "adaptive"
    return "budget"


def _normalize_anthropic_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _provider_from_json(data: object) -> ProviderConfig:
    if not isinstance(data, dict):
        raise ProviderConfigError("Provider entries must be JSON objects")
    provider_type = _string(data.get("type"), "providers[].type")
    if provider_type not in {
        "openai-compatible",
        "anthropic",
        "openai-codex",
        "google-generative-ai",
        "mistral-conversations",
    }:
        raise ProviderConfigError(f"Unsupported provider type: {provider_type}")
    name = _string(data.get("name"), "providers[].name")
    base_url = _string(data.get("base_url"), f"providers[{name}].base_url").rstrip("/")
    api = _optional_provider_api(data.get("api"), f"providers[{name}].api")
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
    compat = _json_dict(data.get("compat", {}), f"providers[{name}].compat")
    model_metadata = _model_metadata_dict(
        data.get("model_metadata", {}),
        models,
        f"providers[{name}].model_metadata",
    )
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
    thinking_defaults = _raw_thinking_defaults_dict(
        data.get("thinking_defaults", {}), f"providers[{name}].thinking_defaults"
    )
    if default_model not in models:
        models = (*models, default_model)
    if provider_type == "anthropic":
        return AnthropicProviderConfig(
            name=name,
            base_url=base_url,
            api=api or "anthropic-messages",
            api_key_env=api_key_env,
            credential_name=credential_name,
            models=models,
            default_model=default_model,
            context_windows=context_windows,
            headers=headers,
            compat=compat,
            model_metadata=model_metadata,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_retry_delay_seconds=max_retry_delay_seconds,
            thinking_levels=thinking_levels,
            thinking_models=thinking_models,
            thinking_default=thinking_default,
            thinking_parameter=thinking_parameter,
            thinking_defaults=thinking_defaults,
        )
    if provider_type == "openai-codex":
        _reject_catalog_only_legacy_metadata(compat, model_metadata)
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
            thinking_defaults=thinking_defaults,
        )
    return OpenAICompatibleProviderConfig(
        name=name,
        base_url=base_url,
        api=api or _default_api_for_kind(provider_type),
        api_key_env=api_key_env,
        credential_name=credential_name,
        models=models,
        default_model=default_model,
        context_windows=context_windows,
        headers=headers,
        compat=compat,
        model_metadata=model_metadata,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
        thinking_levels=thinking_levels,
        thinking_models=thinking_models,
        thinking_default=thinking_default,
        thinking_parameter=thinking_parameter,
        thinking_defaults=thinking_defaults,
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


def _validate_model_metadata(
    models: tuple[str, ...],
    model_metadata: dict[str, ProviderModelMetadata],
) -> None:
    model_names = set(models)
    for model, metadata in model_metadata.items():
        if model not in model_names:
            raise ProviderConfigError(f"Provider model_metadata key is not in models: {model}")
        if metadata.context_window is not None and metadata.context_window <= 0:
            raise ProviderConfigError("Provider model_metadata context_window must be positive")
        if metadata.max_tokens is not None and metadata.max_tokens <= 0:
            raise ProviderConfigError("Provider model_metadata max_tokens must be positive")
        if any(item not in {"text", "image"} for item in metadata.input):
            raise ProviderConfigError("Provider model_metadata input must contain text or image")
        if any(value < 0 for value in metadata.cost.values()):
            raise ProviderConfigError("Provider model_metadata cost values must be non-negative")
        _validate_runtime_cost_tiers(metadata.cost_tiers)
        _validate_json_object(metadata.compat, "Provider model_metadata compat")
        _validate_string_dict(metadata.headers, "Provider model_metadata headers")
        for level, value in metadata.thinking_level_map.items():
            normalize_thinking_level(level)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ProviderConfigError(
                    "Provider model_metadata thinking_level_map values must be strings or null"
                )


def _validate_runtime_cost_tiers(tiers: tuple[ModelCostTier, ...]) -> None:
    if tiers and tiers[-1].max_input_tokens is not None:
        raise ProviderConfigError(
            "Provider model_metadata final cost tier must omit max_input_tokens"
        )
    previous_limit = 0
    for tier in tiers:
        if any(value < 0 for value in tier.cost.values()):
            raise ProviderConfigError(
                "Provider model_metadata cost tier values must be non-negative"
            )
        if tier.max_input_tokens is None:
            continue
        if tier.max_input_tokens <= previous_limit:
            raise ProviderConfigError(
                "Provider model_metadata cost tier limits must be strictly increasing"
            )
        previous_limit = tier.max_input_tokens


def _validate_string_dict(value: dict[str, str], field_name: str) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"{field_name} keys must be non-empty strings")
        if not isinstance(item, str) or not item.strip():
            raise ProviderConfigError(f"{field_name} values must be non-empty strings")


def _validate_json_object(value: dict[str, Any], field_name: str) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"{field_name} keys must be non-empty strings")
        _validate_json_value(item, f"{field_name}.{key}")


def _validate_json_value(value: object, field_name: str) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderConfigError(f"{field_name} object keys must be strings")
            _validate_json_value(item, f"{field_name}.{key}")
        return
    raise ProviderConfigError(f"{field_name} must be JSON-compatible")


def _reject_catalog_only_legacy_metadata(
    compat: dict[str, Any],
    model_metadata: dict[str, ProviderModelMetadata],
) -> None:
    if compat or model_metadata:
        raise ProviderConfigError("OpenAI Codex legacy provider metadata is not supported")


def _validate_thinking_defaults(thinking_defaults: dict[str, ThinkingLevel]) -> None:
    for model, thinking_level in thinking_defaults.items():
        if not isinstance(model, str) or not model.strip():
            raise ProviderConfigError("Provider thinking_defaults keys must be non-empty strings")
        try:
            normalize_thinking_level(thinking_level)
        except ValueError as exc:
            raise ProviderConfigError(str(exc)) from exc


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


def _optional_provider_api(value: object, field_name: str) -> ProviderApi | None:
    if value is None:
        return None
    if value in {
        "openai-completions",
        "openai-responses",
        "anthropic-messages",
        "openai-codex-responses",
        "google-generative-ai",
        "mistral-conversations",
    }:
        return cast(ProviderApi, value)
    raise ProviderConfigError(f"Provider field has unsupported API: {field_name}")


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


def _json_dict(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be an object: {field_name}")
    items: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"Provider field must have string keys: {field_name}")
        _validate_json_value(item, f"{field_name}.{key}")
        items[key.strip()] = item
    return items


def _model_metadata_dict(
    value: object,
    models: tuple[str, ...],
    field_name: str,
) -> dict[str, ProviderModelMetadata]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be an object: {field_name}")
    model_names = set(models)
    items: dict[str, ProviderModelMetadata] = {}
    for key, item in value.items():
        model = _string(key, field_name)
        if model not in model_names:
            raise ProviderConfigError(f"Provider model_metadata key is not in models: {model}")
        if not isinstance(item, dict):
            raise ProviderConfigError(
                f"Provider model_metadata entries must be objects: {field_name}"
            )
        items[model] = ProviderModelMetadata(
            name=_optional_string(item.get("name"), f"{field_name}.{model}.name"),
            api=_optional_provider_api(item.get("api"), f"{field_name}.{model}.api"),
            base_url=_optional_string(item.get("base_url"), f"{field_name}.{model}.base_url"),
            reasoning=_optional_bool(item.get("reasoning"), f"{field_name}.{model}.reasoning"),
            input=_optional_string_tuple(item.get("input"), f"{field_name}.{model}.input"),
            cost=_float_dict(item.get("cost", {}), f"{field_name}.{model}.cost"),
            cost_tiers=_cost_tiers(item.get("cost_tiers", []), f"{field_name}.{model}.cost_tiers"),
            context_window=_optional_positive_int(
                item.get("context_window"), f"{field_name}.{model}.context_window"
            ),
            max_tokens=_optional_positive_int(
                item.get("max_tokens"), f"{field_name}.{model}.max_tokens"
            ),
            headers=_string_dict(item.get("headers", {}), f"{field_name}.{model}.headers"),
            compat=_json_dict(item.get("compat", {}), f"{field_name}.{model}.compat"),
            thinking_level_map=_thinking_level_map_dict(
                item.get("thinking_level_map", {}),
                f"{field_name}.{model}.thinking_level_map",
            ),
        )
    return items


def _cost_tiers(value: object, field_name: str) -> tuple[ModelCostTier, ...]:
    if not isinstance(value, list):
        raise ProviderConfigError(f"Provider field must be an array: {field_name}")
    tiers: list[ModelCostTier] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProviderConfigError(f"Provider cost tiers must be objects: {field_name}")
        tier_field = f"{field_name}.{index}"
        allowed = {"max_input_tokens", "input", "output", "cacheRead", "cacheWrite"}
        if set(item) - allowed:
            raise ProviderConfigError(f"Provider cost tier has unknown fields: {tier_field}")
        cost = {
            key: _non_negative_float(item.get(key), f"{tier_field}.{key}")
            for key in ("input", "output", "cacheRead", "cacheWrite")
        }
        tiers.append(
            ModelCostTier(
                max_input_tokens=_optional_positive_int(
                    item.get("max_input_tokens"), f"{tier_field}.max_input_tokens"
                ),
                cost=cost,
            )
        )
    result = tuple(tiers)
    _validate_runtime_cost_tiers(result)
    return result


def _thinking_level_map_dict(
    value: object,
    field_name: str,
) -> dict[ThinkingLevel, str | None]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be an object: {field_name}")
    items: dict[ThinkingLevel, str | None] = {}
    for key, item in value.items():
        level = _optional_thinking_level(key, field_name)
        if level is None:
            raise ProviderConfigError(f"Provider field must be a thinking mode: {field_name}")
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ProviderConfigError(
                f"Provider field values must be strings or null: {field_name}"
            )
        items[level] = item.strip() if isinstance(item, str) else None
    return items


def _float_dict(value: object, field_name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ProviderConfigError(f"Provider field must be a number object: {field_name}")
    items: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProviderConfigError(f"Provider field must be a number object: {field_name}")
        if not isinstance(item, int | float) or isinstance(item, bool) or item < 0:
            raise ProviderConfigError(f"Provider field values must be non-negative: {field_name}")
        items[key.strip()] = float(item)
    return items


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ProviderConfigError(f"Provider field must be a boolean: {field_name}")
    return value


def _optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProviderConfigError(f"Provider field must be a positive integer: {field_name}")
    return value


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

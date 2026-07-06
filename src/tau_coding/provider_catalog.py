"""Built-in provider catalog for Tau login/setup flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from tau_agent.types import JSONValue
from tau_coding.thinking import ThinkingLevel, ThinkingParameter

ProviderKind = Literal[
    "openai-compatible",
    "anthropic",
    "openai-codex",
    "google-generative-ai",
    "mistral-conversations",
]
ProviderApi = Literal[
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
    "openai-codex-responses",
    "google-generative-ai",
    "mistral-conversations",
]
ModelInput = Literal["text", "image"]
ThinkingLevelMap = dict[ThinkingLevel, str | None]


@dataclass(frozen=True, slots=True)
class ModelCatalogMetadata:
    """Provider-catalog metadata for a single model."""

    name: str | None = None
    api: ProviderApi | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    input: tuple[ModelInput, ...] = ()
    cost: dict[str, float] | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, JSONValue] = field(default_factory=dict)
    thinking_level_map: ThinkingLevelMap = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThinkingMode:
    """A canonical Tau thinking level's provider-specific behavior."""

    api_value: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelOverride:
    """Built-in model behavior that differs from its provider defaults.

    Some aggregators (for example OpenCode Go) expose models with heterogeneous
    thinking APIs behind a single provider entry: some models take OpenAI's
    ``reasoning_effort`` parameter, some are served through Anthropic's Messages
    API thinking types, and some cannot disable reasoning at all. The
    declarative TOML catalog describes one thinking configuration per provider,
    so these per-model behaviors live here as a lookup keyed by
    ``(provider_name, model)``.
    """

    kind: ProviderKind | None = None
    thinking_modes: Mapping[ThinkingLevel, ThinkingMode] | None = None
    thinking_default: ThinkingLevel | None = None
    always_thinking: bool = False


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """A built-in provider Tau can present during login."""

    name: str
    display_name: str
    kind: ProviderKind
    base_url: str
    api_key_env: str
    credential_name: str | None
    models: tuple[str, ...]
    default_model: str
    docs_url: str
    api: ProviderApi | None = None
    context_windows: dict[str, int] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, JSONValue] = field(default_factory=dict)
    model_metadata: dict[str, ModelCatalogMetadata] = field(default_factory=dict)
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None
    model_overrides: dict[str, ProviderModelOverride] | None = None


def _load_builtin_catalog() -> tuple[ProviderCatalogEntry, ...]:
    # Imported lazily: catalog_loader imports ProviderCatalogEntry from this module.
    from tau_coding.catalog_loader import builtin_catalog

    return builtin_catalog()


BUILTIN_PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = _load_builtin_catalog()


def builtin_provider_entry(name: str) -> ProviderCatalogEntry | None:
    """Return a built-in catalog entry by provider name."""
    for entry in BUILTIN_PROVIDER_CATALOG:
        if entry.name == name:
            return entry
    return None


def catalog_model_override(provider_name: str, model: str | None) -> ProviderModelOverride | None:
    """Return built-in per-model metadata for a provider/model pair."""
    if model is None:
        return None
    entry = builtin_provider_entry(provider_name)
    if entry is None or entry.model_overrides is None:
        return None
    return entry.model_overrides.get(model)

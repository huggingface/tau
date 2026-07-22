"""Built-in provider catalog for Tau login/setup flows."""

from __future__ import annotations

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
AuthMethod = Literal["api_key", "oauth"]


@dataclass(frozen=True, slots=True)
class ModelCostTier:
    """Model rates that apply up to an optional input-token limit."""

    cost: dict[str, float]
    max_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalogMetadata:
    """Provider-catalog metadata for a single model."""

    name: str | None = None
    api: ProviderApi | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    input: tuple[ModelInput, ...] = ()
    cost: dict[str, float] | None = None
    cost_tiers: tuple[ModelCostTier, ...] = ()
    context_window: int | None = None
    max_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    compat: dict[str, JSONValue] = field(default_factory=dict)
    thinking_level_map: ThinkingLevelMap = field(default_factory=dict)


def model_cost_for_input_tokens(
    metadata: ModelCatalogMetadata,
    input_tokens: int,
) -> dict[str, float] | None:
    """Return model rates for an input size, falling back to the flat base cost."""
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens < 0:
        raise ValueError("input_tokens must be a non-negative integer")
    for tier in metadata.cost_tiers:
        if tier.max_input_tokens is None or input_tokens <= tier.max_input_tokens:
            return dict(tier.cost)
    return dict(metadata.cost) if metadata.cost is not None else None


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
    auth_methods: tuple[AuthMethod, ...] = ("api_key",)


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

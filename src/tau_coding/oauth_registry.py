"""Built-in and extension-ready OAuth provider registry."""

from __future__ import annotations

from collections.abc import Iterable

from tau_coding.oauth import OpenAICodexOAuthProvider
from tau_coding.oauth_anthropic import AnthropicOAuthProvider
from tau_coding.oauth_github_copilot import GitHubCopilotOAuthProvider
from tau_coding.oauth_types import OAuthProvider

_BUILTIN_PROVIDERS: tuple[OAuthProvider, ...] = tuple(
    [AnthropicOAuthProvider(), GitHubCopilotOAuthProvider(), OpenAICodexOAuthProvider()]
)
_registry: dict[str, OAuthProvider] = {provider.id: provider for provider in _BUILTIN_PROVIDERS}


def get_oauth_provider(provider_id: str) -> OAuthProvider | None:
    """Return a registered OAuth provider by stable provider ID."""
    return _registry.get(provider_id)


def get_oauth_providers() -> tuple[OAuthProvider, ...]:
    """Return all registered OAuth providers in registration order."""
    return tuple(_registry.values())


def oauth_provider_ids() -> frozenset[str]:
    """Return IDs accepted by Tau's subscription login flow."""
    return frozenset(_registry)


def register_oauth_provider(provider: OAuthProvider) -> None:
    """Register or replace an OAuth provider implementation."""
    if not provider.id.strip():
        raise ValueError("OAuth provider id must not be empty")
    _registry[provider.id] = provider


def unregister_oauth_provider(provider_id: str) -> None:
    """Remove a custom provider or restore a replaced built-in provider."""
    builtin = next(
        (provider for provider in _BUILTIN_PROVIDERS if provider.id == provider_id),
        None,
    )
    if builtin is None:
        _registry.pop(provider_id, None)
    else:
        _registry[provider_id] = builtin


def reset_oauth_providers(providers: Iterable[OAuthProvider] = _BUILTIN_PROVIDERS) -> None:
    """Reset the registry, primarily for deterministic extension tests."""
    _registry.clear()
    _registry.update((provider.id, provider) for provider in providers)

"""Runtime provider construction for Tau coding sessions."""

from __future__ import annotations

from dataclasses import replace
from os import environ
from typing import Protocol

from tau_ai import (
    AnthropicConfig,
    AnthropicProvider,
    GoogleGenerativeAIProvider,
    MistralConversationsProvider,
    ModelProvider,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
    OpenAICompatibleProvider,
    RuntimeProviderAuth,
)
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.oauth import (
    account_id_from_access_token,
    oauth_credential_is_expired,
    refresh_openai_codex_token,
)
from tau_coding.oauth_registry import get_oauth_provider
from tau_coding.oauth_types import OAuthProvider
from tau_coding.provider_config import (
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderConfigError,
    anthropic_config_from_provider,
    openai_compatible_config_from_provider,
    provider_thinking_levels,
    validate_provider_model,
)
from tau_coding.thinking import ThinkingLevel, normalize_thinking_level, reasoning_effort_for_level


class ClosableModelProvider(ModelProvider, Protocol):
    """Runtime provider object Tau owns and can close."""

    async def aclose(self) -> None:
        """Close any provider-owned resources."""
        ...


def create_model_provider(
    provider: ProviderConfig,
    *,
    credential_store: FileCredentialStore | None = None,
    model: str | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> ClosableModelProvider:
    """Create a runtime model provider from durable provider settings."""
    if model is not None:
        validate_provider_model(provider, model)
    credentials = credential_store or FileCredentialStore()
    if isinstance(provider, AnthropicProviderConfig):
        credential = _oauth_credential(provider, credentials)
        config = anthropic_config_from_provider(
            provider,
            credential_reader=credentials,
            model=model,
            thinking_level=thinking_level,
        )
        if credential is not None:
            runtime_auth = _required_oauth_provider(provider.name).runtime_auth(credential)
            config = replace(
                config,
                api_key=runtime_auth.api_key,
                bearer_auth=True,
                headers={**dict(config.headers or {}), **dict(runtime_auth.headers or {})},
                oauth_system_prompt="You are Claude Code, Anthropic's official CLI for Claude.",
                credential_resolver=OAuthRuntimeCredentialResolver(
                    provider,
                    credential_store=credentials,
                ),
            )
        return AnthropicProvider(config)
    if isinstance(provider, OpenAICodexProviderConfig):
        return OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=OpenAICodexCredentialResolver(
                    provider,
                    credential_store=credentials,
                ),
                base_url=provider.base_url,
                provider_name=provider.name,
                headers=provider.headers,
                timeout_seconds=provider.timeout_seconds,
                max_retries=provider.max_retries,
                max_retry_delay_seconds=provider.max_retry_delay_seconds,
                reasoning_effort=_codex_reasoning_effort(
                    provider,
                    model=model,
                    thinking_level=thinking_level,
                ),
            )
        )
    if isinstance(provider, OpenAICompatibleProviderConfig):
        credential = _oauth_credential(provider, credentials)
        compatible_config = openai_compatible_config_from_provider(
            provider,
            credential_reader=credentials,
            model=model,
            thinking_level=thinking_level,
        )
        if credential is not None:
            runtime_auth = _required_oauth_provider(provider.name).runtime_auth(credential)
            compatible_config = replace(
                compatible_config,
                api_key=runtime_auth.api_key,
                base_url=runtime_auth.base_url or compatible_config.base_url,
                headers={
                    **dict(compatible_config.headers or {}),
                    **dict(runtime_auth.headers or {}),
                },
                credential_resolver=OAuthRuntimeCredentialResolver(
                    provider,
                    credential_store=credentials,
                ),
            )
        selected_api = compatible_config.api
        if selected_api == "anthropic-messages":
            if credential is None:
                raise ProviderConfigError(
                    "Anthropic-protocol models on openai-compatible providers require OAuth"
                )
            anthropic_config = AnthropicConfig(
                api_key=compatible_config.api_key,
                base_url=compatible_config.base_url,
                headers=compatible_config.headers,
                timeout_seconds=compatible_config.timeout_seconds,
                max_retries=compatible_config.max_retries,
                max_retry_delay_seconds=compatible_config.max_retry_delay_seconds,
                provider_name=compatible_config.provider_name,
                bearer_auth=True,
                credential_resolver=compatible_config.credential_resolver,
            )
            return AnthropicProvider(anthropic_config)
        if selected_api == "google-generative-ai":
            return GoogleGenerativeAIProvider(compatible_config)
        if selected_api == "mistral-conversations":
            return MistralConversationsProvider(compatible_config)
        return OpenAICompatibleProvider(compatible_config)
    raise ProviderConfigError(f"Unsupported provider config: {provider.name}")


def _codex_reasoning_effort(
    provider: OpenAICodexProviderConfig,
    *,
    model: str | None,
    thinking_level: ThinkingLevel | None,
) -> str | None:
    if thinking_level is None or provider.thinking_parameter != "reasoning.effort":
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
    if normalized == "off":
        return None
    if normalized == "minimal":
        return "low"
    return reasoning_effort_for_level(normalized)


class OpenAICodexCredentialResolver:
    """Resolve and refresh OpenAI Codex OAuth credentials for one request."""

    def __init__(
        self,
        provider: OpenAICodexProviderConfig,
        *,
        credential_store: FileCredentialStore,
    ) -> None:
        self._provider = provider
        self._credential_store = credential_store

    async def __call__(self) -> OpenAICodexCredentials:
        """Return a valid Codex access token and account id."""
        credential_name = self._provider.credential_name
        if credential_name:
            credential = self._credential_store.get_oauth(credential_name)
            if credential is not None:
                credential = await self._refresh_if_needed(credential_name, credential)
                if credential.account_id is None:
                    raise RuntimeError("OpenAI Codex OAuth credential is missing account_id")
                return OpenAICodexCredentials(
                    access_token=credential.access,
                    account_id=credential.account_id,
                )

        access_token = environ.get(self._provider.api_key_env)
        if access_token:
            account_id = account_id_from_access_token(access_token)
            if account_id is None:
                raise RuntimeError(
                    f"{self._provider.api_key_env} must contain an OpenAI Codex access JWT"
                )
            return OpenAICodexCredentials(access_token=access_token, account_id=account_id)

        credential_hint = f"Run /login {self._provider.name}."
        raise RuntimeError(f"Missing OpenAI Codex OAuth credentials. {credential_hint}")

    async def _refresh_if_needed(
        self,
        credential_name: str,
        credential: OAuthCredential,
    ) -> OAuthCredential:
        if not oauth_credential_is_expired(credential):
            return credential
        refreshed = await refresh_openai_codex_token(credential.refresh)
        if refreshed != credential:
            self._credential_store.set_oauth(credential_name, refreshed)
        return refreshed


def _oauth_credential(
    provider: ProviderConfig,
    credential_store: FileCredentialStore,
) -> OAuthCredential | None:
    if provider.credential_name is None or get_oauth_provider(provider.name) is None:
        return None
    return credential_store.get_oauth(provider.credential_name)


class OAuthRuntimeCredentialResolver:
    """Refresh provider-neutral OAuth credentials immediately before a request."""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        credential_store: FileCredentialStore,
    ) -> None:
        self._provider = provider
        self._credential_store = credential_store

    async def __call__(self) -> RuntimeProviderAuth:
        credential_name = self._provider.credential_name
        if credential_name is None:
            raise RuntimeError(f"Provider {self._provider.name} has no credential name")
        credential = self._credential_store.get_oauth(credential_name)
        if credential is None:
            raise RuntimeError(
                f"Missing OAuth credentials for {self._provider.name}. "
                f"Run /login {self._provider.name}."
            )
        oauth_provider = _required_oauth_provider(self._provider.name)
        refreshed = await oauth_provider.refresh(credential)
        if refreshed != credential:
            self._credential_store.set_oauth(credential_name, refreshed)
        auth = oauth_provider.runtime_auth(refreshed)
        return RuntimeProviderAuth(
            api_key=auth.api_key,
            base_url=auth.base_url,
            headers=auth.headers,
        )


def _required_oauth_provider(provider_name: str) -> OAuthProvider:
    oauth_provider = get_oauth_provider(provider_name)
    if oauth_provider is None:
        raise RuntimeError(f"No OAuth implementation is registered for {provider_name}")
    return oauth_provider

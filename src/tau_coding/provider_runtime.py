"""Runtime provider construction for Tau coding sessions."""

from __future__ import annotations

import asyncio
from asyncio import AbstractEventLoop, get_running_loop
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import replace
from inspect import isawaitable
from os import environ
from typing import Protocol
from weakref import WeakKeyDictionary

from tau_agent.provider import ModelProvider
from tau_ai.anthropic import AnthropicProvider
from tau_ai.env import AnthropicConfig, OpenAICompatibleConfig, RuntimeProviderAuth
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.mistral import MistralConversationsProvider
from tau_ai.openai_codex import (
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from tau_ai.openai_compatible import OpenAICompatibleProvider
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.extensions.providers import (
    CredentialReader,
    DynamicProvider,
    OpenAICompatibleTransport,
    ProviderModel,
    ProviderRuntimeContext,
    resolve_provider_auth,
)
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
    anthropic_cache_settings,
    anthropic_config_from_provider,
    openai_compatible_config_from_provider,
    provider_model_max_tokens,
    provider_model_supports_images,
    provider_thinking_levels,
    validate_huggingface_inference_provider,
    validate_provider_model,
)
from tau_coding.thinking import ThinkingLevel, normalize_thinking_level, reasoning_effort_for_level


class ClosableModelProvider(ModelProvider, Protocol):
    """Runtime provider object Tau owns and can close."""

    async def aclose(self) -> None:
        """Close any provider-owned resources."""
        ...


async def create_dynamic_model_provider(
    provider: DynamicProvider,
    *,
    model: str,
    credential_store: CredentialReader | None = None,
    environment: Mapping[str, str] | None = None,
) -> ClosableModelProvider:
    """Create a candidate runtime from a process-local provider definition.

    Authentication is resolved only here, immediately before construction.
    This path never converts or writes the dynamic definition to durable
    ``ProviderConfig`` settings.
    """
    selected_model = _dynamic_model(provider, model)
    auth = await resolve_provider_auth(
        provider.auth,
        credentials=(credential_store if credential_store is not None else FileCredentialStore()),
        environment=environment if environment is not None else environ,
    )
    context = ProviderRuntimeContext(provider_id=provider.id, auth=auth)
    if provider.runtime_factory is not None:
        candidate = provider.runtime_factory(context, selected_model)
        runtime = await candidate if isawaitable(candidate) else candidate
        if not hasattr(runtime, "stream_response") or not hasattr(runtime, "aclose"):
            raise ProviderConfigError(
                f"Runtime factory for {provider.id} returned an unsupported provider"
            )
        return runtime

    transport = provider.transport
    assert isinstance(transport, OpenAICompatibleTransport)
    selected_api = selected_model.api or transport.api
    if selected_api not in {"openai-completions", "openai-responses"}:
        raise ProviderConfigError(
            f"OpenAI-compatible dynamic provider {provider.id} cannot use api {selected_api}"
        )
    headers = _merge_dynamic_headers(
        transport.headers,
        selected_model.headers,
        auth.headers,
    )
    has_authorization = any(key.casefold() == "authorization" for key in headers)
    if auth.api_key is not None and auth.omit_authorization_header and not has_authorization:
        raise ProviderConfigError(
            f"OpenAI-compatible dynamic provider {provider.id} resolved an API key "
            "while requesting Authorization omission"
        )
    config = OpenAICompatibleConfig(
        api_key=auth.api_key or "",
        base_url=selected_model.base_url or transport.base_url,
        headers=headers,
        timeout_seconds=transport.timeout_seconds,
        max_retries=transport.max_retries,
        max_retry_delay_seconds=transport.max_retry_delay_seconds,
        api=selected_api,
        max_tokens=selected_model.max_tokens,
        supports_images=(
            selected_model.input_modalities is not None
            and "image" in selected_model.input_modalities
        ),
        compat=selected_model.compat,
        provider_name=provider.id,
        omit_authorization_header=auth.omit_authorization_header,
        # Dynamic providers explicitly own their API choice. A local model id
        # resembling gpt-* or *codex* must not reroute to /responses.
        infer_api_from_model=False,
    )
    return OpenAICompatibleProvider(config)


def _dynamic_model(provider: DynamicProvider, model: str) -> ProviderModel:
    for candidate in provider.models:
        if candidate.id == model:
            return candidate
    raise ProviderConfigError(f"Model is not configured for provider {provider.id}: {model}")


def _merge_dynamic_headers(*values: Mapping[str, str]) -> dict[str, str]:
    """Merge transport/model/auth headers case-insensitively, latest value winning."""
    merged: dict[str, str] = {}
    names: dict[str, str] = {}
    for value in values:
        for key, item in value.items():
            normalized = key.casefold()
            previous = names.get(normalized)
            if previous is not None:
                merged.pop(previous)
            names[normalized] = key
            merged[key] = item
    return merged


def create_model_provider(
    provider: ProviderConfig,
    *,
    credential_store: FileCredentialStore | None = None,
    model: str | None = None,
    thinking_level: ThinkingLevel | None = None,
    inference_provider: str | None = None,
    response_headers_observer: Callable[[Mapping[str, str]], None] | None = None,
) -> ClosableModelProvider:
    """Create a runtime model provider from durable provider settings."""
    if model is not None:
        validate_provider_model(provider, model)
    if inference_provider is not None:
        if provider.name != "huggingface" or model is None:
            raise ProviderConfigError(
                "Inference-provider pinning is only available for Hugging Face models"
            )
        inference_provider = validate_huggingface_inference_provider(inference_provider)
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
            oauth_retention, _ = anthropic_cache_settings(provider, model, oauth=True)
            config = replace(
                config,
                api_key=runtime_auth.api_key,
                bearer_auth=True,
                headers={**dict(config.headers or {}), **dict(runtime_auth.headers or {})},
                oauth_system_prompt="You are Claude Code, Anthropic's official CLI for Claude.",
                cache_retention=oauth_retention,
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
                supports_images=provider_model_supports_images(provider, model),
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
        if inference_provider is not None and model is not None:
            compatible_config = replace(
                compatible_config,
                model_aliases={model: f"{model}:{inference_provider}"},
            )
        if response_headers_observer is not None:
            compatible_config = replace(
                compatible_config,
                response_headers_observer=response_headers_observer,
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
            gateway_retention, gateway_cache_control_on_tools = anthropic_cache_settings(
                provider, model, oauth=True
            )
            anthropic_config = AnthropicConfig(
                api_key=compatible_config.api_key,
                base_url=compatible_config.base_url,
                headers=compatible_config.headers,
                timeout_seconds=compatible_config.timeout_seconds,
                provider_name=compatible_config.provider_name,
                max_retries=compatible_config.max_retries,
                max_retry_delay_seconds=compatible_config.max_retry_delay_seconds,
                max_tokens=provider_model_max_tokens(provider, model),
                bearer_auth=True,
                credential_resolver=compatible_config.credential_resolver,
                supports_images=compatible_config.supports_images,
                # Resolved from compat like the first-party path, so a gateway
                # proxying real Claude can opt back in per provider or per model.
                cache_retention=gateway_retention,
                cache_control_on_tools=gateway_cache_control_on_tools,
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
        async with _refresh_lock(credential_name):
            stored = self._credential_store.get_oauth(credential_name) or credential
            if not oauth_credential_is_expired(stored):
                return stored
            refreshed = await refresh_openai_codex_token(stored.refresh)
            if refreshed != stored:
                self._credential_store.set_oauth(credential_name, refreshed)
        return refreshed


_REFRESH_LOCKS: MutableMapping[AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


def _refresh_lock(credential_name: str) -> asyncio.Lock:
    """Return this loop's refresh lock for one stored credential.

    Providers rotate the refresh token on use: the old one dies the moment a
    refresh succeeds. A session issues provider calls concurrently (the agent
    loop and session auto-naming, for two), so without serialization several
    tasks read the same expired credential and spend the same refresh token.
    One of them wins, the losers 400, and whichever write lands last can leave
    a superseded token on disk — which fails on the *next* run, long after the
    race that caused it. Holding this lock across the network call, and
    re-reading the store inside it, keeps a token spent at most once.

    Locks are cached per event loop because ``asyncio.Lock`` binds to the
    running loop on first contention: a lock cached across loops appears to
    work — the uncontended path never touches the loop — until two tasks
    contend it in a later loop and it raises.
    """
    locks = _REFRESH_LOCKS.setdefault(get_running_loop(), {})
    lock = locks.get(credential_name)
    if lock is None:
        lock = asyncio.Lock()
        locks[credential_name] = lock
    return lock


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
        oauth_provider = _required_oauth_provider(self._provider.name)
        async with _refresh_lock(credential_name):
            # Read inside the lock: a task that waited here while another
            # refreshed sees the rotated credential and skips its own refresh.
            credential = self._credential_store.get_oauth(credential_name)
            if credential is None:
                raise RuntimeError(
                    f"Missing OAuth credentials for {self._provider.name}. "
                    f"Run /login {self._provider.name}."
                )
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

"""Runtime provider construction for Tau coding sessions."""

from os import environ
from typing import Protocol

from tau_ai import (
    AnthropicProvider,
    LLMObserver,
    ModelProvider,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
    OpenAICompatibleProvider,
)
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.oauth import (
    account_id_from_access_token,
    oauth_credential_is_expired,
    refresh_openai_codex_token,
)
from tau_coding.provider_config import (
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    ProviderConfig,
    anthropic_config_from_provider,
    openai_compatible_config_from_provider,
)
from tau_coding.thinking import ThinkingLevel


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
    llm_observer: LLMObserver | None = None,
) -> ClosableModelProvider:
    """Create a runtime model provider from durable provider settings."""
    credentials = credential_store or FileCredentialStore()
    if isinstance(provider, AnthropicProviderConfig):
        return AnthropicProvider(
            anthropic_config_from_provider(provider, credential_reader=credentials),
            observer=llm_observer,
        )
    if isinstance(provider, OpenAICodexProviderConfig):
        return OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=OpenAICodexCredentialResolver(
                    provider,
                    credential_store=credentials,
                ),
                base_url=provider.base_url,
                headers=provider.headers,
                timeout_seconds=provider.timeout_seconds,
                max_retries=provider.max_retries,
                max_retry_delay_seconds=provider.max_retry_delay_seconds,
            ),
            observer=llm_observer,
        )
    return OpenAICompatibleProvider(
        openai_compatible_config_from_provider(
            provider,
            credential_reader=credentials,
            model=model,
            thinking_level=thinking_level,
        ),
        observer=llm_observer,
    )


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
        self._credential_store.set_oauth(credential_name, refreshed)
        return refreshed

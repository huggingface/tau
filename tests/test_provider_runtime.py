from dataclasses import replace

import pytest

from tau_ai import AnthropicProvider, OpenAICodexProvider, OpenAICompatibleProvider
from tau_coding import provider_runtime
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.provider_config import (
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderModelMetadata,
    provider_config_from_catalog_entry,
    resolve_startup_thinking_level,
)
from tau_coding.provider_runtime import OpenAICodexCredentialResolver, create_model_provider


def test_create_model_provider_returns_openai_codex_provider(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")

    provider = create_model_provider(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICodexProvider)


def test_create_model_provider_uses_codex_model_image_capability(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    config = provider_config_from_catalog_entry("openai-codex")

    vision_provider = create_model_provider(
        config,
        credential_store=store,
        model="gpt-5.6-sol",
    )
    text_provider = create_model_provider(
        config,
        credential_store=store,
        model="gpt-5.3-codex-spark",
    )

    assert isinstance(vision_provider, OpenAICodexProvider)
    assert isinstance(text_provider, OpenAICodexProvider)
    assert vision_provider._config.supports_images is True
    assert text_provider._config.supports_images is False


def test_direct_openai_runtime_enables_responses_cache_affinity(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key("openai", "sk-test")

    provider = create_model_provider(
        provider_config_from_catalog_entry("openai"),
        credential_store=store,
        model="gpt-5.4",
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.compat["supportsPromptCacheKey"] is True
    assert provider._config.compat["sendSessionAffinityHeaders"] is True
    assert provider._config.compat["sessionAffinityFormat"] == "openai"


def test_compatible_gateway_defaults_to_no_openai_cache_affinity(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key("together", "gateway-key")

    provider = create_model_provider(
        provider_config_from_catalog_entry("together"),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.compat["supportsPromptCacheKey"] is False
    assert provider._config.compat["sendSessionAffinityHeaders"] is False


def test_create_model_provider_uses_anthropic_oauth_runtime_auth(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "anthropic",
        OAuthCredential(
            access="anthropic-oauth-access",
            refresh="anthropic-refresh",
            expires=9999999999999,
        ),
    )

    provider = create_model_provider(AnthropicProviderConfig(), credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.bearer_auth is True
    assert provider._config.credential_resolver is not None
    assert provider._config.oauth_system_prompt is not None
    assert provider._config.headers is not None
    assert provider._config.headers["Authorization"] == "Bearer anthropic-oauth-access"
    # Subscription auth is not billed per token, so ask for the 1 hour cache TTL.
    assert provider._config.cache_retention == "long"


def test_anthropic_api_key_auth_keeps_the_default_cache_retention(tmp_path) -> None:
    """1h cache writes cost 2x base, so an API-key user must not get them silently."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key("anthropic", "sk-test")

    provider = create_model_provider(AnthropicProviderConfig(), credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "short"


@pytest.mark.parametrize(
    "provider_name",
    ["minimax", "minimax-cn", "fireworks", "vercel-ai-gateway"],
)
def test_anthropic_protocol_gateways_disable_cache_breakpoints(
    provider_name: str,
    tmp_path,
) -> None:
    """Gateways speaking the Anthropic protocol may reject cache_control blocks."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key(provider_name, "gateway-key")
    config = provider_config_from_catalog_entry(provider_name)

    provider = create_model_provider(config, credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "none"


def test_provider_compat_can_re_enable_cache_breakpoints_on_a_gateway(tmp_path) -> None:
    """A gateway proxying real Claude must be able to opt back in without a source edit."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key("minimax", "gateway-key")
    config = provider_config_from_catalog_entry("minimax")
    config = replace(config, compat={**config.compat, "supportsCacheControl": True})

    provider = create_model_provider(config, credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "short"
    assert provider._config.cache_control_on_tools is True


def test_provider_compat_can_clamp_the_one_hour_ttl_on_oauth(tmp_path) -> None:
    """The escape hatch if Anthropic ever stops honoring ttl=1h on subscriptions."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "anthropic",
        OAuthCredential(access="a", refresh="r", expires=9999999999999),
    )
    config = replace(AnthropicProviderConfig(), compat={"supportsLongCacheRetention": False})

    provider = create_model_provider(config, credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "short"


def test_provider_compat_can_suppress_only_the_tools_breakpoint(tmp_path) -> None:
    """Some gateways accept cache_control everywhere except inside tool objects."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_api_key("anthropic", "sk-test")
    config = replace(AnthropicProviderConfig(), compat={"supportsCacheControlOnTools": False})

    provider = create_model_provider(config, credential_store=store)

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "short"
    assert provider._config.cache_control_on_tools is False


def test_copilot_anthropic_protocol_models_disable_cache_breakpoints(tmp_path) -> None:
    """Copilot proxies the Anthropic protocol, so it gets no cache_control either."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "github-copilot",
        OAuthCredential(
            access="tid=1;proxy-ep=proxy.business.githubcopilot.com",
            refresh="github-token",
            expires=9999999999999,
        ),
    )

    provider = create_model_provider(
        provider_config_from_catalog_entry("github-copilot"),
        credential_store=store,
        model="claude-haiku-4.5",
    )

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "none"


def test_copilot_model_metadata_compat_can_enable_cache_breakpoints(tmp_path) -> None:
    """Per-model compat reaches the openai-compatible Anthropic-protocol branch."""
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "github-copilot",
        OAuthCredential(
            access="tid=1;proxy-ep=proxy.business.githubcopilot.com",
            refresh="github-token",
            expires=9999999999999,
        ),
    )
    config = provider_config_from_catalog_entry("github-copilot")
    metadata = config.model_metadata["claude-haiku-4.5"]
    config = replace(
        config,
        model_metadata={
            **config.model_metadata,
            "claude-haiku-4.5": replace(
                metadata, compat={**metadata.compat, "supportsCacheControl": True}
            ),
        },
    )

    provider = create_model_provider(config, credential_store=store, model="claude-haiku-4.5")

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.cache_retention == "long"


def test_create_model_provider_uses_copilot_token_base_url(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "github-copilot",
        OAuthCredential(
            access="tid=1;proxy-ep=proxy.business.githubcopilot.com",
            refresh="github-token",
            expires=9999999999999,
        ),
    )
    provider = create_model_provider(
        provider_config_from_catalog_entry("github-copilot"),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.base_url == "https://api.business.githubcopilot.com"
    assert provider._config.credential_resolver is not None


def test_create_model_provider_rejects_model_not_declared_for_provider(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = OpenAICompatibleProviderConfig(
        name="local",
        models=("qwen",),
        default_model="qwen",
    )

    with pytest.raises(
        ProviderConfigError,
        match="Model is not configured for provider local: llama",
    ):
        create_model_provider(provider_config, credential_store=store, model="llama")


def test_create_model_provider_maps_codex_reasoning_effort_like_pi(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = OpenAICodexProviderConfig(
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=("gpt-5.5",),
        thinking_parameter="reasoning.effort",
    )

    off_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="off",
    )
    minimal_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="minimal",
    )
    xhigh_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="xhigh",
    )

    assert isinstance(off_provider, OpenAICodexProvider)
    assert isinstance(minimal_provider, OpenAICodexProvider)
    assert isinstance(xhigh_provider, OpenAICodexProvider)
    assert off_provider._config.reasoning_effort is None
    assert minimal_provider._config.reasoning_effort == "low"
    assert xhigh_provider._config.reasoning_effort == "xhigh"


def test_create_model_provider_coerces_unsupported_startup_thinking_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    # Regression: startup used to pass the global default ("medium") straight
    # to create_model_provider, which crashed for models like kimi-code:k3
    # that only support xhigh.
    monkeypatch.setenv("TAU_TEST_KIMI_CODE_API_KEY", "test-key")
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = OpenAICompatibleProviderConfig(
        name="kimi-code",
        api_key_env="TAU_TEST_KIMI_CODE_API_KEY",
        models=("k3",),
        default_model="k3",
        thinking_levels=("medium", "xhigh"),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
        model_metadata={
            "k3": ProviderModelMetadata(
                reasoning=True,
                thinking_level_map={
                    "off": None,
                    "minimal": None,
                    "low": None,
                    "medium": None,
                    "high": None,
                    "xhigh": "max",
                },
            ),
        },
    )

    with pytest.raises(
        ProviderConfigError,
        match="Thinking mode medium is not available for kimi-code:k3",
    ):
        create_model_provider(
            provider_config,
            credential_store=store,
            model="k3",
            thinking_level="medium",
        )

    provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="k3",
        thinking_level=resolve_startup_thinking_level(provider_config, "k3"),
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.reasoning_effort == "max"


@pytest.mark.anyio
async def test_openai_codex_credential_resolver_refreshes_expired_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "openai-codex",
        OAuthCredential(
            access="old-access",
            refresh="old-refresh",
            expires=1,
            account_id="old-account",
        ),
    )

    async def fake_refresh(refresh_token: str) -> OAuthCredential:
        assert refresh_token == "old-refresh"
        return OAuthCredential(
            access="new-access",
            refresh="new-refresh",
            expires=9999999999999,
            account_id="new-account",
        )

    monkeypatch.setattr(provider_runtime, "refresh_openai_codex_token", fake_refresh)

    resolver = OpenAICodexCredentialResolver(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    credentials = await resolver()

    assert credentials.access_token == "new-access"
    assert credentials.account_id == "new-account"
    assert store.get_oauth("openai-codex") == OAuthCredential(
        access="new-access",
        refresh="new-refresh",
        expires=9999999999999,
        account_id="new-account",
    )

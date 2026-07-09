import pytest

from tau_ai import AnthropicProvider, OpenAICodexProvider
from tau_coding import provider_runtime
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.provider_config import (
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderModelMetadata,
)
from tau_coding.provider_runtime import OpenAICodexCredentialResolver, create_model_provider


def test_create_model_provider_returns_openai_codex_provider(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")

    provider = create_model_provider(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICodexProvider)


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


def test_create_model_provider_preserves_adaptive_thinking_type_when_swapping_to_anthropic(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model with ``kind=anthropic`` and a ``thinking_level_map`` value of
    ``adaptive`` must end up with ``thinking_type='adaptive'`` (not the budget
    fallback) once the runtime swaps to ``AnthropicProviderConfig``."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = OpenAICompatibleProviderConfig(
        name="opencode-go",
        base_url="https://opencode.ai/zen/go/v1",
        models=("minimax-m3",),
        default_model="minimax-m3",
        thinking_levels=("off", "high"),
        thinking_models=("minimax-m3",),
        thinking_default="high",
        model_metadata={
            "minimax-m3": ProviderModelMetadata(
                api="anthropic-messages",
                thinking_level_map={"off": "disabled", "high": "adaptive"},
            ),
        },
    )

    runtime_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="minimax-m3",
        thinking_level="high",
    )

    assert isinstance(runtime_provider, AnthropicProvider)
    assert runtime_provider._config.thinking_type == "adaptive"
    assert runtime_provider._config.thinking_budget_tokens is None


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
